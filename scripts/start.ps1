param([switch]$Build)
$ErrorActionPreference = 'Stop'
$stockRoot = Split-Path -Parent $PSScriptRoot
$stockPython = Join-Path $stockRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $stockPython)) { throw '请先按 README 创建 .venv 并安装依赖。' }
if (-not (Test-Path -LiteralPath (Join-Path $stockRoot '.env'))) { throw '请先初始化数据库配置。' }
if ($Build) {
    Push-Location (Join-Path $stockRoot 'frontend')
    try { npm run build; if ($LASTEXITCODE -ne 0) { throw '前端构建失败' } } finally { Pop-Location }
}
$stockRuntime = Join-Path $stockRoot 'runtime'
New-Item -ItemType Directory -Path $stockRuntime -Force | Out-Null
$env:PYTHONPATH = Join-Path $stockRoot 'backend'
$env:PYTHONUTF8 = '1'
$stockPort = & $stockPython -c 'from stockapp.config import settings; print(settings().api_port)'
if ($LASTEXITCODE -ne 0) { throw 'API 端口配置读取失败，未启动服务。' }
$stockUrl = "http://127.0.0.1:$stockPort"
if (Test-Path -LiteralPath (Join-Path $stockRuntime 'processes.json')) {
    $stockPrevious = Get-Content -LiteralPath (Join-Path $stockRuntime 'processes.json') -Raw | ConvertFrom-Json
    foreach ($stockId in @($stockPrevious.api, $stockPrevious.worker)) {
        if (-not $stockId) { continue }
        if (Get-Process -Id $stockId -ErrorAction SilentlyContinue) { throw '已有登记进程运行，请使用 scripts/stop.ps1 后重启。' }
    }
}
if (Get-NetTCPConnection -LocalPort $stockPort -State Listen -ErrorAction SilentlyContinue) { throw "$stockPort 端口已被占用，未启动重复服务。" }
& $stockPython -c 'from stockapp import db; db.migrate()'
if ($LASTEXITCODE -ne 0) { throw '数据库迁移失败，未启动服务。' }
$stockExpectedDatabase = & $stockPython -c 'from stockapp.config import settings; print(settings().db_name)'
if ($LASTEXITCODE -ne 0) { throw '数据库配置读取失败，未启动服务。' }
$stockRegistry = Join-Path $stockRuntime 'processes.json'
$stockWorker = $null
try {
    $stockWorker = Start-Process -FilePath $stockPython -ArgumentList '-m','stockapp.worker' -WorkingDirectory $stockRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $stockRuntime 'worker.out.log') -RedirectStandardError (Join-Path $stockRuntime 'worker.err.log')
    $stockProcesses = @{api=$null;worker=$stockWorker.Id;root=$stockRoot;port=[int]$stockPort;started=(Get-Date).ToString('o')}
    $stockProcesses | ConvertTo-Json | Set-Content -LiteralPath $stockRegistry -Encoding UTF8
    $stockApi = Start-Process -FilePath $stockPython -ArgumentList '-m','uvicorn','stockapp.api:app','--host','127.0.0.1','--port',$stockPort -WorkingDirectory $stockRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $stockRuntime 'api.out.log') -RedirectStandardError (Join-Path $stockRuntime 'api.err.log')
    $stockProcesses.api = $stockApi.Id
    $stockProcesses | ConvertTo-Json | Set-Content -LiteralPath $stockRegistry -Encoding UTF8
    $stockDeadline = (Get-Date).AddSeconds(20)
    $stockReady = $false
    do {
        if ($stockWorker.HasExited -or $stockApi.HasExited) { throw '服务进程提前退出，请检查 runtime 日志。' }
        try {
            $stockHealth = Invoke-RestMethod -Uri "$stockUrl/api/health" -TimeoutSec 2
            $stockReady = $stockHealth.status -eq 'ok' -and $stockHealth.database -eq $stockExpectedDatabase -and $stockHealth.worker -eq $true
        } catch { $stockReady = $false }
        if ($stockReady) { break }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $stockDeadline)
    if (-not $stockReady) { throw '20 秒内 API、数据库或工作进程未就绪，请检查 runtime 日志。' }
} catch {
    $stockFailure = $_
    if ($stockWorker) { & (Join-Path $PSScriptRoot 'stop.ps1') }
    throw $stockFailure
}
Write-Output "API、数据库和工作进程已就绪。本地入口：$stockUrl 。工作进程日志位于 runtime。"

