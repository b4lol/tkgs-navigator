"""Scan state machine shared by the screen and the background updater.

Tunes candidate transponders on the Enigma2 main loop, runs the worker in a separate
process and reports to a listener with status(message), progress(event) and
finished(operation, event, code). It owns playback only between tuning and the end of
the scan, and gives it back to whatever was playing before.
"""

import json
from pathlib import Path
import shlex
import shutil
import tempfile
import time

from enigma import eConsoleAppContainer, eDVBDB, eServiceReference, eTimer

from ..core.discovery import format_target
from ..core.storage import BACKUP_DIR
from .i18n import _
from .signals import bind_signal, unbind_signal

CONFIG_DIR = Path("/etc/enigma2")
OUTPUT_LIMIT = 2 * 1024 * 1024
LOCK_DEADLINE = 12
SCAN_TIMEOUT = 120  # Upper bound; a scan ends as soon as the table is complete.
IDLE_TIMEOUT = 20  # Published TKGS transponders.
DEEP_IDLE_TIMEOUT = 8  # Other transponders tried by deep search.
WORKER = Path(__file__).resolve().parents[1] / "worker.py"


def latest_backup(config_dir):
    root = Path(config_dir) / BACKUP_DIR
    if not root.exists():
        return None
    backups = sorted(p.name for p in root.iterdir() if (p / "manifest.json").is_file())
    return backups[-1] if backups else None


def playing_demux(session, adapter=0):
    """Demux device of the playing service, or None when the image does not expose it."""
    try:
        stream = session.nav.getCurrentService().stream()
        data = stream and stream.getStreamingData()
        demux = data and data.get("demux")
    except Exception:
        return None
    if demux is None or demux < 0:
        return None
    return "/dev/dvb/adapter%d/demux%d" % (adapter, demux)


