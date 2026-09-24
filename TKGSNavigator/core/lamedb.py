"""Read lamedb 4/5 without modifying the receiver's service database."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Tuple, Union

from .constants import (
    DEFAULT_ORBITAL,
    POLARIZATIONS,
    RADIO_SERVICE_TYPES,
    TV_SERVICE_TYPES,
    TuningTarget,
)
from .parser import Channel

# (dvb_namespace, transport_stream_id, original_network_id)
TransponderKey = Tuple[int, int, int]
Skipped = Dict[str, Union[int, str]]


@dataclass(frozen=True)
class Transponder:
    key: TransponderKey
    frequency: int
    symbol_rate: int
    polarization: int
    orbital: int


@dataclass(frozen=True)
class Service:
    sid: int
    key: TransponderKey
    kind: int
    name: str

    @property
    def reference(self) -> str:
        """Enigma2 service reference, e.g. 1:0:19:65:1:1:1A40000:0:0:0:."""
        namespace, tsid, onid = self.key
        return "1:0:%X:%X:%X:%X:%X:0:0:0:" % (self.kind, self.sid, tsid, onid, namespace)


class ServiceDatabase:
    def __init__(
        self, transponders: dict[TransponderKey, Transponder], services: list[Service]
    ) -> None:
        self.transponders = transponders
        self.services = services
        self.by_sid: dict[int, list[Service]] = {}
        self.by_key: dict[TransponderKey, list[Service]] = {}
        for service in services:
            self.by_sid.setdefault(service.sid, []).append(service)
            self.by_key.setdefault(service.key, []).append(service)

    @classmethod
    def load(cls, path: str | Path) -> ServiceDatabase:
        """Read lamedb, or lamedb5 next to it when lamedb is absent."""
        path = Path(path)
        if not path.exists() and path.name == "lamedb":
            path = path.with_name("lamedb5")
        return cls.parse(path.read_text(encoding="utf-8", errors="replace"))

    @classmethod
    def parse(cls, text: str) -> ServiceDatabase:
        """Parse lamedb 4 or 5 text.

        Raises:
            ValueError: on an unsupported version or a truncated record.
        """
        lines = text.splitlines()
        if not lines or lines[0].strip() not in ("eDVB services /4/", "eDVB services /5/"):
            raise ValueError("Only lamedb 4 and 5 are supported")
        transponders: dict[TransponderKey, Transponder] = {}
        services: list[Service] = []

        def add_tp(identity: str, params: str) -> bool:
            try:
                fields = [int(v, 16) for v in identity.split(":")[:3]]
                if len(fields) != 3:
                    return False
            except ValueError:
                return False
            if not params.startswith(("s ", "s:")):
                return True  # terrestrial/cable record: consumed, not stored
            try:
                values = params[2:].split(",", 1)[0].split(":")
                if len(values) < 5:
                    return True
                key = (fields[0], fields[1], fields[2])
                freq, sr, pol, _, orbital = map(int, values[:5])
            except ValueError:
                return True
            transponders[key] = Transponder(key, freq, sr, pol, orbital % 3600)
            return True

        def add_service(identity: str, name: str) -> bool:
            try:
                values = identity.split(":")
                if len(values) < 6:
                    return False
                sid, ns, tsid, onid = (int(v, 16) for v in values[:4])
                kind = int(values[4], 10)  # lamedb writes service_type in decimal.
            except ValueError:
                return False
            # Stray records (e.g. the all-zero placeholder some editors leave
            # behind) are skipped without consuming the following name line.
            if not 0 < sid <= 65535 or not 0 <= kind <= 255:
                return False
            services.append(Service(sid, (ns, tsid, onid), kind, name))
            return True

        if "/5/" in lines[0]:
            for line in lines[1:]:
                if line.startswith("t:"):
                    identity, params = line[2:].split(",", 1)
                    add_tp(identity, params)
                elif line.startswith("s:"):
                    identity, rest = line[2:].split(",", 1)
                    add_service(identity, next(csv.reader([rest]))[0])
        else:
            mode, index = "", 1
            while index < len(lines):
                line = lines[index].strip()
                index += 1
                if line in ("transponders", "services", "end"):
                    mode = line
                    continue
                if not line or line == "/":
                    continue
                if mode == "transponders":
                    if index >= len(lines):
                        raise ValueError("Truncated transponder record")
                    if add_tp(line, lines[index].strip()):
                        index += 1
                elif mode == "services":
                    if index + 1 >= len(lines):
                        raise ValueError("Truncated service record")
                    if add_service(line, lines[index]):
                        index += 2
        return cls(transponders, services)

    def tuning_service(
        self,
        frequency_mhz: int,
        polarization: str,
        symbol_rate_ksym: int,
        orbital: int = DEFAULT_ORBITAL,
    ) -> Service:
        """Return a service on the matching transponder to tune the frontend with.

        Raises:
            ValueError: when no such transponder is in lamedb.
        """
        pol = POLARIZATIONS[polarization]
        keys = {
            key
            for key, tp in self.transponders.items()
            if tp.orbital == orbital
            and tp.polarization == pol
            and abs(tp.frequency - frequency_mhz * 1000) <= 2000
            and abs(tp.symbol_rate - symbol_rate_ksym * 1000) <= 1000
        }
        candidates = [service for key in keys for service in self.by_key.get(key, ())]
        if not candidates:
            raise ValueError(
                "TKGS frequency is not in the service database. "
                "Run the receiver's network scan first."
            )
        return sorted(candidates, key=lambda s: (s.key, s.sid))[0]

    def tuning_candidates(
        self, targets: Iterable[TuningTarget], orbital: int = DEFAULT_ORBITAL
    ) -> list[tuple[TuningTarget, Service]]:
        """Return (target, service) for each distinct target found in lamedb, in order."""
        candidates: list[tuple[TuningTarget, Service]] = []
        seen: set[TuningTarget] = set()
        for target in targets:
            if target in seen:
                continue
            seen.add(target)
            try:
                service = self.tuning_service(
                    target.frequency, target.polarization, target.symbol_rate, orbital
                )
            except ValueError:
                continue
            candidates.append((target, service))
        return candidates

    def orbital_targets(self, orbital: int = DEFAULT_ORBITAL) -> list[TuningTarget]:
        """Transponders on the orbital that have at least one service, by frequency."""
        letters = {code: letter for letter, code in POLARIZATIONS.items()}
        targets = {
            TuningTarget(
                round(tp.frequency / 1000), letters[tp.polarization], round(tp.symbol_rate / 1000)
            )
            for key, tp in self.transponders.items()
            if tp.orbital == orbital and key in self.by_key and tp.polarization in letters
        }
        return sorted(targets)

    def _on_orbital(self, service: Service, orbital: int) -> bool:
        transponder = self.transponders.get(service.key)
        return transponder is not None and transponder.orbital == orbital

    def candidates(self, channel: Channel, orbital: int = DEFAULT_ORBITAL) -> list[Service]:
        """Services on the orbital that may carry the channel.

        When the table supplies TSID and ONID the full key must match; otherwise the SID
        alone is used. Radio channels match radio service types, all others TV types.
        """
        kinds = RADIO_SERVICE_TYPES if channel.radio else TV_SERVICE_TYPES
        found = {}
        for service in self.by_sid.get(channel.sid, []):
            if service.kind not in kinds or not self._on_orbital(service, orbital):
                continue
            if channel.tsid is not None and service.key[1] != channel.tsid:
                continue
            if channel.onid is not None and service.key[2] != channel.onid:
                continue
            found[service.reference] = service
        return list(found.values())

    def key_hit_rate(self, channels: Iterable[Channel], orbital: int = DEFAULT_ORBITAL) -> float:
        """Share of channels whose (ONID, TSID, SID) exists on the orbital, ignoring type."""
        keys = {
            (service.key[2], service.key[1], service.sid)
            for service in self.services
            if self._on_orbital(service, orbital)
        }
        listed = [(c.onid, c.tsid, c.sid) for c in channels]
        return sum(key in keys for key in listed) / len(listed) if listed else 0.0

    def match(
        self, channels: Iterable[Channel], orbital: int = DEFAULT_ORBITAL
    ) -> tuple[list[tuple[Channel, Service]], list[Skipped]]:
        """Pair each channel with its single service on the orbital; others are skipped."""
        matched: list[tuple[Channel, Service]] = []
        skipped: list[Skipped] = []
        for channel in channels:
            found = self.candidates(channel, orbital)
            if len(found) != 1:
                skipped.append(
                    {
                        "lcn": channel.lcn,
                        "name": channel.name,
                        "reason": "ambiguous" if found else "missing",
                    }
                )
                continue
            matched.append((channel, found[0]))
        return matched, skipped
