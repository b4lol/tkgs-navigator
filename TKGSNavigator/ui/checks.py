"""Checks and preparations before tuning, shared by the screen and the background updater."""

from ..core.constants import DEFAULT_ORBITAL, POLARIZATIONS, TKGS_TRANSPONDERS
from ..core.discovery import parse_target, plan_discovery
from ..core.lamedb import ServiceDatabase
from .i18n import _


class NoTransponder(ValueError):
    """lamedb has no transponder on the orbital to look for the table on."""


def tuner_slot(orbital=DEFAULT_ORBITAL):
    """Slot of the first DVB-S tuner set up for the orbital; False if none, None if unknown."""
    try:
        from Components.NimManager import nimmanager

        for slot in nimmanager.getNimListOfType("DVB-S"):
            if any(satellite[0] == orbital for satellite in nimmanager.getSatListForNim(slot)):
                return slot
        return False
    except Exception:
        return None


def scan_candidates(session, lamedb, cfg):
    """Return the transponders to try, or raise ValueError explaining why a scan cannot start."""
    if session.nav.getRecordings():
        raise ValueError(_("Cannot scan while a recording is in progress."))
    if tuner_slot() is False:
        raise ValueError(_("No tuner is set up for Türksat 42.0°E."))
    candidates = plan_discovery(ServiceDatabase.load(lamedb), parse_target(cfg.learned.value))
    if not candidates:
        raise NoTransponder(_("No Türksat 42.0°E channels are in the service database."))
    return candidates


def _satellite_parameters(enigma, target):
    satellite = enigma.eDVBFrontendParametersSatellite
    parameters = satellite()
    parameters.frequency = target.frequency * 1000
    parameters.symbol_rate = target.symbol_rate * 1000
    parameters.polarisation = POLARIZATIONS[target.polarization]
    parameters.fec = satellite.FEC_Auto
    parameters.inversion = satellite.Inversion_Unknown
    parameters.orbital_position = DEFAULT_ORBITAL
    parameters.system = satellite.System_DVB_S
    parameters.modulation = satellite.Modulation_QPSK
    parameters.rolloff = satellite.RollOff_alpha_0_35
    parameters.pilot = satellite.Pilot_Unknown
    frontend = enigma.eDVBFrontendParameters()
    frontend.setDVBS(parameters)
    return frontend


def run_channel_search(session, on_done):
    """Run the receiver's own channel search on the TKGS transponders with network search.

    Network search follows the NIT, so every Türksat transponder it lists is scanned as
    well. on_done() is called when the search screen closes. Returns False when the image
    does not offer the scanner API, so the caller can fall back to asking the user.
    """
    try:
        import enigma
        from Screens.ServiceScan import ServiceScan

        slot = tuner_slot()
        if slot is None or slot is False:
            return False
        transponders = [_satellite_parameters(enigma, target) for target in TKGS_TRANSPONDERS]
        scan = {
            "transponders": transponders,
            "feid": slot,
            "flags": enigma.eComponentScan.scanNetworkSearch,
            "networkid": 0,
        }
    except Exception:
        return False

    def closed(*result):
        try:
            enigma.eDVBDB.getInstance().saveServicelist()
        except Exception:
            pass
        on_done()

    session.openWithCallback(closed, ServiceScan, [scan])
    return True


def offer_channel_search(session, message):
    """Ask before opening the receiver's channel search screen (fallback for old images)."""
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
