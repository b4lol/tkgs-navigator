"""Atomic per-file bouquet updates, immutable backups, and rollback on errors."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import fcntl
import hashlib
import json
import os
import re
import tempfile
import uuid

from .text import clean_name

BOUQUET = "userbouquet.tkgs_navigator.tv"
INDEX = "bouquets.tv"
BOUQUET_NAME = "TKGS Navigator"
BACKUP_DIR = "tkgs-navigator-backups"
LOCK_FILE = ".tkgs-navigator.lock"
LINK = '#SERVICE 1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "%s" ORDER BY bouquet' % BOUQUET
BACKUP_ID_PATTERN = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}")


def digest(data):
    return None if data is None else hashlib.sha256(data).hexdigest()


def atomic_write(path, data):
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


def render_bouquet(matched):
    if not matched:
        raise ValueError("No matching channels; the existing list is kept")
    rows = ["#NAME " + BOUQUET_NAME]
    seen = set()
    for channel, service in sorted(matched, key=lambda pair: pair[0].lcn):
        if service.reference in seen:
            continue
        seen.add(service.reference)
        rows.extend(["#SERVICE " + service.reference,
                     "#DESCRIPTION " + clean_name(channel.name)])
    return ("\n".join(rows) + "\n").encode("utf-8")


class BouquetStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.backups = self.directory / BACKUP_DIR

    @contextmanager
    def _lock(self):
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

    def _read(self, name):
        path = self.directory / name
        if path.is_symlink():
            raise ValueError("Refusing to modify a symlinked channel file")
        return path.read_bytes() if path.exists() else None

    def _replace(self, name, content):
        if content is None:
            (self.directory / name).unlink(missing_ok=True)
        else:
            atomic_write(self.directory / name, content)

    def _transaction(self, desired, before):
        if any(self._read(name) != before[name] for name in (BOUQUET, INDEX)):
            raise ValueError("A channel file was changed by another process")
        if before == desired:
            return None
        self.backups.mkdir(exist_ok=True)
        ident = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        backup = self.backups / ident
        backup.mkdir()
        manifest = {"schema": 1, "before": {}, "after": {}}
        for name in (BOUQUET, INDEX):
            if before[name] is not None:
                atomic_write(backup / name, before[name])
            manifest["before"][name] = digest(before[name])
            manifest["after"][name] = digest(desired[name])
        atomic_write(backup / "manifest.json", json.dumps(manifest, indent=2).encode("utf-8"))
        changed = []
        try:
            # Write target before index; remove target after index on restore.
            order = (INDEX, BOUQUET) if desired[BOUQUET] is None else (BOUQUET, INDEX)
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

    def apply(self, matched):
        bouquet = render_bouquet(matched)
        with self._lock():
            before = {name: self._read(name) for name in (BOUQUET, INDEX)}
            index = before[INDEX] or b"#NAME Bouquets (TV)\n"
            # Preserve unknown bytes and all unrelated lines, remove only our link duplicates.
            lines = index.splitlines()
            token = ('FROM BOUQUET "%s"' % BOUQUET).encode("ascii")
            lines = [line for line in lines if not (line.startswith(b"#SERVICE ") and token in line)]
            lines.append(LINK.encode("ascii"))
            return self._transaction({BOUQUET: bouquet, INDEX: b"\n".join(lines) + b"\n"}, before)

    def restore(self, ident):
        if not BACKUP_ID_PATTERN.fullmatch(ident):
            raise ValueError("Invalid backup identifier")
        with self._lock():
            folder = self.backups / ident
            manifest = json.loads((folder / "manifest.json").read_text())
            if manifest.get("schema") != 1:
                raise ValueError("Unsupported backup format")
            desired, before = {}, {}
            for name in (BOUQUET, INDEX):
                expected = manifest["before"][name]
                desired[name] = (folder / name).read_bytes() if expected is not None else None
                if digest(desired[name]) != expected:
                    raise ValueError("Backup integrity check failed")
                before[name] = self._read(name)
                if digest(before[name]) != manifest["after"][name]:
                    raise ValueError("Channels changed after the backup; automatic rollback stopped")
            return self._transaction(desired, before)
