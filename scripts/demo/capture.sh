#!/usr/bin/env bash
# capture.sh PATH SCHEME W H [shot.mjs args...]
#
# Point a headless Chromium at the running app, already logged in as $EMAIL and
# with that account's newest job active, then run the given --act script.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
API=${API:-http://127.0.0.1:8000}
APP=${APP:-http://127.0.0.1:5173}
: "${EMAIL:?set EMAIL to the account to record}"
: "${PASSWORD:?set PASSWORD for that account}"

path=$1; scheme=$2; w=$3; h=$4; shift 4

TOKEN=$(curl -s -m 10 -X POST "$API/auth/login" -H 'content-type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
USER=$(curl -s -m 10 "$API/auth/me" -H "authorization: Bearer $TOKEN")
JOB=$(curl -s -m 10 "$API/jobs" -H "authorization: Bearer $TOKEN" \
  | python3 -c "import sys,json;j=json.load(sys.stdin);print(j[0]['id'] if j else '')")

node "$HERE/shot.mjs" --url "$APP$path" --scheme "$scheme" --w "$w" --h "$h" --wait 3000 \
  --local "stock=$scheme" \
  --local "interview_coach.token=$TOKEN" \
  --local "interview_coach.user=$USER" \
  --local "interview_coach.active_job_id=$JOB" \
  "$@"
