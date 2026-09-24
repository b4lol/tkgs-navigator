"""Receiver UI: tune on the main loop, collect in a worker, apply explicitly."""

import json
from pathlib import Path
import shlex
import shutil
import tempfile
import time

from Components.ActionMap import NumberActionMap
from Components.config import KEY_0, KEY_LEFT, KEY_RIGHT, configfile, getConfigListEntry
from Components.ConfigList import ConfigList
from Components.Label import Label
from Components.MenuList import MenuList
from Components.ProgressBar import ProgressBar
from enigma import eConsoleAppContainer, eDVBDB, eServiceReference, eTimer
from Screens.Screen import Screen

from ..core.constants import TKGS_TRANSPONDERS, TuningTarget
from ..core.lamedb import ServiceDatabase
from ..core.storage import BACKUP_DIR
from .config import settings
from .i18n import _
from .signals import bind_signal, unbind_signal
from .skin import make_skin

CONFIG_DIR = Path("/etc/enigma2")
LAMEDB = CONFIG_DIR / "lamedb"
OUTPUT_LIMIT = 2 * 1024 * 1024
LOCK_DEADLINE = 12
IDLE_TIMEOUT = 20


class NavigatorScreen(Screen):
    def __init__(self, session):
        self.skin = make_skin()
        Screen.__init__(self, session)
        self.cfg = settings()
        entries = [
            (_("Frequency (MHz)"), self.cfg.frequency),
            (_("Polarization"), self.cfg.polarization),
            (_("Symbol rate (kSym/s)"), self.cfg.symbol_rate),
            (_("Max scan (s)"), self.cfg.timeout),
            (_("DVB adapter"), self.cfg.adapter),
            (_("Demux number"), self.cfg.demux),
        ]
        self["config"] = ConfigList([getConfigListEntry(label, value) for label, value in entries])
        for name, text in {
            "title": "TKGS NAVIGATOR",
            "subtitle": _("Türksat 42°E · Local scan · Preview and apply"),
            "status": _("Check the settings, then press Green to scan."),
            "metrics": _(
                "The service database is left untouched. "
                "The new list is written to a separate bouquet."
            ),
            "red": _("Close"),
            "green": _("Scan"),
            "yellow": _("Apply"),
            "blue": _("Undo"),
        }.items():
            self[name] = Label(text)
        self["channels"] = MenuList([])
        self["progress"] = ProgressBar()
        self["progress"].setRange((0, 100))
        actions = {
            "red": self.cancel,
            "cancel": self.cancel,
            "green": self.start_scan,
            "ok": self.start_scan,
            "yellow": self.apply,
            "blue": self.restore,
            "up": lambda: self.move(-1),
            "down": lambda: self.move(1),
            "left": lambda: self.edit(KEY_LEFT),
            "right": lambda: self.edit(KEY_RIGHT),
            "pageUp": lambda: self["channels"].pageUp(),
            "pageDown": lambda: self["channels"].pageDown(),
        }
        for digit in range(10):
            actions[str(digit)] = self.number
        self["actions"] = NumberActionMap(
            ["SetupActions", "ColorActions", "NumberActions", "DirectionActions"], actions, -1
        )
        self.state = "idle"
        self.report = None
        self.pending = None
        self.buffer = ""
        self.original = None
        self.playback_changed = False
        self.cancel_requested = False
        self.closed = False
        self.workdir = Path(tempfile.mkdtemp(prefix="tkgs-navigator-"))
        self.capture_path = self.workdir / "capture.json"
        self.container = eConsoleAppContainer()
        self.bindings = [
            bind_signal(self.container, "dataAvail", self.receive),
            bind_signal(self.container, "appClosed", self.finished),
        ]
        self.timer = eTimer()
        if hasattr(self.timer, "callback"):
            self.bindings.append(bind_signal(self.timer, "callback", self.check_lock))
        else:
            self.bindings.append(bind_signal(self.timer, "timeout", self.check_lock))
        self.last_backup = self._latest_backup()
        self.onClose.append(self.cleanup)

    @staticmethod
    def _latest_backup():
        root = CONFIG_DIR / BACKUP_DIR
        if not root.exists():
            return None
        backups = sorted(p.name for p in root.iterdir() if (p / "manifest.json").is_file())
        return backups[-1] if backups else None

    def edit(self, key):
        if self.state == "idle":
            self["config"].handleKey(key)

    def number(self, value):
        self.edit(KEY_0 + value)

    def move(self, direction):
        if self.state != "idle":
            return
        widget = self["config"].instance
        widget.moveSelection(widget.moveUp if direction < 0 else widget.moveDown)

    def status(self, message):
        self["status"].setText(message)

    def start_scan(self):
        if self.state != "idle":
            return
        try:
            if self.session.nav.getRecordings():
                raise ValueError(_("Cannot scan while a recording is in progress."))
            database = ServiceDatabase.load(LAMEDB)
            self.candidates = database.tuning_candidates(self._targets())
            if not self.candidates:
                raise ValueError(
                    _(
                        "No TKGS transponder is in the service database. "
                        "Run the receiver's network scan first."
                    )
                )
            self.device = "/dev/dvb/adapter%d/demux%d" % (
                self.cfg.adapter.value,
                self.cfg.demux.value,
            )
            if not Path(self.device).exists():
                raise ValueError(_("Selected DVB device not found: ") + self.device)
            for entry in self["config"].list:
                entry[1].save()
            configfile.save()
            self.report = None
            self["channels"].setList([])
            self["progress"].setValue(0)
            self.original = self.session.nav.getCurrentlyPlayingServiceReference()
            self.playback_changed = True
            self.cancel_requested = False
            self._tune(0)
        except Exception as error:
            self._fail(str(error))

    def _targets(self):
        configured = TuningTarget(
            self.cfg.frequency.value, self.cfg.polarization.value, self.cfg.symbol_rate.value
        )
        return (configured,) + TKGS_TRANSPONDERS

    def _tune(self, index, reason=""):
        target, service = self.candidates[index]
        self.candidate = index
        self.target_reference = service.reference
        self.state = "tuning"
        self.deadline = time.monotonic() + LOCK_DEADLINE
        self.status(
            (reason + " " if reason else "")
            + _("Tuning to %(frequency)d %(polarization)s %(symbol_rate)d; waiting for tuner lock…")
            % target._asdict()
        )
        if self.session.nav.playService(eServiceReference(service.reference)):
            raise ValueError(_("Could not play the TKGS service"))
        self.timer.start(200, False)
        self["red"].setText(_("Cancel"))

    def _try_next(self, reason):
        """Tune the next candidate transponder; return False when none is left."""
        if self.candidate + 1 >= len(self.candidates):
            return False
        try:
            self._tune(self.candidate + 1, reason)
        except Exception as error:
            self._fail(str(error))
        return True

    def _fail(self, message):
        self.timer.stop()
        self.state = "idle"
        self.restore_playback()
        self["red"].setText(_("Close"))
        self.status(message)

    def check_lock(self):
        if self.state != "tuning":
            self.timer.stop()
            return
        try:
            service = self.session.nav.getCurrentService()
            frontend = service and service.frontendInfo()
            current = self.session.nav.getCurrentlyPlayingServiceReference()
            status = frontend.getFrontendStatus() if frontend else {}
            locked = (
                current
                and current.toString() == self.target_reference
                and status
                and status.get("tuner_locked")
            )
        except Exception:
            locked = False
        if locked:
            self.timer.stop()
            timeout = self.cfg.timeout.value
            self.launch(
                "scan",
                [
                    "scan",
                    "--device",
                    self.device,
                    "--timeout",
                    str(timeout),
                    "--idle-timeout",
                    str(min(IDLE_TIMEOUT, timeout)),
                    "--lamedb",
                    str(LAMEDB),
                    "--save-capture",
                    str(self.capture_path),
                ],
            )
        elif time.monotonic() >= self.deadline:
            self.timer.stop()
            if not self._try_next(_("Tuner did not lock.")):
                self._fail(_("Tuner did not lock. Check the frequency and satellite settings."))

    def launch(self, state, arguments):
        self.state, self.buffer, self.pending = state, "", None
        worker = Path(__file__).resolve().parents[1] / "worker.py"
        command = " ".join(shlex.quote(arg) for arg in ["python3", "-u", str(worker)] + arguments)
        self.status(
            {
                "scan": _("Fetching the TKGS table…"),
                "apply": _("Backing up and writing the list…"),
                "restore": _("Restoring the previous list…"),
            }[state]
        )
        try:
            code = self.container.execute(command)
        except Exception as error:
            self.pending = {"event": "error", "message": str(error)}
            code = -1
        if code:
            self.finished(code)

    def receive(self, data):
        if self.closed:
            return
        self.buffer += data.decode("ascii", "replace") if isinstance(data, bytes) else data
        if len(self.buffer) > OUTPUT_LIMIT:
            self.buffer = ""
            self.status(_("Process output exceeded the limit."))
            if self.state == "scan":
                self.cancel()
            return
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("event") == "progress":
                count, expected = event["sections"], event["expected"]
                self["progress"].setValue(min(99, int(100 * count / expected)) if expected else 0)
                self["metrics"].setText(
                    _(
                        "%(count)s / %(expected)s sections · %(elapsed).1f s · "
                        "%(duplicates)d duplicates skipped"
                    )
                    % {
                        "count": count,
                        "expected": expected or "?",
                        "elapsed": event["elapsed"],
                        "duplicates": event["duplicates"],
                    }
                )
            else:
                self.pending = event

    def finished(self, code):
        if self.closed:
            return
        operation = self.state
        event = self.pending or {}
        if (
            operation == "scan"
            and not self.cancel_requested
            and event.get("event") == "result"
            and event.get("sections") == 0
            and self._try_next(_("No TKGS data on this transponder."))
        ):
            return
        self.state = "idle"
        self["red"].setText(_("Close"))
        if operation == "scan":
            self.restore_playback()
        if self.cancel_requested:
            self.cancel_requested = False
            self.report = None
            self.status(_("Scan cancelled; the channel list was not changed."))
        elif event.get("event") == "result" and code in (0, 2):
            self.report = event
            matched = {item["lcn"] for item in event["matched"]}
            rows = [
                "%4d  %s%s"
                % (
                    item["lcn"],
                    item["name"],
                    "" if item["lcn"] in matched else "  [%s]" % _("no match"),
                )
                for item in event["channels"]
            ]
            self["channels"].setList(rows)
            self["progress"].setValue(100 if event["complete"] else 0)
            self["metrics"].setText(
                _("%(channels)d channels · %(matched)d matched · %(skipped)d skipped")
                % {
                    "channels": len(event["channels"]),
                    "matched": len(event["matched"]),
                    "skipped": len(event["skipped"]),
                }
            )
            if event["can_apply"]:
                self.status(
                    " ".join(
                        event["warnings"] + [_("Preview ready. Press Yellow to apply the list.")]
                    )
                )
            else:
                self.status(" ".join(event["warnings"]) or _("The table could not be validated."))
        elif code == 0 and event.get("event") in ("applied", "restored"):
            self.last_backup = event.get("backup") or self.last_backup
            self.report = None
            try:
                eDVBDB.getInstance().reloadBouquets()
                self.status(
                    _("The channel list was updated.")
                    if operation == "apply"
                    else _("The previous list was restored.")
                )
            except Exception:
                self.status(_("Files were written; restart the Enigma2 GUI to see the list."))
        else:
            self.status(event.get("message", _("Operation failed (code %s).") % code))

    def apply(self):
        if self.state != "idle":
            return
        if not self.report or not self.report.get("can_apply"):
            self.status(_("Run a successful scan first."))
            return
        self.launch(
            "apply", ["apply", "--capture", str(self.capture_path), "--config-dir", str(CONFIG_DIR)]
        )

    def restore(self):
        if self.state != "idle":
            return
        if not self.last_backup:
            self.status(_("No TKGS Navigator backup to undo."))
            return
        self.launch(
            "restore", ["restore", "--config-dir", str(CONFIG_DIR), "--backup", self.last_backup]
        )

    def cancel(self):
        if self.state in ("apply", "restore"):
            self.status(_("Waiting for the file operation to finish…"))
        elif self.state == "tuning":
            self._fail(_("Scan cancelled."))
        elif self.state == "scan":
            self.cancel_requested = True
            self.status(_("Stopping the scan…"))
            self.container.kill()
        else:
            self.close()

    def restore_playback(self):
        if self.playback_changed:
            self.playback_changed = False
            try:
                if self.original:
                    self.session.nav.playService(self.original)
                else:
                    self.session.nav.stopService()
            except Exception:
                self.status(_("Could not reopen the previous channel; select it manually."))

    def cleanup(self):
        self.closed = True
        self.timer.stop()
        if self.state == "scan":
            self.container.kill()
        self.restore_playback()
        for binding in self.bindings:
            unbind_signal(binding)
        self.bindings = []
        shutil.rmtree(str(self.workdir), ignore_errors=True)
