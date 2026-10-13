#!/usr/bin/env python3
"""Audit every member of the pinned Windows archives, without extracting files.

Usage: python scripts/audit_windows_paths.py ARCHIVE_DIRECTORY [--write]
Archives must already be downloaded. Python 3.14 supplies Zstandard; older
developer interpreters need the zstandard package (never installed into QGIS).
The generated audit does not change the scientific lock or invalidate engines.
"""

import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile
from urllib.parse import urlsplit
import zipfile

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def tar_members(path):
    if path.name.endswith(".tar.bz2"):
        with tarfile.open(path, "r|bz2") as archive:
            yield archive
        return
    try:
        from compression.zstd import ZstdFile
    except ImportError:
        from zstandard import ZstdDecompressor

        def ZstdFile(stream):
            return ZstdDecompressor().stream_reader(stream)

    with zipfile.ZipFile(path) as archive:
        # Inspect payload AND metadata, including members absent from paths.json.
        def members():
            for name in archive.namelist():
                if not name.endswith(".tar.zst"):
                    continue
                with archive.open(name) as compressed, ZstdFile(compressed) as raw:
                    with tarfile.open(fileobj=raw, mode="r|") as tar:
                        yield from tar

        yield members()


def windows_length(path):
    return len(str(path).encode("utf-16-le")) // 2


def audit(archives):
    lock_path = ROOT / "runtime/locks/win-64.json"
    lock = json.loads(lock_path.read_text())
    bootstrap = json.loads((ROOT / "runtime/bootstrap.json").read_text())
    result = {
        "schema": 1,
        "lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
        "installer_sha256": bootstrap["platforms"]["win-64"]["sha256"],
        "package_count": len(lock["packages"]),
        "member_count": 0,
        "cache_file": "",
        "cache_directory": "",
        "prefix_file": "",
        "prefix_directory": "",
    }

    def longest(key, value):
        if windows_length(value) > windows_length(result[key]):
            result[key] = str(value)

    for package in lock["packages"]:
        path = archives / package["filename"]
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if path.stat().st_size != package["size"] or digest != package["sha256"]:
            raise ValueError(f"Archive failed verification: {path.name}")
        url = urlsplit(package["url"])
        stem = path.name.removesuffix(".conda").removesuffix(".tar.bz2")
        # Micromamba 2.9 uses the original URL's namespace below pkgs_dirs.
        namespace = PurePosixPath(url.scheme, url.netloc, url.path.lstrip("/")).parent / stem
        with tar_members(path) as archive:
            for member in archive:
                relative = PurePosixPath(member.name)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError(f"Invalid archive member: {member.name}")
                result["member_count"] += 1
                destination = namespace / relative
                longest("cache_directory", destination if member.isdir() else destination.parent)
                if not member.isdir():
                    longest("cache_file", destination)
                if relative.parts and relative.parts[0] != "info":
                    longest("prefix_directory", relative if member.isdir() else relative.parent)
                    if not member.isdir():
                        longest("prefix_file", relative)
        # Installer-generated records not present in archive member listings.
        longest("cache_file", namespace / "info/repodata_record.json")
        longest("prefix_file", PurePosixPath("conda-meta") / (stem + ".json"))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    result = audit(args.archives)
    serialized = json.dumps(result, indent=2) + "\n"
    if args.write:
        (ROOT / "runtime/windows-paths.json").write_text(serialized, encoding="utf-8")
    else:
        expected = json.loads((ROOT / "runtime/windows-paths.json").read_text())
        if result != expected:
            raise SystemExit("Windows archive paths changed; regenerate the audit with --write.")
    print(serialized)
