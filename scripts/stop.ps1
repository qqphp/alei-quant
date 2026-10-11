$ErrorActionPreference = 'Stop'
$stockRoot = Split-Path -Parent $PSScriptRoot
$stockRegistry = Join-Path $stockRoot 'runtime\processes.json'
if (-not (Test-Path -LiteralPath $stockRegistry)) { Write-Output '没有登记的启动进程。'; exit }
$stockProcesses = Get-Content -LiteralPath $stockRegistry -Raw | ConvertFrom-Json
if ([IO.Path]::GetFullPath($stockProcesses.root) -ne [IO.Path]::GetFullPath($stockRoot)) { throw '进程记录属于其它目录，未停止。' }
$stockInventory = Get-CimInstance Win32_Process
function Stop-StockTree([int]$stockTreeId) {
    foreach ($stockDescendant in @($stockInventory | Where-Object { $_.ParentProcessId -eq $stockTreeId })) {
        if ($stockDescendant.CommandLine -match 'stockapp\.(runner|worker|api:app)') { Stop-StockTree $stockDescendant.ProcessId }
    }
    $stockCurrent = Get-CimInstance Win32_Process -Filter "ProcessId=$stockTreeId"
    if ($stockCurrent -and $stockCurrent.CommandLine -match 'stockapp\.(runner|worker|api:app)') { Stop-Process -Id $stockTreeId -ErrorAction SilentlyContinue }
}
foreach ($stockId in @($stockProcesses.api, $stockProcesses.worker)) {
    if (-not $stockId) { continue }
    $stockProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$stockId"
    if ($stockProcess -and $stockProcess.CommandLine -match 'stockapp\.(worker|api:app)') {
        Stop-StockTree $stockId
    }
}
Write-Output '已停止登记的本地服务；执行中的任务将在重启后标记中断，排队和等待任务保持原检查点。'

