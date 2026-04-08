param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"

$projectRoot = "D:\xian-travel-agent"
$pythonExe = "D:\xian-travel-agent\.venv\Scripts\python.exe"
$backendUrl = "http://127.0.0.1:8000/health"
$frontendUrl = "http://127.0.0.1:5173"
$pidFile = Join-Path $projectRoot "scripts\playground.pids.json"

function Get-ListeningPid {
    param(
        [int]$Port
    )

    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
        if ($conn -and $conn.OwningProcess) {
            return [int]$conn.OwningProcess
        }
    } catch {
        return $null
    }

    return $null
}

if (-not (Test-Path $pythonExe)) {
    throw "Python 不存在: $pythonExe"
}

# 先尝试关闭上一次启动的进程，避免端口占用
if (Test-Path $pidFile) {
    try {
        $last = Get-Content -Raw -Path $pidFile | ConvertFrom-Json
        foreach ($name in @("backend_pid", "frontend_pid")) {
            $pidVal = $last.$name
            if ($pidVal) {
                $proc = Get-Process -Id $pidVal -ErrorAction SilentlyContinue
                if ($proc) {
                    Stop-Process -Id $pidVal -Force -ErrorAction SilentlyContinue
                }
            }
        }
    } catch {
        # ignore invalid pid file
    }
}

$backendProc = Start-Process -FilePath $pythonExe -WorkingDirectory $projectRoot -ArgumentList @(
    "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"
) -PassThru

$frontendProc = Start-Process -FilePath $pythonExe -WorkingDirectory (Join-Path $projectRoot "frontend") -ArgumentList @(
    "-m", "http.server", "5173"
) -PassThru

# 等待启动
Start-Sleep -Seconds 4

$backendOk = $false
$frontendOk = $false
$backendPid = Get-ListeningPid -Port 8000
$frontendPid = Get-ListeningPid -Port 5173

try {
    $resp = Invoke-WebRequest -Uri $backendUrl -UseBasicParsing -TimeoutSec 5
    if ($resp.StatusCode -eq 200) {
        $backendOk = $true
    }
} catch {
    $backendOk = $false
}

try {
    $resp = Invoke-WebRequest -Uri $frontendUrl -UseBasicParsing -TimeoutSec 5
    if ($resp.StatusCode -eq 200) {
        $frontendOk = $true
    }
} catch {
    $frontendOk = $false
}

$pids = [ordered]@{
    backend_pid = ($(if ($backendPid) { $backendPid } else { $backendProc.Id }))
    frontend_pid = ($(if ($frontendPid) { $frontendPid } else { $frontendProc.Id }))
    backend_url = "http://127.0.0.1:8000"
    frontend_url = $frontendUrl
    started_at = (Get-Date).ToString("s")
    backend_ok = $backendOk
    frontend_ok = $frontendOk
}

$pids | ConvertTo-Json | Set-Content -Path $pidFile -Encoding utf8

Write-Host "backend pid: $($pids.backend_pid) | ok=$backendOk"
Write-Host "frontend pid: $($pids.frontend_pid) | ok=$frontendOk"
Write-Host "frontend: $frontendUrl"
Write-Host "health:   $backendUrl"

if (-not $NoBrowser) {
    Start-Process $frontendUrl | Out-Null
}
