# QQBot Studio · 图形化配置向导 (WPF)
$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $here
try { $env:PYTHONUTF8 = "1" } catch {}

Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase

# 任务栏使用本程序自己的图标
try {
    Add-Type -Namespace QBS -Name Native -MemberDefinition '[DllImport("shell32.dll", CharSet=CharSet.Unicode, SetLastError=true)] public static extern int SetCurrentProcessExplicitAppUserModelID(string AppID);'
    [void][QBS.Native]::SetCurrentProcessExplicitAppUserModelID("QQBotStudio.ControlPanel")
} catch {}

$xamlPath = Join-Path $here "setup.xaml"
[xml]$xaml = Get-Content -LiteralPath $xamlPath -Raw -Encoding UTF8
$reader = New-Object System.Xml.XmlNodeReader $xaml
$win = [Windows.Markup.XamlReader]::Load($reader)

function N($n) { $win.FindName($n) }

# 品牌图标
$brand = Join-Path $here "assets\qqbotstudio_icon.png"
if (-not (Test-Path -LiteralPath $brand)) { $brand = Join-Path $here "assets\logo.png" }
try {
    if (Test-Path -LiteralPath $brand) {
        $bmp = New-Object Windows.Media.Imaging.BitmapImage
        $bmp.BeginInit(); $bmp.UriSource = New-Object Uri($brand); $bmp.CacheOption = "OnLoad"; $bmp.EndInit()
        (N "imgMark").Source = $bmp
        $win.Icon = $bmp
    }
} catch {}

$txtStatus = N "txtStatus"
function Set-Msg($t) { $txtStatus.Text = $t }

(N "TitleBar").Add_MouseLeftButtonDown({ try { $win.DragMove() } catch {} })
(N "btnMin")   | ForEach-Object { $_.Add_Click({ $win.WindowState = "Minimized" }) }
(N "btnClose") | ForEach-Object { $_.Add_Click({ $win.Close() }) }
(N "btnCancel")| ForEach-Object { $_.Add_Click({ $win.Close() }) }

function Get-Json($p) {
    if (Test-Path -LiteralPath $p) {
        try { return (Get-Content -LiteralPath $p -Raw -Encoding UTF8 | ConvertFrom-Json) } catch { return $null }
    }
    return $null
}

$cfg = Get-Json (Join-Path $here "配置模板.json")
if (-not $cfg) {
    [void][Windows.MessageBox]::Show("找不到 配置模板.json，无法配置。", "QQBot Studio", "OK", "Error")
    $win.Close(); return
}
$existing = Get-Json (Join-Path $here "config.json")
if ($existing) {
    foreach ($p in $existing.PSObject.Properties) {
        $cfg | Add-Member -Force -NotePropertyName $p.Name -NotePropertyValue $p.Value
    }
    Set-Msg "检测到已有 config.json，已载入，可修改后保存。"
}

function Prop($o, $name, $def) {
    try { $v = $o.$name } catch { $v = $null }
    if ($null -eq $v) { return $def } else { return $v }
}
function SetTxt($n, $v) { (N $n).Text = [string]$v }
function SetChk($n, $v) { (N $n).IsChecked = [bool]$v }

