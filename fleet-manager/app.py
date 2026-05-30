"""Fleet Manager API for Prometheus-based Linux fleet discovery."""

from __future__ import annotations

import functools
import json
import logging
import os
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import requests
from flask import Flask, Response, jsonify, render_template, request
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from werkzeug.exceptions import HTTPException

API_PORT = 8080
DEFAULT_NODE_EXPORTER_PORT = 9100
DEFAULT_SSH_PORT = 22
HEARTBEAT_INTERVAL_SECONDS = 30
UNREACHABLE_AFTER_SECONDS = 120
PROMETHEUS_TIMEOUT_SECONDS = 5
PROMETHEUS_RELOAD_TIMEOUT_SECONDS = 5
SERVICE_TIMEOUT_SECONDS = 3
SSH_TIMEOUT_SECONDS = 20
REGISTRATION_WINDOW_SECONDS = 60
REGISTRATION_LIMIT = 20
MAX_LABELS = 20
SAFE_COMMANDS = {
    "df": ["df", "-h"],
    "free": ["free", "-m"],
    "uptime": ["uptime"],
    "systemctl-node-exporter": ["systemctl", "status", "node_exporter", "--no-pager"],
}

DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "/data/nodes.db"))
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
ALERTMANAGER_URL = os.getenv("ALERTMANAGER_URL", "http://alertmanager:9093")
PUBLIC_PROMETHEUS_URL = os.getenv("PUBLIC_PROMETHEUS_URL", "http://localhost:9090")
PUBLIC_GRAFANA_URL = os.getenv("PUBLIC_GRAFANA_URL", "http://localhost:3000")
PUBLIC_ALERTMANAGER_URL = os.getenv("PUBLIC_ALERTMANAGER_URL", "http://localhost:9093")
API_SECRET_KEY = os.getenv("FLEET_API_SECRET_KEY")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

FLEET_NODES_TOTAL = Gauge("fleet_nodes_total", "Fleet nodes by status.", ["status"])
REGISTRATION_ERRORS = Counter(
    "fleet_registration_errors_total",
    "Node registration errors.",
    ["reason"],
)
HTTP_DURATION = Histogram(
    "fleet_http_request_duration_seconds",
    "Fleet Manager HTTP request latency.",
    ["method", "endpoint", "status"],
)
SSH_DURATION = Histogram(
    "fleet_ssh_command_duration_seconds",
    "SSH command latency.",
    ["operation"],
)
PROMETHEUS_RELOADS = Counter(
    "fleet_prometheus_reload_total",
    "Prometheus reload requests.",
    ["result"],
)


class JsonFormatter(logging.Formatter):
    """Format log records as JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Return a JSON log entry."""
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"))


def configure_logging() -> None:
    """Configure process-wide structured logging."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=LOG_LEVEL, handlers=[handler], force=True)


configure_logging()
LOGGER = logging.getLogger("fleet-manager")
if not API_SECRET_KEY:
    raise RuntimeError("FLEET_API_SECRET_KEY must be set")
app = Flask(__name__)
registration_attempts: dict[str, list[float]] = {}


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def db_connect() -> sqlite3.Connection:
    """Create a SQLite connection with row dictionaries enabled."""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Create database tables."""
    with db_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS nodes (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              hostname TEXT NOT NULL,
              ip TEXT NOT NULL UNIQUE,
              status TEXT NOT NULL,
              ssh_user TEXT NOT NULL,
              ssh_port INTEGER NOT NULL,
              ssh_key_path TEXT,
              registered_at TEXT NOT NULL,
              last_seen_at TEXT,
              labels TEXT NOT NULL,
              node_exporter_port INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS node_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              node_id INTEGER,
              event_type TEXT NOT NULL,
              detail TEXT NOT NULL,
              occurred_at TEXT NOT NULL,
              FOREIGN KEY(node_id) REFERENCES nodes(id)
            );
            """
        )


def row_to_node(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a database row to an API node object."""
    node = dict(row)
    node["labels"] = json.loads(node["labels"] or "{}")
    return node


def add_event(conn: sqlite3.Connection, node_id: int | None, event_type: str, detail: str) -> None:
    """Insert a node event."""
    conn.execute(
        "INSERT INTO node_events (node_id, event_type, detail, occurred_at) VALUES (?, ?, ?, ?)",
        (node_id, event_type, detail, utc_now()),
    )


