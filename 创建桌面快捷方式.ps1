# 在桌面创建 QQBot Studio 控制台快捷方式
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$desktop = [Environment]::GetFolderPath('Desktop')
$lnk = Join-Path $desktop "QQBot Studio.lnk"
$icon = Join-Path $here "assets\qqbotstudio_icon.ico"
if (-not (Test-Path -LiteralPath $icon)) { $icon = Join-Path $here "assets\avatar_icon.ico" }
if (-not (Test-Path -LiteralPath $icon)) { $icon = Join-Path $here "assets\icon.ico" }

try {
    $ws = New-Object -ComObject WScript.Shell
    $sc = $ws.CreateShortcut($lnk)
    $sc.TargetPath = Join-Path $here "控制台.bat"
    $sc.WorkingDirectory = $here
    if (Test-Path -LiteralPath $icon) { $sc.IconLocation = $icon }
    $sc.Description = "QQBot Studio 控制台"
    $sc.WindowStyle = 1
    $sc.Save()
    Write-Host "[+] 已在桌面创建快捷方式：QQBot Studio" -ForegroundColor Green
} catch {
    Write-Host "[x] 创建快捷方式失败：$($_.Exception.Message)" -ForegroundColor Red
}
