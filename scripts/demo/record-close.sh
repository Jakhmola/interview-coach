#!/usr/bin/env bash
# Record just the closing marks: the highlighter, the stamp and the verdict.
#
#   EMAIL=... PASSWORD=... ./record-close.sh <finished-session-id>
#
# A finished round plays its closing marks every time it is opened fresh, so
# this scene can be re-shot on its own without driving a whole round again.
# Recording starts before the page mounts, because the marks are made as the
# round's first render lands.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${OUT:-$HERE/frames}
mkdir -p "$OUT"
rm -f "$OUT"/closing-*.png "$OUT"/closing.txt

: "${1:?pass the id of a finished session}"

timeout "${DEADLINE:-180}" "$HERE/capture.sh" "/interview/$1" "${SCHEME:-light}" "${W:-1200}" "${H:-800}" \
  --wait 200 \
  --act "rec=$OUT/closing|1800" \
  --act "waitfor=.practice-done|30000" \
  --act "wait=5000" \
  --act "recstop"