def update_node_status(conn: sqlite3.Connection, node_id: int, status: str, detail: str) -> None:
    """Change node status and record an event."""
    conn.execute(
        "UPDATE nodes SET status = ?, last_seen_at = COALESCE(last_seen_at, ?) WHERE id = ?",
        (status, utc_now(), node_id),
    )
    add_event(conn, node_id, "state_change", detail)
    LOGGER.info("node_state_changed node_id=%s status=%s", node_id, status)


def all_nodes(include_decommissioned: bool = False) -> list[dict[str, Any]]:
    """Return all nodes."""
    query = "SELECT * FROM nodes"
    params: tuple[Any, ...] = ()
    if not include_decommissioned:
        query += " WHERE status != ?"
        params = ("DECOMMISSIONED",)
    query += " ORDER BY id"
    with db_connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [row_to_node(row) for row in rows]


def error_response(message: str, code: str, status: int) -> tuple[Response, int]:
    """Return a structured JSON error response."""
    return jsonify({"error": message, "code": code}), status


def auth_required(func: Callable[..., Any]) -> Callable[..., Any]:
    """Require X-API-Key or Bearer token authentication."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        token = request.headers.get("X-API-Key", "")
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.removeprefix("Bearer ").strip()
        if token != API_SECRET_KEY:
            return error_response("Unauthorized", "unauthorized", 401)
        return func(*args, **kwargs)

    return wrapper


def validate_labels(labels: Any) -> dict[str, str]:
    """Validate and normalize node labels."""
    if labels is None:
        return {}
    if not isinstance(labels, dict) or len(labels) > MAX_LABELS:
        raise ValueError("labels must be an object with at most 20 keys")
    return {str(key): str(value) for key, value in labels.items()}


def validate_registration(data: dict[str, Any]) -> dict[str, Any]:
    """Validate a registration request."""
    hostname = str(data.get("hostname") or data.get("name") or "").strip()
    ip = str(data.get("ip") or "").strip()
    if not hostname or not ip:
        raise ValueError("hostname and ip are required")
    return {
        "hostname": hostname,
        "ip": ip,
        "ssh_user": str(data.get("ssh_user") or "root"),
        "ssh_port": int(data.get("ssh_port") or DEFAULT_SSH_PORT),
        "ssh_key_path": data.get("ssh_key_path"),
        "labels": validate_labels(data.get("labels")),
        "node_exporter_port": int(data.get("node_exporter_port") or DEFAULT_NODE_EXPORTER_PORT),
        "install_node_exporter": bool(data.get("install_node_exporter", False)),
    }


def rate_limit_registration(client_id: str) -> bool:
    """Return true when registration should be allowed."""
    now = time.time()
    attempts = [
        ts for ts in registration_attempts.get(client_id, [])
        if now - ts < REGISTRATION_WINDOW_SECONDS
    ]
    if len(attempts) >= REGISTRATION_LIMIT:
        registration_attempts[client_id] = attempts
        return False
    attempts.append(now)
    registration_attempts[client_id] = attempts
    return True


def run_ssh_command(node: dict[str, Any], command: Iterable[str], operation: str) -> str:
    """Run a read-only SSH command with timeout."""
    host = node["ip"]
    port = str(node["ssh_port"])
    user = node["ssh_user"]
    key_path = node.get("ssh_key_path")
    ssh_cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={SSH_TIMEOUT_SECONDS}",
        "-p",
        port,
    ]
    if key_path:
        ssh_cmd.extend(["-i", str(key_path)])
    ssh_cmd.append(f"{user}@{host}")
    ssh_cmd.extend(command)
    with SSH_DURATION.labels(operation=operation).time():
        result = subprocess.run(
            ssh_cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=SSH_TIMEOUT_SECONDS,
        )
    LOGGER.info("ssh_operation_completed operation=%s host=%s", operation, host)
    return result.stdout


def install_node_exporter(node: dict[str, Any]) -> None:
    """Install node_exporter by invoking the local install script over SSH."""
    script_path = Path("/app/scripts/install-node-exporter.sh")
    command = ["bash", "-s"]
    with script_path.open("r", encoding="utf-8") as script_file:
        script = script_file.read()
    host = node["ip"]
    ssh_cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={SSH_TIMEOUT_SECONDS}",
        "-p",
        str(node["ssh_port"]),
    ]
    if node.get("ssh_key_path"):
        ssh_cmd.extend(["-i", str(node["ssh_key_path"])])
    ssh_cmd.extend([f"{node['ssh_user']}@{host}", *command])
    with SSH_DURATION.labels(operation="install_node_exporter").time():
        subprocess.run(
            ssh_cmd,
            input=script,
            check=True,
            capture_output=True,
            text=True,
            timeout=SSH_TIMEOUT_SECONDS,
        )


def prometheus_query(promql: str) -> list[dict[str, Any]]:
    """Query Prometheus instant API."""
    response = requests.get(
        f"{PROMETHEUS_URL}/api/v1/query",
        params={"query": promql},
        timeout=PROMETHEUS_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("data", {}).get("result", [])


def prometheus_targets() -> list[dict[str, Any]]:
    """Return active Prometheus scrape targets."""
    response = requests.get(
        f"{PROMETHEUS_URL}/api/v1/targets",
        timeout=PROMETHEUS_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("data", {}).get("activeTargets", [])


def service_available(url: str) -> bool:
    """Return true when a service endpoint responds successfully."""
    try:
        response = requests.get(url, timeout=SERVICE_TIMEOUT_SECONDS)
        return response.ok
    except requests.RequestException:
        return False


def reload_prometheus() -> None:
    """Request a Prometheus configuration reload."""
    try:
        response = requests.post(
            f"{PROMETHEUS_URL}/-/reload",
            timeout=PROMETHEUS_RELOAD_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        PROMETHEUS_RELOADS.labels(result="success").inc()
    except requests.RequestException as exc:
        PROMETHEUS_RELOADS.labels(result="error").inc()
        LOGGER.warning("prometheus_reload_failed error=%s", exc)


def target_for_node(node: dict[str, Any]) -> dict[str, Any]:
    """Build one Prometheus HTTP SD target entry."""
    labels = {
        "__meta_fleet_node_id": str(node["id"]),
        "hostname": node["hostname"],
        "node_status": node["status"],
        **node["labels"],
    }
    return {"targets": [f"{node['ip']}:{node['node_exporter_port']}"], "labels": labels}


def refresh_fleet_metrics() -> None:
    """Update fleet manager gauges."""
    counts: dict[str, int] = {
        "PENDING": 0,
        "REGISTERING": 0,
        "ACTIVE": 0,
        "UNREACHABLE": 0,
        "DECOMMISSIONED": 0,
    }
    for node in all_nodes(include_decommissioned=True):
        counts[node["status"]] = counts.get(node["status"], 0) + 1
    for status, count in counts.items():
        FLEET_NODES_TOTAL.labels(status=status).set(count)


def node_up(node: dict[str, Any]) -> bool:
    """Return true when Prometheus reports the node target as up."""
    instance = f"{node['ip']}:{node['node_exporter_port']}"
    result = prometheus_query(f'up{{instance="{instance}"}}')
    return bool(result and result[0]["value"][1] == "1")


def heartbeat_loop() -> None:
    """Continuously reconcile node lifecycle state from Prometheus up metrics."""
    while True:
        try:
            reconcile_nodes()
        except (requests.RequestException, sqlite3.Error) as exc:
            LOGGER.warning("heartbeat_failed error=%s", exc)
        time.sleep(HEARTBEAT_INTERVAL_SECONDS)


def reconcile_nodes() -> None:
    """Mark nodes active or unreachable based on Prometheus health."""
    now = utc_now()
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT * FROM nodes WHERE status NOT IN ('DECOMMISSIONED', 'PENDING')"
        ).fetchall()
        for row in rows:
            node = row_to_node(row)
            is_up = node_up(node)
            if is_up and node["status"] != "ACTIVE":
                conn.execute("UPDATE nodes SET last_seen_at = ? WHERE id = ?", (now, node["id"]))
                update_node_status(conn, node["id"], "ACTIVE", "Prometheus target recovered")
            elif is_up:
                conn.execute("UPDATE nodes SET last_seen_at = ? WHERE id = ?", (now, node["id"]))
            elif should_mark_unreachable(node) and node["status"] != "UNREACHABLE":
                update_node_status(conn, node["id"], "UNREACHABLE", "Prometheus up stayed 0")
    refresh_fleet_metrics()


def should_mark_unreachable(node: dict[str, Any]) -> bool:
    """Return true when node last_seen_at is older than threshold."""
    if not node.get("last_seen_at"):
        return True
    last_seen = datetime.fromisoformat(node["last_seen_at"])
    elapsed = datetime.now(timezone.utc) - last_seen
    return elapsed.total_seconds() > UNREACHABLE_AFTER_SECONDS


@app.before_request
def before_request() -> None:
    """Store request start time."""
    request.environ["started_at"] = time.time()


@app.after_request
def after_request(response: Response) -> Response:
    """Record request duration metrics."""
    elapsed = time.time() - request.environ.get("started_at", time.time())
    HTTP_DURATION.labels(
        method=request.method,
        endpoint=request.endpoint or "unknown",
        status=str(response.status_code),
    ).observe(elapsed)
    return response


@app.errorhandler(Exception)
def handle_error(exc: Exception) -> tuple[Response, int]:
    """Return structured errors."""
    if isinstance(exc, HTTPException):
        return error_response(exc.description, exc.name.lower().replace(" ", "_"), exc.code or 500)
    LOGGER.exception("unhandled_error")
    return error_response("Internal server error", "internal_error", 500)


@app.get("/")
def console() -> str:
    """Render the Fleet Manager web console."""
    return render_template("index.html")


@app.post("/api/nodes/register")
@auth_required
def register_node() -> tuple[Response, int]:
    """Register a node and expose it through HTTP SD."""
    client_id = request.remote_addr or "unknown"
    if not rate_limit_registration(client_id):
        REGISTRATION_ERRORS.labels(reason="rate_limited").inc()
        return error_response("Registration rate limit exceeded", "rate_limited", 429)
    try:
        node = validate_registration(request.get_json(force=True) or {})
    except (ValueError, TypeError) as exc:
        REGISTRATION_ERRORS.labels(reason="validation").inc()
        return error_response(str(exc), "invalid_request", 400)
    try:
        return complete_registration(node)
    except sqlite3.IntegrityError:
        REGISTRATION_ERRORS.labels(reason="duplicate").inc()
        return error_response("Node already registered", "duplicate_node", 409)
    except (subprocess.SubprocessError, OSError) as exc:
        REGISTRATION_ERRORS.labels(reason=exc.__class__.__name__).inc()
        LOGGER.warning("registration_failed host=%s error=%s", node["ip"], exc)
        return error_response("Registration failed", "registration_failed", 500)


def complete_registration(node: dict[str, Any]) -> tuple[Response, int]:
    """Persist a validated node registration."""
    with db_connect() as conn:
        existing = conn.execute(
            "SELECT id, status FROM nodes WHERE ip = ?",
            (node["ip"],),
        ).fetchone()
        if existing and existing["status"] != "DECOMMISSIONED":
            raise sqlite3.IntegrityError("active node already exists")
        if existing:
            return reactivate_node(conn, node, int(existing["id"]))
        cursor = conn.execute(
            """
            INSERT INTO nodes (
              hostname, ip, status, ssh_user, ssh_port, ssh_key_path,
              registered_at, last_seen_at, labels, node_exporter_port
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                node["hostname"],
                node["ip"],
                "PENDING",
                node["ssh_user"],
                node["ssh_port"],
                node.get("ssh_key_path"),
                utc_now(),
                utc_now(),
                json.dumps(node["labels"], sort_keys=True),
                node["node_exporter_port"],
            ),
        )
        node_id = int(cursor.lastrowid)
        add_event(conn, node_id, "registered", "Node registration accepted")
        update_node_status(conn, node_id, "REGISTERING", "Node registration started")
    node["id"] = node_id
    if node["install_node_exporter"]:
        install_node_exporter(node)
    with db_connect() as conn:
        update_node_status(conn, node_id, "ACTIVE", "Node registered for scraping")
    reload_prometheus()
    refresh_fleet_metrics()
    LOGGER.info("node_registered node_id=%s host=%s", node_id, node["ip"])
    return jsonify({"id": node_id, "status": "ACTIVE"}), 201


