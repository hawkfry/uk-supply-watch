#!/usr/bin/env python3
"""Open XLSX zip archives only after the metadata has been checked.

ZipFile is not constructed until the central directory is known to be small,
non-ZIP64, and free of duplicate or absurd members.
"""

from __future__ import annotations

import struct
import zipfile
from io import BytesIO

MAX_CENTRAL_DIRECTORY = 1024 * 1024
MAX_MEMBERS = 256
MAX_MEMBER_BYTES = 16 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
EOCD_SIG = b"PK\x05\x06"
CD_SIG = b"PK\x01\x02"
LOCAL_SIG = b"PK\x03\x04"
ZIP64_EOCD_SIG = b"PK\x06\x06"
ZIP64_LOCATOR_SIG = b"PK\x06\x07"


class ZipRejected(ValueError):
    """The archive metadata is not safe to hand to ZipFile."""


def validate_zip(blob: bytes) -> None:
    """Reject hostile zip metadata. Does not construct ZipFile."""
    if not isinstance(blob, (bytes, bytearray)) or len(blob) < 22:
        raise ZipRejected("archive is too small")
    if ZIP64_EOCD_SIG in blob or ZIP64_LOCATOR_SIG in blob:
        raise ZipRejected("ZIP64 archives are refused")
    eocd = _find_eocd(blob)
    disk, cd_disk, entries, total, cd_size, cd_offset = _parse_eocd(blob, eocd)
    if disk != 0 or cd_disk != 0:
        raise ZipRejected("multi-disk archives are refused")
    if entries == 0xFFFF or total == 0xFFFF or cd_size == 0xFFFFFFFF or cd_offset == 0xFFFFFFFF:
        raise ZipRejected("ZIP64 markers are refused")
    if entries != total:
        raise ZipRejected("central directory entry counts disagree")
    if entries > MAX_MEMBERS or total > MAX_MEMBERS:
        raise ZipRejected("too many archive members")
    if cd_size > MAX_CENTRAL_DIRECTORY:
        raise ZipRejected("central directory is larger than 1 MiB")
    if cd_offset < 0 or cd_size < 0 or cd_offset + cd_size > eocd:
        raise ZipRejected("central directory is outside the archive")
    _walk_central_directory(blob, cd_offset, cd_size, entries)


def open_xlsx(blob: bytes) -> zipfile.ZipFile:
    """Validate metadata, then open the archive."""
    validate_zip(blob)
    return zipfile.ZipFile(BytesIO(blob))


def read_member(archive: zipfile.ZipFile, name: str) -> bytes:
    """Read one member that the metadata check already bounded."""
    info = archive.getinfo(name)
    if info.file_size > MAX_MEMBER_BYTES:
        raise ZipRejected("member expands past 16 MiB")
    if info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
        raise ZipRejected("member compression ratio is too high")
    data = archive.read(name)
    if len(data) > MAX_MEMBER_BYTES:
        raise ZipRejected("member read past 16 MiB")
    return data


def parse_xml(data: bytes):
    """Parse workbook XML after rejecting unsafe declarations and doctypes."""
    import xml.etree.ElementTree as ET

    if len(data) > MAX_MEMBER_BYTES:
        raise ZipRejected("XML member is too large")
    sniff = data.lstrip()[:240]
    if sniff.startswith(b"<?xml"):
        end = sniff.find(b"?>")
        if end < 0:
            raise ZipRejected("XML declaration is truncated")
        decl = sniff[: end + 2].decode("ascii", "replace").lower()
        if any(token in decl for token in ("entity", "system", "public", "doctype")):
            raise ZipRejected("unsafe XML declaration")
        if "encoding" in decl and "utf-8" not in decl and "utf8" not in decl:
            raise ZipRejected("XML encoding is not UTF-8")
    head = data[:800].lower()
    if b"<!doctype" in head or b"<!entity" in head:
        raise ZipRejected("XML doctype is refused")
    return ET.fromstring(data)


def _find_eocd(blob: bytes) -> int:
    start = max(0, len(blob) - (22 + 65535))
    idx = blob.rfind(EOCD_SIG, start)
    while idx != -1:
        if idx + 22 <= len(blob):
            comment_len = int.from_bytes(blob[idx + 20 : idx + 22], "little")
            if idx + 22 + comment_len == len(blob):
                return idx
        idx = blob.rfind(EOCD_SIG, start, idx)
    raise ZipRejected("end of central directory does not match the file")


def _parse_eocd(blob: bytes, offset: int) -> tuple[int, int, int, int, int, int]:
    try:
        disk, cd_disk, entries, total, cd_size, cd_offset = struct.unpack_from("<HHHHII", blob, offset + 4)
    except struct.error as exc:
        raise ZipRejected("end of central directory is truncated") from exc
    return disk, cd_disk, entries, total, cd_size, cd_offset


def _walk_central_directory(blob: bytes, offset: int, size: int, expected: int) -> None:
    end = offset + size
    pos = offset
    seen: set[bytes] = set()
    count = 0
    while pos < end:
        if pos + 46 > end or blob[pos : pos + 4] != CD_SIG:
            raise ZipRejected("central directory entry is malformed")
        method = struct.unpack_from("<H", blob, pos + 10)[0]
        comp, uncomp, name_len, extra_len, comment_len, disk_start = struct.unpack_from("<IIHHHH", blob, pos + 20)
        local_off = struct.unpack_from("<I", blob, pos + 42)[0]
        if disk_start != 0:
            raise ZipRejected("member starts on another disk")
        if comp == 0xFFFFFFFF or uncomp == 0xFFFFFFFF:
            raise ZipRejected("ZIP64 member sizes are refused")
        if uncomp > MAX_MEMBER_BYTES or comp > MAX_MEMBER_BYTES:
            raise ZipRejected("member is larger than 16 MiB")
        if comp and uncomp / comp > MAX_COMPRESSION_RATIO:
            raise ZipRejected("member compression ratio is too high")
        if method not in (0, 8):
            raise ZipRejected("member compression method is not allowed")
        if method == 0 and comp != uncomp:
            raise ZipRejected("stored member sizes disagree")
        name_end = pos + 46 + name_len
        entry_end = name_end + extra_len + comment_len
        if name_len == 0 or entry_end > end:
            raise ZipRejected("central directory entry runs outside the directory")
        name = blob[pos + 46 : name_end]
        if name in seen or b".." in name.split(b"/") or name.startswith(b"/") or b"\\" in name:
            raise ZipRejected("duplicate or unsafe member name")
        seen.add(name)
        _check_local_header(blob, local_off, comp, name)
        count += 1
        if count > MAX_MEMBERS:
            raise ZipRejected("too many archive members")
        pos = entry_end
    if pos != end or count != expected:
        raise ZipRejected("central directory entry count does not match the directory")


def _check_local_header(blob: bytes, offset: int, compressed: int, name: bytes) -> None:
    if offset < 0 or offset + 30 > len(blob) or blob[offset : offset + 4] != LOCAL_SIG:
        raise ZipRejected("local header is outside the archive")
    name_len, extra_len = struct.unpack_from("<HH", blob, offset + 26)
    data_start = offset + 30 + name_len + extra_len
    if data_start + compressed > len(blob):
        raise ZipRejected("compressed member runs past the end of the archive")
    local_name = blob[offset + 30 : offset + 30 + name_len]
    if local_name != name:
        raise ZipRejected("local header name does not match the directory")
