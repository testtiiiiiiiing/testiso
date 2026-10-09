"""Rebuild the supplied Windows PyInstaller container without executing it.

Use CPython 3.13. This preserves its Windows Python/Tcl runtime and bootloader,
replaces the application entry point with the readable sources in this repo,
and adds the stdlib modules required by those sources. Native, independent
builds are available through build_windows.cmd and the Windows CI workflow.
This packager does not establish that the resulting EXE runs on Windows.
"""

import argparse
import hashlib
import importlib.util
import json
import marshal
from pathlib import Path
import struct
import sys
import sysconfig
import types
import zlib

MAGIC = b"MEI\x0c\x0b\x0a\x0b\x0e"
COOKIE = struct.Struct("!8sIIII64s")
ENTRY = struct.Struct("!IIIIBc")
PROJECT = Path(__file__).resolve().parent.parent


def read_archive(data):
    cookie_at = data.rfind(MAGIC)
    if cookie_at < 0 or cookie_at + COOKIE.size != len(data):
        raise ValueError("Expected a complete, unsigned PyInstaller onefile executable")
    magic, total, table_at, table_size, version, library = COOKIE.unpack_from(data, cookie_at)
    start = len(data) - total
    if start <= 0 or table_at + table_size != total - COOKIE.size:
        raise ValueError("Invalid archive boundaries")
    table = data[start + table_at : start + table_at + table_size]
    entries = []
    offset = 0
    while offset < len(table):
        size, position, compressed, uncompressed, flag, kind = ENTRY.unpack_from(table, offset)
        if size < ENTRY.size + 1 or offset + size > len(table) or position + compressed > table_at:
            raise ValueError("Invalid archive table entry")
        name = table[offset + ENTRY.size : offset + size].split(b"\0", 1)[0].decode()
        payload = data[start + position : start + position + compressed]
        unpacked = zlib.decompress(payload) if flag else payload
        if len(unpacked) != uncompressed:
            raise ValueError("Invalid uncompressed length: " + name)
        entries.append((name, kind, flag, payload, unpacked))
        offset += size
    return data[:start], version, library, entries


def bundle_source(launch=True):
    sources = {}
    for name in ("__init__", "base", "safety", "catalog", "studio", "__main__"):
        sources[name] = (PROJECT / "winslim" / (name + ".py")).read_text(encoding="utf-8")
    loader = """import sys, types, os
_sources = SOURCES
_package = types.ModuleType('winslim')
_package.__package__ = 'winslim'
_package.__path__ = []
sys.modules['winslim'] = _package
exec(compile(_sources['__init__'], 'winslim/__init__.py', 'exec'), _package.__dict__)
for _name in ('base', 'safety', 'catalog', 'studio', '__main__'):
    _fullname = 'winslim.' + _name
    _module = types.ModuleType(_fullname)
    _module.__package__ = 'winslim'
    _module.__file__ = os.path.join(getattr(sys, '_MEIPASS', os.getcwd()), 'winslim', _name + '.py')
    sys.modules[_fullname] = _module
    setattr(_package, _name, _module)
    exec(compile(_sources[_name], _module.__file__, 'exec'), _module.__dict__)
""".replace("SOURCES", repr(sources))
    if launch:
        loader += "sys.modules['winslim.__main__'].main()\n"
    return loader


def extend_pyz(pyz):
    if pyz[:4] != b"PYZ\0" or pyz[4:8] != importlib.util.MAGIC_NUMBER:
        raise ValueError("PYZ requires the same CPython 3.13 bytecode version")
    table_at = struct.unpack_from("!I", pyz, 8)[0]
    table = dict(marshal.loads(pyz[table_at:]))
    body = bytearray(pyz[:table_at])
    stdlib = Path(sysconfig.get_path("stdlib"))
    for module in (
        "xml.etree",
        "xml.etree.ElementTree",
        "xml.etree.ElementPath",
        "ctypes.wintypes",
    ):
        parts = module.split(".")
        path = stdlib.joinpath(*parts)
        package = path.is_dir()
        source = path / "__init__.py" if package else path.with_suffix(".py")
        code = compile(source.read_bytes(), module.replace(".", "/") + ".py", "exec")
        payload = zlib.compress(marshal.dumps(code))
        table[module] = (1 if package else 0, len(body), len(payload))
        body.extend(payload)
    body[8:12] = struct.pack("!I", len(body))
    body.extend(marshal.dumps(list(table.items())))
    return bytes(body)


def build(original, output):
    if sys.version_info[:2] != (3, 13):
        raise ValueError("Run this packager with CPython 3.13")
    if original.resolve() == output.resolve():
        raise ValueError("Output must differ from the uploaded original")
    uploaded = original.read_bytes()
    prefix, version, library, entries = read_archive(uploaded)
    if version != 313 or not prefix.startswith(b"MZ"):
        raise ValueError("Expected the supplied Python 3.13 Windows EXE")
    application = marshal.dumps(compile(bundle_source(), "WinSlim_Studio_4_0.py", "exec"))
    body, table = bytearray(), bytearray()
    replaced = 0
    scripts = []
    for name, kind, flag, payload, unpacked in entries:
        if kind == b"s" and name == "winslim":
            unpacked = application
            payload = zlib.compress(unpacked)
            flag = 1
            replaced += 1
        elif kind == b"z":
            unpacked = extend_pyz(unpacked)
            payload = zlib.compress(unpacked) if flag else unpacked
        if kind == b"s":
            code = marshal.loads(unpacked)
            if not isinstance(code, types.CodeType):
                raise ValueError("Invalid script entry")
            scripts.append(name)
        position = len(body)
        body.extend(payload)
        encoded = name.encode() + b"\0"
        length = (ENTRY.size + len(encoded) + 15) & ~15
        table.extend(ENTRY.pack(length, position, len(payload), len(unpacked), flag, kind))
        table.extend(encoded + b"\0" * (length - ENTRY.size - len(encoded)))
    if replaced != 1:
        raise ValueError("Expected exactly one original application script")
    table_at = len(body)
    body.extend(table)
    body.extend(COOKIE.pack(MAGIC, len(body) + COOKIE.size, table_at, len(table), version, library))
    output.parent.mkdir(parents=True, exist_ok=True)
    rebuilt = prefix + bytes(body)
    read_archive(rebuilt)  # Validate every compressed entry and archive boundary.
    output.write_bytes(rebuilt)
    manifest = {
        "version": "4.0.8",
        "original_sha256": hashlib.sha256(uploaded).hexdigest(),
        "output_sha256": hashlib.sha256(rebuilt).hexdigest(),
        "runtime_origin": "Windows Python/Tcl runtime and bootloader from the user-uploaded EXE",
        "application": "Replaced with winslim Python sources in this repository",
        "windows_execution_tested": False,
        "scripts": scripts,
        "source_sha256": {
            str(p.relative_to(PROJECT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((PROJECT / "winslim").glob("*.py"))
        },
    }
    output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {"output": str(output), "bytes": len(rebuilt), "sha256": manifest["output_sha256"]}
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(args.original, args.output)
