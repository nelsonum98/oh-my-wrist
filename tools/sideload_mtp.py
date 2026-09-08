#!/usr/bin/env python3
"""Sideload a Connect IQ .prg onto a Garmin watch over MTP (macOS, libmtp).

Why this exists: `mtp-sendfile` resolves a destination path to a folder id but
never sets the storage id, and Garmin devices then fail with
"get_suggested_storage_id(): could not get storage id from parent id".
Setting both ids explicitly via libmtp's C API works.

Find the ids once with `mtp-folders` (folder id of GARMIN/Apps) and
`mtp-files` (Storage ID printed on any file). Defaults below are for Nelson's
epix Pro (Gen 2) 51mm.

Usage: tools/sideload_mtp.py <local.prg> [--name NAME] [--parent ID] [--storage ID]
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys

LIB = "/opt/homebrew/lib/libmtp.dylib"
FILETYPE_UNKNOWN = 44  # LIBMTP_FILETYPE_UNKNOWN in libmtp 1.1.23


class File(ctypes.Structure):
    _fields_ = [
        ("item_id", ctypes.c_uint32),
        ("parent_id", ctypes.c_uint32),
        ("storage_id", ctypes.c_uint32),
        ("filename", ctypes.c_char_p),
        ("filesize", ctypes.c_uint64),
        ("modificationdate", ctypes.c_long),
        ("filetype", ctypes.c_int),
        ("next", ctypes.c_void_p),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("prg")
    ap.add_argument("--name", help="filename on the watch (default: basename)")
    ap.add_argument("--parent", type=lambda s: int(s, 0), default=16777274, help="GARMIN/Apps folder id")
    ap.add_argument("--storage", type=lambda s: int(s, 0), default=0x00020001, help="storage id")
    a = ap.parse_args()

    if not os.path.isfile(a.prg):
        print(f"no such file: {a.prg}", file=sys.stderr)
        return 2
    name = a.name or os.path.basename(a.prg)

    mtp = ctypes.CDLL(LIB)
    mtp.LIBMTP_Get_First_Device.restype = ctypes.c_void_p
    mtp.LIBMTP_new_file_t.restype = ctypes.POINTER(File)
    mtp.LIBMTP_Send_File_From_File.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(File), ctypes.c_void_p, ctypes.c_void_p,
    ]
    mtp.LIBMTP_Send_File_From_File.restype = ctypes.c_int
    mtp.LIBMTP_Release_Device.argtypes = [ctypes.c_void_p]
    mtp.LIBMTP_Dump_Errorstack.argtypes = [ctypes.c_void_p]
    mtp.LIBMTP_destroy_file_t.argtypes = [ctypes.POINTER(File)]

    mtp.LIBMTP_Init()
    dev = mtp.LIBMTP_Get_First_Device()
    if not dev:
        print("no MTP device found (is the watch on a data cable?)", file=sys.stderr)
        return 1

    f = mtp.LIBMTP_new_file_t()
    f.contents.filename = ctypes.c_char_p(name.encode())  # libmtp frees this; keep it alive
    keep = f.contents.filename
    f.contents.filesize = os.path.getsize(a.prg)
    f.contents.parent_id = a.parent
    f.contents.storage_id = a.storage
    f.contents.filetype = FILETYPE_UNKNOWN

    rc = mtp.LIBMTP_Send_File_From_File(dev, a.prg.encode(), f, None, None)
    if rc != 0:
        mtp.LIBMTP_Dump_Errorstack(dev)
        print(f"send failed rc={rc}", file=sys.stderr)
    else:
        print(f"sent {a.prg} -> GARMIN/Apps/{name} (item_id={f.contents.item_id})")
    mtp.LIBMTP_Release_Device(dev)
    del keep
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
