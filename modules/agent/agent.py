#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
编码 agent broker —— 把 QQ 里转交过来的任务，交给一个编码 agent 处理，再把答复写回发件箱。

工作方式：
  引擎(bot.py) 通过 relay_to_opencode() 把主人的原话写进  relay_dir/inbox/<seq>.txt
  本 broker 轮询 inbox，处理其中的任务，把结果写进               relay_dir/outbox/<seq>.txt
  主人在 QQ 里 /get <seq> 取回答复。

支持两种后端（agent.config.json 的 backend）：
  local    —— 内置的纯 Python 编码 agent（用 OpenAI 兼容接口 + function calling，读写仓库文件）
  opencode —— 调用外部 opencode CLI（opencode run "<task>"，工作目录=仓库）

只依赖 Python 标准库。
"""
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

if sys.stdout:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))  # 默认仓库根 = modules/agent 上两级
DEFAULT_CFG = os.path.join(HERE, "agent.config.json")
DEFAULT_SYSTEM = (
    "你是一个严谨的编码助手，负责修改一个 QQ 聊天机器人框架的代码。"
    "先读 AGENTS.md 了解项目，再动手。改动要小、可编译、符合项目约定。"
    "所有文件用 UTF-8；含中文的 .ps1 必须存成 UTF-8 带 BOM。"
    "不要写死品牌词（爸爸/女儿/鲸鱼娘/霖雨/石丰），称呼走 config.json 的配置项。"
    "完成后用 finish 汇报你改了哪些文件、做了什么、如何验证。"
)


def log(msg):
    line = "[%s] %s" % (time.strftime("%F %T"), msg)
    print(line, flush=True)
    try:
        with open(os.path.join(HERE, "agent.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default


def load_config():
    cfg = load_json(DEFAULT_CFG, {}) or {}
    cfg["repo_dir"] = str(cfg.get("repo_dir") or "").strip() or REPO
    cfg["relay_dir"] = str(cfg.get("relay_dir") or "").strip() or os.path.join(cfg["repo_dir"], "relay")
    cfg["backend"] = str(cfg.get("backend") or "local").strip()
    cfg["poll_interval"] = int(cfg.get("poll_interval") or 5)
    cfg["max_steps"] = int(cfg.get("max_steps") or 25)
    cfg["allow_shell"] = bool(cfg.get("allow_shell", False))
    roots = [str(x) for x in (cfg.get("allowed_roots") or []) if str(x).strip()]
    cfg["allowed_roots"] = roots or [cfg["repo_dir"]]
    cfg["opencode_cmd"] = str(cfg.get("opencode_cmd") or "opencode run")
    cfg["base_url"] = str(cfg.get("base_url") or "").strip()
    cfg["api_key"] = str(cfg.get("api_key") or "").strip()
    cfg["model"] = str(cfg.get("model") or "").strip()
    cfg["system_prompt"] = str(cfg.get("system_prompt") or "").strip() or DEFAULT_SYSTEM
    # 从仓库 config.json / .env 回退模型与密钥，省得配两遍
    repo_cfg = load_json(os.path.join(cfg["repo_dir"], "config.json"), {}) or {}
    env = _read_env(os.path.join(cfg["repo_dir"], ".env"))
    if not cfg["base_url"]:
        cfg["base_url"] = repo_cfg.get("deepseek_base") or env.get("DEEPSEEK_BASE") \
            or "https://api.deepseek.com"
    if not cfg["api_key"]:
        cfg["api_key"] = repo_cfg.get("deepseek_api_key") or env.get("DEEPSEEK_API_KEY") or ""
    if not cfg["model"]:
        cfg["model"] = repo_cfg.get("model") or "deepseek-chat"
    return cfg


def _read_env(path):
    env = {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            for ln in f:
                ln = ln.strip()
                if not ln or ln.startswith("#") or "=" not in ln:
                    continue
                k, v = ln.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return env


# ------------------------------------------------------------------ 文件工具

def _safe_path(path, roots):
    p = os.path.expanduser(str(path or "").strip())
    if not p:
        return None
    if not os.path.isabs(p):
        p = os.path.join(roots[0] if roots else REPO, p)
    p = os.path.normpath(p)
    real = os.path.realpath(p)
    for root in roots:
        r = os.path.realpath(os.path.expanduser(root))
        if real == r or real.startswith(r.rstrip(os.sep) + os.sep):
            return p
    return None


def tool_list_dir(args, roots):
    p = _safe_path(args.get("path", "."), roots)
    if not p:
        return "路径不在允许范围内"
    if not os.path.isdir(p):
        return "不是目录：%s" % p
    out = []
    for name in sorted(os.listdir(p))[:200]:
        full = os.path.join(p, name)
        out.append(name + ("/" if os.path.isdir(full) else ""))
    return "\n".join(out) or "(空目录)"


def tool_read_file(args, roots):
    p = _safe_path(args.get("path"), roots)
    if not p or not os.path.isfile(p):
        return "文件不存在或不在允许范围内"
    with open(p, "r", encoding="utf-8-sig", errors="replace") as f:
        lines = f.readlines()
    start = max(1, int(args.get("start") or 1))
    end = int(args.get("end") or 0) or len(lines)
    end = min(end, len(lines))
    out = []
    for i in range(start, end + 1):
        out.append("%d: %s" % (i, lines[i - 1].rstrip("\n")))
    return "\n".join(out) or "(空)"


def tool_write_file(args, roots):
    p = _safe_path(args.get("path"), roots)
    if not p:
        return "路径不在允许范围内"
    content = args.get("content")
    if content is None:
        return "缺少 content"
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    return "已写入 %s（%d 字符）" % (p, len(content))


def tool_replace_in_file(args, roots):
    p = _safe_path(args.get("path"), roots)
    if not p or not os.path.isfile(p):
        return "文件不存在或不在允许范围内"
    old = args.get("old", "")
    new = args.get("new", "")
    if not old:
        return "缺少 old"
    with open(p, "r", encoding="utf-8-sig", errors="replace") as f:
        src = f.read()
    cnt = src.count(old)
    if cnt == 0:
        return "未找到要替换的内容（old 必须与文件中的原文完全一致，含缩进）"
    if cnt > 1 and not args.get("all"):
        return "old 匹配到 %d 处，请在 old 里多带点上下文，或设 all=true 全部替换" % cnt
    src = src.replace(old, new) if args.get("all") else src.replace(old, new, 1)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(src)
    return "已替换 %s（%d 处）" % (p, cnt)


def tool_run_shell(args, roots):
    cmd = str(args.get("command") or "")
    if not cmd:
        return "缺少 command"
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            cwd=roots[0] if roots else REPO,
            capture_output=True, timeout=int(args.get("timeout") or 120),
        )
        out = (r.stdout or b"").decode("utf-8", "replace")
        err = (r.stderr or b"").decode("utf-8", "replace")
        return ("exit=%d\n" % r.returncode) + (out or "") + (("\n[stderr]\n" + err) if err else "")
    except Exception as e:
        return "执行失败：%s" % e


def _tools_schema(allow_shell):
    t = [
        {"type": "function", "function": {
            "name": "list_dir", "description": "列出目录内容",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string", "description": "相对仓库根的路径，默认 ."}},
                "required": []}}},
        {"type": "function", "function": {
            "name": "read_file", "description": "读取文本文件的指定行范围",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"},
                "start": {"type": "integer", "description": "起始行(1起)，可选"},
                "end": {"type": "integer", "description": "结束行，可选"}},
                "required": ["path"]}}},
        {"type": "function", "function": {
            "name": "write_file", "description": "新建或整体覆盖一个文本文件",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"}},
                "required": ["path", "content"]}}},
        {"type": "function", "function": {
            "name": "replace_in_file", "description": "把文件中的 old 精确替换成 new（推荐的小改动方式）",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"},
                "old": {"type": "string"},
                "new": {"type": "string"},
                "all": {"type": "boolean", "description": "替换全部匹配，默认只替第一处"}},
                "required": ["path", "old", "new"]}}},
        {"type": "function", "function": {
            "name": "finish", "description": "完成任务并汇报结果",
            "parameters": {"type": "object", "properties": {
                "summary": {"type": "string", "description": "改了什么、如何验证"}},
                "required": ["summary"]}}},
    ]
    if allow_shell:
        t.insert(4, {"type": "function", "function": {
            "name": "run_shell", "description": "在仓库根执行一条 PowerShell 命令（谨慎）",
            "parameters": {"type": "object", "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer"}},
                "required": ["command"]}}})
    return t


def _post_chat(base_url, api_key, payload, timeout=180):
    url = base_url.rstrip("/") + "/chat/completions"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + api_key}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def run_local(task, cfg):
    """内置编码 agent：OpenAI 兼容 function calling 循环。"""
    if not cfg["api_key"]:
        return "（没配 api_key：请在 agent.config.json 或仓库 config.json 填 deepseek_api_key）"
    roots = [os.path.abspath(os.path.expanduser(x)) for x in cfg["allowed_roots"]]
    allow_shell = bool(cfg["allow_shell"])
    tools = _tools_schema(allow_shell)
    dispatch = {"list_dir": tool_list_dir, "read_file": tool_read_file,
                "write_file": tool_write_file, "replace_in_file": tool_replace_in_file,
                "run_shell": tool_run_shell}
    messages = [
        {"role": "system", "content": cfg["system_prompt"]},
        {"role": "user", "content": "仓库根目录：%s\n\n任务：%s" % (cfg["repo_dir"], task)},
    ]
    last_text = ""
    for step in range(int(cfg["max_steps"])):
        payload = {"model": cfg["model"], "messages": messages,
                   "tools": tools, "tool_choice": "auto", "temperature": 0.2}
        try:
            resp = _post_chat(cfg["base_url"], cfg["api_key"], payload)
        except urllib.error.HTTPError as e:
            return "模型接口报错 HTTP %s：%s" % (e.code, e.read().decode("utf-8", "replace")[:300])
        except Exception as e:
            return "调用模型失败：%s" % e
        try:
            msg = resp["choices"][0]["message"]
        except Exception:
            return "模型返回异常：%s" % json.dumps(resp, ensure_ascii=False)[:300]
        calls = msg.get("tool_calls") or []
        if msg.get("content"):
            last_text = msg["content"]
        messages.append({"role": "assistant", "content": msg.get("content") or "",
                         "tool_calls": calls} if calls else
                        {"role": "assistant", "content": msg.get("content") or ""})
        if not calls:
            return last_text or "（agent 没有输出）"
        for c in calls:
            fn = c.get("function", {})
            name = fn.get("name")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            log("  step %d -> %s(%s)" % (step + 1, name, _brief(args)))
            if name == "finish":
                return str(args.get("summary") or "").strip() or "（完成，但没给摘要）"
            handler = dispatch.get(name)
            result = handler(args, roots) if handler else "未知工具：%s" % name
            messages.append({"role": "tool", "tool_call_id": c.get("id", ""),
                             "content": str(result)[:8000]})
    return (last_text or "") + "\n（达到 max_steps=%d 上限，任务可能未完成）" % cfg["max_steps"]


def _brief(args):
    s = json.dumps(args, ensure_ascii=False)
    return s[:160] + ("…" if len(s) > 160 else "")


def run_opencode(task, cfg):
    """调用外部 opencode CLI。"""
    cmd = cfg["opencode_cmd"].split()
    try:
        r = subprocess.run(cmd + [task], cwd=cfg["repo_dir"],
                           capture_output=True, timeout=1800)
        out = (r.stdout or b"").decode("utf-8", "replace").strip()
        err = (r.stderr or b"").decode("utf-8", "replace").strip()
        if r.returncode != 0 and not out:
            return "opencode 退出码 %d\n%s" % (r.returncode, err[:1000])
        return (out or err or "(opencode 无输出)")[:8000]
    except FileNotFoundError:
        return "找不到 opencode 命令。请先安装 opencode，或把 backend 改成 local。"
    except Exception as e:
        return "运行 opencode 失败：%s" % e


# ------------------------------------------------------------------ broker 主循环

def process_task(seq, task, cfg):
    log("处理任务 #%s：%s" % (seq, task[:100]))
    if cfg["backend"] == "opencode":
        answer = run_opencode(task, cfg)
    else:
        answer = run_local(task, cfg)
    outbox = os.path.join(cfg["relay_dir"], "outbox")
    os.makedirs(outbox, exist_ok=True)
    with open(os.path.join(outbox, "%s.txt" % seq), "w", encoding="utf-8", newline="") as f:
        f.write(answer or "（无输出）")
    log("已答复 #%s（%d 字符）" % (seq, len(answer or "")))


def main():
    cfg = load_config()
    inbox = os.path.join(cfg["relay_dir"], "inbox")
    outbox = os.path.join(cfg["relay_dir"], "outbox")
    os.makedirs(inbox, exist_ok=True)
    os.makedirs(outbox, exist_ok=True)
    log("broker 启动 | backend=%s | repo=%s | relay=%s"
        % (cfg["backend"], cfg["repo_dir"], cfg["relay_dir"]))
    while True:
        try:
            for name in sorted(os.listdir(inbox)):
                if not name.endswith(".txt"):
                    continue
                seq = name[:-4]
                if os.path.exists(os.path.join(outbox, "%s.txt" % seq)):
                    continue
                task = open(os.path.join(inbox, name), "r",
                            encoding="utf-8-sig", errors="replace").read().strip()
                if not task:
                    continue
                try:
                    process_task(seq, task, cfg)
                except Exception as e:
                    log("任务 #%s 出错：%s" % (seq, e))
                    try:
                        with open(os.path.join(outbox, "%s.txt" % seq), "w",
                                  encoding="utf-8", newline="") as f:
                            f.write("处理出错：%s" % e)
                    except Exception:
                        pass
        except Exception as e:
            log("轮询出错：%s" % e)
        time.sleep(int(cfg["poll_interval"]))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("broker 退出")
