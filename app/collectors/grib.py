"""What every reader of GRIB2 files in this API shares: one decode at a time, and the split of a file into its messages.

ecCodes crashed (no definitions found, then a segmentation fault) when two threads decoded at once on 2026-10-25 (eccodes 2.50,
see ``app/prealert/gfs.py``): every module that decodes takes ``DECODE_LOCK``, the same lock, so that two of them never decode
together either.
"""

from __future__ import annotations

import struct
import threading

DECODE_LOCK = threading.Lock()


def split_messages(data: bytes) -> list[bytes]:
    """Split a GRIB2 buffer into its messages (section 0 holds the length of each); ``ValueError`` when it is not one."""
    out: list[bytes] = []
    i = 0
    while i + 16 <= len(data):
        if data[i : i + 4] != b"GRIB":
            raise ValueError("not a GRIB2 buffer")
        length = struct.unpack(">Q", data[i + 8 : i + 16])[0]
        if length < 16 or i + length > len(data):
            raise ValueError("truncated GRIB2 message")
        out.append(data[i : i + length])
        i += length
    if not out:
        raise ValueError("not a GRIB2 buffer")
    return out
