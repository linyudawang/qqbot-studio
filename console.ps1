# QQBot Studio · 图形控制台 (WPF)
$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $here
try { $env:PYTHONUTF8 = "1" } catch {}

Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase

# 让任务栏使用本程序自己的图标（否则会归到 powershell.exe 的图标）
try {
    Add-Type -Namespace QBS -Name Native -MemberDefinition '[DllImport("shell32.dll", CharSet=CharSet.Unicode, SetLastError=true)] public static extern int SetCurrentProcessExplicitAppUserModelID(string AppID);'
    [void][QBS.Native]::SetCurrentProcessExplicitAppUserModelID("QQBotStudio.ControlPanel")
} catch {}

$xamlPath = Join-Path $here "console.xaml"
[xml]$xaml = Get-Content -LiteralPath $xamlPath -Raw -Encoding UTF8
$reader = New-Object System.Xml.XmlNodeReader $xaml
$win = [Windows.Markup.XamlReader]::Load($reader)

function N($n) { $win.FindName($n) }
$imgLogo   = N "imgLogo"
$imgMark   = N "imgMark"
$dotCfg    = N "dotCfg";   $txtCfg  = N "txtCfg"
$dotRun    = N "dotRun";   $txtRun  = N "txtRun"
$txtMsg    = N "txtMsg"
$TitleBar  = N "TitleBar"

# brand image = 新的 QQBot Studio 图标（侧栏 Logo + 标题栏标记 + 窗口/托盘图标）
$brand = Join-Path $here "assets\qqbotstudio_icon.png"
if (-not (Test-Path -LiteralPath $brand)) { $brand = Join-Path $here "assets\logo.png" }
try {
    if (Test-Path -LiteralPath $brand) {
        $bmp = New-Object Windows.Media.Imaging.BitmapImage
        $bmp.BeginInit(); $bmp.UriSource = New-Object Uri($brand); $bmp.CacheOption = "OnLoad"; $bmp.EndInit()
        $imgLogo.Source = $bmp
        $imgMark.Source = $bmp
        $win.Icon = $bmp
    }
} catch {}

$PS = (Get-Command powershell).Source

function Brush($hex) { New-Object Windows.Media.SolidColorBrush ([Windows.Media.ColorConverter]::ConvertFromString($hex)) }
function Set-Msg($t) { $txtMsg.Text = $t }

function Get-Py {
    if (Get-Command python -ErrorAction SilentlyContinue) { return "python" }
    if (Get-Command py -ErrorAction SilentlyContinue) { return "py" }
    return $null
}

function Get-BotProc {
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*bot.py*" }
}

function Update-Status {
    $cfg = Test-Path -LiteralPath (Join-Path $here "config.json")
    if ($cfg) { $dotCfg.Fill = Brush "#38D39F"; $txtCfg.Text = "配置状态：已配置" }
    else      { $dotCfg.Fill = Brush "#FFB25C"; $txtCfg.Text = "配置状态：未配置" }
    $procs = @(Get-BotProc)
    if ($procs.Count -gt 0) {
        $dotRun.Fill = Brush "#38D39F"
        $txtRun.Text = "运行状态：运行中 · PID " + ($procs[0].ProcessId)
    } else {
        $dotRun.Fill = Brush "#8A98AE"
        $txtRun.Text = "运行状态：已停止"
    }
}

function Start-Script($name) {
    $path = Join-Path $here $name
    if (-not (Test-Path -LiteralPath $path)) { Set-Msg "找不到脚本：$name"; return }
    Start-Process -FilePath $PS -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$path`"") -WorkingDirectory $here
}

function Start-Gui($name) {
    $path = Join-Path $here $name
    if (-not (Test-Path -LiteralPath $path)) { Set-Msg "找不到脚本：$name"; return }
    Start-Process -FilePath $PS -WindowStyle Hidden -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$path`"") -WorkingDirectory $here
}

