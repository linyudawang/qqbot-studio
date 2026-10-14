# Stop whale-girl PC side: kill executor + tunnel by pid, then clean strays.
$root = Split-Path -Parent $PSScriptRoot
$stateDir = Join-Path $root "state"

$cfgPath = Join-Path $root "config.json"
$port = 8765
try {
  $cfg = Get-Content -Raw -LiteralPath $cfgPath | ConvertFrom-Json
  $port = [int]$cfg.port
} catch {}

foreach ($name in @("executor.pid", "tunnel.pid")) {
  $path = Join-Path $stateDir $name
  if (Test-Path -LiteralPath $path) {
    $p = 0
    [void][int]::TryParse((Get-Content -LiteralPath $path -ErrorAction SilentlyContinue), [ref]$p)
    if ($p -gt 0) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
  }
}

$needle = "$port" + ":127.0.0.1:" + "$port"
Get-CimInstance Win32_Process -Filter "Name='ssh.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -like "*$needle*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Write-Host "whale-girl PC side stopped"
