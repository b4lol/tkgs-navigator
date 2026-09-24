"""Turn matched channels into the plugin's bouquet set.

Only the main TV bouquet is always produced. The other bouquets depend on what the table
carries: an alternate HD/SD list when some LCNs have both variants, a radio bouquet for
radio records and, when requested, one bouquet per package id. With the observed layout,
which has none of those fields, the result is the single LCN-ordered bouquet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .lamedb import Service
from .parser import Channel
from .storage import BOUQUET, BOUQUET_NAME, BouquetFile, BouquetPlan
from .text import clean_name

Pair = Tuple[Channel, Service]

# Invisible numbered marker: takes a channel number without showing a line, the spacer
# convention used by bouquet makers such as AutoBouquetsMaker.
SPACER = "1:832:D:0:0:0:0:0:0:0:"
MAX_ALIGNED_LCN = 2000
# Provisional names from the research report; a real recording has to confirm the ids.
PACKAGE_NAMES = {
    0: "National",
    1: "News",
    2: "Sports",
    3: "Music",
    4: "Documentary",
    5: "Kids",
    6: "Radio",
}


@dataclass(frozen=True)
class PlanOptions:
    prefer_hd: bool = True
    categories: bool = False
    align_lcn: bool = False
    first: bool = False


DEFAULT_OPTIONS = PlanOptions()


def render(title: str, rows: Sequence[Optional[Pair]]) -> bytes:
    """Render a bouquet; a None row is a spacer. Services repeated in one bouquet are dropped."""
    lines = ["#NAME " + clean_name(title)]
    seen = set()
    for row in rows:
        if row is None:
            lines.append("#SERVICE " + SPACER)
            continue
        channel, service = row
        if service.reference in seen:
            continue
        seen.add(service.reference)
        lines.extend(["#SERVICE " + service.reference, "#DESCRIPTION " + clean_name(channel.name)])
    return ("\n".join(lines) + "\n").encode("utf-8")


def choose_variants(pairs: Sequence[Pair], prefer_hd: bool) -> List[Pair]:
    """One pair per LCN, in LCN order; where HD and SD variants share an LCN, the preferred."""
    by_lcn: dict[int, List[Pair]] = {}
    for pair in pairs:
        by_lcn.setdefault(pair[0].lcn, []).append(pair)
    chosen = []
    for _, group in sorted(by_lcn.items()):
        chosen.append(min(group, key=lambda pair: (bool(pair[0].hd) != prefer_hd, pair[0].sid)))
    return chosen


def align(pairs: Sequence[Pair]) -> List[Optional[Pair]]:
    """Insert spacers so that, counted from 1, each channel's position equals its LCN."""
    rows: List[Optional[Pair]] = []
    position = 1
    for pair in pairs:
        lcn = pair[0].lcn
        if position <= lcn <= MAX_ALIGNED_LCN:
            rows.extend([None] * (lcn - position))
            position = lcn
        rows.append(pair)
        position += 1
    return rows


def _file(filename: str, title: str, rows: Sequence[Optional[Pair]]) -> BouquetFile:
    services = len({row[1].reference for row in rows if row is not None})
    return BouquetFile(filename, title, render(title, rows), services)


def plan_bouquets(matched: Sequence[Pair], options: PlanOptions = DEFAULT_OPTIONS) -> BouquetPlan:
    """Build the bouquet set for the matched channels.

    Raises:
        ValueError: when nothing matched, so an existing list is never emptied.
    """
    tv = [pair for pair in matched if not pair[0].radio]
    radio = sorted((pair for pair in matched if pair[0].radio), key=lambda pair: pair[0].lcn)
    files = []
    if tv:
        main = choose_variants(tv, options.prefer_hd)
        files.append(_file(BOUQUET, BOUQUET_NAME, align(main) if options.align_lcn else main))
        alternate = choose_variants(tv, not options.prefer_hd)
        if alternate != main:
            variant = "sd" if options.prefer_hd else "hd"
            files.append(
                _file(
                    "userbouquet.tkgs_navigator_%s.tv" % variant,
                    "%s %s" % (BOUQUET_NAME, variant.upper()),
                    alternate,
                )
            )
        if options.categories:
            packages = sorted({pair[0].package for pair in main if pair[0].package is not None})
            for package in packages:
                label = PACKAGE_NAMES.get(package, "Package %d" % package)
                files.append(
                    _file(
                        "userbouquet.tkgs_navigator_pkg%d.tv" % package,
                        "%s - %s" % (BOUQUET_NAME, label),
                        [pair for pair in main if pair[0].package == package],
                    )
                )
    if radio:
        files.append(
            _file("userbouquet.tkgs_navigator_radio.radio", BOUQUET_NAME + " Radio", radio)
        )
    return BouquetPlan(tuple(files), options.first)