# --- window chrome ---
$TitleBar.Add_MouseLeftButtonDown({ try { $win.DragMove() } catch {} })
$TitleBar.Add_MouseRightButtonUp({})
N "btnMin"  | ForEach-Object { $_.Add_Click({ $win.WindowState = "Minimized" }) }
N "btnMax"  | ForEach-Object { $_.Add_Click({ if ($win.WindowState -eq "Maximized") { $win.WindowState = "Normal" } else { $win.WindowState = "Maximized" } }) }
N "btnClose"| ForEach-Object { $_.Add_Click({ $win.Close() }) }
N "btnQuit" | ForEach-Object { $_.Add_Click({ $win.Close() }) }
N "btnRefresh" | ForEach-Object { $_.Add_Click({ Update-Status; Set-Msg "已刷新" }) }

# --- actions ---
N "btnInstall" | ForEach-Object { $_.Add_Click({ Set-Msg "已打开图形化配置向导…"; Start-Gui "setup.ps1" }) }
N "btnStart"   | ForEach-Object { $_.Add_Click({
    if (-not (Test-Path -LiteralPath (Join-Path $here "config.json"))) { Set-Msg "还没配置，请先安装。"; return }
    Set-Msg "已在新窗口启动机器人…"; Start-Script "启动.ps1"
    Start-Sleep -Milliseconds 800; Update-Status
}) }
N "btnStop"    | ForEach-Object { $_.Add_Click({
    $before = @(Get-BotProc).Count
    & $PS -NoProfile -ExecutionPolicy Bypass -File (Join-Path $here "停止.ps1") | Out-Null
    Update-Status
    if ($before -gt 0) { Set-Msg "已停止机器人。" } else { Set-Msg "当前没有运行中的机器人。" }
}) }
N "btnLog"     | ForEach-Object { $_.Add_Click({
    $log = Join-Path $here "bot.log"
    if (Test-Path -LiteralPath $log) {
        $cmd = "`$Host.UI.RawUI.WindowTitle='QQBot Studio 日志'; [Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Content -LiteralPath `"$log`" -Tail 100 -Wait -Encoding UTF8"
        Start-Process -FilePath $PS -ArgumentList @("-NoProfile", "-Command", $cmd) -WorkingDirectory $here
        Set-Msg "已打开日志窗口。"
    } else { Set-Msg "还没有日志，先启动一次吧。" }
}) }
N "btnDoc"     | ForEach-Object { $_.Add_Click({
    $ppt = Join-Path $here "新手上手指南.pptx"
    $md  = Join-Path $here "使用说明.md"
    if (Test-Path -LiteralPath $ppt) { Start-Process $ppt; Set-Msg "已打开图文说明书。" }
    elseif (Test-Path -LiteralPath $md) { Start-Process $md; Set-Msg "已打开使用说明。" }
    else { Set-Msg "未找到说明书文件。" }
}) }
N "btnFolder"  | ForEach-Object { $_.Add_Click({ Start-Process explorer.exe $here; Set-Msg "已打开文件夹。" }) }
N "btnShortcut"| ForEach-Object { $_.Add_Click({ Set-Msg "正在创建桌面快捷方式…"; Start-Script "创建桌面快捷方式.ps1" }) }
N "btnGroup"   | ForEach-Object { $_.Add_Click({
    $gid = "1130036800"
    try { Set-Clipboard -Value $gid } catch {}
    Set-Msg "已复制群号 $gid，正在尝试打开 QQ…"
    $opened = $false
    try {
        Start-Process "mqqapi://card/show_pslcard?src_type=internal&version=1&uin=$gid&card_type=group&source=qrcode" -ErrorAction Stop
        $opened = $true
    } catch {}
    if (-not $opened) {
        try { Start-Process "https://qm.qq.com/cgi-bin/qm/qr?k=&group_code=$gid&jump_from=webapi" -ErrorAction Stop } catch {}
    }
    Set-Msg "群号 $gid 已复制；在 QQ 里搜索该群号即可加入～"
}) }

# --- status timer ---
$timer = New-Object Windows.Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromSeconds(2)
$timer.Add_Tick({ Update-Status })
$timer.Start()

Update-Status
Set-Msg "就绪"
[void]$win.ShowDialog()
