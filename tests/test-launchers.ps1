# 离线启动/重启回归：进程、数据库和网络调用均模拟，只写临时目录。
$ErrorActionPreference = 'Stop'
$stockSourceRoot = Split-Path -Parent $PSScriptRoot
$stockFixtureRoot = Join-Path ([IO.Path]::GetTempPath()) ('stock-launchers-' + [guid]::NewGuid())
$stockFixtureRoot = [IO.Path]::GetFullPath($stockFixtureRoot)
if (-not $stockFixtureRoot.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase)) { throw '临时目录范围异常' }

function Get-TestPython([string]$c) {
    $global:LASTEXITCODE = 0
    if ($c -match 'settings\(\).api_port') { return "$global:stockRegressionPort" }
    if ($c -match 'settings\(\).db_name') { return 'stock_offline_fixture' }
    if ($c -notmatch 'db.migrate\(\)') { throw "未模拟的 Python 调用：$c" }
}
function Start-Process {
    param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle, [switch]$PassThru, $RedirectStandardOutput, $RedirectStandardError)
    $global:stockRegressionLaunches.Add(@{arguments=$ArgumentList; directory=$WorkingDirectory; window=$WindowStyle})
    $global:stockRegressionNextId++
    return [pscustomobject]@{Id=$global:stockRegressionNextId; HasExited=$false}
}
function Get-Process { param($Id, $ErrorAction); return $null }
function Get-NetTCPConnection {
    param($LocalPort, $State, $ErrorAction)
    if ([int]$LocalPort -ne $global:stockRegressionPort) { throw '检查了错误的端口' }
    if ($global:stockRegressionOccupied) { return [pscustomobject]@{OwningProcess=9999} }
    return $null
}
function Invoke-RestMethod {
    param($Uri, $TimeoutSec)
    if ($Uri -ne "http://127.0.0.1:$global:stockRegressionPort/api/health") { throw '健康检查使用了错误的地址' }
    $global:stockRegressionHealthChecks++
    return @{status='ok'; database='stock_offline_fixture'; worker=$true}
}
function Get-CimInstance {
    param($ClassName, $Filter)
    if ($Filter -like 'ProcessId=*') { return [pscustomobject]@{CommandLine='python -m uvicorn stockapp.api:app'} }
    return $null
}
function Stop-Process { param($Id, $ErrorAction); $global:stockRegressionStops.Add($Id) }

try {
    foreach ($stockTestPort in @(8000, 8010)) {
        $global:stockRegressionPort = $stockTestPort
        $global:stockRegressionLaunches = [Collections.Generic.List[object]]::new()
        $global:stockRegressionStops = [Collections.Generic.List[object]]::new()
        $global:stockRegressionNextId = 1000
        $global:stockRegressionHealthChecks = 0
        $global:stockRegressionOccupied = $false
        $stockCaseRoot = Join-Path $stockFixtureRoot "$stockTestPort"
        New-Item -ItemType Directory -Path (Join-Path $stockCaseRoot 'scripts'), (Join-Path $stockCaseRoot '.venv/Scripts') -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $stockCaseRoot '.env') -Value '# fixture'
        Set-Content -LiteralPath (Join-Path $stockCaseRoot '.venv/Scripts/python.exe') -Value '# never executed'
        foreach ($stockName in @('start.ps1', 'restart-api.ps1')) {
            $stockText = Get-Content -LiteralPath (Join-Path $stockSourceRoot "scripts/$stockName") -Raw
            $stockText = $stockText -replace '& \$stockPython -c', 'Get-TestPython -c'
            Set-Content -LiteralPath (Join-Path $stockCaseRoot "scripts/$stockName") -Value $stockText -Encoding utf8
        }
        $stockStartOutput = & (Join-Path $stockCaseRoot 'scripts/start.ps1')
        $stockApiArguments = $stockRegressionLaunches[1].arguments
        if ($stockApiArguments[-2] -ne '--port' -or [int]$stockApiArguments[-1] -ne $stockTestPort) { throw '启动参数端口错误' }
        if ($stockStartOutput -notmatch "127\.0\.0\.1:$stockTestPort") { throw '启动输出地址错误' }
        $stockRestartOutput = & (Join-Path $stockCaseRoot 'scripts/restart-api.ps1')
        if ([int]$stockRegressionLaunches[2].arguments[-1] -ne $stockTestPort -or $stockRegressionHealthChecks -ne 2) { throw '重启端口或就绪检查错误' }
        if ($stockRestartOutput -notmatch "127\.0\.0\.1:$stockTestPort") { throw '重启输出地址错误' }
        $stockRecord = Get-Content -LiteralPath (Join-Path $stockCaseRoot 'runtime/processes.json') -Raw | ConvertFrom-Json
        if ($stockRecord.port -ne $stockTestPort) { throw '登记端口错误' }
        $global:stockRegressionOccupied = $true
        $stockStopsBefore = $stockRegressionStops.Count
        $stockRejected = $false
        try { & (Join-Path $stockCaseRoot 'scripts/restart-api.ps1') } catch { $stockRejected = $_.Exception.Message -match '端口已被其它进程占用' }
        if (-not $stockRejected -or $stockRegressionStops.Count -ne $stockStopsBefore) { throw '重启未保护端口冲突时的原进程' }
        Write-Output "PASS: 端口 $stockTestPort 的启动、重启、就绪检查、地址输出和冲突保护"
    }
} finally {
    Remove-Item -LiteralPath $stockFixtureRoot -Recurse -Force
}
