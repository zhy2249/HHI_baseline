#!/usr/bin/env python3
"""Create/check a portable baseline manifest. No build or codec process is launched."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCOPES = ("source", "cfg", "cmake", "thirdparty", "models", "scripts/HHI测试cfg")
ROOT_FILES = ("CMakeLists.txt", "CMakePresets.json", "Makefile", "COPYING")


def snapshot(root: Path) -> dict:
    paths = [root / name for name in ROOT_FILES]
    for name in SCOPES:
        paths.extend(p for p in (root / name).rglob("*") if p.is_file())
    hashes = {}
    for path in sorted(paths):
        rel = path.relative_to(root).as_posix()
        if (path.name in {"version.h", "log.txt"} or "__pycache__" in path.parts
                or path.suffix.lower() in {".key", ".pem", ".pyc", ".xlsm", ".xlsx", ".log"}
                or "ca_priv" in path.parts or "private" in path.parts):
            continue
        data = path.read_bytes()
        # Match text=auto checkout normalization; binary assets retain their bytes.
        if b"\0" not in data:
            data = data.replace(b"\r\n", b"\n")
        hashes[rel] = hashlib.sha256(data).hexdigest()
    return {"schema": 1, "normalization": "CRLF to LF for files without NUL; version.h excluded",
            "files": hashes}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="only when deliberately defining a NEW baseline")
    parser.add_argument("--manifest", type=Path, default=ROOT / "docs/experiments/baseline_manifest.json")
    args = parser.parse_args()
    current = snapshot(ROOT)
    if args.write:
        if args.manifest.exists():
            parser.error("manifest already exists; do not silently redefine the baseline")
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Recorded {len(current['files'])} files: {args.manifest}")
        return 0
    saved = json.loads(args.manifest.read_text(encoding="utf-8"))
    old, new = saved["files"], current["files"]
    changed = sorted(p for p in old.keys() | new.keys() if old.get(p) != new.get(p))
    for name in changed:
        print("DIFF:", name)
    print(f"{'PASS' if not changed else 'FAIL'}: {len(new)} baseline files, {len(changed)} differences")
    return 1 if changed else 0


if __name__ == "__main__":
    raise SystemExit(main())
