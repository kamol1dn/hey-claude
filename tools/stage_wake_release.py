"""Stage the shared "hey claude" model as a GitHub release asset (M2a).

    .venv\\Scripts\\python tools\\stage_wake_release.py state\\m2a\\run1\\hey_claude.onnx

It does NOT publish anything. It:
1. copies the model, its training report and models/LICENSE to dist/<tag>/;
2. writes the asset's SHA-256 and size into heyclaude/wake_models.py
   (DEFAULT_MODEL), so the app's first-run download only accepts that file;
3. writes release notes and prints the exact `gh release create` command.
Publishing is the maintainer's decision: run the printed command yourself.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from heyclaude.wake_models import DEFAULT_MODEL, RELEASE_TAG, sha256  # noqa: E402

REPO = "kamol1dn/hey-claude"


def notes(report: dict, digest: str, size: int) -> str:
    a = report.get("at_0_5", {})
    hit = a.get("hit", {})
    lines = [
        f"The shared \"hey claude\" wake-word model for hey-claude (openWakeWord format, {size / 1024:.0f} KB).",
        "",
        "hey-claude downloads it on first run into `models/hey_claude.onnx` and checks its SHA-256:",
        f"`{digest}`",
        "",
        "Trained locally with `python -m heyclaude.wake_train default` on **synthetic voices only** "
        "(54 Kokoro voices and blends, with American, British and accented pronunciations: "
        "hey claude / klod / clod / klawd / klode and Russian/Uzbek-style variants), look-alike phrases, "
        "and a sample of openWakeWord's ACAV100M negative features. No real person's recordings.",
        "",
        "Measured (threshold 0.5, the calibrated default):",
    ]
    for k, v in hit.items():
        lines.append(f"- hit rate on {v['n']} held-out {k} clips (voices never trained on): {v['hit_at_0_5'] * 100:.1f}%")
    if "fph" in a:
        lines.append(f"- false wakes on openWakeWord's {report.get('validation_hours')} h validation set: "
                     f"{a['false_wakes']} ({a['fph']:.2f} per hour)")
    lines += ["", "Licence: CC BY-NC-SA 4.0 (see LICENSE in this release and models/LICENSE in the repository)."]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("model", type=Path)
    ap.add_argument("--tag", default=RELEASE_TAG)
    ap.add_argument("--no-code", action="store_true", help="don't write the hash into heyclaude/wake_models.py")
    args = ap.parse_args(argv)

    model = args.model.resolve()
    report_path = model.with_suffix(".report.json")
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
    out = ROOT / "dist" / args.tag
    out.mkdir(parents=True, exist_ok=True)
    asset = out / "hey_claude.onnx"
    shutil.copy2(model, asset)
    files = [asset]
    if report_path.is_file():
        shutil.copy2(report_path, out / "hey_claude.report.json")
        files.append(out / "hey_claude.report.json")
    shutil.copy2(ROOT / "models" / "LICENSE", out / "LICENSE")
    files.append(out / "LICENSE")
    digest, size = sha256(asset), asset.stat().st_size
    (out / "NOTES.md").write_text(notes(report, digest, size), encoding="utf-8")

    if not args.no_code:
        p = ROOT / "heyclaude" / "wake_models.py"
        s = p.read_text(encoding="utf-8")
        s = re.sub(r'"sha256": "[0-9a-f]*",', f'"sha256": "{digest}",', s, count=1)
        s = re.sub(r'"size": \d+,', f'"size": {size},', s, count=1)
        s = s.replace(f'RELEASE_TAG = "{RELEASE_TAG}"', f'RELEASE_TAG = "{args.tag}"', 1)
        p.write_text(s, encoding="utf-8")
        print(f"wrote SHA-256 and size into {p.relative_to(ROOT)}")
    rel = lambda f: str(f.relative_to(ROOT))  # noqa: E731
    print(f"\nstaged {size / 1024:.0f} KB, SHA-256 {digest}")
    print(f"expected URL: {DEFAULT_MODEL['url'].replace(RELEASE_TAG, args.tag)}")
    print("\nNot published. To publish (after the PR is merged):\n")
    print(f'gh release create {args.tag} {" ".join(rel(f) for f in files)} --repo {REPO} '
          f'--title "hey claude wake model v1" --notes-file {rel(out / "NOTES.md")}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
