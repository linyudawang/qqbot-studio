# AGENTS.md — 给 AI 编码助手的项目说明

本文件供任何编码助手（opencode / Claude Code / aider / Cursor 等）阅读，用来理解本项目并安全地加功能。
**改任何东西前先读完本文件。**

## 这是什么
一个 QQ 聊天机器人框架：通过 NapCat(OneBot) 收发 QQ 消息，接任意 OpenAI 兼容大模型做人设化对话，
支持生图/看图/语音/视频理解/梗图等。所有 AI 接口与账号在 `config.json` 里默认留空。

## 目录结构
```
bot.py                 引擎主程序（单文件，核心，见下）
configure.py           交互式配置向导（生成 config.json）
配置模板.json    空白配置模板
config.json            实际配置（含密钥，已被 .gitignore，勿提交/外传）
人设模板.txt    人格模板 → 复制为 persona.txt
persona.txt            实际人设（机器人"是谁/怎么说话"）
提示词模板.json   提示词模板 → 复制为 prompts.json
prompts.json           实际提示词（可按 key 覆盖内置默认）
密钥模板.env.example           密钥模板（可选；config.json 优先）
依赖清单.txt       Python 依赖
一键安装.ps1 / 启动.ps1 / 启动.bat / 停止.ps1   安装与启停
AGENTS.md              本文件
modules/
  pc/                  可选：PC 控制执行端（在宿主机执行 PowerShell）
  agent/               可选：把 QQ 消息转交给编码 agent 的 broker（见下）
```

## 引擎架构（bot.py）
- 启动：`main()`（文件末尾）→ `asyncio.run(Bot().run())`。
- 消息入口：`Bot.handle(ev)`。QQ 事件先分流 `notice`/`request`/`message`（`handle_notice` / `handle_request`）。
- **命令分发**：在 `Bot.handle` 里靠一堆 `if/elif` 匹配 `cmd_body(text)`（去掉前导斜杠/空白后的小写体），
  例如 `/speak`、`/repeat`、`/video`、`/pc`、`/get`、`/apply` 等；相关正则常量形如 `SPEAK_CMD_RE`、`REPEAT_CMD_RE`。
  加"新命令"就在这里加一个分支，用 `await self.reply(mtype, uid, gid, "...")` 回复。
- **大模型对话**：`think(chat_key, text, allow_tools, api, ...)` 组装 system prompt + 历史，调用 `deepseek(...)`。
- **模型工具（function calling）**：`TOOLS` 列表定义工具（name/description/parameters）；
  实际执行在 `think()` 内的 `elif name == "..."` 分支。**加"新工具"要同时改这两处。**
- **称呼/品牌**：源码里写死的「爸爸/女儿/鲸鱼娘/霖雨/石丰」由 `BRAND_TOKENS` 在运行时替换为
  `config.json` 的 `owner_title`/`bot_self_ref`/`bot_species`/`bot_nick`/`owner_name`（函数 `debrand()`）。
  **新增文案请直接用配置项或普通词，不要写死这些品牌词。**
- **自我改码**：`propose_self_patch` / `apply_self_patch` / `rollback_self_patch`（高风险，默认关闭）。
- **转交编码 agent 的桥**：`relay_to_opencode(msg)` 把文本写进宿主机 `relay_dir/inbox/<seq>.txt`；
  `fetch_opencode_reply(seq)` 读 `relay_dir/outbox/<seq>.txt`。由 `modules/agent/` 的 broker 消费。

## 如何加功能（常见需求）
1. **加一个 QQ 命令**：在 `Bot.handle` 的命令分发处加分支（照抄 `/speak` 的写法）。
   回复文本会自动被 `debrand()` 处理，无需手工替换品牌词。
2. **加一个模型可调用的工具**：在 `TOOLS` 加定义，并在 `think()` 的工具执行分支加实现。
   工具返回给模型的内容不要包含写死的品牌词（会被整体替换，通常无害，但会污染文件路径/命令输出）。
3. **改人设/语气**：改 `persona.txt` 或 `prompts.json`（无需动代码）。
4. **改称呼/品牌**：只改 `config.json` 的 `owner_title`/`bot_self_ref`/`bot_species`/`bot_nick`/`owner_name`。
5. **加可选模块**：在 `modules/<name>/` 下放独立脚本/服务，用 HTTP/WS/文件与引擎通信，**尽量不改 bot.py**。

## 约定（务必遵守）
- **不要改 `bot.py` 的对外行为除非必要**：这是"引擎"，用户升级时会整体替换。个性化配置放配置文件。
- 新增第三方依赖写进 `依赖清单.txt`，并说明用途。
- 所有文本文件用 **UTF-8**。含中文的 `.ps1` 在 Windows PowerShell 5.1 下必须存为 **UTF-8 带 BOM**，
  否则会解析报错。
- 配置读取用 `utf-8-sig` 容错 BOM。
- 密钥只放 `config.json` 或 `.env`，**绝不写进代码**。

## 改完必须自检
```powershell
python -m py_compile bot.py            # 语法
python -c "import json; json.load(open('配置模板.json',encoding='utf-8-sig'))"   # JSON 合法
# .ps1 语法：用 [System.Management.Automation.Language.Parser]::ParseFile 校验
```
若改了命令/工具，尽量在本地起 NapCat + 一个测试 QQ 跑通最小用例。

## 安全红线
- `self_patch`（自改代码）与 `modules/pc`（宿主机执行命令）默认关闭；**不要为不可信的人开启**。
- 不要把 `config.json` / `.env` / 任何密钥提交或外发。
- 涉及删除文件、改系统设置的命令，先向用户确认。
