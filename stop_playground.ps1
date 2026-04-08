$ErrorActionPreference = "Stop"

$projectRoot = "D:\xian-travel-agent"
$pidFile = Join-Path $projectRoot "scripts\playground.pids.json"

if (-not (Test-Path $pidFile)) {
    Write-Host "未找到 PID 文件: $pidFile"
    exit 0
}

try {
    $data = Get-Content -Raw -Path $pidFile | ConvertFrom-Json
} catch {
    Write-Host "PID 文件损坏，已删除。"
    Remove-Item -Path $pidFile -Force -ErrorAction SilentlyContinue
    exit 0
}

foreach ($entry in @(
    @{ name = "backend"; pid = $data.backend_pid },
    @{ name = "frontend"; pid = $data.frontend_pid }
)) {
    $pidVal = $entry.pid
    if (-not $pidVal) {
        Write-Host "$($entry.name) pid 为空，跳过。"
        continue
    }

    $proc = Get-Process -Id $pidVal -ErrorAction SilentlyContinue
    if ($proc) {
        Stop-Process -Id $pidVal -Force -ErrorAction SilentlyContinue
        Write-Host "已停止 $($entry.name): pid=$pidVal"
    } else {
        Write-Host "$($entry.name) 进程不存在: pid=$pidVal"
    }
}

Remove-Item -Path $pidFile -Force -ErrorAction SilentlyContinue
Write-Host "已清理: $pidFile"
