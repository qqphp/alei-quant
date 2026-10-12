$ErrorActionPreference = 'Stop'
$stockRoot = Split-Path -Parent $PSScriptRoot
$stockRegistry = Join-Path $stockRoot 'runtime\processes.json'
$stockProcesses = Get-Content -LiteralPath $stockRegistry -Raw | ConvertFrom-Json
if ([IO.Path]::GetFullPath($stockProcesses.root) -ne [IO.Path]::GetFullPath($stockRoot)) { throw '进程记录属于其它目录。' }
$stockPython = Join-Path $stockRoot '.venv\Scripts\python.exe'
$env:PYTHONPATH = Join-Path $stockRoot 'backend'
$env:PYTHONUTF8 = '1'
$stockPort = & $stockPython -c 'from stockapp.config import settings; print(settings().api_port)'
if ($LASTEXITCODE -ne 0) { throw 'API 端口配置读取失败，未停止原服务。' }
$stockExpectedDatabase = & $stockPython -c 'from stockapp.config import settings; print(settings().db_name)'
if ($LASTEXITCODE -ne 0) { throw '数据库配置读取失败，未停止原服务。' }
$stockUrl = "http://127.0.0.1:$stockPort"
$stockApiProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($stockProcesses.api)"
if (-not $stockApiProcess -or $stockApiProcess.CommandLine -notmatch 'stockapp\.api:app') { throw '没有匹配的 API 进程，请使用正常启动入口。' }
$stockApiChildren = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($stockProcesses.api)" | Where-Object { $_.CommandLine -match 'stockapp\.api:app' })
$stockApiIds = @($stockProcesses.api) + @($stockApiChildren.ProcessId)
$stockListeners = @(Get-NetTCPConnection -LocalPort $stockPort -State Listen -ErrorAction SilentlyContinue)
if ($stockListeners | Where-Object { $_.OwningProcess -notin $stockApiIds }) { throw "$stockPort 端口已被其它进程占用，未停止原服务。" }
foreach ($stockChild in $stockApiChildren) {
    if ($stockChild.CommandLine -match 'stockapp\.api:app') { Stop-Process -Id $stockChild.ProcessId -ErrorAction SilentlyContinue }
}
Stop-Process -Id $stockProcesses.api -ErrorAction SilentlyContinue
$stockNewApi = Start-Process -FilePath $stockPython -ArgumentList '-m','uvicorn','stockapp.api:app','--host','127.0.0.1','--port',$stockPort -WorkingDirectory $stockRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $stockRoot 'runtime\api.out.log') -RedirectStandardError (Join-Path $stockRoot 'runtime\api.err.log')
$stockProcesses.api = $stockNewApi.Id
$stockProcesses | Add-Member -NotePropertyName port -NotePropertyValue ([int]$stockPort) -Force
$stockProcesses | ConvertTo-Json | Set-Content -LiteralPath $stockRegistry -Encoding UTF8
$stockDeadline = (Get-Date).AddSeconds(20)
$stockReady = $false
do {
    if ($stockNewApi.HasExited) { throw 'API 进程提前退出，请检查 runtime 日志。' }
    try {
        $stockHealth = Invoke-RestMethod -Uri "$stockUrl/api/health" -TimeoutSec 2
        $stockReady = $stockHealth.status -eq 'ok' -and $stockHealth.database -eq $stockExpectedDatabase -and $stockHealth.worker -eq $true
    } catch { $stockReady = $false }
    if ($stockReady) { break }
    Start-Sleep -Milliseconds 250
} while ((Get-Date) -lt $stockDeadline)
if (-not $stockReady) { throw '20 秒内 API、数据库或工作进程未就绪，请检查 runtime 日志。' }
Write-Output "已重新加载本机 API。本地入口：$stockUrl 。"