class ScanController:
    def __init__(self, session, listener, config_dir=CONFIG_DIR):
        self.session = session
        self.listener = listener
        self.config_dir = Path(config_dir)
        self.state = "idle"
        self.report = None
        self.pending = None
        self.buffer = ""
        self.original = None
        self.playback_changed = False
        self.cancel_requested = False
        self.closed = False
        self.candidates = []
        self.candidate = 0
        self.options = []
        self.last_backup = latest_backup(self.config_dir)
        self.workdir = Path(tempfile.mkdtemp(prefix="tkgs-navigator-"))
        self.capture_path = self.workdir / "capture.json"
        self.container = eConsoleAppContainer()
        self.bindings = [
            bind_signal(self.container, "dataAvail", self.receive),
            bind_signal(self.container, "appClosed", self.finished),
        ]
        self.timer = eTimer()
        name = "callback" if hasattr(self.timer, "callback") else "timeout"
        self.bindings.append(bind_signal(self.timer, name, self.check_lock))

    @property
    def lamedb(self):
        return self.config_dir / "lamedb"

    @property
    def busy(self):
        return self.state != "idle"

    def start(self, candidates, resolve_devices, options=(), timeout=SCAN_TIMEOUT):
        """Tune candidates[0]; resolve_devices() gives the demuxes to listen on after lock."""
        if self.busy:
            return
        self.candidates = list(candidates)
        self.timeout = timeout
        self.resolve_devices = resolve_devices
        self.options = list(options)
        self.report = None
        self.original = self.session.nav.getCurrentlyPlayingServiceReference()
        self.playback_changed = True
        self.cancel_requested = False
        try:
            self._tune(0)
        except Exception as error:
            self._fail(str(error))

    def _tune(self, index, reason=""):
        candidate = self.candidates[index]
        self.candidate = index
        self.target_reference = candidate.service.reference
        self.state = "tuning"
        self.deadline = time.monotonic() + LOCK_DEADLINE
        values = dict(candidate.target._asdict(), index=index + 1, total=len(self.candidates))
        if candidate.deep:
            message = _(
                "Searching for the TKGS table (%(index)d/%(total)d): "
                "%(frequency)d %(polarization)s %(symbol_rate)d…"
            )
        else:
            message = _(
                "Tuning to %(frequency)d %(polarization)s %(symbol_rate)d; waiting for tuner lock…"
            )
        self.listener.status(
            ("" if candidate.deep or not reason else reason + " ") + message % values
        )
        current = self.session.nav.getCurrentlyPlayingServiceReference()
        playing = current and current.toString() == candidate.service.reference
        # Replaying the running service is refused by enigma2; it is already tuned.
        if not playing and self.session.nav.playService(
            eServiceReference(candidate.service.reference)
        ):
            raise ValueError(_("Could not play the TKGS service"))
        self.timer.start(200, False)

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
        self.listener.finished("scan", {"event": "error", "message": message}, 1)

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
            try:
                devices = self.resolve_devices()
            except Exception as error:
                self._fail(str(error))
                return
            idle = DEEP_IDLE_TIMEOUT if self.candidates[self.candidate].deep else IDLE_TIMEOUT
            arguments = ["scan", "--timeout", str(self.timeout)]
            for device in devices:
                arguments += ["--device", device]
            self.launch(
                "scan",
                arguments
                + ["--idle-timeout", str(min(idle, self.timeout))]
                + ["--lamedb", str(self.lamedb), "--save-capture", str(self.capture_path)]
                + self.options,
            )
        elif time.monotonic() >= self.deadline:
            self.timer.stop()
            if not self._try_next(_("Tuner did not lock.")):
                self._fail(_("Tuner did not lock. Check the frequency and satellite settings."))

    def launch(self, state, arguments):
        self.state, self.buffer, self.pending = state, "", None
        command = " ".join(shlex.quote(arg) for arg in ["python3", "-u", str(WORKER)] + arguments)
        self.listener.status(
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
            self.listener.status(_("Process output exceeded the limit."))
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
                self.listener.progress(event)
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
        if operation == "scan":
            self.restore_playback()
        if self.cancel_requested:
            self.cancel_requested = False
            self.report = None
            event = {"event": "cancelled"}
        elif event.get("event") == "result" and code in (0, 2) and not event.get("sections"):
            event = {
                "event": "error",
                "message": _("No TKGS table was found on %(count)d transponders.")
                % {"count": len(self.candidates)},
            }
        elif event.get("event") == "result" and code in (0, 2):
            event = dict(event, transponder=format_target(self.candidates[self.candidate].target))
            self.report = event
        elif code == 0 and event.get("event") in ("applied", "restored"):
            self.last_backup = event.get("backup") or self.last_backup
            self.report = None
            try:
                eDVBDB.getInstance().reloadBouquets()
            except Exception:
                event = dict(event, reload_failed=True)
        elif event.get("event") not in ("error", "cancelled"):
            event = {"event": "error", "message": _("Operation failed (code %s).") % code}
        self.listener.finished(operation, event, code)

    def apply(self):
        """Apply the last successful preview; return False when there is none."""
        if self.busy or not self.report or not self.report.get("can_apply"):
            return False
        self.launch(
            "apply",
            ["apply", "--capture", str(self.capture_path), "--config-dir", str(self.config_dir)]
            + self.options,
        )
        return True

    def restore(self):
        """Undo the last change; return False when there is no backup."""
        if self.busy or not self.last_backup:
            return False
        self.launch(
            "restore",
            ["restore", "--config-dir", str(self.config_dir), "--backup", self.last_backup],
        )
        return True

    def cancel(self):
        """Stop tuning or scanning; return False when idle (nothing to cancel)."""
        if self.state in ("apply", "restore"):
            self.listener.status(_("Waiting for the file operation to finish…"))
        elif self.state == "tuning":
            self.timer.stop()
            self.state = "idle"
            self.restore_playback()
            self.listener.finished("scan", {"event": "cancelled"}, 130)
        elif self.state == "scan":
            self.cancel_requested = True
            self.listener.status(_("Stopping the scan…"))
            self.container.kill()
        else:
            return False
        return True

    def release_playback(self):
        """Forget the saved service: someone else (e.g. leaving standby) now owns playback."""
        self.playback_changed = False

    def restore_playback(self):
        if self.playback_changed:
            self.playback_changed = False
            try:
                if self.original:
                    self.session.nav.playService(self.original)
                else:
                    self.session.nav.stopService()
            except Exception:
                self.listener.status(
                    _("Could not reopen the previous channel; select it manually.")
                )

    def close(self):
        self.closed = True
        self.timer.stop()
        if self.state == "scan":
            self.container.kill()
        self.restore_playback()
        for binding in self.bindings:
            unbind_signal(binding)
        self.bindings = []
        shutil.rmtree(str(self.workdir), ignore_errors=True)
