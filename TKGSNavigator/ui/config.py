"""Persistent plugin settings, created lazily on first use."""

from Components.config import ConfigInteger, ConfigSelection, ConfigSubsection, config

from ..core.constants import TKGS_TRANSPONDERS
from .i18n import _

DEFAULTS = {
    "frequency": TKGS_TRANSPONDERS[0].frequency,
    "polarization": TKGS_TRANSPONDERS[0].polarization,
    "symbol_rate": TKGS_TRANSPONDERS[0].symbol_rate,
    "timeout": 60,
    "adapter": 0,
    "demux": 0,
}


def settings():
    if not hasattr(config.plugins, "tkgs_navigator"):
        group = ConfigSubsection()
        group.frequency = ConfigInteger(default=DEFAULTS["frequency"], limits=(3000, 14000))
        group.polarization = ConfigSelection(
            default=DEFAULTS["polarization"], choices=[("V", _("Vertical")), ("H", _("Horizontal"))]
        )
        group.symbol_rate = ConfigInteger(default=DEFAULTS["symbol_rate"], limits=(1000, 45000))
        group.timeout = ConfigInteger(default=DEFAULTS["timeout"], limits=(10, 180))
        group.adapter = ConfigInteger(default=DEFAULTS["adapter"], limits=(0, 15))
        group.demux = ConfigInteger(default=DEFAULTS["demux"], limits=(0, 31))
        config.plugins.tkgs_navigator = group
    return config.plugins.tkgs_navigator
