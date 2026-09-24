"""Receiver UI: settings, preview and explicit apply on top of the scan controller."""

from Components.ActionMap import NumberActionMap
from Components.config import KEY_0, KEY_LEFT, KEY_RIGHT, configfile, getConfigListEntry
from Components.ConfigList import ConfigList
from Components.Label import Label
from Components.MenuList import MenuList
from Components.ProgressBar import ProgressBar
from Screens.Screen import Screen

from . import controller as scan
from .checks import NoTransponder, offer_channel_search, scan_candidates
from .config import device_resolver, plan_arguments, setting_entries, settings
from .i18n import _
from .skin import make_skin


class NavigatorScreen(Screen):
    def __init__(self, session):
        self.skin = make_skin()
        Screen.__init__(self, session)
        self.cfg = settings()
        self["config"] = ConfigList(
            [getConfigListEntry(label, value) for label, value in setting_entries(self.cfg)]
        )
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
        self.controller = scan.ScanController(session, self, scan.CONFIG_DIR)
        self.onClose.append(self.controller.close)

    def edit(self, key):
        if not self.controller.busy:
            self["config"].handleKey(key)

    def number(self, value):
        self.edit(KEY_0 + value)

    def move(self, direction):
        if self.controller.busy:
            return
        widget = self["config"].instance
        widget.moveSelection(widget.moveUp if direction < 0 else widget.moveDown)

    # Controller listener -------------------------------------------------------------

    def status(self, message):
        self["status"].setText(message)
        if self.controller.busy:
            self["red"].setText(_("Cancel"))

    def progress(self, event):
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

    def finished(self, operation, event, code):
        self["red"].setText(_("Close"))
        kind = event.get("event")
        if kind == "cancelled":
            self.status(_("Scan cancelled; the channel list was not changed."))
        elif kind == "result":
            self.show_result(event)
        elif kind in ("applied", "restored"):
            if event.get("reload_failed"):
                self.status(_("Files were written; restart the Enigma2 GUI to see the list."))
            elif operation == "apply":
                self.status(_("The channel list was updated."))
            else:
                self.status(_("The previous list was restored."))
        else:
            self.status(event.get("message") or _("Operation failed (code %s).") % code)

    def show_result(self, event):
        matched = {item["lcn"] for item in event["matched"]}
        self["channels"].setList(
            [
                "%4d  %s%s"
                % (
                    item["lcn"],
                    item["name"],
                    "" if item["lcn"] in matched else "  [%s]" % _("no match"),
                )
                for item in event["channels"]
            ]
        )
        self["progress"].setValue(100 if event["complete"] else 0)
        self["metrics"].setText(
            _(
                "%(channels)d channels · %(matched)d matched · %(skipped)d skipped · "
                "%(bouquets)d bouquets"
            )
            % {
                "channels": len(event["channels"]),
                "matched": len(event["matched"]),
                "skipped": len(event["skipped"]),
                "bouquets": len(event.get("bouquets", [])),
            }
        )
        if event["can_apply"]:
            self.status(
                " ".join(event["warnings"] + [_("Preview ready. Press Yellow to apply the list.")])
            )
        else:
            self.status(" ".join(event["warnings"]) or _("The table could not be validated."))

    # Keys ------------------------------------------------------------------------------

    def start_scan(self):
        if self.controller.busy:
            return
        try:
            candidates = scan_candidates(self.session, self.controller.lamedb, self.cfg)
        except NoTransponder as error:
            self.status(str(error))
            offer_channel_search(self.session, str(error))
            return
        except Exception as error:
            self.status(str(error))
            return
        for entry in self["config"].list:
            entry[1].save()
        configfile.save()
        self["channels"].setList([])
        self["progress"].setValue(0)
        self.controller.start(
            candidates,
            self.cfg.timeout.value,
            device_resolver(self.cfg, self.session, scan.playing_demux),
            plan_arguments(self.cfg),
        )

    def apply(self):
        if not self.controller.busy and not self.controller.apply():
            self.status(_("Run a successful scan first."))

    def restore(self):
        if not self.controller.busy and not self.controller.restore():
            self.status(_("No TKGS Navigator backup to undo."))

    def cancel(self):
        if not self.controller.cancel():
            self.close()