SetTxt txtBotQq      (Prop $cfg 'bot_qq' '')
$oq = Prop $cfg 'owner_qq' @()
if ($oq -is [array]) { $oq = ($oq -join ',') }
SetTxt txtOwnerQq    $oq
SetTxt txtOwnerName  (Prop $cfg 'owner_name' '')
SetTxt txtBotNick    (Prop $cfg 'bot_nick' '小助手')
SetTxt txtOwnerTitle (Prop $cfg 'owner_title' '主人')
SetTxt txtBotSelfRef (Prop $cfg 'bot_self_ref' '我')
SetTxt txtBotSpecies (Prop $cfg 'bot_species' 'AI助手')
SetTxt txtWsUrl      (Prop $cfg 'ws_url' 'ws://127.0.0.1:3001')
SetTxt txtDsBase     (Prop $cfg 'deepseek_base' 'https://api.deepseek.com')
SetTxt txtModel      (Prop $cfg 'model' 'deepseek-chat')
SetTxt txtDsKey      (Prop $cfg 'deepseek_api_key' '')
SetTxt txtT2iKey     (Prop $cfg 't2i_api_key' '')
SetTxt txtAsrKey     (Prop $cfg 'asr_api_key' '')
SetTxt txtBingKey    (Prop $cfg 'bing_api_key' '')
SetTxt txtAvatarKey  (Prop $cfg 'avatar_api_key' '')
SetChk chkTts        (Prop $cfg 'tts' $true)
$eng = [string](Prop $cfg 'tts_engine' 'sapi')
$cmb = N "cmbTts"
$found = $false
foreach ($it in $cmb.Items) { if ($it.Content -eq $eng) { $cmb.SelectedItem = $it; $found = $true } }
if (-not $found) { $cmb.SelectedIndex = 0 }
SetTxt txtSovitsUrl  (Prop $cfg 'tts_sovits_url' 'http://127.0.0.1:9880')
SetTxt txtSovitsRef  (Prop $cfg 'tts_sovits_ref' '')
SetTxt txtIndexUrl   (Prop $cfg 'tts_indextts_url' 'http://127.0.0.1:8000')
SetTxt txtIndexVoice (Prop $cfg 'tts_indextts_voice' '')
SetTxt txtExecutorUrl (Prop $cfg 'executor_url' '')
SetTxt txtExecutorSecret (Prop $cfg 'executor_secret' '')
SetChk chkSelfPatch  ([bool](Prop $cfg 'self_patch_enabled' $false))

SetChk chkVideoMake  ([bool](Prop $cfg 'video_make' $true))
SetChk chkAivideo    ([bool](Prop $cfg 'aivideo' $false))
$prov = [string](Prop $cfg 'aivideo_provider' 'kling')
$cmbp = N "cmbAivideoProvider"
$fp = $false
foreach ($it in $cmbp.Items) { if ($it.Content -eq $prov) { $cmbp.SelectedItem = $it; $fp = $true } }
if (-not $fp) { $cmbp.SelectedIndex = 0 }
SetTxt txtKlingAccess  (Prop $cfg 'kling_access_key' '')
SetTxt txtKlingSecret  (Prop $cfg 'kling_secret_key' '')
SetTxt txtKlingModel   (Prop $cfg 'kling_model' 'kling-v2-5-turbo')
$kmod = [string](Prop $cfg 'kling_mode' 'std')
$cmbkm = N "cmbKlingMode"
$fm = $false
foreach ($it in $cmbkm.Items) { if ($it.Content -eq $kmod) { $cmbkm.SelectedItem = $it; $fm = $true } }
if (-not $fm) { $cmbkm.SelectedIndex = 0 }
SetTxt txtAivideoKey   (Prop $cfg 'aivideo_api_key' '')
SetTxt txtAivideoModel (Prop $cfg 'aivideo_model' 'doubao-seedance-2-0-fast-260128')

function Get-Py {
    $p = Join-Path $here "python\python.exe"
    if (Test-Path -LiteralPath $p) { return $p }
    if (Get-Command python -ErrorAction SilentlyContinue) { return "python" }
    if (Get-Command py -ErrorAction SilentlyContinue) { return "py" }
    return $null
}

