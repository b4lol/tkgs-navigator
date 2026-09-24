"""Checks run before tuning, shared by the screen and the background updater."""

from ..core.constants import DEFAULT_ORBITAL
from ..core.lamedb import ServiceDatabase
from .config import targets
from .i18n import _


class NoTransponder(ValueError):
    """lamedb has none of the TKGS transponders; the receiver's channel search must run."""


def tuner_for_orbital(orbital=DEFAULT_ORBITAL):
    """True/False when a DVB-S tuner is (not) set up for the orbital; None if unknown."""
    try:
        from Components.NimManager import nimmanager

        for slot in nimmanager.getNimListOfType("DVB-S"):
            if any(satellite[0] == orbital for satellite in nimmanager.getSatListForNim(slot)):
                return True
        return False
    except Exception:
        return None


def scan_candidates(session, lamedb, cfg):
    """Return the tuning candidates, or raise ValueError explaining why a scan cannot start."""
    if session.nav.getRecordings():
        raise ValueError(_("Cannot scan while a recording is in progress."))
    if tuner_for_orbital() is False:
        raise ValueError(_("No tuner is set up for Türksat 42.0°E."))
    candidates = ServiceDatabase.load(lamedb).tuning_candidates(targets(cfg))
    if not candidates:
        raise NoTransponder(
            _(
                "No TKGS transponder is in the service database. "
                "Run the receiver's network scan first."
            )
        )
    return candidates


def offer_channel_search(session, message):
    """Ask before opening the receiver's own channel search; nothing is scanned otherwise."""
    from Screens.MessageBox import MessageBox

    def answer(confirmed):
        if confirmed:
            from Screens.ScanSetup import ScanSetup

            session.open(ScanSetup)

    session.openWithCallback(
        answer,
        MessageBox,
        message + "\n" + _("Open the channel search now?"),
        MessageBox.TYPE_YESNO,
    )
