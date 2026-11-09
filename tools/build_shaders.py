"""Compile the island's shaders (ui/island/*.frag) into Qt's .qsb packages.

    .venv\\Scripts\\python tools\\build_shaders.py           # rebuild what changed
    .venv\\Scripts\\python tools\\build_shaders.py --check   # exit 1 if a .qsb is missing or stale

The .qsb files are committed, so running hey-claude never needs the shader
compiler. Targets: HLSL 5.0 (D3D11, the normal path), GLSL 3.00 ES / 1.20 /
1.50 (OpenGL), MSL 1.2. pyside6-qsb ships with PySide6.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHADER_DIR = ROOT / "ui" / "island"
TARGETS = ["--glsl", "300 es,120,150", "--hlsl", "50", "--msl", "12"]


def qsb_exe() -> str | None:
    exe = shutil.which("pyside6-qsb")
    if exe:
        return exe
    cand = Path(sys.executable).parent / ("pyside6-qsb.exe" if sys.platform == "win32" else "pyside6-qsb")
    return str(cand) if cand.exists() else None


def stamp(src: Path) -> str:
    """The source's hash, line endings normalised (git may check it out with CRLF)."""
    return hashlib.sha256(src.read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:16]


def stale(src: Path) -> bool:
    out = src.with_suffix(src.suffix + ".qsb")
    mark = src.with_suffix(src.suffix + ".qsb.src")
    return not out.exists() or not mark.exists() or mark.read_text().strip() != stamp(src)


def build(src: Path, exe: str) -> None:
    out = src.with_suffix(src.suffix + ".qsb")
    subprocess.run([exe, *TARGETS, "-o", str(out), str(src)], check=True)
    src.with_suffix(src.suffix + ".qsb.src").write_text(stamp(src) + "\n")
    print(f"built {out.relative_to(ROOT)}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="only check that every .qsb is up to date")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    srcs = sorted(SHADER_DIR.glob("*.frag")) + sorted(SHADER_DIR.glob("*.vert"))
    bad = [s for s in srcs if a.force or stale(s)]
    if a.check:
        for s in bad:
            print(f"stale or missing: {s.relative_to(ROOT)}.qsb (run tools/build_shaders.py)")
        return 1 if bad else 0
    exe = qsb_exe()
    if not exe:
        print("pyside6-qsb not found (it comes with PySide6)")
        return 1
    for s in bad:
        build(s, exe)
    return 0


if __name__ == "__main__":
    sys.exit(main())
