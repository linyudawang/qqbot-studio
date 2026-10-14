# 启动编码 agent broker（后台运行）。日志见同目录 agent.log
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $here
if (-not (Test-Path -LiteralPath "agent.config.json")) {
    Copy-Item -LiteralPath "配置模板.json" -Destination "agent.config.json"
    Write-Host "已生成 agent.config.json。请先填写（尤其 relay_dir 要与引擎 config.json 一致），然后再运行本脚本。"
    return
}
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*agent.py*" }
if ($running) {
    Write-Host "broker 已在运行（PID $($running.ProcessId -join ',')）。"
    return
}
Start-Process -FilePath "python" -ArgumentList "agent.py" -WorkingDirectory $here -WindowStyle Hidden
Write-Host "编码 agent broker 已在后台启动。日志：$here\agent.log"
