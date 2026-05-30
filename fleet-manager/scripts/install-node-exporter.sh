#!/usr/bin/env bash
set -euo pipefail

NODE_EXPORTER_VERSION="${NODE_EXPORTER_VERSION:-1.8.2}"
NODE_EXPORTER_USER="node_exporter"
NODE_EXPORTER_GROUP="node_exporter"
INSTALL_DIR="/usr/local/bin"
TMP_DIR="$(mktemp -d)"
ARCHIVE_NAME=""
DOWNLOAD_URL=""
CHECKSUM_URL=""

cleanup() {
  rm -rf "${TMP_DIR}"
}

require_command() {
  local command_name="${1}"
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "Missing required command: ${command_name}" >&2
    exit 10
  fi
}

detect_architecture() {
  local machine
  machine="$(uname -m)"
  case "${machine}" in
    x86_64 | amd64)
      echo "amd64"
      ;;
    aarch64 | arm64)
      echo "arm64"
      ;;
    *)
      echo "Unsupported architecture: ${machine}" >&2
      exit 11
      ;;
  esac
}

create_user() {
  if getent group "${NODE_EXPORTER_GROUP}" >/dev/null 2>&1; then
    echo "Group ${NODE_EXPORTER_GROUP} already exists"
  else
    groupadd --system "${NODE_EXPORTER_GROUP}"
  fi

  if id -u "${NODE_EXPORTER_USER}" >/dev/null 2>&1; then
    echo "User ${NODE_EXPORTER_USER} already exists"
  else
    useradd \
      --system \
      --no-create-home \
      --shell /usr/sbin/nologin \
      --gid "${NODE_EXPORTER_GROUP}" \
      "${NODE_EXPORTER_USER}"
  fi
}

download_node_exporter() {
  local arch="${1}"
  ARCHIVE_NAME="node_exporter-${NODE_EXPORTER_VERSION}.linux-${arch}.tar.gz"
  DOWNLOAD_URL="https://github.com/prometheus/node_exporter/releases/download/v${NODE_EXPORTER_VERSION}/${ARCHIVE_NAME}"
  CHECKSUM_URL="https://github.com/prometheus/node_exporter/releases/download/v${NODE_EXPORTER_VERSION}/sha256sums.txt"

  curl -fsSL "${DOWNLOAD_URL}" -o "${TMP_DIR}/${ARCHIVE_NAME}"
  curl -fsSL "${CHECKSUM_URL}" -o "${TMP_DIR}/sha256sums.txt"
}

verify_checksum() {
  cd "${TMP_DIR}"
  grep " ${ARCHIVE_NAME}$" sha256sums.txt | sha256sum -c -
}

install_binary() {
  if command -v node_exporter >/dev/null 2>&1; then
    local current_version
    current_version="$(node_exporter --version 2>&1 | head -n 1 || true)"
    if [[ "${current_version}" == *"${NODE_EXPORTER_VERSION}"* ]]; then
      echo "node_exporter ${NODE_EXPORTER_VERSION} already installed"
      return
    fi
  fi

  tar -xzf "${TMP_DIR}/${ARCHIVE_NAME}" -C "${TMP_DIR}"
  install \
    -o root \
    -g root \
    -m 0755 \
    "${TMP_DIR}/${ARCHIVE_NAME%.tar.gz}/node_exporter" \
    "${INSTALL_DIR}/node_exporter"
}

write_systemd_unit() {
  cat >/etc/systemd/system/node_exporter.service <<'UNIT'
[Unit]
Description=Prometheus Node Exporter
Documentation=https://github.com/prometheus/node_exporter
After=network-online.target
Wants=network-online.target

[Service]
User=node_exporter
Group=node_exporter
Type=simple
ExecStart=/usr/local/bin/node_exporter --collector.systemd --collector.processes --collector.tcpstat --collector.netstat
Restart=on-failure
RestartSec=5s
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
MemoryDenyWriteExecute=true
ReadWritePaths=/run

[Install]
WantedBy=multi-user.target
UNIT
}

reload_and_start_service() {
  systemctl daemon-reload
  systemctl enable --now node_exporter
}

verify_health() {
  sleep 2
  curl -sf "http://localhost:9100/metrics" >/dev/null
}

main() {
  trap cleanup EXIT
  require_command curl
  require_command sha256sum
  require_command tar
  require_command systemctl
  require_command useradd
  require_command groupadd

  local arch
  arch="$(detect_architecture)"
  create_user
  download_node_exporter "${arch}"
  verify_checksum
  install_binary
  write_systemd_unit
  reload_and_start_service
  verify_health
  echo "node_exporter ${NODE_EXPORTER_VERSION} installed and healthy"
}

main "$@"