def reactivate_node(
    conn: sqlite3.Connection,
    node: dict[str, Any],
    node_id: int,
) -> tuple[Response, int]:
    """Reactivate a decommissioned node with new registration data."""
    conn.execute(
        """
        UPDATE nodes
        SET hostname = ?, status = ?, ssh_user = ?, ssh_port = ?, ssh_key_path = ?,
            registered_at = ?, last_seen_at = ?, labels = ?, node_exporter_port = ?
        WHERE id = ?
        """,
        (
            node["hostname"],
            "REGISTERING",
            node["ssh_user"],
            node["ssh_port"],
            node.get("ssh_key_path"),
            utc_now(),
            utc_now(),
            json.dumps(node["labels"], sort_keys=True),
            node["node_exporter_port"],
            node_id,
        ),
    )
    add_event(conn, node_id, "registered", "Node registration accepted")
    node["id"] = node_id
    if node["install_node_exporter"]:
        install_node_exporter(node)
    update_node_status(conn, node_id, "ACTIVE", "Node registered for scraping")
    reload_prometheus()
    refresh_fleet_metrics()
    return jsonify({"id": node_id, "status": "ACTIVE"}), 201


@app.get("/api/nodes")
@auth_required
def list_nodes() -> Response:
    """List registered nodes."""
    return jsonify(all_nodes(include_decommissioned=True))


