"""Order in which transponders are tried when looking for the TKGS table.

1. the transponder where the table was last found (learned),
2. the published TKGS data transponders,
3. every other transponder on the orbital that lamedb can tune (deep search, capped).

Only transponders present in lamedb qualify, and each physical transponder is tried once.
"""

from __future__ import annotations

from typing import NamedTuple, Optional

from .constants import DEFAULT_ORBITAL, POLARIZATIONS, TKGS_TRANSPONDERS, TuningTarget
from .lamedb import Service, ServiceDatabase

MAX_DEEP_CANDIDATES = 40


class Candidate(NamedTuple):
    target: TuningTarget
    service: Service
    deep: bool  # Found by deep search; tried with a shorter idle timeout.


def format_target(target: TuningTarget) -> str:
    return "%d:%s:%d" % target


def parse_target(text: str) -> Optional[TuningTarget]:
    """Read a stored "MHz:polarization:kSym/s" value; None when empty or malformed."""
    parts = text.split(":")
    if len(parts) != 3 or parts[1] not in POLARIZATIONS:
        return None
    try:
        return TuningTarget(int(parts[0]), parts[1], int(parts[2]))
    except ValueError:
        return None


def plan_discovery(
    database: ServiceDatabase,
    learned: Optional[TuningTarget] = None,
    orbital: int = DEFAULT_ORBITAL,
    deep_limit: int = MAX_DEEP_CANDIDATES,
) -> list[Candidate]:
    preferred = ([learned] if learned else []) + list(TKGS_TRANSPONDERS)
    candidates = [
        Candidate(target, service, False)
        for target, service in database.tuning_candidates(preferred, orbital)
    ]
    tried = {candidate.service.key for candidate in candidates}
    others = [target for target in database.orbital_targets(orbital) if target not in preferred]
    deep = 0
    for target, service in database.tuning_candidates(others, orbital):
        if deep >= deep_limit:
            break
        if service.key in tried:
            continue
        tried.add(service.key)
        candidates.append(Candidate(target, service, True))
        deep += 1
    return candidates
