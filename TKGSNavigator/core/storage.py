"""Atomic bouquet-set updates, immutable backups, and rollback on errors."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Dict, Iterator, Optional, Tuple
import uuid

# Channel file name -> content, None when the file does not exist.
Snapshot = Dict[str, Optional[bytes]]

BOUQUET = "userbouquet.tkgs_navigator.tv"
INDEX = "bouquets.tv"
RADIO_INDEX = "bouquets.radio"
INDEXES = {"tv": INDEX, "radio": RADIO_INDEX}
INDEX_HEADERS = {"tv": b"#NAME Bouquets (TV)\n", "radio": b"#NAME Bouquets (Radio)\n"}
LINK_TYPES = {"tv": 1, "radio": 2}
BOUQUET_NAME = "TKGS Navigator"
BACKUP_DIR = "tkgs-navigator-backups"
LOCK_FILE = ".tkgs-navigator.lock"
BACKUP_ID_PATTERN = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}")
# Every file this plugin may create; nothing outside this set is ever written or removed.
OWN_BOUQUET = re.compile(r"userbouquet\.tkgs_navigator(?:_[a-z0-9]+)*\.(tv|radio)")
OWN_LINK = re.compile(
    rb'#SERVICE 1:7:[12]:0:0:0:0:0:0:0:FROM BOUQUET "userbouquet\.tkgs_navigator'
    rb'(?:_[a-z0-9]+)*\.(?:tv|radio)"'
)


def digest(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


def atomic_write(path: str | Path, data: bytes) -> None:
    """Replace path with data via a synced temporary file, then sync the directory."""
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".%s." % path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, str(path))
        directory = os.open(str(path.parent), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def bouquet_kind(filename: str) -> str:
    """Return "tv" or "radio" for one of this plugin's bouquet file names.

    Raises:
        ValueError: for any other name.
    """
    match = OWN_BOUQUET.fullmatch(filename)
    if not match:
        raise ValueError("Invalid bouquet file name: %r" % filename)
    return match.group(1)


@dataclass(frozen=True)
class BouquetFile:
    filename: str
    title: str
    content: bytes
    services: int

    def __post_init__(self) -> None:
        bouquet_kind(self.filename)

    @property
    def kind(self) -> str:
        return bouquet_kind(self.filename)


@dataclass(frozen=True)
class BouquetPlan:
    """The complete set of bouquets this plugin should own after an update."""

    files: Tuple[BouquetFile, ...]
    first: bool = False  # Link our bouquets at the top of the index instead of the end.

    def __post_init__(self) -> None:
        names = [file.filename for file in self.files]
        if not names:
            raise ValueError("No matching channels; the existing list is kept")
        if len(set(names)) != len(names):
            raise ValueError("Duplicate bouquet file names")


def link_index(index: bytes | None, kind: str, filenames: list[str], first: bool) -> bytes | None:
    """Replace this plugin's links in an index, keeping every other byte and line."""
    if index is None and not filenames:
        return None
    lines = [
        line for line in (index or INDEX_HEADERS[kind]).splitlines() if not OWN_LINK.match(line)
    ]
    links = [
        b'#SERVICE 1:7:%d:0:0:0:0:0:0:0:FROM BOUQUET "%s" ORDER BY bouquet'
        % (LINK_TYPES[kind], name.encode("ascii"))
        for name in filenames
    ]
    at = (1 if lines and lines[0].startswith(b"#NAME") else 0) if first else len(lines)
    return b"\n".join(lines[:at] + links + lines[at:]) + b"\n"


def _manifest_name(name: str) -> str:
    if name not in INDEXES.values():
        bouquet_kind(name)
    return name


