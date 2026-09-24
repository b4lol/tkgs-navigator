"""Read lamedb 4/5 without modifying the receiver's service database."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Tuple, Union

from .constants import DEFAULT_ORBITAL, POLARIZATIONS, TV_SERVICE_TYPES, TuningTarget
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
        for service in services:
            self.by_sid.setdefault(service.sid, []).append(service)

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
            ValueError: on an unsupported version or a malformed record.
        """
        lines = text.splitlines()
        if not lines or lines[0].strip() not in ("eDVB services /4/", "eDVB services /5/"):
            raise ValueError("Only lamedb 4 and 5 are supported")
        transponders: dict[TransponderKey, Transponder] = {}
        services: list[Service] = []

        def add_tp(identity: str, params: str) -> None:
            if not params.startswith(("s ", "s:")):
                return
            fields = [int(v, 16) for v in identity.split(":")[:3]]
            values = params[2:].split(",", 1)[0].split(":")
            if len(fields) != 3 or len(values) < 5:
                raise ValueError("Incomplete transponder fields")
            key = (fields[0], fields[1], fields[2])
            freq, sr, pol, _, orbital = map(int, values[:5])
            transponders[key] = Transponder(key, freq, sr, pol, orbital % 3600)

        def add_service(identity: str, name: str) -> None:
            values = identity.split(":")
            if len(values) < 6:
                raise ValueError("Incomplete service fields")
            sid, ns, tsid, onid = (int(v, 16) for v in values[:4])
            kind = int(values[4], 10)  # lamedb writes service_type in decimal.
            if not 0 < sid <= 65535 or not 0 <= kind <= 255:
                raise ValueError("Invalid service identifier")
            services.append(Service(sid, (ns, tsid, onid), kind, name))

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
                    add_tp(line, lines[index].strip())
                    index += 1
                elif mode == "services":
                    if index + 1 >= len(lines):
                        raise ValueError("Truncated service record")
                    add_service(line, lines[index])
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
        candidates = [service for service in self.services if service.key in keys]
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

    def match(
        self, channels: Iterable[Channel], orbital: int = DEFAULT_ORBITAL
    ) -> tuple[list[tuple[Channel, Service]], list[Skipped]]:
        """Pair each channel with its single TV service on the orbital; others are skipped."""
        matched: list[tuple[Channel, Service]] = []
        skipped: list[Skipped] = []
        for channel in channels:
            candidates = {
                s.reference: s
                for s in self.by_sid.get(channel.sid, [])
                if s.key in self.transponders
                and self.transponders[s.key].orbital == orbital
                and s.kind in TV_SERVICE_TYPES
            }
            if len(candidates) != 1:
                skipped.append(
                    {
                        "lcn": channel.lcn,
                        "name": channel.name,
                        "reason": "ambiguous" if candidates else "missing",
                    }
                )
                continue
            matched.append((channel, next(iter(candidates.values()))))
        return matched, skipped
