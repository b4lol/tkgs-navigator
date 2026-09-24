"""Receiver UI. With automatic updates on (the default) opening it is enough: it finds the
table, fills missing channels with the receiver's own search, and applies a clean result."""

from Components.ActionMap import NumberActionMap
from Components.config import KEY_0, KEY_LEFT, KEY_RIGHT, configfile, getConfigListEntry
from Components.ConfigList import ConfigList
from Components.Label import Label
from Components.MenuList import MenuList
from Components.ProgressBar import ProgressBar
from Screens.Screen import Screen

from . import controller as scan
from .checks import NoTransponder, offer_channel_search, run_channel_search, scan_candidates
from .config import demux_resolver, plan_arguments, remember, setting_entries, settings
from .i18n import _
from .skin import make_skin

# Share of listed channels missing from lamedb above which the receiver's channel search
# is run once before the list is applied.
MISSING_SEARCH_RATIO = 0.3


def missing_ratio(event):
    channels = len(event.get("channels", []))
    missing = sum(1 for item in event.get("skipped", []) if item.get("reason") == "missing")
    return missing / channels if channels else 0.0


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
            "subtitle": _("Türksat 42°E · Automatic TKGS channel list"),
            "status": _("Press Green to update the channel list."),
            "metrics": _(
                "The service database is left untouched. "
                "The new list is written to separate bouquets."
            ),
            "red": _("Close"),
            "green": _("Update now"),
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
        self.searched = False  # The receiver's channel search runs at most once per visit.
        self.onClose.append(self.controller.close)
        self.onFirstExecBegin.append(self.opened)

    def opened(self):
        if self.cfg.automatic.value:
            self.start_scan()

    def edit(self, key):
        if not self.controller.busy:
            self["config"].handleKey(key)
            self["config"].list[0][1].save()
            configfile.save()

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
            remember(self.cfg, event)
            self.show_result(event)
            if not self.searched and missing_ratio(event) > MISSING_SEARCH_RATIO:
                self.search_channels(_("Many listed channels are not in the service database."))
            elif self.cfg.automatic.value and event["can_apply"] and not event["warnings"]:
                self.controller.apply()
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

    # Actions ---------------------------------------------------------------------------

    def search_channels(self, reason):
        """Fill lamedb with the receiver's own channel search, then scan again.

        In automatic mode the search starts by itself; otherwise, or when the image lacks
        the scanner API, the user is asked first.
        """
        self.searched = True
        self.status(reason)
        if self.cfg.automatic.value:
            self.status(reason + " " + _("Searching for channels…"))
            if run_channel_search(self.session, self.start_scan):
                return
        offer_channel_search(self.session, reason)

    def start_scan(self):
        if self.controller.busy:
            return
        try:
            candidates = scan_candidates(self.session, self.controller.lamedb, self.cfg)
        except NoTransponder as error:
            if self.searched:
                self.status(str(error))
            else:
                self.search_channels(str(error))
            return
        except Exception as error:
            self.status(str(error))
            return
        self["channels"].setList([])
        self["progress"].setValue(0)
        self.controller.start(
            candidates, demux_resolver(self.session, scan.playing_demux), plan_arguments()
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
