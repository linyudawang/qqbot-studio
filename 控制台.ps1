# QQBot Studio · 控制台
$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $here
try {
    chcp 65001 | Out-Null
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $env:PYTHONUTF8 = "1"
} catch {}

$APP = "QQBot Studio"
$VERSION = "1.1.1"
$PS = "powershell"
$BYPASS = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File")

function Show-Banner {
    Clear-Host
    $line = "=" * 60
    Write-Host $line -ForegroundColor DarkCyan
    Write-Host ("   {0}   v{1}" -f $APP, $VERSION) -ForegroundColor Cyan
    Write-Host "   一键部署的 AI QQ 机器人框架" -ForegroundColor DarkGray
    Write-Host $line -ForegroundColor DarkCyan
}

function Pause-Menu { Write-Host ""; Read-Host "  按回车返回主菜单" | Out-Null }

while ($true) {
    Show-Banner
    $installed = Test-Path -LiteralPath (Join-Path $here "config.json")
    $color = if ($installed) { "Green" } else { "Yellow" }
    $status = if ($installed) { "已配置" } else { "未配置（请先选 [1] 安装）" }
    Write-Host ""
    Write-Host "   当前状态：$status" -ForegroundColor $color
    Write-Host ""
    Write-Host "   [1] 安装 / 重新配置"
    Write-Host "   [2] 启动机器人"
    Write-Host "   [3] 停止机器人"
    Write-Host "   [4] 查看运行日志"
    Write-Host "   [5] 打开图文说明书"
    Write-Host "   [6] 打开本文件夹"
    Write-Host "   [7] 创建桌面快捷方式"
    Write-Host "   [0] 退出"
    Write-Host ""
    $c = (Read-Host "   请输入序号").Trim()

    switch ($c) {
        "1" {
            & $PS @BYPASS (Join-Path $here "一键安装.ps1")
        }
        "2" {
            if (-not $installed) {
                Write-Host "   还没有配置，请先选 [1] 完成安装。" -ForegroundColor Yellow
                Pause-Menu
                continue
            }
            $startArgs = $BYPASS + @("`"$(Join-Path $here '启动.ps1')`"")
            Start-Process -FilePath $PS -ArgumentList $startArgs
            Write-Host "   已在新窗口启动机器人。停止请选 [3]。" -ForegroundColor Green
            Pause-Menu
        }
        "3" {
            & $PS @BYPASS (Join-Path $here "停止.ps1")
            Pause-Menu
        }
        "4" {
            $log = Join-Path $here "bot.log"
            if (Test-Path -LiteralPath $log) {
                Write-Host "   （显示最近 40 行并实时跟随，按 Ctrl+C 退出）" -ForegroundColor DarkGray
                Write-Host ""
                Get-Content -LiteralPath $log -Tail 40 -Wait
            } else {
                Write-Host "   还没有日志，先启动一次吧。" -ForegroundColor Yellow
                Pause-Menu
            }
        }
        "5" {
            $ppt = Join-Path $here "新手上手指南.pptx"
            $md = Join-Path $here "使用说明.md"
            if (Test-Path -LiteralPath $ppt) { Start-Process $ppt }
            elseif (Test-Path -LiteralPath $md) { Start-Process $md }
            else { Write-Host "   未找到说明书。" -ForegroundColor Yellow; Pause-Menu }
        }
        "6" {
            Start-Process explorer.exe $here
        }
        "7" {
            & $PS @BYPASS (Join-Path $here "创建桌面快捷方式.ps1")
            Pause-Menu
        }
        "0" { break }
        default { Write-Host "   请输入 0-7 之间的序号。" -ForegroundColor Yellow; Start-Sleep -Milliseconds 700 }
    }
}
