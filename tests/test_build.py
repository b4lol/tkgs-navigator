import ast
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

from tools.build import ROOT, build


def ar_members(data):
    if data[:8] != b"!<arch>\n":
        raise ValueError("Not ar")
    offset, members = 8, {}
    while offset < len(data):
        header = data[offset : offset + 60]
        name = header[:16].decode("ascii").strip().rstrip("/")
        size = int(header[48:58])
        offset += 60
        members[name] = data[offset : offset + size]
        offset += size + size % 2
    return members


class BuildTests(unittest.TestCase):
    def test_packages_reproducible_and_sources_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            first = build(Path(folder) / "a")
            second = build(Path(folder) / "b")
            for left, right in zip(first, second):
                self.assertEqual(left.read_bytes(), right.read_bytes())
            for path in first[:2]:
                members = ar_members(path.read_bytes())
                self.assertEqual(members["debian-binary"], b"2.0\n")
                with tarfile.open(fileobj=io.BytesIO(members["control.tar.gz"])) as archive:
                    self.assertEqual(archive.getnames(), ["control"])
                with tarfile.open(fileobj=io.BytesIO(members["data.tar.gz"])) as archive:
                    names = archive.getnames()
                    self.assertTrue(any(name.endswith("TKGSNavigator/plugin.py") for name in names))
                    self.assertFalse(any(name.endswith(".pyc") for name in names))
                    for name in names:
                        if name.endswith(".py"):
                            original = ROOT / "TKGSNavigator" / name.split("TKGSNavigator/", 1)[1]
                            self.assertEqual(
                                archive.extractfile(name).read(), original.read_bytes()
                            )

    def test_gzip_header_is_platform_independent_and_license_shipped(self):
        with tempfile.TemporaryDirectory() as folder:
            artifacts = build(Path(folder) / "out")
            for path in artifacts[:2]:
                data = ar_members(path.read_bytes())["data.tar.gz"]
                self.assertEqual(data[:10], b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff")
                with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                    self.assertTrue(
                        any(name.endswith("TKGSNavigator/LICENSE") for name in archive.getnames())
                    )
            source = next(p for p in artifacts if p.name.endswith("source.tar.gz"))
            with tarfile.open(fileobj=io.BytesIO(source.read_bytes())) as archive:
                self.assertTrue(
                    any(name.endswith("tkgs-navigator/LICENSE") for name in archive.getnames())
                )

    def test_python38_syntax(self):
        for folder in ("TKGSNavigator", "tools", "tests"):
            for path in (ROOT / folder).rglob("*.py"):
                ast.parse(path.read_text(encoding="utf-8"), feature_version=(3, 8))


if __name__ == "__main__":
    unittest.main()
