"""When a file or folder was created, for ordering the library by capture date.

Python 3.12 exposes no creation ("birth") time on Linux, so it comes from the
``statx`` system call through libc. A recording's wav is created when capture
starts and keeps that time through the rename into the library, so its birth
time is when the meeting was recorded. Where the file system keeps no birth
time, the last modification time stands in.
"""

import ctypes
import os
import struct

_AT_FDCWD = -100
_STATX_BTIME = 0x800
# struct statx: stx_mask is the first u32, stx_btime (s64 seconds, u32
# nanoseconds) starts at byte 80. The kernel fills at most 256 bytes.
_STATX_SIZE = 256
_BTIME_OFFSET = 80

_statx = None


def _load_statx():
    global _statx
    if _statx is None:
        try:
            statx = ctypes.CDLL(None, use_errno=True).statx
        except (AttributeError, OSError, TypeError):
            _statx = False
        else:
            statx.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p]
            statx.restype = ctypes.c_int
            _statx = statx
    return _statx


def _birth_time(path):
    statx = _load_statx()
    if not statx:
        return None
    buffer = ctypes.create_string_buffer(_STATX_SIZE)
    if statx(_AT_FDCWD, os.fsencode(path), 0, _STATX_BTIME, buffer) != 0:
        return None
    (mask,) = struct.unpack_from("=I", buffer, 0)
    if not mask & _STATX_BTIME:
        return None
    seconds, nanoseconds = struct.unpack_from("=qI", buffer, _BTIME_OFFSET)
    return seconds + nanoseconds / 1e9


def created_at(path):
    """Creation time of ``path`` as a Unix timestamp."""
    stat = os.stat(path)
    return getattr(stat, "st_birthtime", None) or _birth_time(path) or stat.st_mtime
