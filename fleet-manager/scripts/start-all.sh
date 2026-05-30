#!/usr/bin/env bash
set -euo pipefail

API_KEY="${FLEET_API_SECRET_KEY:-change-me-fleet-api}"

require_command() {
  local command_name="${1}"
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "Missing required command: ${command_name}" >&2
    exit 2
  fi
}

wait_http_ok() {
  local name="${1}"
  local url="${2}"
  local attempts="${3:-40}"

  for _ in $(seq 1 "${attempts}"); do
    if curl -fsS "${url}" >/dev/null; then
      echo "PASS ${name}"
      return
    fi
    sleep 3
  done

  echo "Timed out waiting for ${name} at ${url}" >&2
  exit 3
}

register_node() {
  local node_name="${1}"
  local body
  body="{\"hostname\":\"${node_name}\",\"ip\":\"${node_name}\",\"node_exporter_port\":9100,\"ssh_user\":\"root\",\"install_node_exporter\":false,\"labels\":{\"env\":\"dev\",\"simulator\":\"true\"}}"

  if curl -fsS "http://localhost:8080/api/nodes" -H "X-API-Key: ${API_KEY}" | grep -q "\"ip\":\"${node_name}\""; then
    echo "SKIP ${node_name} already registered"
    return
  fi

  curl -fsS -X POST "http://localhost:8080/api/nodes/register" \
    -H "Content-Type: application/json" \
    -H "X-API-Key: ${API_KEY}" \
    -d "${body}" >/dev/null
  echo "PASS registered ${node_name}"
}

open_url() {
  local url="${1}"
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "${url}" >/dev/null 2>&1 || true
  elif command -v open >/dev/null 2>&1; then
    open "${url}" >/dev/null 2>&1 || true
  fi
}

main() {
  require_command curl
  require_command docker-compose

  docker-compose up -d --build
  wait_http_ok "Fleet Manager" "http://localhost:8080/health"
  wait_http_ok "Prometheus" "http://localhost:9090/-/healthy"
  wait_http_ok "Alertmanager" "http://localhost:9093/-/healthy"
  wait_http_ok "Grafana" "http://localhost:3000/api/health"

  register_node "node1"
  register_node "node2"
  register_node "node3"

  open_url "http://localhost:8080"
  open_url "http://localhost:3000"
  open_url "http://localhost:9090"
  open_url "http://localhost:9093"

  echo "Fleet Manager Console: http://localhost:8080"
  echo "Grafana:               http://localhost:3000  admin / change-me-grafana"
  echo "Prometheus:            http://localhost:9090"
  echo "Alertmanager:          http://localhost:9093"
  echo "Use API key in the console: ${API_KEY}"
}

main "$@"
