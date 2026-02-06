#!/usr/bin/env bash
set -euo pipefail

AUTH="rudderpi"
PHONE_PORT="8080"

# 1) Get IPv4 of eth0 (skip 169.254.x.x)
ETH_IP="$(ip -4 -o addr show dev eth0 scope global | awk '{print $4}' | cut -d/ -f1 | head -n1 || true)"
if [[ -z "${ETH_IP}" ]]; then
  echo "No IPv4 on eth0 yet"
  exit 1
fi

# 2) Get default gateway used on eth0 (works even if wg has a default route)
GW="$(ip -4 route show default dev eth0 | awk '{print $3}' | head -n1 || true)"
if [[ -z "${GW}" ]]; then
  echo "No default gateway on eth0"
  exit 1
fi

# 3) POST to phone
URL="http://${GW}:${PHONE_PORT}/rudder-pi/ip"
JSON="{\"ip\":\"${ETH_IP}\"}"

curl --fail --silent --show-error \
  -X POST "${URL}" \
  -H "Content-Type: application/json" \
  -H "X-Auth: ${AUTH}" \
  -d "${JSON}"

echo "Provisioned ${ETH_IP} to ${URL}"
