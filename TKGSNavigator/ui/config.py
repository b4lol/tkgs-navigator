"""Persistent plugin settings, created lazily on first use."""
from Components.config import (config, ConfigSubsection, ConfigInteger, ConfigSelection)

DEFAULTS = {"frequency": 12380, "polarization": "V", "symbol_rate": 27500,
            "timeout": 60, "adapter": 0, "demux": 0}


def settings():
    if not hasattr(config.plugins, "tkgs_navigator"):
        group = ConfigSubsection()
        group.frequency = ConfigInteger(default=DEFAULTS["frequency"], limits=(3000, 14000))
        group.polarization = ConfigSelection(default=DEFAULTS["polarization"],
                                              choices=[("V", "Vertical"), ("H", "Horizontal")])
        group.symbol_rate = ConfigInteger(default=DEFAULTS["symbol_rate"], limits=(1000, 45000))
        group.timeout = ConfigInteger(default=DEFAULTS["timeout"], limits=(10, 180))
        group.adapter = ConfigInteger(default=DEFAULTS["adapter"], limits=(0, 15))
        group.demux = ConfigInteger(default=DEFAULTS["demux"], limits=(0, 31))
        config.plugins.tkgs_navigator = group
    return config.plugins.tkgs_navigator
