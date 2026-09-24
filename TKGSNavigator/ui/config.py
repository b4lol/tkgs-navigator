"""Persistent plugin settings, created lazily on first use, and what they translate to."""

from pathlib import Path

from Components.config import (
    ConfigInteger,
    ConfigSelection,
    ConfigSubsection,
    ConfigYesNo,
    config,
)

from ..core.constants import TKGS_TRANSPONDERS, TuningTarget
from .i18n import _

DEFAULTS = {
    "frequency": TKGS_TRANSPONDERS[0].frequency,
    "polarization": TKGS_TRANSPONDERS[0].polarization,
    "symbol_rate": TKGS_TRANSPONDERS[0].symbol_rate,
    "timeout": 60,
    "adapter": 0,
    "demux": 0,
    "auto_hour": 5,
}
MAX_TIMESTAMP = 2**31 - 1


def settings():
    if not hasattr(config.plugins, "tkgs_navigator"):
        group = ConfigSubsection()
        group.frequency = ConfigInteger(default=DEFAULTS["frequency"], limits=(3000, 14000))
        group.polarization = ConfigSelection(
            default=DEFAULTS["polarization"], choices=[("V", _("Vertical")), ("H", _("Horizontal"))]
        )
        group.symbol_rate = ConfigInteger(default=DEFAULTS["symbol_rate"], limits=(1000, 45000))
        group.timeout = ConfigInteger(default=DEFAULTS["timeout"], limits=(10, 180))
        group.demux_mode = ConfigSelection(
            default="auto", choices=[("auto", _("Automatic")), ("manual", _("Manual"))]
        )
        group.adapter = ConfigInteger(default=DEFAULTS["adapter"], limits=(0, 15))
        group.demux = ConfigInteger(default=DEFAULTS["demux"], limits=(0, 31))
        group.prefer = ConfigSelection(default="hd", choices=[("hd", "HD"), ("sd", "SD")])
        group.categories = ConfigYesNo(default=False)
        group.align_lcn = ConfigYesNo(default=False)
        group.bouquet_first = ConfigYesNo(default=False)
        group.auto_update = ConfigYesNo(default=False)
        group.auto_hour = ConfigInteger(default=DEFAULTS["auto_hour"], limits=(0, 23))
        group.auto_apply = ConfigYesNo(default=False)
        group.last_auto_update = ConfigInteger(default=0, limits=(0, MAX_TIMESTAMP))
        config.plugins.tkgs_navigator = group
    return config.plugins.tkgs_navigator


def setting_entries(cfg):
    """(label, setting) rows for the settings list, in display order."""
    return [
        (_("Frequency (MHz)"), cfg.frequency),
        (_("Polarization"), cfg.polarization),
        (_("Symbol rate (kSym/s)"), cfg.symbol_rate),
        (_("Max scan (s)"), cfg.timeout),
        (_("Demux selection"), cfg.demux_mode),
        (_("DVB adapter"), cfg.adapter),
        (_("Demux number"), cfg.demux),
        (_("Main list"), cfg.prefer),
        (_("Category bouquets"), cfg.categories),
        (_("Channel number = LCN"), cfg.align_lcn),
        (_("Bouquet at the top"), cfg.bouquet_first),
        (_("Daily automatic update"), cfg.auto_update),
        (_("Update hour"), cfg.auto_hour),
        (_("Apply updates automatically"), cfg.auto_apply),
    ]


def targets(cfg):
    """The configured transponder first, then the known TKGS transponders."""
    configured = TuningTarget(cfg.frequency.value, cfg.polarization.value, cfg.symbol_rate.value)
    return (configured,) + TKGS_TRANSPONDERS


def plan_arguments(cfg):
    """Worker options for the bouquet plan."""
    arguments = ["--prefer", cfg.prefer.value]
    for flag, setting in (
        ("--categories", cfg.categories),
        ("--align-lcn", cfg.align_lcn),
        ("--bouquet-first", cfg.bouquet_first),
    ):
        if setting.value:
            arguments.append(flag)
    return arguments


def device_resolver(cfg, session, detect):
    """Return a callable giving the demux path once the tuner has locked.

    In automatic mode the demux of the playing TKGS service is used when the image exposes
    it; otherwise, and in manual mode, the configured adapter and demux.
    """

    def resolve():
        device = detect(session, cfg.adapter.value) if cfg.demux_mode.value == "auto" else None
        device = device or "/dev/dvb/adapter%d/demux%d" % (cfg.adapter.value, cfg.demux.value)
        if not Path(device).exists():
            raise ValueError(_("Selected DVB device not found: ") + device)
        return device

    return resolve
