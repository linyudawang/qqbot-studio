# 停止本目录启动的机器人进程
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$killed = 0
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" | ForEach-Object {
    if ($_.CommandLine -and $_.CommandLine -like "*bot.py*") {
        try {
            Stop-Process -Id $_.ProcessId -Force
            Write-Host "[+] 已停止 PID $($_.ProcessId)" -ForegroundColor Green
            $killed++
        } catch {}
    }
}
if ($killed -eq 0) { Write-Host "[*] 没有找到正在运行的机器人进程。" -ForegroundColor Yellow }