@app.delete("/api/nodes/<int:node_id>")
@auth_required
def deregister_node(node_id: int) -> Response | tuple[Response, int]:
    """Decommission a node and remove it from service discovery."""
    with db_connect() as conn:
        node = conn.execute("SELECT id FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if not node:
            return error_response("Node not found", "not_found", 404)
        update_node_status(conn, node_id, "DECOMMISSIONED", "Node removed from fleet")
    reload_prometheus()
    refresh_fleet_metrics()
    return jsonify({"id": node_id, "status": "DECOMMISSIONED"})


@app.get("/api/nodes/<int:node_id>/metrics")
@auth_required
def node_metrics(node_id: int) -> Response | tuple[Response, int]:
    """Return selected Prometheus metrics for a node."""
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
    if not row:
        return error_response("Node not found", "not_found", 404)
    node = row_to_node(row)
    instance = f"{node['ip']}:{node['node_exporter_port']}"
    metrics = {
        "cpu": prometheus_query(f'instance:node_cpu_usage:rate5m{{instance="{instance}"}}'),
        "memory": prometheus_query(f'instance:node_memory_usage:ratio{{instance="{instance}"}}'),
        "disk": prometheus_query(f'instance:node_disk_usage:ratio{{instance="{instance}"}}'),
        "up": prometheus_query(f'up{{instance="{instance}"}}'),
    }
    return jsonify({"node_id": node_id, "instance": instance, "metrics": metrics})


@app.post("/api/nodes/<int:node_id>/exec")
@auth_required
def exec_command(node_id: int) -> Response | tuple[Response, int]:
    """Run an approved read-only command over SSH."""
    payload = request.get_json(force=True) or {}
    command_name = str(payload.get("command") or "")
    if command_name not in SAFE_COMMANDS:
        return error_response("Command is not allowed", "command_not_allowed", 400)
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
    if not row:
        return error_response("Node not found", "not_found", 404)
    try:
        output = run_ssh_command(row_to_node(row), SAFE_COMMANDS[command_name], command_name)
    except (subprocess.SubprocessError, OSError) as exc:
        return error_response(str(exc), "ssh_failed", 502)
    return jsonify({"node_id": node_id, "command": command_name, "output": output})


@app.get("/api/sd/targets")
def service_discovery() -> Response:
    """Return Prometheus HTTP service discovery targets."""
    targets = [
        target_for_node(node)
        for node in all_nodes()
        if node["status"] in {"REGISTERING", "ACTIVE", "UNREACHABLE"}
    ]
    return jsonify(targets)


@app.get("/api/system/status")
@auth_required
def system_status() -> Response:
    """Return consolidated service, node, and scrape status."""
    targets = prometheus_targets()
    return jsonify(
        {
            "links": {
                "console": "http://localhost:8080",
                "prometheus": PUBLIC_PROMETHEUS_URL,
                "grafana": PUBLIC_GRAFANA_URL,
                "alertmanager": PUBLIC_ALERTMANAGER_URL,
            },
            "nodes": all_nodes(include_decommissioned=True),
            "services": {
                "fleet_manager": True,
                "prometheus": service_available(f"{PROMETHEUS_URL}/-/healthy"),
                "alertmanager": service_available(f"{ALERTMANAGER_URL}/-/healthy"),
            },
            "targets": [
                {
                    "health": target.get("health"),
                    "instance": target.get("labels", {}).get("instance"),
                    "job": target.get("labels", {}).get("job"),
                    "lastError": target.get("lastError"),
                    "lastScrape": target.get("lastScrape"),
                }
                for target in targets
            ],
        }
    )


@app.get("/health")
def health() -> Response:
    """Return service health."""
    active = sum(1 for node in all_nodes() if node["status"] == "ACTIVE")
    return jsonify({"status": "ok", "nodes_active": active})


@app.post("/api/alerts/webhook")
def alertmanager_webhook() -> Response:
    """Accept Alertmanager webhook notifications."""
    LOGGER.info("alertmanager_webhook_received")
    return jsonify({"status": "accepted"})


@app.get("/metrics")
def metrics() -> Response:
    """Expose Prometheus metrics."""
    refresh_fleet_metrics()
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


def start_background_threads() -> None:
    """Start background workers once."""
    thread = threading.Thread(target=heartbeat_loop, daemon=True)
    thread.start()


init_db()
refresh_fleet_metrics()
start_background_threads()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=API_PORT)