(N "btnSave") | ForEach-Object { $_.Add_Click({
    $o = [ordered]@{}
    $o.bot_qq = (N "txtBotQq").Text.Trim()
    $o.owner_qq = @(((N "txtOwnerQq").Text.Replace('，',',') -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }))
    $o.owner_name = (N "txtOwnerName").Text.Trim()
    $o.bot_nick = (N "txtBotNick").Text.Trim()
    $o.owner_title = (N "txtOwnerTitle").Text.Trim()
    $o.bot_self_ref = (N "txtBotSelfRef").Text.Trim()
    $o.bot_species = (N "txtBotSpecies").Text.Trim()
    $o.ws_url = (N "txtWsUrl").Text.Trim()
    $o.deepseek_base = (N "txtDsBase").Text.Trim()
    $o.model = (N "txtModel").Text.Trim()
    $o.deepseek_api_key = (N "txtDsKey").Text.Trim()
    $o.t2i_api_key = (N "txtT2iKey").Text.Trim()
    $o.asr_api_key = (N "txtAsrKey").Text.Trim()
    $o.bing_api_key = (N "txtBingKey").Text.Trim()
    $o.avatar_api_key = (N "txtAvatarKey").Text.Trim()
    $o.tts = [bool](N "chkTts").IsChecked
    $sel = (N "cmbTts").SelectedItem
    if ($sel) { $o.tts_engine = [string]$sel.Content } else { $o.tts_engine = "sapi" }
    $o.tts_sovits_url = (N "txtSovitsUrl").Text.Trim()
    $o.tts_sovits_ref = (N "txtSovitsRef").Text.Trim()
    $o.tts_indextts_url = (N "txtIndexUrl").Text.Trim()
    $o.tts_indextts_voice = (N "txtIndexVoice").Text.Trim()
    $o.executor_url = (N "txtExecutorUrl").Text.Trim()
    $o.executor_secret = (N "txtExecutorSecret").Text.Trim()
    $o.self_patch_enabled = [bool](N "chkSelfPatch").IsChecked

    $o.video_make = [bool](N "chkVideoMake").IsChecked
    $o.aivideo = [bool](N "chkAivideo").IsChecked
    $pSel = (N "cmbAivideoProvider").SelectedItem
    if ($pSel) { $o.aivideo_provider = [string]$pSel.Content } else { $o.aivideo_provider = "kling" }
    $o.kling_access_key = (N "txtKlingAccess").Text.Trim()
    $o.kling_secret_key = (N "txtKlingSecret").Text.Trim()
    $o.kling_model = (N "txtKlingModel").Text.Trim()
    $mSel = (N "cmbKlingMode").SelectedItem
    if ($mSel) { $o.kling_mode = [string]$mSel.Content } else { $o.kling_mode = "std" }
    $o.aivideo_api_key = (N "txtAivideoKey").Text.Trim()
    $o.aivideo_model = (N "txtAivideoModel").Text.Trim()

    $py = Get-Py
    if (-not $py) { Set-Msg "[x] 找不到 Python，无法写入配置。"; return }

    $wizPath = Join-Path $here "_wizard.json"
    $json = $o | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText($wizPath, $json, (New-Object Text.UTF8Encoding $false))

    Set-Msg "正在保存…"
    $out = & $py (Join-Path $here "wizard_apply.py") 2>&1
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $here "config.json"))) {
        Set-Msg "[x] 保存失败：$out"
        return
    }

    if (-not (Test-Path -LiteralPath (Join-Path $here "persona.txt"))) {
        Copy-Item -LiteralPath (Join-Path $here "人设模板.txt") -Destination (Join-Path $here "persona.txt") -ErrorAction SilentlyContinue
    }
    if (-not (Test-Path -LiteralPath (Join-Path $here "prompts.json"))) {
        Copy-Item -LiteralPath (Join-Path $here "提示词模板.json") -Destination (Join-Path $here "prompts.json") -ErrorAction SilentlyContinue
    }

    $warn = ""
    if (-not $o.bot_qq) { $warn += "`n· 还没填【机器人 QQ 号】，机器人无法登录。" }
    if (-not $o.deepseek_api_key) { $warn += "`n· 还没填【对话大模型 API Key】，无法对话。" }

    if ($warn) {
        [void][Windows.MessageBox]::Show("配置已保存，但还需补充：$warn`n`n可稍后在控制台点【安装 / 重新配置】再改。", "QQBot Studio", "OK", "Warning")
        Set-Msg "已保存（有提醒项）"
    } else {
        [void][Windows.MessageBox]::Show("配置完成！`n`n下一步：回到控制台点【启动机器人】。`n（记得先启动 NapCat 并用机器人 QQ 登录）", "QQBot Studio", "OK", "Information")
        $win.Close()
    }
}) }

Set-Msg "就绪"
[void]$win.ShowDialog()
