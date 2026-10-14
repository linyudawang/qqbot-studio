# Start PC-side executor + tunnel keeper. Idempotent.
# config.json keys: port, secret, ssh_host, ssh_key, napcat_dir(可选), bot_qq(可选)
$root = Split-Path -Parent $PSScriptRoot
$cfgPath = Join-Path $root "config.json"
$cfg = Get-Content -Raw -LiteralPath $cfgPath | ConvertFrom-Json
$port = [int]$cfg.port
$secret = $cfg.secret

$stateDir = Join-Path $root "state"
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
$exePidFile = Join-Path $stateDir "executor.pid"
$tunPidFile = Join-Path $stateDir "tunnel.pid"
$tunNapPidFile = Join-Path $stateDir "tunnel-napcat.pid"
$napPidFile = Join-Path $stateDir "napcat.pid"

function Test-Alive($path) {
  if (Test-Path -LiteralPath $path) {
    $p = 0
    [void][int]::TryParse((Get-Content -LiteralPath $path -ErrorAction SilentlyContinue), [ref]$p)
    if ($p -gt 0 -and (Get-Process -Id $p -ErrorAction SilentlyContinue)) { return $true }
  }
  return $false
}

if (-not (Test-Alive $exePidFile)) {
  $exeScript = Join-Path $PSScriptRoot "执行端.ps1"
  $p = Start-Process -FilePath "powershell.exe" -WindowStyle Hidden -PassThru -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
    "-File", "`"$exeScript`"",
    "-Port", "$port", "-Secret", "$secret"
  )
  Set-Content -LiteralPath $exePidFile -Value $p.Id -Encoding ASCII
}

# 反向隧道：仅当配置了 ssh_host 时才启动（机器人跑在远端服务器时用）
if ($cfg.ssh_host) {
  if (-not (Test-Alive $tunPidFile)) {
    $tunScript = Join-Path $PSScriptRoot "隧道.ps1"
    $p = Start-Process -FilePath "powershell.exe" -WindowStyle Hidden -PassThru -ArgumentList @(
      "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
      "-File", "`"$tunScript`""
    )
    Set-Content -LiteralPath $tunPidFile -Value $p.Id -Encoding ASCII
  }

  if (-not (Test-Alive $tunNapPidFile)) {
    $tunNapScript = Join-Path $PSScriptRoot "隧道NapCat.ps1"
    $p = Start-Process -FilePath "powershell.exe" -WindowStyle Hidden -PassThru -ArgumentList @(
      "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
      "-File", "`"$tunNapScript`"", "-ConfigPath", "`"$cfgPath`""
    )
    Set-Content -LiteralPath $tunNapPidFile -Value $p.Id -Encoding ASCII
  }
}

# 可选：顺带拉起本机 NapCat（配置了 napcat_dir 才做）
if (-not (Test-Alive $napPidFile)) {
  $napDir = $cfg.napcat_dir
  $napBat = if ($napDir) { Join-Path $napDir "launcher-user.bat" } else { "" }
  if ($napBat -and (Test-Path -LiteralPath $napBat)) {
    $qqArg = if ($cfg.bot_qq) { [string]$cfg.bot_qq } else { "" }
    $p = Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", "`"$napBat`" $qqArg") -WorkingDirectory $napDir -WindowStyle Minimized -PassThru
    Set-Content -LiteralPath $napPidFile -Value $p.Id -Encoding ASCII
  } else {
    Write-Host "INFO: 未配置/未找到 NapCat，跳过（napcat_dir 为空或路径不存在）。"
  }
}

$e = Get-Content -LiteralPath $exePidFile
Write-Host "PC 执行端已启动 (executor pid=$e, port=$port)"
