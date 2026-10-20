#!/usr/bin/env bash
# Proof that a built image works: run it, ask /v1/health and /v1/embedded/frame, check that it is healthy,
# does not run as root and keeps the simulator off. Used by the CI and by the publication workflow.
#
#   scripts/docker_smoke.sh vigie-api:ci
#
# The frame is built from the real providers (the container reaches the Internet); with none of them
# reachable it is still a valid frame with empty fields.
set -euo pipefail

image="${1:?usage: docker_smoke.sh <image>}"
port="${SMOKE_PORT:-18080}"
name="vigie-smoke-$$"
work="$(mktemp -d)"

cleanup() {
  echo "--- container log (last lines)"
  docker logs "$name" 2>&1 | tail -n 30 || true
  docker rm --force "$name" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

docker run --detach --name "$name" --publish "127.0.0.1:${port}:8080" \
  --env VIGIE_CONTACT="${VIGIE_CONTACT:-}" "$image" >/dev/null

echo "--- GET /v1/health"
curl --fail --silent --show-error --retry 30 --retry-delay 1 --retry-connrefused --retry-all-errors \
  --max-time 10 --output "$work/health.json" "http://127.0.0.1:${port}/v1/health"
cat "$work/health.json" | head -c 400; echo
grep -q '"status"' "$work/health.json"

echo "--- GET /v1/embedded/frame"
curl --fail --silent --show-error --retry 3 --retry-delay 2 --retry-all-errors \
  --max-time 120 --output "$work/frame.txt" "http://127.0.0.1:${port}/v1/embedded/frame?device_type=waveshare-7b"
head -n 3 "$work/frame.txt" | cut -c1-160
head -n 1 "$work/frame.txt" | grep -q '^HEAD|'
tail -n 1 "$work/frame.txt" | grep -Eq '^END\|[0-9A-F]{8}$'

echo "--- the simulator is off by default"
code="$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 10 \
  --request POST --header 'Content-Type: application/json' --data '{"scenario":"clear"}' \
  "http://127.0.0.1:${port}/v1/simulate")"
echo "POST /v1/simulate -> HTTP ${code}"
test "$code" = "403"

echo "--- not running as root"
uid="$(docker exec "$name" id -u)"
echo "uid ${uid}"
test "$uid" != "0"

echo "--- the Docker health check turns healthy"
for _ in $(seq 1 60); do
  state="$(docker inspect --format '{{.State.Health.Status}}' "$name")"
  [ "$state" = "healthy" ] && break
  sleep 2
done
echo "health check: ${state}"
test "$state" = "healthy"

echo "OK: ${image} answers /v1/health and /v1/embedded/frame"
