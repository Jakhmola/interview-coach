#!/usr/bin/env bash
# Drive one real 2-topic round against the running app and record it in scenes.
#
# Each answer is recorded in two parts: "-a" ends a beat after the send, when the
# interviewer starts thinking; "-b" starts when the thinking ends and the reply
# begins to stream. The wait in between is never recorded, which is how a real
# round with a local model on a laptop GPU cuts down to a README GIF.
#
#   EMAIL=you@example.com PASSWORD=... APP=http://127.0.0.1:5173 ./record-demo.sh
#
# Scenes land in ./frames as PREFIX-0000.png plus PREFIX.txt (frame + seconds);
# assemble.py turns a chosen list of them into the GIF.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${OUT:-$HERE/frames}
mkdir -p "$OUT"
rm -f "$OUT"/*.png "$OUT"/*.txt

COMPOSER="form.composer textarea"
SEND="form.composer button[type=submit]"
HOLD="div.composer .btn-primary"          # Next topic / See the scorecard
THINK=".practice-loading:not(.subtle)"    # the interviewer is thinking
DONE=".practice-done"
STATE='"prev=" + document.querySelectorAll(".prev-row").length + " | remarks=" + document.querySelectorAll(".remark").length + " | assessment=" + !!document.querySelector(".assessment") + " | composer=" + !!document.querySelector("form.composer") + " | hold=" + (document.querySelector("div.composer .btn-primary")?.textContent?.trim() ?? "-") + " | done=" + !!document.querySelector(".practice-done") + " | " + new Date().toISOString().slice(11,19)'

ans() { python3 -c "import json;print(json.load(open('$HERE/answers.json'))[$1])"; }

# ── the packet cover, then into a round (a real client-side nav, so the view
#    transition plays) ──
ACTS=(--act "waitfor=.box.next .btn-primary|20000"
      --act "rec=$OUT/cover|1800"
      --act "wait=1900"
      --act "cursor=.box.next .btn-primary"
      --act "waitfor=.practice-start-cta|20000"
      --act "wait=1600"
      --act "recstop"
      # One unbroken scene from the round sheet to a ready composer. The round's
      # first question streams in before any thinking note appears, so splitting
      # this the way an answer is split would miss the one shot worth having.
      --act "rec=$OUT/pick|1500"
      --act "wait=900"
      --act "eval=(()=>{const el=document.querySelector('input[type=range]'); const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set; [4,3,2].forEach((v,i)=>setTimeout(()=>{s.call(el,String(v)); el.dispatchEvent(new Event('input',{bubbles:true}));}, i*160)); return 'topics -> 2';})()"
      --act "wait=900"
      --act "cursor=.practice-start-cta"
      --act "waitfor=form.composer|300000"
      --act "wait=2200"
      --act "recstop"
      --act "eval=$STATE")

# One answer: type it, send it, then pick the recording back up when the reply
# starts arriving. Extra cycles after a topic closes are harmless no-ops.
cycle() {
  local n=$1 text=$2
  ACTS+=(--act "rec=$OUT/cyc$n-a|1500"
         --act "wait=500"
         --act "typein=$COMPOSER|28|$text"
         --act "cursor=$SEND"
         --act "waitgone=form.composer|10000"
         --act "waitfor=$THINK|12000"
         --act "wait=900"
         --act "recstop"
         --act "waitgone=$THINK|300000"
         --act "rec=$OUT/cyc$n-b|1500"
         --act "waitfor=$HOLD, form.composer, $DONE|300000"
         --act "wait=2400"
         --act "recstop"
         --act "eval=$STATE")
}

for i in 0 1 2 3 4 5; do cycle "1$i" "$(ans $i)"; done

# ── the candidate turns the page ──
ACTS+=(--act "wait=6000"
       --act "rec=$OUT/turn|1500"
       --act "wait=1200"
       --act "cursor=$HOLD"
       --act "waitfor=.prev-row|30000"
       --act "wait=2600"
       --act "recstop"
       --act "eval=$STATE")

for i in 5 0 1 2 3 4; do cycle "2$i" "$(ans $i)"; done

# ── the closing: stamp, verdict, scorecard ──
ACTS+=(--act "wait=6000"
       --act "rec=$OUT/close|1500"
       --act "wait=1200"
       --act "cursor=$HOLD"
       --act "waitfor=$DONE|30000"
       --act "wait=4500"
       --act "recstop"
       --act "eval=$STATE")

timeout "${DEADLINE:-2400}" "$HERE/capture.sh" /setup "${SCHEME:-light}" "${W:-1200}" "${H:-800}" "${ACTS[@]}"
