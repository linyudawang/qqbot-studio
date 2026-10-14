$root = Split-Path -Parent $PSScriptRoot
$stateDir = Join-Path $root "state"
$exePidFile = Join-Path $stateDir "executor.pid"
$tunPidFile = Join-Path $stateDir "tunnel.pid"

function Test-Alive($path) {
  if (Test-Path -LiteralPath $path) {
    $p = 0
    [void][int]::TryParse((Get-Content -LiteralPath $path -ErrorAction SilentlyContinue), [ref]$p)
    if ($p -gt 0 -and (Get-Process -Id $p -ErrorAction SilentlyContinue)) { return $true }
  }
  return $false
}

$running = (Test-Alive $exePidFile) -and (Test-Alive $tunPidFile)

Write-Host ""
Write-Host "  ===== 电脑端执行器（可选模块）====="
if ($running) {
  Write-Host "  当前状态： 运行中（机器人可以操控这台电脑）" -ForegroundColor Green
} else {
  Write-Host "  当前状态： 已停止" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "  Y = 启动     N = 停止     直接回车 = 退出"
$ans = Read-Host "  请输入"

if ($ans -eq "Y" -or $ans -eq "y") {
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "启动电脑端.ps1")
} elseif ($ans -eq "N" -or $ans -eq "n") {
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "停止电脑端.ps1")
}
Start-Sleep -Seconds 3
