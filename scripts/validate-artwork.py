#!/usr/bin/env python3
"""Validate the exact Plebian wallpaper distribution bundle."""

from __future__ import annotations

import hashlib
import errno
import os
import stat
import struct
import sys
import zlib


WALLPAPER_SHA256 = (
    "60f63c37f054f7ffd061b47e09a3c22fbf595eec6f161c13e95344ca1a724778"
)
DESKTOP_README_SHA256 = (
    "650b64787bb1ad6073bad24dd51faec08e7ef0a17bfdbffe121076f0c8c71c10"
)
ATTRIBUTION_SHA256 = (
    "5216b6ee1ef154dab56cc5d0a026d28f67ed50feec4129d4fedd6ae2fc2b2fb6"
)
GPL2_SHA256 = (
    "8177f97513213526df2cf6184d8ff986c675afb514d4e68a404010521b880643"
)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_CONTRACT = (1920, 1080, 8, 2, 0, 0, 0)
MAX_WALLPAPER_BYTES = 32 * 1024 * 1024
MAX_NOTICE_BYTES = 1024 * 1024


class ValidationError(ValueError):
    pass


def regular_bytes(path: str, limit: int, label: str) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValidationError(
                f"{label} is not a regular non-symlink file: {path}"
            ) from exc
        raise ValidationError(f"{label} is missing or unreadable: {path}: {exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValidationError(
                f"{label} is not a regular non-symlink file: {path}"
            )
        if metadata.st_size > limit:
            raise ValidationError(f"{label} exceeds its {limit}-byte limit: {path}")
        try:
            with os.fdopen(descriptor, "rb", closefd=True) as stream:
                descriptor = -1
                data = stream.read(limit + 1)
        except OSError as exc:
            raise ValidationError(f"could not read {label}: {path}: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(data) > limit:
        raise ValidationError(f"{label} exceeds its {limit}-byte limit: {path}")
    return data


def require_hash(data: bytes, expected: str, label: str) -> None:
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise ValidationError(
            f"{label} SHA-256 mismatch: expected {expected}, got {actual}"
        )


def validate_png(data: bytes) -> None:
    if not data.startswith(PNG_SIGNATURE):
        raise ValidationError("wallpaper does not have the PNG signature")

    position = len(PNG_SIGNATURE)
    chunks: list[tuple[bytes, bytes]] = []
    saw_iend = False
    while position < len(data):
        if len(data) - position < 12:
            raise ValidationError("wallpaper has a truncated PNG chunk")
        length = struct.unpack(">I", data[position : position + 4])[0]
        chunk_end = position + 12 + length
        if chunk_end > len(data):
            raise ValidationError("wallpaper has a PNG chunk beyond end of file")
        chunk_type = data[position + 4 : position + 8]
        payload = data[position + 8 : position + 8 + length]
        expected_crc = struct.unpack(">I", data[position + 8 + length : chunk_end])[0]
        actual_crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise ValidationError(
                f"wallpaper PNG chunk {chunk_type!r} has an invalid CRC"
            )
        chunks.append((chunk_type, payload))
        position = chunk_end
        if chunk_type == b"IEND":
            if length != 0:
                raise ValidationError("wallpaper PNG IEND chunk is not empty")
            saw_iend = True
            break

    if not saw_iend or position != len(data):
        raise ValidationError("wallpaper PNG is missing IEND or has trailing bytes")
    if not chunks or chunks[0][0] != b"IHDR" or len(chunks[0][1]) != 13:
        raise ValidationError("wallpaper PNG must begin with one 13-byte IHDR")
    if sum(chunk_type == b"IHDR" for chunk_type, _ in chunks) != 1:
        raise ValidationError("wallpaper PNG has multiple IHDR chunks")
    if sum(chunk_type == b"IEND" for chunk_type, _ in chunks) != 1:
        raise ValidationError("wallpaper PNG has multiple IEND chunks")

    header = struct.unpack(">IIBBBBB", chunks[0][1])
    if header != PNG_CONTRACT:
        raise ValidationError(
            f"wallpaper PNG contract mismatch: expected {PNG_CONTRACT}, got {header}"
        )

    idat_indexes = [
        index for index, (chunk_type, _) in enumerate(chunks)
        if chunk_type == b"IDAT"
    ]
    if not idat_indexes:
        raise ValidationError("wallpaper PNG has no IDAT data")
    if idat_indexes != list(range(idat_indexes[0], idat_indexes[-1] + 1)):
        raise ValidationError("wallpaper PNG IDAT chunks are not consecutive")
    for chunk_type, _ in chunks:
        if not (chunk_type[0] & 0x20) and chunk_type not in {
            b"IHDR", b"PLTE", b"IDAT", b"IEND"
        }:
            raise ValidationError(f"wallpaper PNG has unknown critical chunk {chunk_type!r}")

    compressed = b"".join(chunks[index][1] for index in idat_indexes)
    expected_size = (1920 * 3 + 1) * 1080
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(compressed, expected_size + 1)
    except zlib.error as exc:
        raise ValidationError(f"wallpaper PNG has invalid compressed data: {exc}") from exc
    # Do not flush an oversized or unfinished stream: flush() has no useful
    # output bound on older Python versions and could expand hostile input well
    # beyond the known scanline contract.
    if (len(raw) > expected_size or not decoder.eof or decoder.unused_data
            or decoder.unconsumed_tail):
        raise ValidationError("wallpaper PNG has invalid or oversized compressed data")
    try:
        raw += decoder.flush()
    except zlib.error as exc:
        raise ValidationError(f"wallpaper PNG has invalid compressed data: {exc}") from exc
    if len(raw) != expected_size:
        raise ValidationError("wallpaper PNG has an invalid decompressed scanline size")
    stride = 1920 * 3 + 1
    if any(raw[offset] > 4 for offset in range(0, len(raw), stride)):
        raise ValidationError("wallpaper PNG uses an invalid scanline filter")


def validate_text(
    path: str,
    expected_hash: str,
    label: str,
    required_markers: tuple[str, ...],
) -> None:
    data = regular_bytes(path, MAX_NOTICE_BYTES, label)
    require_hash(data, expected_hash, label)
    if not data or b"\x00" in data or not data.endswith(b"\n"):
        raise ValidationError(f"{label} is empty, contains NUL, or lacks a final newline")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError(f"{label} is not UTF-8: {exc}") from exc
    missing = [marker for marker in required_markers if marker not in text]
    if missing:
        raise ValidationError(f"{label} lacks required marker: {missing[0]}")


def validate_bundle(paths: list[str]) -> None:
    if len(paths) != 4:
        raise ValidationError(
            "usage: validate-artwork.py WALLPAPER DESKTOP_README ATTRIBUTION GPL2"
        )
    wallpaper, desktop_readme, attribution, gpl2 = paths
    wallpaper_data = regular_bytes(
        wallpaper, MAX_WALLPAPER_BYTES, "Plebian wallpaper"
    )
    require_hash(wallpaper_data, WALLPAPER_SHA256, "Plebian wallpaper")
    validate_png(wallpaper_data)
    validate_text(
        desktop_readme,
        DESKTOP_README_SHA256,
        "desktop artwork README",
        ("# Plebian-OS desktop artwork", WALLPAPER_SHA256, "GPL-2.0-or-later"),
    )
    validate_text(
        attribution,
        ATTRIBUTION_SHA256,
        "artwork attribution",
        ("# Plebian-OS installer artwork attribution", "Debian 13 Ceratopsian", "GPL-2.0+"),
    )
    validate_text(
        gpl2,
        GPL2_SHA256,
        "GPL version 2 license",
        ("GNU GENERAL PUBLIC LICENSE", "Version 2, June 1991", "How to Apply These Terms"),
    )


def main() -> int:
    try:
        validate_bundle(sys.argv[1:])
    except ValidationError as exc:
        print(f"validate-artwork: {exc}", file=sys.stderr)
        return 1
    print("Plebian artwork bundle valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
