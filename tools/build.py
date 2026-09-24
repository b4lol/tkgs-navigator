#!/usr/bin/env python3
"""Build deterministic DEB/IPK and source archives using the standard library.

The gzip header is written explicitly (fixed MTIME, XFL and OS bytes) so the
output does not depend on the platform or Python version. The compressed body
still comes from the system zlib, so byte-identical archives are guaranteed for
a given zlib release; the archived contents are identical regardless of zlib.
"""
import argparse
import hashlib
import io
import os
from pathlib import Path
import struct
import tarfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"
PACKAGE = "enigma2-plugin-extensions-tkgs-navigator"
MODULE = "TKGSNavigator"
SLUG = "tkgs-navigator"


def gzip_bytes(data, mtime):
    compressor = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    body = compressor.compress(data) + compressor.flush()
    header = b"\x1f\x8b\x08\x00" + struct.pack("<I", mtime & 0xffffffff) + b"\x02\xff"
    trailer = struct.pack("<II", zlib.crc32(data) & 0xffffffff, len(data) & 0xffffffff)
    return header + body + trailer


def tar_bytes(files, epoch):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, content in sorted(files.items()):
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode, entry.mtime = len(content), 0o644, epoch
            entry.uid = entry.gid = 0
            entry.uname = entry.gname = "root"
            archive.addfile(entry, io.BytesIO(content))
    return gzip_bytes(stream.getvalue(), epoch)


def ar_bytes(members, epoch):
    result = bytearray(b"!<arch>\n")
    for name, content in members:
        header = "{:<16}{:<12}{:<6}{:<6}{:<8}{:<10}`\n".format(name + "/", epoch, 0, 0, "100644", len(content))
        result.extend(header.encode("ascii"))
        result.extend(content)
        if len(content) % 2:
            result.extend(b"\n")
    return bytes(result)


def build(output, libdir="usr/lib", epoch=0):
    if libdir not in ("usr/lib", "usr/lib64"):
        raise ValueError("libdir must be usr/lib or usr/lib64")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    install = libdir + "/enigma2/python/Plugins/Extensions/%s/" % MODULE
    files = {install + str(path.relative_to(ROOT / MODULE)): path.read_bytes()
             for path in (ROOT / MODULE).rglob("*.py")}
    files[install + "README.md"] = (ROOT / "README.md").read_bytes()
    files[install + "NOTICE.md"] = (ROOT / "NOTICE.md").read_bytes()
    files[install + "LICENSE"] = (ROOT / "LICENSE").read_bytes()
    data = tar_bytes(files, epoch)
    suffix = "-lib64" if libdir.endswith("64") else ""
    artifacts = []
    for extension, dependency in (("deb", "enigma2, python3 (>= 3.8)"),
                                  ("ipk", "enigma2, python3-core (>= 3.8), python3-modules")):
        control = ("Package: %s\nVersion: %s\nArchitecture: all\n"
                   "Maintainer: TKGS Navigator Project\nSection: extra\nPriority: optional\n"
                   "Depends: %s\nInstalled-Size: %d\n"
                   "Description: Local TKGS scan, preview and backed-up bouquet updates\n" % (
                       PACKAGE, VERSION, dependency, (sum(map(len, files.values())) + 1023) // 1024))
        control_tar = tar_bytes({"control": control.encode("utf-8")}, epoch)
        target = output / ("%s_%s_all%s.%s" % (PACKAGE, VERSION, suffix, extension))
        target.write_bytes(ar_bytes([("debian-binary", b"2.0\n"), ("control.tar.gz", control_tar),
                                    ("data.tar.gz", data)], epoch))
        artifacts.append(target)
    source_files = {}
    for directory in (MODULE, "tests", "tools", "docs", "examples", ".github"):
        for path in (ROOT / directory).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                source_files[SLUG + "/" + str(path.relative_to(ROOT))] = path.read_bytes()
    for name in ("README.md", "NOTICE.md", "LICENSE", "pyproject.toml", ".gitignore"):
        source_files[SLUG + "/" + name] = (ROOT / name).read_bytes()
    source = output / ("%s-%s-source.tar.gz" % (SLUG, VERSION))
    source.write_bytes(tar_bytes(source_files, epoch))
    artifacts.append(source)
    checksum = output / ("SHA256SUMS%s" % suffix)
    checksum.write_text("".join(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name + "\n"
                                for path in artifacts), encoding="ascii")
    return artifacts


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(ROOT / "dist"))
    parser.add_argument("--libdir", choices=["usr/lib", "usr/lib64"], default="usr/lib")
    arguments = parser.parse_args()
    for artifact in build(arguments.output, arguments.libdir, int(os.environ.get("SOURCE_DATE_EPOCH", "0"))):
        print(artifact)
