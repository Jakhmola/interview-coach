# Recording the demo GIF

`docs/demo.gif` is a real round: a headless Chromium drives the running app with
the same clicks a candidate makes, and every frame is the app's own output.
Nothing is mocked or drawn by hand. Re-record it whenever the UI changes enough
that the old GIF misrepresents the product.

## What you need

- The app running (`make up`, plus the Vite dev server or a built frontend).
- `chromium`, `node`, `ffmpeg`, `python3` on the host.
- An account whose packet is prepped - a CV, a job description, at least one
  supporting doc - because the round is real and the questions are grounded in
  those documents.

## Record

```sh
EMAIL=you@example.com PASSWORD=... APP=http://127.0.0.1:5173 ./record-demo.sh
```

It runs a full two-topic round (roughly 5-20 minutes, depending on how fast the
model answers) and writes scenes to `frames/`: `PREFIX-0000.png` for the images
and `PREFIX.txt` listing each frame with the seconds it should hold.

The answers it types are in `answers.json`. Swap them for your own if you want
different topics on camera.

The closing marks can also be shot on their own, which is worth knowing when a
round records well but its last moments do not:

```sh
EMAIL=... PASSWORD=... ./record-close.sh <finished-session-id>
```

A finished round makes its marks again every time it is opened fresh, so that
scene never needs a whole round re-driven to get it.

## Cut

The GIF in the README was cut with:

```sh
python3 assemble.py ../../docs/demo.gif --w 1000 --fps 10 --colors 64 --dither none \
  frames/cover:2.4 \
  frames/pick@0-58:1.8 frames/pick@88-158:1.0:0.35 \
  frames/cyc10-a:1.7 frames/cyc10-b:1.25 \
  frames/cyc13-b@0-620:4.5 \
  frames/turn:1.15 \
  frames/closing:1.15
```

That is the packet, a round opened, the first question streaming in, an answer
typed and sent, the follow-up it draws, the topic being scored, the page turned,
and the closing marks: eight slices of seven scenes, out of the thirty a round
records.

Each argument is `SCENE[@START-END][:SPEED[:MAXHOLD]]`: `SPEED` divides the
recorded durations (1.3 plays a scene 30% faster), `MAXHOLD` re-clamps a frame
that sits too long, and `@START-END` takes only those frames, for a scene worth
showing but not in full. Pick the scenes that tell the story and drop the rest -
a round records more answers than a GIF should show.

Keep the result under about 5 MB so GitHub loads it inline on a phone. The
levers, in the order worth pulling: fewer scenes, then `--w`, then `--fps`, then
`--colors`. On a flat interface like this one `--dither none` costs nothing
visible and saves a good fraction of the file.

## How the waits disappear

A local model thinks for 10 to 40 seconds between turns, which no one wants to
watch. Each answer is recorded in two parts: `-a` stops a beat after the send,
`-b` starts when the thinking ends and the reply begins to stream. The wait in
between is never recorded, so a cut of the two runs continuously without any
frame being faked - what you see is the app's own output at its own pace.

Scenes that are honest but long are handled in the cut instead, with the speed
factors in the command above: the interviewer takes twenty seconds or so to
write an assessment, which is worth showing but not in full. Nothing is
re-timed to flatter the app - the page turn and the closing marks, the two
moments where the interface itself is doing the work, run near enough their own
speed, and the model's own pace is only ever cut, never stretched.

The opening is the exception to the two-part rule and is recorded unbroken: a
round's first question streams in before any thinking note appears, so the wait
there is a blinking caret in an empty question box. Thirty-odd identical frames,
cut out by taking two ranges of the one scene, and the seam is invisible.

## The pieces

| File | What it does |
| --- | --- |
| `shot.mjs` | Chromium over CDP: screenshots, `rec`/`recstop` screencasts, scripted acts |
| `capture.sh` | Logs in, seeds the session, points `shot.mjs` at the app |
| `record-demo.sh` | The act script for a full round, scene by scene |
| `record-close.sh` | The closing marks of a finished round, on their own |
| `assemble.py` | Chosen scenes to a palette-optimised GIF |
| `answers.json` | The answers typed on camera |

`shot.mjs` is also useful on its own for UI review: `--act shot=FILE` at any
point in a scripted flow, `--scheme dark` for night stock, `--w`/`--h` for a
viewport.
