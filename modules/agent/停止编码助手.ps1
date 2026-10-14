# 停止编码 agent broker
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*agent.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host "已停止 PID $($_.ProcessId)" }
Write-Host "编码 agent broker 已停止。"
