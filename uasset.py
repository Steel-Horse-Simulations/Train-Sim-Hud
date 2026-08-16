"""
A real Unreal `.uasset` package reader: header, name map, export table.

WHY THIS REPLACES THE STRING SCAN
---------------------------------
Everything before this read assets by scanning for length-prefixed strings
and inferring structure from statistics. That worked for pulling names out of
a table, and failed at everything else - four separate searches for station
identity in the DataTrack layers all came back negative.

They were aimed at the wrong asset. The schedule lives in the
`RouteTimetableDefinition` index asset, where each instruction carries its
own `Destination.Name`, `ArrivalTime`, `CompletionTime` and `bIsStopping`.
There was never a join to find, so no amount of statistics on the wrong file
would have produced one.

Reading that properly means reading the package properly: the name map gives
FName indices their text, and the export map says where each object's
serialised data begins in the `.uexp`. Both come from the header.

Layout confirmed against the public reference implementation at
github.com/hcfairbanks/tsw_projects (`hud/src-tauri/src/uasset.rs`), which
the user pointed to. Written from the format, not copied.
"""

import os
import struct

UASSET_MAGIC = 0x9E2A83C1


class Reader:
    """Cursor over package bytes, resolving FNames through the name map."""

    def __init__(self, data, names=None):
        self.data = data
        self.pos = 0
        self.names = names or []

    def remaining(self):
        return len(self.data) - self.pos

    def seek(self, p):
        self.pos = max(0, min(int(p), len(self.data)))

    def skip(self, n):
        self.pos += n

    def _unpack(self, fmt, size):
        if self.pos + size > len(self.data):
            self.pos = len(self.data)
            return 0
        v = struct.unpack_from(fmt, self.data, self.pos)[0]
        self.pos += size
        return v

    def u8(self):
        return self._unpack("<B", 1)

    def i32(self):
        return self._unpack("<i", 4)

    def i64(self):
        return self._unpack("<q", 8)

    def f32(self):
        return self._unpack("<f", 4)

    def guid(self):
        b = self.data[self.pos:self.pos + 16]
        self.pos += 16
        return b

    def fstr(self):
        """FString: length-prefixed, ASCII when positive, UTF-16LE when
        negative. A bogus length consumes nothing rather than raising - a
        malformed asset should degrade, not crash the app."""
        n = self.i32()
        if n == 0:
            return ""
        if n > 0:
            if n > self.remaining():
                self.pos = len(self.data)
                return ""
            raw = self.data[self.pos:self.pos + n]
            self.pos += n
            return raw.split(b"\x00")[0].decode("utf-8", "replace")
        n = -n * 2
        if n > self.remaining():
            self.pos = len(self.data)
            return ""
        raw = self.data[self.pos:self.pos + n]
        self.pos += n
        return raw.split(b"\x00\x00")[0].decode("utf-16-le", "replace")

    def fname(self):
        """FName: int32 index into the name map plus an int32 Number.

        Number is an instance suffix, stored off-by-one so that 0 means
        "no suffix" - Platform_2 is stored as Number 3.
        """
        idx = self.i32()
        num = self.i32()
        if idx < 0 or idx >= len(self.names):
            return f"?{idx}"
        s = self.names[idx]
        return f"{s}_{num - 1}" if num > 0 else s

    def ftext(self, size):
        """FText: flags and history type, then the string payload. Only the
        source string is wanted here, so the simple path is taken and the
        cursor is left to the caller's explicit seek."""
        end = self.pos + size
        self.skip(4)          # Flags
        self.skip(1)          # HistoryType
        out = ""
        while self.pos < end:
            s = self.fstr()
            if s and not out:
                out = s
            if not s:
                break
        self.seek(end)
        return out


class Tag:
    __slots__ = ("name", "ptype", "size", "struct_type", "inner_type",
                 "bool_val", "value_offset")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def __repr__(self):
        return f"<{self.name}:{self.ptype} size={self.size}>"


