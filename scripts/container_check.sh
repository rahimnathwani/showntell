#!/usr/bin/env bash
# Build the hosted image and run it locked down as deploy/compose.yaml does (read-only root, every
# capability dropped, no-new-privileges, the Chromium seccomp profile), with the dev login and fake
# speech, then drive it from inside with scripts/container_e2e.py. CI's container job runs this.
#
#   scripts/container_check.sh            # IMAGE=showntell:check by default
set -euo pipefail
cd "$(dirname "$0")/.."

image="${IMAGE:-showntell:check}"
name="showntell-check-$$"
volume="$name-data"
cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then docker logs "$name" 2>&1 | tail -40 || true; fi
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker volume rm "$volume" >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT

docker build -t "$image" .
docker run -d --name "$name" \
  --read-only --tmpfs /tmp:size=512m --shm-size 256m \
  --cap-drop ALL --security-opt no-new-privileges:true --security-opt seccomp=deploy/chromium-seccomp.json \
  -v "$volume:/data" \
  -e SHOWNTELL_HOST=127.0.0.1 -e SHOWNTELL_PUBLIC_URL=http://localhost:4747 \
  -e SHOWNTELL_DEV_USERS=check@example.com -e SHOWNTELL_SPEECH=fake \
  "$image" >/dev/null

for _ in $(seq 1 60); do
  if docker exec "$name" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4747/health', timeout=2)" 2>/dev/null; then
    break
  fi
  sleep 2
done
[ "$(docker exec "$name" id -u)" = "10001" ] || { echo "FAIL: not running as uid 10001"; exit 1; }
docker exec -i "$name" python - < scripts/container_e2e.py
