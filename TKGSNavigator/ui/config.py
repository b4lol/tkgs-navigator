"""The one user setting, what the plugin remembers, and what it detects instead of asking."""

import glob
import re

from Components.config import (
    ConfigInteger,
    ConfigSubsection,
    ConfigText,
    ConfigYesNo,
    config,
    configfile,
)

from .i18n import _

MAX_TIMESTAMP = 2**31 - 1
DEMUX_PATTERN = "/dev/dvb/adapter*/demux*"


def settings():
    if not hasattr(config.plugins, "tkgs_navigator"):
        group = ConfigSubsection()
        # Scan on open, apply clean results, and update daily in standby.
        group.automatic = ConfigYesNo(default=True)
        # "MHz:polarization:kSym/s" of the transponder where the table was last found.
        group.learned = ConfigText(default="")
        group.last_auto_update = ConfigInteger(default=0, limits=(0, MAX_TIMESTAMP))
        config.plugins.tkgs_navigator = group
    return config.plugins.tkgs_navigator


def remember(cfg, event):
    """Store where a complete table was found so the next scan starts there."""
    if event.get("complete") and event.get("transponder"):
        cfg.learned.value = event["transponder"]
        cfg.learned.save()
        configfile.save()


def setting_entries(cfg):
    """(label, setting) rows for the settings list."""
    return [(_("Automatic updates"), cfg.automatic)]


def numbering_per_bouquet():
    """True when the image numbers every bouquet from 1 (alternative numbering mode)."""
    try:
        return bool(config.usage.alternative_number_mode.value)
    except Exception:
        return False


def plan_arguments():
    """Worker options for the bouquet plan, chosen for the receiver.

    HD variants first; category, radio and HD/SD lists whenever the table carries the
    data; channel numbers equal to LCNs. Unless the image numbers each bouquet from 1,
    the bouquet is linked at the top so that its numbering starts at 1.
    """
    arguments = ["--prefer", "hd", "--categories", "--align-lcn"]
    if not numbering_per_bouquet():
        arguments.append("--bouquet-first")
    return arguments


def _demux_order(path):
    return tuple(int(number) for number in re.findall(r"\d+", path))


def list_demuxes():
    return sorted(glob.glob(DEMUX_PATTERN), key=_demux_order)


def demux_resolver(session, detect):
    """Return a callable giving the demux devices to listen on once the tuner has locked.

    The demux of the playing TKGS service comes first when the image reports it; every
    other demux follows, and the capture keeps whichever one delivers the table.
    """

    def resolve():
        devices = list_demuxes()
        detected = detect(session)
        if detected in devices:
            devices.remove(detected)
            devices.insert(0, detected)
        if not devices:
            raise ValueError(_("No DVB demux device was found."))
        return devices

    return resolve
