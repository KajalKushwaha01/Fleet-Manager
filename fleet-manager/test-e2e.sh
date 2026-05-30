#!/usr/bin/env bash
set -euo pipefail

API_KEY="${FLEET_API_SECRET_KEY:-change-me-fleet-api}"
BASE_URL="${BASE_URL:-http://localhost:8080}"
PROM_URL="${PROM_URL:-http://localhost:9090}"
GRAFANA_URL="${GRAFANA_URL:-http://localhost:3000}"
ALERTMANAGER_URL="${ALERTMANAGER_URL:-http://localhost:9093}"
NODE_ID=""
FAILURES=0
PYTHON_BIN="${PYTHON_BIN:-python3}"

require_command() {
  local command_name="${1}"
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "FAIL missing command ${command_name}"
    exit 2
  fi
}

pass() {
  echo "PASS ${1}"
}

fail() {
  echo "FAIL ${1}"
  FAILURES=$((FAILURES + 1))
}

json_value() {
  local expression="${1}"
  "${PYTHON_BIN}" -c "import json,sys; data=json.load(sys.stdin); print(${expression})"
}

check_health() {
  if [[ "$(curl -fsS "${BASE_URL}/health" | json_value "data.get('status')")" == "ok" ]]; then
    pass "fleet-manager health"
  else
    fail "fleet-manager health"
  fi
}

register_node() {
  local response
  local existing_id
  existing_id="$(
    curl -fsS "${BASE_URL}/api/nodes" -H "X-API-Key: ${API_KEY}" |
      "${PYTHON_BIN}" -c "import json,sys; data=json.load(sys.stdin); print(next((n['id'] for n in data if n.get('ip') == 'node1'), ''))"
  )"
  if [[ "${existing_id}" =~ ^[0-9]+$ ]]; then
    curl -fsS -X DELETE "${BASE_URL}/api/nodes/${existing_id}" -H "X-API-Key: ${API_KEY}" >/dev/null
  fi
  response="$(
    curl -fsS -X POST "${BASE_URL}/api/nodes/register" \
      -H "Content-Type: application/json" \
      -H "X-API-Key: ${API_KEY}" \
      -d '{"hostname":"node1","ip":"node1","node_exporter_port":9100,"ssh_user":"root","install_node_exporter":false,"labels":{"env":"dev"}}'
  )"
  NODE_ID="$(printf '%s' "${response}" | json_value "data.get('id')")"
  if [[ "${NODE_ID}" =~ ^[0-9]+$ ]]; then
    pass "register node"
  else
    fail "register node"
  fi
}

list_nodes() {
  if [[ "$(curl -fsS "${BASE_URL}/api/nodes" -H "X-API-Key: ${API_KEY}" | json_value "len(data) >= 1")" == "True" ]]; then
    pass "list nodes"
  else
    fail "list nodes"
  fi
}

check_sd() {
  if [[ "$(curl -fsS "${BASE_URL}/api/sd/targets" | json_value "data[0]['targets'][0]")" == "node1:9100" ]]; then
    pass "service discovery target"
  else
    fail "service discovery target"
  fi
}

check_prometheus_targets() {
  sleep 20
  if [[ "$(curl -fsS "${PROM_URL}/api/v1/targets" | json_value "any(t.get('labels', {}).get('instance') == 'node1:9100' for t in data['data']['activeTargets'])")" == "True" ]]; then
    pass "prometheus target present"
  else
    fail "prometheus target present"
  fi
}

check_metrics() {
  local metrics
  metrics="$(curl -fsS "${BASE_URL}/metrics")"
  if grep -q "fleet_nodes_total" <<<"${metrics}"; then
    pass "fleet manager metrics"
  else
    fail "fleet manager metrics"
  fi
}

check_grafana() {
  if [[ "$(curl -fsS "${GRAFANA_URL}/api/health" | json_value "data.get('database')")" == "ok" ]]; then
    pass "grafana health"
  else
    fail "grafana health"
  fi
}

check_alertmanager() {
  if curl -fsS "${ALERTMANAGER_URL}/-/healthy" >/dev/null; then
    pass "alertmanager health"
  else
    fail "alertmanager health"
  fi
}

main() {
  require_command curl
  require_command "${PYTHON_BIN}"
  check_health
  register_node
  list_nodes
  check_sd
  check_prometheus_targets
  check_metrics
  check_grafana
  check_alertmanager
  if [[ "${FAILURES}" -gt 0 ]]; then
    exit 1
  fi
}

main "$@"
