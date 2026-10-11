$ErrorActionPreference = 'Stop'
$stockRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $stockRoot 'backend'
$env:PYTHONUTF8 = '1'
Push-Location $stockRoot
try {
    & (Join-Path $stockRoot '.venv\Scripts\python.exe') -m stockapp.maintenance backup
    if ($LASTEXITCODE -ne 0) { throw '备份未完成，请检查本地服务及权限。' }
} finally { Pop-Location }
