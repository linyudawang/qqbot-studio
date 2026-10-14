# AI 机器人框架 · 一键部署向导（Windows）
# 用法：右键「用 PowerShell 运行」，或在本目录执行：  powershell -ExecutionPolicy Bypass -File 一键安装.ps1
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $here
try {
    chcp 65001 | Out-Null
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $env:PYTHONUTF8 = "1"
} catch {}

function Info($m) { Write-Host "[*] $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "[+] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[!] $m" -ForegroundColor Yellow }
function Err($m)  { Write-Host "[x] $m" -ForegroundColor Red }

Write-Host ("=" * 60) -ForegroundColor DarkCyan
Write-Host "   QQBot Studio   v1.0.0" -ForegroundColor Cyan
Write-Host "   一键部署向导" -ForegroundColor DarkGray
Write-Host ("=" * 60) -ForegroundColor DarkCyan
Write-Host ""

# --- 1. 检查 Python ---
$py = $null
foreach ($c in @("python", "py")) {
    try {
        $v = & $c --version 2>&1
        if ($LASTEXITCODE -eq 0) { $py = $c; $pyver = "$v"; break }
    } catch {}
}
if (-not $py) {
    Err "没找到 Python。请先安装 Python 3.10+（安装时勾选 Add to PATH）：https://www.python.org/downloads/"
    Read-Host "按回车退出"; exit 1
}
Ok "Python: $pyver ($py)"

# --- 2. 安装依赖 ---
Info "安装 Python 依赖..."
& $py -m pip install --upgrade pip | Out-Null
& $py -m pip install -r 依赖清单.txt
if ($LASTEXITCODE -ne 0) { Warn "依赖安装有报错，请检查网络或 pip 源。" } else { Ok "依赖安装完成。" }

# --- 3. 生成配置 ---
Write-Host ""
Info "进入配置向导（生成 config.json）..."
& $py configure.py
if ($LASTEXITCODE -ne 0) { Err "配置向导未完成。"; Read-Host "按回车退出"; exit 1 }

# --- 4. 人格文件 ---
if (-not (Test-Path -LiteralPath "persona.txt")) {
    Copy-Item -LiteralPath "人设模板.txt" -Destination "persona.txt"
    Ok "已生成 persona.txt（可自行编辑人格，注意存 UTF-8）。"
}

# --- 5. 提示词文件 ---
if (-not (Test-Path -LiteralPath "prompts.json")) {
    Copy-Item -LiteralPath "提示词模板.json" -Destination "prompts.json"
    Ok "已生成 prompts.json。"
}

# --- 6. 可选：PC 控制模块 ---
Write-Host ""
if (Test-Path -LiteralPath "modules\pc") {
    $pcAns = Read-Host "是否启用【PC 控制】模块（让机器人在你电脑上执行命令）？(y/N)"
    if ($pcAns -match "^[yY]") {
        if (-not (Test-Path -LiteralPath "modules\pc\config.json")) {
            Copy-Item -LiteralPath "modules\pc\配置模板.json" -Destination "modules\pc\config.json" -ErrorAction SilentlyContinue
        }
        Warn "PC 模块已放置到 modules\pc，请阅读 modules\pc\ 内脚本说明后再运行。默认未自动开机启动。"
    } else {
        Ok "跳过 PC 模块。"
    }
}

# --- 7. 可选：桌宠 ---
Write-Host ""
if ((Test-Path -LiteralPath "modules\pet") -and (Get-Command node -ErrorAction SilentlyContinue)) {
    $petAns = Read-Host "是否安装 Live2D 桌宠依赖（需要 Node.js，可能较慢）？(y/N)"
    if ($petAns -match "^[yY]") {
        Push-Location "modules\pet"
        npm install
        Pop-Location
        Ok "桌宠依赖安装完成。"
    }
} elseif (Test-Path -LiteralPath "modules\pet") {
    Warn "未检测到 Node.js，跳过桌宠。"
}

# --- 完成 ---
Write-Host ""
Ok "部署完成！"
Write-Host "启动：  .\启动.ps1   或双击 启动.bat" -ForegroundColor White
Write-Host "配置：  编辑 config.json（改完重启即可）" -ForegroundColor White
Write-Host "人格：  编辑 persona.txt" -ForegroundColor White
Write-Host "注意：  别忘了先启动 NapCat 并用 bot_qq 登录 QQ。" -ForegroundColor Yellow
Read-Host "按回车退出"
