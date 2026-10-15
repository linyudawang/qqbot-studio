# QQBot Studio · 一键配置（启动图形化配置向导）
# 用法：双击本文件，或在本目录执行：
#   powershell -ExecutionPolicy Bypass -File 一键安装.ps1
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $here
try { $env:PYTHONUTF8 = "1" } catch {}

$setup = Join-Path $here "setup.ps1"
if (-not (Test-Path -LiteralPath $setup)) {
    Write-Host "[x] 找不到 setup.ps1，无法启动图形化向导。" -ForegroundColor Red
    Read-Host "按回车退出"; exit 1
}

$ps = (Get-Command powershell).Source
Start-Process -FilePath $ps -WindowStyle Hidden -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$setup`"") -WorkingDirectory $here
