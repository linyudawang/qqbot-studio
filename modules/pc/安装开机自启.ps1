# Enable auto-start at logon WITHOUT admin: drop a hidden-launcher VBS in Startup.
$startScript = Join-Path $PSScriptRoot "启动电脑端.ps1"
$startup = [Environment]::GetFolderPath("Startup")
$vbsPath = Join-Path $startup "WhaleGirlAutostart.vbs"

$vbs = 'CreateObject("WScript.Shell").Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""' + $startScript + '""", 0, False'
[System.IO.File]::WriteAllText($vbsPath, $vbs, (New-Object System.Text.ASCIIEncoding))

Write-Host "auto-start enabled: $vbsPath"
