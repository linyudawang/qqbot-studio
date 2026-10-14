# Whale-girl NapCat reverse-tunnel keeper.
# Exposes this PC's NapCat WebSocket (127.0.0.1:3001) as server 127.0.0.1:3003.
param(
  [string]$ConfigPath = (Join-Path $PSScriptRoot "config.json")
)

$cfg = Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json
$key = $cfg.ssh_key
$sshHost = $cfg.ssh_host

$sshExe = "C:\Windows\System32\OpenSSH\ssh.exe"
if (-not (Test-Path -LiteralPath $sshExe)) { $sshExe = "ssh.exe" }

$remote = "3003:127.0.0.1:3001"
$sshArgs = @(
  "-i", $key,
  "-o", "StrictHostKeyChecking=accept-new",
  "-o", "ServerAliveInterval=30",
  "-o", "ServerAliveCountMax=3",
  "-o", "ExitOnForwardFailure=yes",
  "-o", "TCPKeepAlive=yes",
  "-N", "-R", $remote, $sshHost
)

Write-Host "napcat tunnel keeper starting -> $sshHost ($remote)"
while ($true) {
  & $sshExe @sshArgs
  Write-Host "napcat tunnel exited, retrying in 5s..."
  Start-Sleep -Seconds 5
}