## Architecture
```text
+---------------------+        +---------------------+
| Operators / CI      |        | Grafana             |
| curl / scripts      |        | :3000               |
+----------+----------+        +----------+----------+
           |                              |
           v                              v
+----------+----------+        +----------+----------+
| Fleet Manager API   |<-------+ Prometheus          |
| :8080               | HTTP SD| :9090               |
| SQLite + metrics    |        | rules + TSDB        |
+----------+----------+        +----------+----------+
           |                              |
           | reload                       | alerts
           v                              v
+----------+----------+        +----------+----------+
| node_exporter       |        | Alertmanager        |
| node1/node2/node3   |        | :9093               |
+---------------------+        +---------------------+
```

## Prerequisites
Docker Engine 24.0+ and Docker Compose v2.20+ are required. Install `curl` and `jq` locally for the test commands. Linux hosts that use `scripts/install-node-exporter.sh` need `systemd`, `curl`, `tar`, `sha256sum`, `useradd`, and `groupadd`.

## Quick Start (3 commands max to get running)
```bash
cp .env .env.local 2>/dev/null || true
docker-compose up -d --build
curl -fsS http://localhost:8080/health | jq .
```

## Step-by-Step Start Guide
1. Open PowerShell in this project folder:
   ```powershell
   cd "C:\Users\Admin\Desktop\fleet-manager"
   ```

2. Start everything, wait for health checks, auto-register simulators, and open all local URLs:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\start-all.ps1
   ```

3. Open the main console if it is not already open:
   ```text
   http://localhost:8080
   ```

4. Enter this API key in the console Access box:
   ```text
   change-me-fleet-api
   ```

5. Use the console for day-to-day checks:
   - Service Health shows Fleet Manager, Prometheus, and Alertmanager.
   - Nodes shows lifecycle state and removal actions.
   - Prometheus Targets shows scrape health.
   - Register Node validates hostname, target host, port, and labels JSON before calling the API.

6. Native tools are also opened by the script:
   ```text
   Grafana:      http://localhost:3000  admin / change-me-grafana
   Prometheus:   http://localhost:9090
   Alertmanager: http://localhost:9093
   ```

7. Stop everything when finished:
   ```powershell
   docker-compose down
   ```

## Configuration
| Variable | Default | Purpose |
|---|---:|---|
| `GRAFANA_ADMIN_PASSWORD` | `change-me-grafana` | Grafana admin password. Change before shared use. |
| `FLEET_API_SECRET_KEY` | `change-me-fleet-api` | API key accepted in `X-API-Key` or `Authorization: Bearer`. |
| `PROMETHEUS_RETENTION_DAYS` | `15` | Prometheus TSDB retention in days. |
| `ALERTMANAGER_SLACK_WEBHOOK` | empty | Reserved for Slack receiver integration. |
| `DATABASE_PATH` | `/data/nodes.db` | SQLite database location inside Fleet Manager. |
| `PROMETHEUS_URL` | `http://prometheus:9090` | Prometheus base URL used by Fleet Manager. |
| `LOG_LEVEL` | `INFO` | Structured JSON log level. |

## API Reference
All protected endpoints require `X-API-Key: ${FLEET_API_SECRET_KEY}` or `Authorization: Bearer ${FLEET_API_SECRET_KEY}`.

| Method | Path | Body | Response |
|---|---|---|---|
| `POST` | `/api/nodes/register` | `{"hostname":"node1","ip":"node1","ssh_user":"root","ssh_port":22,"node_exporter_port":9100,"install_node_exporter":false,"labels":{"env":"dev"}}` | `201 {"id":1,"status":"ACTIVE"}` |
| `GET` | `/api/nodes` | none | `200` array of nodes with lifecycle status and labels |
| `DELETE` | `/api/nodes/{id}` | none | `200 {"id":1,"status":"DECOMMISSIONED"}` |
| `GET` | `/api/nodes/{id}/metrics` | none | `200` selected Prometheus query results for the node |
| `POST` | `/api/nodes/{id}/exec` | `{"command":"df"}` where command is `df`, `free`, `uptime`, or `systemctl-node-exporter` | `200 {"output":"..."}` |
| `GET` | `/api/sd/targets` | none | Prometheus HTTP SD target list |
| `GET` | `/health` | none | `{"status":"ok","nodes_active":N}` |
| `GET` | `/metrics` | none | Prometheus text exposition |
| `POST` | `/api/alerts/webhook` | Alertmanager webhook payload | `{"status":"accepted"}` |

