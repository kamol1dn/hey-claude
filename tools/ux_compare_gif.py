"""Side-by-side before/after GIF of one scenario from two ux_stress runs.

    .venv\\Scripts\\python tools\\ux_compare_gif.py BEFORE_DIR AFTER_DIR OUT.gif

Each side is the scenario's island frames (state/ux/runs/<run>/<scenario>-<n>/frames),
lined up on the moment the request starts (the `turn` event), at 10 fps.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from heyclaude import ux_probe as UX  # noqa: E402


def side(run: Path):
    ev = UX.load_timeline(run / "timeline.jsonl")
    t_turn = next(e["t"] for e in ev if e["k"] == "turn")
    grabs = [g for g in ev if g["k"] == "grab" and (run / "frames" / g["file"]).exists()]
    return t_turn, grabs


def frame_at(grabs, t):
    best = None
    for g in grabs:
        if g["t"] <= t:
            best = g
        else:
            break
    return best


def main(before, after, out, pre=2.0, post=40.0, fps=10, scale=0.6):
    from PIL import Image, ImageDraw
    sides = []
    for d in (Path(before), Path(after)):
        t0, grabs = side(d)
        sides.append((d, t0, grabs))
    first = Image.open(sides[0][0] / "frames" / sides[0][2][0]["file"])
    W, H = first.size
    crop_h = min(H, max(g["box"][1] + g["box"][3] for _, _, gs in sides for g in gs) + 14)
    frames = []
    n = int((pre + post) * fps)
    bg = (27, 27, 31, 255)
    for i in range(n):
        dt = -pre + i / fps
        canvas = Image.new("RGBA", (W * 2 + 12, crop_h + 22), bg)
        d = ImageDraw.Draw(canvas)
        for k, (run, t0, grabs) in enumerate(sides):
            g = frame_at(grabs, t0 + dt)
            if g is not None:
                im = Image.open(run / "frames" / g["file"]).convert("RGBA").crop((0, 0, W, crop_h))
                canvas.alpha_composite(im, (k * (W + 12), 22))
            d.text((k * (W + 12) + 8, 5), f"{'BEFORE' if k == 0 else 'AFTER'}  {dt:+5.1f}s  {g['state'] if g else ''}",
                   fill=(190, 190, 190, 255))
        if scale != 1:
            canvas = canvas.resize((round(canvas.width * scale), round(canvas.height * scale)), Image.LANCZOS)
        frames.append(canvas.convert("P", palette=Image.ADAPTIVE, colors=96))
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=int(1000 / fps), loop=0, optimize=False)
    print(f"{out}: {len(frames)} frames")


if __name__ == "__main__":
    main(*sys.argv[1:4])
