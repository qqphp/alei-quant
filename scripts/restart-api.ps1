$ErrorActionPreference = 'Stop'
$stockRoot = Split-Path -Parent $PSScriptRoot
$stockRegistry = Join-Path $stockRoot 'runtime\processes.json'
$stockProcesses = Get-Content -LiteralPath $stockRegistry -Raw | ConvertFrom-Json
if ([IO.Path]::GetFullPath($stockProcesses.root) -ne [IO.Path]::GetFullPath($stockRoot)) { throw '进程记录属于其它目录。' }
$stockApiProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($stockProcesses.api)"
if (-not $stockApiProcess -or $stockApiProcess.CommandLine -notmatch 'stockapp\.api:app') { throw '没有匹配的 API 进程，请使用正常启动入口。' }
foreach ($stockChild in @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($stockProcesses.api)")) {
    if ($stockChild.CommandLine -match 'stockapp\.api:app') { Stop-Process -Id $stockChild.ProcessId -ErrorAction SilentlyContinue }
}
Stop-Process -Id $stockProcesses.api -ErrorAction SilentlyContinue
$env:PYTHONPATH = Join-Path $stockRoot 'backend'
$env:PYTHONUTF8 = '1'
$stockNewApi = Start-Process -FilePath (Join-Path $stockRoot '.venv\Scripts\python.exe') -ArgumentList '-m','uvicorn','stockapp.api:app','--host','127.0.0.1','--port','8000' -WorkingDirectory $stockRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $stockRoot 'runtime\api.out.log') -RedirectStandardError (Join-Path $stockRoot 'runtime\api.err.log')
$stockProcesses.api = $stockNewApi.Id
$stockProcesses | ConvertTo-Json | Set-Content -LiteralPath $stockRegistry -Encoding UTF8
Write-Output '已重新加载本机 API。'
