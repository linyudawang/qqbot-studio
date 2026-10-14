<img src="assets/logo.png" width="420" alt="QQBot Studio">

# QQBot Studio

一个把 QQ 机器人（基于 DeepSeek 大模型 + NapCat/OneBot 协议）打包成**开箱即用软件**的框架。角色设定、密钥、称呼全部留空，装完自己填，无需改代码。

> 版本：v1.2.3 ｜ 平台：Windows ｜ 语言：Python 3.10+

## 能做什么

- 群聊/私聊陪聊，可自定义人格与称呼
- 看图、听语音、发图、文生图、图搜图
- 语音回复（TTS）、视频内容理解
- **智能增强包**：长期记忆、定时提醒、联网搜索（`/web`、`/source`）
- 可选模块：**电脑操控**（远程执行）、**编码助手**（对话即改代码）

## 快速开始

1. 双击 `控制台.bat` → 选 `[1] 安装 / 重新配置`，按向导填 DeepSeek 密钥与角色设定
2. 再选 `[2] 启动机器人`

详细图文教程见 [`使用说明.md`](使用说明.md)，新手 PPT 见 [`新手上手指南.pptx`](新手上手指南.pptx)。

## 目录结构

```
aibot-framework/
├─ 控制台.bat / 控制台.ps1     # 统一入口（菜单）
├─ bot.py                      # 引擎主程序
├─ configure.py                # 交互式配置向导
├─ 配置模板.json / 人设模板.txt / 提示词模板.json / 密钥模板.env.example
├─ 一键安装.ps1 / 启动.ps1 / 停止.ps1
├─ modules/
│  ├─ pc/      # 可选：电脑操控
│  └─ agent/   # 可选：编码助手
└─ assets/     # 图标与 Logo
```

## 安全提示

- 本仓库**不含**任何密钥。请勿把生成的 `config.json`、`.env`、`persona.txt`、`prompts.json`、日志提交到公开仓库（已在 `.gitignore` 中忽略）。
- 密钥一旦泄露请立即在服务商后台**轮换**。

## 许可证

暂未附带开源许可证。
