#!/usr/bin/env bash
# Installs the built wheel outside the repository and runs it end to end with fake speech:
# version, doctor, play on a two-slide file, then shuts the daemon down and checks it left
# no process and no lockfile behind. Run after `uv build`: bash scripts/wheel_smoke.sh
set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
wheel=$(ls "$repo"/dist/showntell-*.whl | head -n 1)
work=$(mktemp -d "${TMPDIR:-/tmp}/showntell-smoke.XXXXXX")
cd "$work"

export SHOWNTELL_DATA="$work/data"
export SHOWNTELL_SPEECH=fake
export SHOWNTELL_PORT="${SHOWNTELL_PORT:-4791}"
run() { uvx --no-config --from "$wheel" showntell "$@"; }
# On failure, stop any daemon this script started so no process outlives the job.
trap 'pkill -f "showntell serve.*$SHOWNTELL_DATA" 2>/dev/null || true' EXIT

echo "== wheel: $wheel"
echo "== work dir: $work"
test "$(run version)" = "$(basename "$wheel" | cut -d- -f2)"

echo "== doctor"
run doctor --speech fake

cat > talk.md <<'EOF'
# Smoke test
> Built wheel, fake speech

This walkthrough checks the installed wheel.

## One list

- parse
- voice

First [parse](#parse) the Markdown file into slides and their cues, and only then [voice](#voice) each paragraph.
EOF

echo "== play"
url=$(run play talk.md --no-open 2> play.err)
echo "$url"
cat play.err
if grep -q "^- " play.err; then echo "play reported problems"; exit 1; fi
case "$url" in
  http://localhost:*/w/*) ;;
  *) echo "unexpected link: $url"; exit 1 ;;
esac
lock="$SHOWNTELL_DATA/daemon.json"
test -f "$lock" || { echo "no lockfile after play"; exit 1; }

echo "== walkthrough is listed"
run list | grep -q "Smoke test"

echo "== shutdown"
read -r pid port token < <(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["pid"], d["port"], d["token"])' "$lock")
curl -fsS -X POST -H "Authorization: Bearer $token" -H "Content-Type: application/json" -d '{}' \
  "http://127.0.0.1:$port/api/rpc/shutdown"
echo
for _ in $(seq 1 100); do
  kill -0 "$pid" 2>/dev/null || break
  sleep 0.1
done
if kill -0 "$pid" 2>/dev/null; then echo "daemon $pid still running"; kill "$pid"; exit 1; fi
if pgrep -f "showntell serve.*$SHOWNTELL_DATA" >/dev/null; then echo "stray showntell process"; exit 1; fi
if test -e "$lock"; then echo "lockfile left behind: $lock"; exit 1; fi

echo "== clean exit"
rm -rf "$work"