## Alert Rules
| Alert | Severity | Trigger |
|---|---|---|
| `NodeDown` | critical | `up == 0` for a Linux node for 2 minutes |
| `NodeFlapping` | warning | `changes(up[15m]) > 4` |
| `UnexpectedReboot` | critical | Uptime below 600 seconds |
| `Watchdog` | none | Constant alert pipeline heartbeat |
| `HighCPUCritical` | critical | CPU usage ratio above 95% for 5 minutes |
| `HighCPUWarning` | warning | CPU usage ratio above 80% for 10 minutes |
| `HighIOWait` | warning | CPU iowait above 20% for 10 minutes |
| `HighCPUSteal` | warning | CPU steal above 10% for 5 minutes |
| `LoadAverageHigh` | warning | Load1 divided by CPU count above 2 |
| `MemoryCritical` | critical | Memory usage above 95% for 2 minutes |
| `MemoryHigh` | warning | Memory usage above 85% for 10 minutes |
| `SwapUsageHigh` | warning | Swap usage above 50% for 5 minutes |
| `OOMKillDetected` | critical | OOM kill counter increased |
| `DiskSpaceCritical` | critical | Filesystem free space below 10% |
| `DiskSpaceWarning` | warning | Filesystem free space below 20% |
| `DiskFillingSoon` | critical | `predict_linear` says disk fills within 4 hours |
| `DiskFillingIn24h` | warning | `predict_linear` says disk fills within 24 hours |
| `InodesCritical` | critical | Free inodes below 10% |
| `DiskIOSaturation` | warning | Disk busy time above 90% |
| `NetworkInterfaceDown` | warning | Non-loopback interface reports down |
| `NetworkReceiveSaturation` | warning | Receive throughput above 100 MiB/s |
| `NetworkReceiveErrors` | warning | Receive error rate above 0 |
| `NetworkPacketDrops` | warning | Receive or transmit packet drops above 0 |
| `ClockDriftHigh` | warning | Absolute clock offset above 0.1 seconds |
| `FileDescriptorsCritical` | critical | File descriptor usage above 90% |
| `ZombieProcesses` | warning | More than 10 zombie processes |
| `HighContextSwitches` | warning | Context switches above 10000/s |
| `FleetManagerDown` | critical | Prometheus cannot scrape Fleet Manager |
| `HighRegistrationErrors` | warning | Registration error rate above 0.1/s |
| `ManyNodesUnreachable` | critical | More than 3 nodes marked unreachable |
| `APISlowResponses` | warning | Fleet Manager p95 latency above 2 seconds |

## How to Test
Register a simulated node:
```bash
curl -fsS -X POST http://localhost:8080/api/nodes/register \
  -H "Content-Type: application/json" \
  -H "X-API-Key: change-me-fleet-api" \
  -d '{"hostname":"node1","ip":"node1","node_exporter_port":9100,"install_node_exporter":false,"labels":{"env":"dev"}}' | jq .
```

List nodes:
```bash
curl -fsS http://localhost:8080/api/nodes -H "X-API-Key: change-me-fleet-api" | jq .
```

Trigger `NodeDown`:
```bash
docker-compose stop node1
sleep 150
curl -fsS http://localhost:9093/api/v2/alerts | jq '.[] | select(.labels.alertname=="NodeDown")'
```

Verify Prometheus scraping:
```bash
curl -fsS http://localhost:9090/api/v1/targets | jq '.data.activeTargets[] | {job: .labels.job, instance: .labels.instance, health: .health}'
```

Verify Grafana dashboard loads:
```bash
curl -fsS -u "admin:change-me-grafana" http://localhost:3000/api/search?query=Fleet | jq .
```

## Observability Approach
Latency is covered by `fleet_http_request_duration_seconds` and the `APISlowResponses` alert. Traffic is covered by request counters implicit in the histogram and node network receive recording rules. Errors are covered by registration error counters, network errors, packet drops, and OOM alerts. Saturation is covered across CPU, memory, disk, inodes, file descriptors, disk I/O, and load average. Prometheus uses recording rules for repeated CPU, memory, disk, network, and fleet count expressions.

## Linux Internals Notes
The node exporter installer creates a dedicated `node_exporter` system user and group, installs a checksum-verified binary into `/usr/local/bin`, and writes a hardened systemd unit. The unit includes `NoNewPrivileges=true`, `ProtectSystem=strict`, `ProtectHome=true`, and `PrivateTmp=true`, plus additional kernel and privilege hardening. Extra collectors include `systemd`, `processes`, `tcpstat`, and `netstat`.

## Design Decisions
HTTP SD is used because the Fleet Manager API is the source of truth for registered nodes and Prometheus can poll it directly without sharing a mutable file volume. SQLite is the default because it is operationally simple for local and small fleet deployments; switch to Postgres by replacing the connection layer with a Postgres driver and applying the same table schema. Prometheus pull collection is used because it provides scrape health, backpressure, and standard service discovery semantics; push would hide target reachability and complicate lifecycle state.

## Known Limitations
API authentication is a shared API key, not RBAC. Node registration can optionally run SSH installation, but SSH trust bootstrap is still external. Alertmanager receivers are local webhook placeholders. No mTLS is configured between services. SQLite is not suitable for highly concurrent multi-replica Fleet Manager deployments. Simulated node exporters expose host-level container metrics, not full VM diversity.

## What to Add Next
Add Thanos for long-term metrics and HA Prometheus. Add mTLS between Prometheus, Fleet Manager, and exporters. Add RBAC and per-user audit logs. Add Ansible integration for SSH bootstrap, key rotation, package management, and node decommission workflows.
