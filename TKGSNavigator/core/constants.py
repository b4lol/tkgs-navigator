"""Single source for TKGS broadcast and Enigma2 service constants."""
from collections import namedtuple

TKGS_PID = 8181
TKGS_TABLE_ID = 0xA7
DEFAULT_ORBITAL = 420  # Türksat 42.0°E, expressed in tenths of a degree.

TuningTarget = namedtuple("TuningTarget", "frequency polarization symbol_rate")

# TKGS data transponders on Türksat 42°E (MHz, polarization, kSym/s), in trial order.
# Published listings, not a broadcaster guarantee; the receiver's lamedb decides which exist.
TKGS_TRANSPONDERS = (TuningTarget(12380, "V", 27500), TuningTarget(12423, "H", 30000))

POLARIZATIONS = {"H": 0, "V": 1, "L": 2, "R": 3}
TV_SERVICE_TYPES = (1, 4, 17, 22, 25, 31, 32)
