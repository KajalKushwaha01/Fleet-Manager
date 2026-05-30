const state = {
  apiKey: localStorage.getItem("fleetApiKey") || "",
  status: null,
};

const selectors = {
  activeCount: document.querySelector("#active-count"),
  apiKey: document.querySelector("#api-key"),
  authError: document.querySelector("#auth-error"),
  authForm: document.querySelector("#auth-form"),
  connectionState: document.querySelector("#connection-state"),
  hostname: document.querySelector("#hostname"),
  ip: document.querySelector("#ip"),
  labels: document.querySelector("#labels"),
  lastRefresh: document.querySelector("#last-refresh"),
  nodesTable: document.querySelector("#nodes-table"),
  refreshButton: document.querySelector("#refresh-button"),
  registerError: document.querySelector("#register-error"),
  registerForm: document.querySelector("#register-form"),
  serviceList: document.querySelector("#service-list"),
  targetCount: document.querySelector("#target-count"),
  targetList: document.querySelector("#target-list"),
  toast: document.querySelector("#toast"),
  unreachableCount: document.querySelector("#unreachable-count"),
};

function showToast(message) {
  selectors.toast.textContent = message;
  selectors.toast.classList.add("visible");
  window.setTimeout(() => selectors.toast.classList.remove("visible"), 2600);
}

function setConnection(label, className) {
  selectors.connectionState.textContent = label;
  selectors.connectionState.className = `status-pill ${className}`;
}

function authHeaders() {
  return { "X-API-Key": state.apiKey };
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      ...(options.headers || {}),
      ...authHeaders(),
    },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `Request failed with ${response.status}`);
  }
  return payload;
}

function statusPill(value) {
  const normalized = String(value || "unknown").toLowerCase();
  const className = normalized === "active" || normalized === "up" || normalized === "true"
    ? "ok"
    : normalized === "unreachable" || normalized === "down" || normalized === "false"
      ? "danger"
      : "neutral";
  return `<span class="status-pill ${className}">${value}</span>`;
}

function renderServices(services) {
  selectors.serviceList.innerHTML = Object.entries(services)
    .map(([name, ok]) => `
      <div class="service-item">
        <strong>${name.replace("_", " ")}</strong>
        ${statusPill(ok)}
      </div>
    `)
    .join("");
}

function renderNodes(nodes) {
  if (!nodes.length) {
    selectors.nodesTable.innerHTML = '<tr><td colspan="6">No nodes registered yet.</td></tr>';
    return;
  }
  selectors.nodesTable.innerHTML = nodes.map((node) => `
    <tr>
      <td>${node.id}</td>
      <td>${node.hostname}</td>
      <td>${node.ip}:${node.node_exporter_port}</td>
      <td>${statusPill(node.status)}</td>
      <td>${node.last_seen_at || "Never"}</td>
      <td>
        ${node.status === "DECOMMISSIONED"
          ? "Removed"
          : `<button class="danger-button" type="button" data-delete-node="${node.id}">Remove</button>`}
      </td>
    </tr>
  `).join("");
}

function renderTargets(targets) {
  if (!targets.length) {
    selectors.targetList.innerHTML = '<div class="target-item">No Prometheus targets discovered.</div>';
    return;
  }
  selectors.targetList.innerHTML = targets.map((target) => `
    <div class="target-item">
      <div>
        <strong>${target.instance || "unknown"}</strong>
        <p>${target.job || "unknown job"}${target.lastError ? ` - ${target.lastError}` : ""}</p>
      </div>
      ${statusPill(target.health)}
    </div>
  `).join("");
}

function renderSummary(payload) {
  const nodes = payload.nodes || [];
  const targets = payload.targets || [];
  selectors.activeCount.textContent = nodes.filter((node) => node.status === "ACTIVE").length;
  selectors.unreachableCount.textContent = nodes.filter((node) => node.status === "UNREACHABLE").length;
  selectors.targetCount.textContent = targets.filter((target) => target.health === "up").length;
  selectors.lastRefresh.textContent = new Date().toLocaleTimeString();
}

function render(payload) {
  renderSummary(payload);
  renderServices(payload.services || {});
  renderNodes(payload.nodes || []);
  renderTargets(payload.targets || []);
}

async function refreshStatus() {
  if (!state.apiKey) {
    setConnection("Locked", "neutral");
    return;
  }
  try {
    const payload = await requestJson("/api/system/status");
    state.status = payload;
    render(payload);
    setConnection("Connected", "ok");
    selectors.authError.textContent = "";
  } catch (error) {
    setConnection("Auth failed", "danger");
    selectors.authError.textContent = error.message;
  }
}

function parseLabels() {
  const raw = selectors.labels.value.trim();
  if (!raw) {
    return {};
  }
  const parsed = JSON.parse(raw);
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new Error("Labels must be a JSON object.");
  }
  return parsed;
}

function validateRegistration() {
  const hostname = selectors.hostname.value.trim();
  const ip = selectors.ip.value.trim();
  const port = Number(document.querySelector("#exporter-port").value);
  if (hostname.length < 2) {
    throw new Error("Hostname must be at least 2 characters.");
  }
  if (!ip) {
    throw new Error("IP or Docker host is required.");
  }
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error("Exporter port must be between 1 and 65535.");
  }
  return {
    hostname,
    ip,
    install_node_exporter: document.querySelector("#install-exporter").checked,
    labels: parseLabels(),
    node_exporter_port: port,
    ssh_user: document.querySelector("#ssh-user").value.trim() || "root",
  };
}

async function registerNode(event) {
  event.preventDefault();
  selectors.registerError.textContent = "";
  try {
    const payload = validateRegistration();
    await requestJson("/api/nodes/register", {
      body: JSON.stringify(payload),
      headers: { "Content-Type": "application/json" },
      method: "POST",
    });
    showToast("Node registered");
    await refreshStatus();
  } catch (error) {
    selectors.registerError.textContent = error.message;
  }
}

async function deleteNode(nodeId) {
  await requestJson(`/api/nodes/${nodeId}`, { method: "DELETE" });
  showToast(`Node ${nodeId} removed`);
  await refreshStatus();
}

function setupEvents() {
  selectors.apiKey.value = state.apiKey;
  selectors.authForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    state.apiKey = selectors.apiKey.value.trim();
    localStorage.setItem("fleetApiKey", state.apiKey);
    await refreshStatus();
  });
  selectors.refreshButton.addEventListener("click", refreshStatus);
  selectors.registerForm.addEventListener("submit", registerNode);
  selectors.nodesTable.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-delete-node]");
    if (button) {
      await deleteNode(button.dataset.deleteNode);
    }
  });
}

setupEvents();
refreshStatus();
window.setInterval(refreshStatus, 15000);
