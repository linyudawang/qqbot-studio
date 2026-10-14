# Whale-girl reverse-tunnel keeper. Runs ssh -R forever, auto-reconnects.
# Exposes this PC's local executor (127.0.0.1:$port) as server 127.0.0.1:$port.
param(
  [string]$ConfigPath = (Join-Path $PSScriptRoot "config.json")
)

$cfg = Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json
$key = $cfg.ssh_key
$sshHost = $cfg.ssh_host
$port = [int]$cfg.port

$sshExe = "C:\Windows\System32\OpenSSH\ssh.exe"
if (-not (Test-Path -LiteralPath $sshExe)) { $sshExe = "ssh.exe" }

$remote = -join @($port, ":127.0.0.1:", $port)
$sshArgs = @(
  "-i", $key,
  "-o", "StrictHostKeyChecking=accept-new",
  "-o", "ServerAliveInterval=30",
  "-o", "ServerAliveCountMax=3",
  "-o", "ExitOnForwardFailure=yes",
  "-o", "TCPKeepAlive=yes",
  "-N", "-R", $remote, $sshHost
)

Write-Host "tunnel keeper starting -> $sshHost ($remote)"
while ($true) {
  & $sshExe @sshArgs
  Write-Host "tunnel exited, retrying in 5s..."
  Start-Sleep -Seconds 5
}
