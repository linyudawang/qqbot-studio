# Whale-girl PC executor: HTTP endpoint that runs PowerShell commands.
# Binds 127.0.0.1 only; requires X-Secret header. Reached from the server via
# an SSH reverse tunnel. ASCII-only on purpose (PS 5.1 BOM safety).
param(
  [int]$Port = 8765,
  [string]$Secret = "change-me"
)

$ErrorActionPreference = "Stop"
$prefix = "http://127.0.0.1:$Port/"

$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add($prefix)
try {
  $listener.Start()
} catch {
  Write-Host "FAILED to start listener on $prefix : $_"
  exit 1
}
Write-Host "executor listening on $prefix"

function Invoke-RemoteCommand {
  param([string]$Command, [int]$TimeoutSec)
  $tmp = Join-Path $env:TEMP ("wg_" + [guid]::NewGuid().ToString("N") + ".ps1")
  $preamble = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;" + "`r`n" + '$OutputEncoding=[System.Text.Encoding]::UTF8;' + "`r`n"
  $full = $preamble + $Command
  [System.IO.File]::WriteAllText($tmp, $full, (New-Object System.Text.UTF8Encoding($true)))

  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = "powershell.exe"
  $psi.Arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$tmp`""
  $psi.UseShellExecute = $false
  $psi.CreateNoWindow = $true
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
  $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
  $psi.WorkingDirectory = $env:USERPROFILE

  $p = New-Object System.Diagnostics.Process
  $p.StartInfo = $psi
  [void]$p.Start()
  $outTask = $p.StandardOutput.ReadToEndAsync()
  $errTask = $p.StandardError.ReadToEndAsync()
  $timed = $false
  if (-not $p.WaitForExit($TimeoutSec * 1000)) {
    $timed = $true
    try { $p.Kill() } catch {}
  }
  $out = ""
  $err = ""
  try { $out = $outTask.Result } catch {}
  try { $err = $errTask.Result } catch {}
  $code = $null
  try { if ($p.HasExited) { $code = $p.ExitCode } } catch {}
  try { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue } catch {}
  if ($timed) { $err = $err + "`n[TIMEOUT after $TimeoutSec s]" }
  return @{ ok = $true; code = $code; stdout = $out; stderr = $err }
}

function Write-Json {
  param($Response, $Obj, [int]$Status = 200)
  $Response.StatusCode = $Status
  $Response.ContentType = "application/json; charset=utf-8"
  $json = $Obj | ConvertTo-Json -Depth 6 -Compress
  $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
  $Response.ContentLength64 = $bytes.Length
  $Response.OutputStream.Write($bytes, 0, $bytes.Length)
}

while ($listener.IsListening) {
  $ctx = $null
  try { $ctx = $listener.GetContext() } catch { break }
  $req = $ctx.Request
  $res = $ctx.Response
  try {
    if ($req.HttpMethod -ne "POST" -or $req.Url.AbsolutePath -ne "/run") {
      Write-Json $res @{ ok = $false; error = "not found" } 404
      continue
    }
    if ($req.Headers["X-Secret"] -ne $Secret) {
      Write-Json $res @{ ok = $false; error = "forbidden" } 403
      continue
    }
    $reader = New-Object System.IO.StreamReader($req.InputStream, [System.Text.Encoding]::UTF8)
    $body = $reader.ReadToEnd()
    $reader.Close()
    $obj = $null
    try { $obj = $body | ConvertFrom-Json } catch { $obj = $null }
    if (-not $obj -or -not $obj.command) {
      Write-Json $res @{ ok = $false; error = "bad request" } 400
      continue
    }
    $timeout = 60
    if ($obj.timeout) { $timeout = [int]$obj.timeout }
    if ($timeout -lt 1) { $timeout = 1 }
    if ($timeout -gt 600) { $timeout = 600 }
    $result = Invoke-RemoteCommand -Command ([string]$obj.command) -TimeoutSec $timeout
    Write-Json $res $result 200
  } catch {
    try { Write-Json $res @{ ok = $false; error = "$_" } 500 } catch {}
  } finally {
    try { $res.Close() } catch {}
  }
}