class BouquetStore:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.backups = self.directory / BACKUP_DIR

    @contextmanager
    def _lock(self) -> Iterator[None]:
        if not self.directory.is_dir():
            raise ValueError("Enigma2 configuration directory not found")
        with (self.directory / LOCK_FILE).open("a") as stream:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Another TKGS Navigator operation is in progress") from None
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _read(self, name: str) -> bytes | None:
        path = self.directory / name
        if path.is_symlink():
            raise ValueError("Refusing to modify a symlinked channel file")
        return path.read_bytes() if path.exists() else None

    def _replace(self, name: str, content: bytes | None) -> None:
        if content is None:
            (self.directory / name).unlink(missing_ok=True)
        else:
            atomic_write(self.directory / name, content)

    def _owned(self) -> set[str]:
        return {path.name for path in self.directory.iterdir() if OWN_BOUQUET.fullmatch(path.name)}

    def _transaction(self, desired: Snapshot, before: Snapshot) -> str | None:
        if any(self._read(name) != content for name, content in before.items()):
            raise ValueError("A channel file was changed by another process")
        if before == desired:
            return None
        self.backups.mkdir(exist_ok=True)
        ident = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        backup = self.backups / ident
        backup.mkdir()
        for name, content in before.items():
            if content is not None:
                atomic_write(backup / name, content)
        manifest = {
            "schema": 2,
            "before": {name: digest(content) for name, content in before.items()},
            "after": {name: digest(desired[name]) for name in before},
        }
        atomic_write(backup / "manifest.json", json.dumps(manifest, indent=2).encode("utf-8"))
        # Bouquets are written before the indexes that link them and removed after.
        indexes = [name for name in INDEXES.values() if name in before]
        bouquets = [name for name in sorted(before) if name not in indexes]
        order = (
            [name for name in bouquets if desired[name] is not None]
            + indexes
            + [name for name in bouquets if desired[name] is None]
        )
        changed: list[str] = []
        try:
            for name in order:
                if self._read(name) != before[name]:
                    raise ValueError("A channel file was changed by another process")
                if before[name] != desired[name]:
                    changed.append(name)
                    self._replace(name, desired[name])
        except Exception:
            for name in reversed(changed):
                self._replace(name, before[name])
            raise
        return ident

    def apply(self, plan: BouquetPlan) -> str | None:
        """Back up, then make the plan's bouquets and index links the plugin's only ones.

        Bouquets from an earlier plan that the new plan lacks are removed. Returns the backup
        id, or None when the files already match.
        """
        with self._lock():
            planned = {file.filename: file.content for file in plan.files}
            names = self._owned() | set(planned)
            before: Snapshot = {name: self._read(name) for name in names}
            desired: Snapshot = {name: planned.get(name) for name in names}
            for kind, index in INDEXES.items():
                current = self._read(index)
                linked = [file.filename for file in plan.files if file.kind == kind]
                updated = link_index(current, kind, linked, plan.first)
                if current is not None or updated is not None:
                    before[index], desired[index] = current, updated
            return self._transaction(desired, before)

    def restore(self, ident: str) -> str | None:
        """Restore a backup if the channel files are still exactly as that operation left them.

        Reads schema 1 (0.3.0 and earlier) and schema 2 manifests; only this plugin's
        bouquets and the two indexes can be named in either.
        """
        if not BACKUP_ID_PATTERN.fullmatch(ident):
            raise ValueError("Invalid backup identifier")
        with self._lock():
            folder = self.backups / ident
            manifest = json.loads((folder / "manifest.json").read_text())
            if manifest.get("schema") not in (1, 2):
                raise ValueError("Unsupported backup format")
            names = [_manifest_name(name) for name in manifest["before"]]
            if set(names) != set(manifest["after"]):
                raise ValueError("Backup integrity check failed")
            desired: Snapshot = {}
            before: Snapshot = {}
            for name in names:
                expected = manifest["before"][name]
                desired[name] = (folder / name).read_bytes() if expected is not None else None
                if digest(desired[name]) != expected:
                    raise ValueError("Backup integrity check failed")
                before[name] = self._read(name)
                if digest(before[name]) != manifest["after"][name]:
                    raise ValueError(
                        "Channels changed after the backup; automatic rollback stopped"
                    )
            return self._transaction(desired, before)
