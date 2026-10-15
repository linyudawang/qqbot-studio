# 启动机器人
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $here
try {
    chcp 65001 | Out-Null
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $env:PYTHONUTF8 = "1"
} catch {}

Write-Host ""
Write-Host "   QQBot Studio   v1.2.6" -ForegroundColor Cyan
Write-Host "   一键部署的 AI QQ 机器人框架" -ForegroundColor DarkGray
Write-Host ("   " + ("-" * 42)) -ForegroundColor DarkCyan
Write-Host ""

if (-not (Test-Path -LiteralPath "config.json")) {
    Write-Host "[x] 还没配置，请先运行 一键安装.ps1" -ForegroundColor Red
    Read-Host "按回车退出"; exit 1
}

$py = if (Get-Command python -ErrorAction SilentlyContinue) { "python" } else { "py" }
Write-Host "[*] 启动机器人（Ctrl+C 停止）..." -ForegroundColor Cyan
& $py bot.py
