#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置向导 GUI 的后端：把界面填写的值合并进 config.json。

由 setup.ps1 调用：
  1) 界面把各字段写到 _wizard.json（扁平 JSON）
  2) 本脚本读取 配置模板.json + 已有 config.json + _wizard.json，
     合并后写入 config.json（保留模板中其余字段），最后删除 _wizard.json。
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(BASE, "配置模板.json")
CONFIG = os.path.join(BASE, "config.json")
WIZ = os.path.join(BASE, "_wizard.json")


def load(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def main():
    base = load(TEMPLATE)
    if base is None:
        print("找不到 配置模板.json，无法写入配置。", file=sys.stderr)
        return 1
    existing = load(CONFIG) or {}
    values = load(WIZ)
    if values is None:
        print("找不到 _wizard.json。", file=sys.stderr)
        return 1

    base.update(existing)
    base.update(values)

    # owner_qq 规范化：单个值也转成列表
    v = base.get("owner_qq")
    if isinstance(v, str):
        base["owner_qq"] = [v] if v.strip() else []
    elif isinstance(v, list):
        base["owner_qq"] = [str(x).strip() for x in v if str(x).strip()]
    else:
        base["owner_qq"] = []

    # 根据 Key 是否存在，自动推导开关
    base["t2i"] = bool(base.get("t2i_api_key"))
    base["asr"] = bool(base.get("asr_api_key"))
    base["avatar"] = bool(base.get("avatar_api_key"))
    base.pop("_comment", None)

    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(base, f, ensure_ascii=False, indent=2)

    try:
        os.remove(WIZ)
    except Exception:
        pass

    print("config.json written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
