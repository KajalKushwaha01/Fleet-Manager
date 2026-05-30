import requests

PROMETHEUS = "http://localhost:9090"

def query(promql):
    try:
        resp = requests.get(
            f"{PROMETHEUS}/api/v1/query",
            params={"query": promql},
            timeout=5
        )
        data = resp.json()
        return data.get("data", {}).get("result", [])
    except Exception as e:
        print(f"Prometheus error: {e}")
        return []

def get_all_nodes():
    results = query("up")
    nodes = []
    for r in results:
        instance = r["metric"].get("instance", "unknown")
        job = r["metric"].get("job", "")
        if "node" in job or "9100" in instance:
            nodes.append({
                "instance": instance,
                "status": "online" if r["value"][1] == "1" else "offline"
            })
    return nodes

def get_cpu(instance):
    promql = f'100 - (avg by(instance)(rate(node_cpu_seconds_total{{mode="idle",instance="{instance}"}}[5m])) * 100)'
    result = query(promql)
    if result:
        return round(float(result[0]["value"][1]), 1)
    return 0

def get_ram(instance):
    promql = f'(1-(node_memory_MemAvailable_bytes{{instance="{instance}"}}/node_memory_MemTotal_bytes{{instance="{instance}"}})) * 100'
    result = query(promql)
    if result:
        return round(float(result[0]["value"][1]), 1)
    return 0

def get_disk(instance):
    promql = f'(1-(node_filesystem_avail_bytes{{instance="{instance}",mountpoint="/"}}/node_filesystem_size_bytes{{instance="{instance}",mountpoint="/"}})) * 100'
    result = query(promql)
    if result:
        return round(float(result[0]["value"][1]), 1)
    return 0

def get_windows_cpu(instance):
    promql = f'100 - (avg by(instance)(rate(windows_cpu_time_total{{mode="idle",instance="{instance}"}}[5m])) * 100)'
    result = query(promql)
    if result:
        return round(float(result[0]["value"][1]), 1)
    return 0

def get_windows_ram(instance):
    promql = f'100 - ((windows_os_physical_memory_free_bytes{{instance="{instance}"}} / windows_cs_physical_memory_bytes{{instance="{instance}"}}) * 100)'
    result = query(promql)
    if result:
        return round(float(result[0]["value"][1]), 1)
    return 0