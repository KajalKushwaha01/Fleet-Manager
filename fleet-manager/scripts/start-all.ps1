param(
    [string]$ApiKey = "change-me-fleet-api"
)

$ErrorActionPreference = "Stop"

function Wait-HttpOk {
    param(
        [string]$Name,
        [string]$Url,
        [hashtable]$Headers = @{},
        [int]$Attempts = 40
    )

    for ($index = 1; $index -le $Attempts; $index++) {
        try {
            Invoke-RestMethod -Uri $Url -Headers $Headers -TimeoutSec 3 | Out-Null
            Write-Host "PASS $Name"
            return
        } catch {
            Start-Sleep -Seconds 3
        }
    }

    throw "Timed out waiting for $Name at $Url"
}

function Register-SimulatorNode {
    param([string]$NodeName)

    $nodes = Invoke-RestMethod `
        -Uri "http://localhost:8080/api/nodes" `
        -Headers @{ "X-API-Key" = $ApiKey }

    $existing = $nodes | Where-Object { $_.ip -eq $NodeName -and $_.status -ne "DECOMMISSIONED" } | Select-Object -First 1
    if ($existing) {
        Write-Host "SKIP $NodeName already registered"
        return
    }

    $body = @{
        hostname = $NodeName
        ip = $NodeName
        node_exporter_port = 9100
        ssh_user = "root"
        install_node_exporter = $false
        labels = @{ env = "dev"; simulator = "true" }
    } | ConvertTo-Json -Compress

    Invoke-RestMethod `
        -Uri "http://localhost:8080/api/nodes/register" `
        -Method Post `
        -Headers @{ "X-API-Key" = $ApiKey } `
        -ContentType "application/json" `
        -Body $body | Out-Null

    Write-Host "PASS registered $NodeName"
}

docker-compose up -d --build

Wait-HttpOk -Name "Fleet Manager" -Url "http://localhost:8080/health"
Wait-HttpOk -Name "Prometheus" -Url "http://localhost:9090/-/healthy"
Wait-HttpOk -Name "Alertmanager" -Url "http://localhost:9093/-/healthy"
Wait-HttpOk -Name "Grafana" -Url "http://localhost:3000/api/health"

Register-SimulatorNode -NodeName "node1"
Register-SimulatorNode -NodeName "node2"
Register-SimulatorNode -NodeName "node3"

Start-Process "http://localhost:8080"
Start-Process "http://localhost:3000"
Start-Process "http://localhost:9090"
Start-Process "http://localhost:9093"

Write-Host ""
Write-Host "Fleet Manager Console: http://localhost:8080"
Write-Host "Grafana:               http://localhost:3000  admin / change-me-grafana"
Write-Host "Prometheus:            http://localhost:9090"
Write-Host "Alertmanager:          http://localhost:9093"
Write-Host ""
Write-Host "Use API key in the console: $ApiKey"
