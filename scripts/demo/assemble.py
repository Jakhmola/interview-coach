#!/usr/bin/env python3
"""Cut recorded segments into one GIF.

usage: assemble.py OUT.gif [--w 900] [--fps 12] [--colors 128] [--crop x,y,w,h]
                   [--dither none] [--tail 2.0] SEG[:SPEED[:MAXHOLD]] ...

Each SEG is a prefix written by shot.mjs's rec/recstop (SEG.txt = "frame<TAB>seconds").
SPEED divides every duration (1.6 = 60% faster); MAXHOLD re-clamps a still frame,
which is the knob for trimming a beat that drags. SEG@START-END takes only those
frames, for when a scene is worth showing but not in full.
"""

import subprocess
import sys
from pathlib import Path

args = sys.argv[1:]
out = args.pop(0)
opt = {
    "w": "900",
    "fps": "12",
    "colors": "128",
    "crop": None,
    "dither": "bayer:bayer_scale=3",
    "tail": "2.0",
}
segs = []
while args:
    a = args.pop(0)
    if a.startswith("--"):
        opt[a[2:]] = args.pop(0)
    else:
        segs.append(a)

lines, total = [], 0.0
for spec in segs:
    name, *rest = spec.split(":")
    speed = float(rest[0]) if rest else 1.0
    cap = float(rest[1]) if len(rest) > 1 else None
    lo, hi = 0, None
    if "@" in name:
        name, span = name.split("@")
        lo, hi = (int(v) for v in span.split("-"))
    txt = Path(f"{name}.txt")
    if not txt.exists():
        sys.exit(f"missing {txt}")
    n = 0
    for line in txt.read_text().splitlines()[lo:hi]:
        frame, dur = line.split("\t")
        d = float(dur)
        if cap is not None:
            d = min(d, cap)
        d /= speed
        lines.append(f"file '{frame}'\nduration {d:.3f}")
        total += d
        n += 1
    print(f"{Path(name).name}: {n} frames, {total:.1f}s running")

# Hold the closing frame before the loop restarts, so the last thing the GIF
# says is readable rather than a flash.
head, _, dur = lines[-1].partition("\nduration ")
if float(opt["tail"]) > float(dur):
    total += float(opt["tail"]) - float(dur)
    lines[-1] = f"{head}\nduration {float(opt['tail']):.3f}"

last = lines[-1].split("\n")[0]
concat = Path(out).with_suffix(".concat.txt")
concat.write_text("\n".join(lines) + f"\n{last}\n")

vf = []
if opt["crop"]:
    x, y, w, h = opt["crop"].split(",")
    vf.append(f"crop={w}:{h}:{x}:{y}")
vf += [
    f"fps={opt['fps']}",
    f"scale={opt['w']}:-1:flags=lanczos",
    # stats_mode/diff_mode both exploit how little of a page actually changes
    # between frames: the palette is spent on what moves, and only the changed
    # rectangle is rewritten, which is most of the file size on a static layout.
    f"split[a][b];[a]palettegen=max_colors={opt['colors']}:stats_mode=diff[p];"
    f"[b][p]paletteuse=dither={opt['dither']}:diff_mode=rectangle",
]
cmd = [
    "ffmpeg",
    "-y",
    "-loglevel",
    "error",
    "-f",
    "concat",
    "-safe",
    "0",
    "-i",
    str(concat),
    "-vf",
    ",".join(vf),
    "-loop",
    "0",
    out,
]
subprocess.run(cmd, check=True)
size = Path(out).stat().st_size / 1e6
print(f"{out}: {total:.1f}s, {size:.1f} MB")
