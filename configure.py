#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交互式配置向导：读取 配置模板.json，问答后生成 config.json。

用法：
  python configure.py            # 交互式
  python configure.py --reset    # 从空白模板重来（忽略已有 config.json）

也被 一键安装.ps1 调用，实现一键部署。
"""
import argparse
import json
import os
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE = os.path.dirname(os.path.abspath(__file__))
EXAMPLE = os.path.join(BASE, "配置模板.json")
CONFIG = os.path.join(BASE, "config.json")


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def ask(prompt, default=""):
    d = (" [%s]" % default) if default else ""
    try:
        val = input("%s%s: " % (prompt, d)).strip()
    except EOFError:
        val = ""
    return val or default


def ask_yes(prompt, default=True):
    d = "Y/n" if default else "y/N"
    try:
        val = input("%s (%s): " % (prompt, d)).strip().lower()
    except EOFError:
        val = ""
    if not val:
        return default
    return val in ("y", "yes", "是", "1", "true")


def ask_list(prompt, default=None):
    default = default or []
    cur = ",".join(str(x) for x in default)
    val = ask(prompt + "（多个用逗号分隔）", cur)
    return [x.strip() for x in val.replace("，", ",").split(",") if x.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="忽略已有 config.json，从模板重来")
    args = ap.parse_args()

    base = load_json(EXAMPLE)
    if base is None:
        print("找不到 配置模板.json，无法继续。")
        return 1

    existing = None if args.reset else load_json(CONFIG)
    if existing:
        base.update(existing)
        print("检测到已有 config.json，回车保留旧值。\n")
    else:
        print("开始配置。直接回车 = 用默认值 / 留空。\n")

    print("==== 1/4 账号与连接 ====")
    base["bot_qq"] = ask("机器人 QQ 号（必填）", base.get("bot_qq", ""))
    base["owner_qq"] = ask_list("主人 QQ 号（拥有全部管理权限）", base.get("owner_qq", []))
    base["owner_name"] = ask("主人的称呼", base.get("owner_name", ""))
    base["bot_nick"] = ask("机器人昵称", base.get("bot_nick", "小助手"))
    base["owner_title"] = ask("机器人对主人的称呼（如 主人/老板/爸爸）",
                             base.get("owner_title", "主人"))
    base["bot_self_ref"] = ask("机器人对自己的人称（如 我/本喵/女儿）",
                               base.get("bot_self_ref", "我"))
    base["bot_species"] = ask("机器人的物种/定位（如 AI助手/鲸鱼娘/猫娘）",
                              base.get("bot_species", "AI助手"))
    base["ws_url"] = ask("NapCat OneBot 反向 WS 地址", base.get("ws_url", "ws://127.0.0.1:3001"))

    print("\n==== 2/4 对话大模型（OpenAI 兼容）====")
    base["deepseek_base"] = ask("接口 Base URL", base.get("deepseek_base", "https://api.deepseek.com"))
    base["model"] = ask("模型名", base.get("model", "deepseek-chat"))
    base["deepseek_api_key"] = ask("API Key", base.get("deepseek_api_key", ""))

    print("\n==== 3/4 可选接口（不需要就留空/回车跳过）====")
    base["t2i_api_key"] = ask("图像生成 API Key（智谱GLM/CogView 等）", base.get("t2i_api_key", ""))
    if base["t2i_api_key"]:
        base["t2i"] = True
    base["asr_api_key"] = ask("语音识别(ASR) API Key", base.get("asr_api_key", ""))
    base["asr"] = bool(base.get("asr_api_key"))
    base["bing_api_key"] = ask("Bing 图像搜索 API Key", base.get("bing_api_key", ""))
    base["avatar_api_key"] = ask("虚拟形象生成 API Key（火山方舟 ARK）", base.get("avatar_api_key", ""))
    base["avatar"] = bool(base.get("avatar_api_key"))

    print("\n==== 4/4 语音合成(TTS) ====")
    print("  可选：sapi(系统自带,免配置) / gptsovits(本地) / indextts(本地) / edge / glm")
    engine = ask("TTS 引擎", base.get("tts_engine", "sapi"))
    base["tts_engine"] = engine
    base["tts"] = ask_yes("是否启用语音", base.get("tts", True))
    if engine == "gptsovits":
        base["tts_sovits_url"] = ask("GPT-SoVITS 服务地址", base.get("tts_sovits_url", "http://127.0.0.1:9880"))
        base["tts_sovits_ref"] = ask("参考音频 wav 路径", base.get("tts_sovits_ref", ""))
    elif engine == "indextts":
        base["tts_indextts_url"] = ask("IndexTTS 服务地址", base.get("tts_indextts_url", "http://127.0.0.1:8000"))
        base["tts_indextts_voice"] = ask("注册的音色名", base.get("tts_indextts_voice", ""))

    print("\n==== 安全功能（默认关闭）====")
    base["executor_url"] = ask("PC 控制执行端地址（留空=不用）", base.get("executor_url", ""))
    base["executor_secret"] = ask("PC 执行端通信秘钥", base.get("executor_secret", ""))
    base["self_patch_enabled"] = ask_yes("是否允许机器人自改代码（高风险）", False)

    base.pop("_comment", None)

    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(base, f, ensure_ascii=False, indent=2)

    print("\n已生成 config.json")
    if not base.get("bot_qq"):
        print("⚠ 还没填 bot_qq，机器人无法登录。")
    if not base.get("deepseek_api_key"):
        print("⚠ 还没填 deepseek_api_key，无法对话。")
    print("下一步：运行  启动.ps1  或双击 启动.bat 启动。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已取消。")
        sys.exit(1)