def read_tag(r):
    """One FPropertyTag, or None at the terminating 'None'."""
    name = r.fname()
    if name == "None" or name.startswith("?"):
        return None
    ptype = r.fname()
    size = r.i32()
    r.skip(4)                       # ArrayIndex
    t = Tag(name=name, ptype=ptype, size=size, struct_type="",
            inner_type="", bool_val=0)
    if ptype == "StructProperty":
        t.struct_type = r.fname()
        r.guid()
    elif ptype == "BoolProperty":
        t.bool_val = r.u8()
    elif ptype in ("ByteProperty", "EnumProperty", "ArrayProperty", "SetProperty"):
        t.inner_type = r.fname()
    elif ptype == "MapProperty":
        r.fname()
        r.fname()
    if r.u8() != 0:                 # HasPropertyGuid
        r.guid()
    t.value_offset = r.pos
    return t


class Package:
    """A parsed .uasset header plus its .uexp payload."""

    def __init__(self, uasset_path):
        self.path = uasset_path
        self.names = []
        self.exports = []
        self.uexp = b""
        self.header_size = 0
        self._read()

    def _read(self):
        with open(self.path, "rb") as f:
            data = f.read()
        r = Reader(data)
        if (r.i32() & 0xFFFFFFFF) != UASSET_MAGIC:
            raise ValueError("not a .uasset package (bad magic)")
        legacy = r.i32()
        if legacy != -4:
            r.skip(4)               # LegacyUE3Version
        r.skip(4)                   # FileVersionUE4
        r.skip(4)                   # FileVersionLicenseeUE4
        cv_count = r.i32()
        r.skip(max(0, cv_count) * 20)

        self.header_size = r.i32()  # TotalHeaderSize
        r.fstr()                    # FolderName
        r.skip(4)                   # PackageFlags

        name_count, name_offset = r.i32(), r.i32()
        r.skip(8)                   # GatherableText count + offset
        export_count, export_offset = r.i32(), r.i32()

        r.seek(name_offset)
        for _ in range(max(0, name_count)):
            self.names.append(r.fstr())
            r.skip(4)               # two uint16 hashes

        r2 = Reader(data, self.names)
        r2.seek(export_offset)
        for _ in range(max(0, export_count)):
            class_index = r2.i32()
            r2.skip(8)              # SuperIndex, TemplateIndex
            outer_index = r2.i32()
            object_name = r2.fname()
            r2.skip(4)              # ObjectFlags
            serial_size = r2.i64()
            serial_offset = r2.i64()
            r2.skip(60)
            self.exports.append({
                "object_name": object_name,
                "class_index": class_index,
                "outer_index": outer_index,
                "serial_size": serial_size,
                "serial_offset": serial_offset,
            })

        # The payload lives in a sibling .uexp for cooked assets. Export
        # offsets are absolute across header+payload, so the header size is
        # subtracted to index into the .uexp.
        uexp_path = os.path.splitext(self.path)[0] + ".uexp"
        self.uexp_missing = False
        if os.path.isfile(uexp_path):
            with open(uexp_path, "rb") as f:
                self.uexp = f.read()
        elif len(data) > self.header_size + 16:
            # Uncooked or single-file: the payload really is in the .uasset.
            self.uexp = data
            self.header_size = 0
        else:
            # Cooked asset whose .uexp was not extracted alongside it. The
            # header parses, the export table looks fine, and there is simply
            # no payload - so a parse returns ZERO services and no error,
            # which reads as "this route has no timetable" when in fact the
            # file is half there. Flagged so the caller can say so.
            self.uexp = b""
            self.uexp_missing = True

    def export_body(self, export):
        start = int(export["serial_offset"]) - self.header_size
        size = int(export["serial_size"])
        if start < 0 or size <= 0 or start >= len(self.uexp):
            return b""
        return self.uexp[start:start + size]

    def reader_for(self, export):
        return Reader(self.export_body(export), self.names)

    def summary(self):
        return {
            "path": self.path,
            "name_count": len(self.names),
            "export_count": len(self.exports),
            "header_size": self.header_size,
            "uexp_bytes": len(self.uexp),
            "uexp_missing": getattr(self, "uexp_missing", False),
            "exports": [
                {"object_name": e["object_name"],
                 "serial_size": e["serial_size"]}
                for e in self.exports[:20]
            ],
        }
