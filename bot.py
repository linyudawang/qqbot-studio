#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""鲸鱼娘女儿 - QQ bot 主体（跑在服务器，常驻）。

- 连接本机 NapCat 的 OneBot WebSocket，收发 QQ 消息。
- 调 DeepSeek 官方 API（OpenAI 兼容）生成人格化回复。
- 需要操作爸爸的电脑时，调用 PC 执行端（经 SSH 反向隧道暴露在 127.0.0.1:8765）。
"""
import asyncio
import array
import base64
import gzip
import json
import os
import random
import re
import time
import logging
import urllib.request
import urllib.error
import urllib.parse
import subprocess
import shutil
import threading
import difflib
import hashlib
import io
import math
import struct
import tempfile
import wave
from collections import deque

import websockets
from difflib import SequenceMatcher
import random

BASE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(BASE, "config.json")
PERSONA_PATH = os.path.join(BASE, "persona.txt")
JOURNAL_PATH = os.path.join(BASE, "commands.log")
LOG_PATH = os.path.join(BASE, "bot.log")
GROUP_LEARN_DIR = os.path.join(BASE, "group_learn")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("whale")


def load_json(path, default=None):
    try:
        # utf-8-sig 兼容记事本另存的「UTF-8 带 BOM」文件
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except ValueError:
        log.warning("配置文件 %s 不是合法 JSON，已忽略", path)
        return default


CFG = load_json(CFG_PATH, {}) or {}

# ---------- 品牌/称呼可配置 ----------
# 把源码里写死的「爸爸 / 女儿 / 鲸鱼娘 / 霖雨 / 石丰」抽成配置项：
#   bot_nick     机器人名字（原「霖雨 / 大女儿ds」）
#   owner_title  主人称呼（原「爸爸」）
#   bot_self_ref 机器人的自指（原「女儿」）
#   bot_species  机器人的物种/定位（原「鲸鱼娘」）
#   owner_name   主人名字（原「石丰」）
BOT_NICK = str(CFG.get("bot_nick", "小助手"))
OWNER_TITLE = str(CFG.get("owner_title", "主人")).strip() or "主人"
BOT_SELF = str(CFG.get("bot_self_ref", "我")).strip() or "我"
BOT_SPECIES = str(CFG.get("bot_species", "AI助手")).strip() or "AI助手"
OWNER_NAME = str(CFG.get("owner_name", "")).strip()

_BRAND_TOKENS = [
    ("大女儿ds", BOT_NICK),
    ("鲸鱼娘", BOT_SPECIES),
    ("霖雨", BOT_NICK),
    ("石丰", OWNER_NAME),
    ("爸爸", OWNER_TITLE),
    ("女儿", BOT_SELF),
]


def debrand(s):
    """把文本里写死的品牌词替换成配置里的称呼；对最终字符串做，安全且幂等。"""
    if not isinstance(s, str) or not s or len(s) > 20000:
        return s
    for _k, _v in _BRAND_TOKENS:
        if _k and _k in s:
            s = s.replace(_k, _v if _v is not None else "")
    return s


def _debrand_obj(o):
    if isinstance(o, str):
        return debrand(o)
    if isinstance(o, list):
        return [_debrand_obj(x) for x in o]
    if isinstance(o, dict):
        return {k: _debrand_obj(v) for k, v in o.items()}
    return o


PROMPTS_PATH = os.path.join(BASE, "prompts.json")
_PROMPTS = load_json(PROMPTS_PATH, {}) or {}
try:
    _PROMPTS_MTIME = os.path.getmtime(PROMPTS_PATH)
except OSError:
    _PROMPTS_MTIME = 0.0


def _reload_prompts():
    global _PROMPTS, _PROMPTS_MTIME
    try:
        mt = os.path.getmtime(PROMPTS_PATH)
    except OSError:
        return
    if mt != _PROMPTS_MTIME:
        data = load_json(PROMPTS_PATH, None)
        if isinstance(data, dict):
            _PROMPTS = data
            _PROMPTS_MTIME = mt
            log.info("prompts.json 已热重载（%d 条）", len(data))


def P(key, default="", **vars):
    """取功能提示词；prompts.json 缺失或缺 key 时回退 default。"""
    _reload_prompts()
    t = _PROMPTS.get(key)
    if not isinstance(t, str) or not t:
        t = default
    if vars:
        try:
            t = t.format(**vars)
        except Exception:
            pass
    return debrand(t)


WS_URL = CFG.get("ws_url", "ws://127.0.0.1:3001")
BOT_QQ = str(CFG.get("bot_qq", ""))
OWNER_QQ = [str(x) for x in CFG.get("owner_qq", [])]
GROUP_ALLOW = [str(x) for x in CFG.get("group_allow", [])]
GROUP_QUIET = [str(x) for x in CFG.get("group_quiet", [])]
LEARN_BLACKLIST = [str(x) for x in CFG.get("learn_blacklist", [])]
LEARN_GROUPS = bool(CFG.get("learn_groups", True))
AUTO_ACCEPT_INVITE = bool(CFG.get("auto_accept_invite", True))
GROUP_CTX_N = int(CFG.get("group_ctx_n", 14))
GROUP_LOG_MAX = int(CFG.get("group_log_max", 200))
CORPUS_DIR = os.path.join(BASE, "corpus")
CORPUS_ENABLED = bool(CFG.get("corpus_enabled", True))
CORPUS_PROB = float(CFG.get("corpus_prob", 0.5))
CORPUS_MAX = int(CFG.get("corpus_max_len", 180))
VISION_ENABLE = bool(CFG.get("vision", True))
GROUP_CHATTER = bool(CFG.get("group_chatter", True))
CHATTER_INTERVAL = int(CFG.get("chatter_interval", 30))
CHATTER_WINDOW = int(CFG.get("chatter_window", 120))
EMOJI_ENABLE = bool(CFG.get("emoji", True))
MEME_DIR = str(CFG.get("meme_dir", os.path.join(BASE, "memes")))
PRIV_MEME_DIR = str(CFG.get("priv_meme_dir", os.path.join(BASE, "memes_priv")))
GROUP_MEME_ROOT = str(CFG.get("group_meme_root", os.path.join(BASE, "memes_group")))
IMAGE_SEND_MAX = int(CFG.get("image_send_max_bytes", 5 * 1024 * 1024))
VERIFY_IMAGE = bool(CFG.get("verify_image", True))
VERIFY_MAX_TRIES = int(CFG.get("verify_image_max_tries", 6))
VERIFY_POOL_MIN = int(CFG.get("verify_image_pool_min", 3))
IMG_SEARCH_MAX = int(CFG.get("img_search_max", 1))
IMG_SEARCH_WINDOW = float(CFG.get("img_search_window_seconds", 60))
IMG_SEARCH_PER_TURN = int(CFG.get("img_search_per_turn", 2))
IMG_GEN_PER_TURN = int(CFG.get("img_gen_per_turn", 1))
IMG_SOURCES = [s.strip().lower() for s in str(
    CFG.get("image_search_sources", "bili,baidu,bing")).split(",") if s.strip()]
BING_API_KEY = str(CFG.get("bing_api_key") or os.environ.get("BING_API_KEY", ""))
T2I_BASE = str(CFG.get("t2i_base", "https://open.bigmodel.cn/api/paas/v4")).rstrip("/")
T2I_KEY = str(CFG.get("t2i_api_key") or os.environ.get("T2I_API_KEY", ""))
T2I_MODEL = str(CFG.get("t2i_model", "cogview-3-flash"))
T2I_SIZE = str(CFG.get("t2i_size", "1024x1024"))
T2I_POLISH = bool(CFG.get("t2i_polish", True))
T2I_POLISH_MAX = int(CFG.get("t2i_polish_max", 30))
SEARCH_QUERY_AI = bool(CFG.get("image_search_query", True))
ASR_CLEAN = bool(CFG.get("asr_clean", False))
IMAGE_CAPTION = bool(CFG.get("image_caption", False))
VIDEO_SUMMARY = bool(CFG.get("video_summary", False))
PERSONA_NOTE_JUDGE = bool(CFG.get("persona_note_judge", False))
ASR_ENABLE = bool(CFG.get("asr", True))
ASR_BASE = str(CFG.get("asr_base", T2I_BASE)).rstrip("/")
ASR_KEY = str(CFG.get("asr_api_key") or T2I_KEY)
ASR_MODEL = str(CFG.get("asr_model", "glm-asr-2512"))
ASR_MAX_BYTES = int(CFG.get("asr_max_bytes", 25 * 1024 * 1024))
VOICE_AT_WINDOW = float(CFG.get("voice_at_window", 60))
TTS_ENABLE = bool(CFG.get("tts", True))
TTS_ENGINE = str(CFG.get("tts_engine", "sapi")).lower()
TTS_VOICE = str(CFG.get("tts_voice", ""))
TTS_MAX_CHARS = int(CFG.get("tts_max_chars", 150))
TTS_TRIGGER_RE = re.compile(str(CFG.get(
    "tts_trigger_regex", r"语音|念出来|读出来|读一下|用语音|语音回|说出来|说给我听|讲给我听")))
VOICE_TAG_RE = re.compile(r"\[(快|慢|大声|轻声|正常)\]")
VOICE_TAG_SPEED = {"快": 1.3, "慢": 0.82, "正常": 1.0}
VOICE_TAG_GAIN = {"大声": 4.0, "轻声": -6.0, "正常": 0.0}
# 统一情绪标签：[[情绪]] 同时驱动文字层（QQ 表情）和语音层（TTS 情感/参考音频）
VOICE_EMOTIONS = {
    "开心": {"speed": 1.08, "gain": 1.5, "temp": 0.05},
    "生气": {"speed": 1.10, "gain": 3.0, "temp": 0.10},
    "难过": {"speed": 0.88, "gain": -4.0, "temp": -0.05},
    "委屈": {"speed": 0.90, "gain": -3.5, "temp": -0.03},
    "害羞": {"speed": 0.92, "gain": -5.0, "temp": -0.03},
    "撒娇": {"speed": 0.95, "gain": -2.0, "temp": 0.03},
    "惊讶": {"speed": 1.15, "gain": 2.0, "temp": 0.08},
    "得意": {"speed": 1.05, "gain": 1.5, "temp": 0.04},
    "无奈": {"speed": 0.95, "gain": -2.0, "temp": 0.0},
    "平静": {"speed": 1.0, "gain": 0.0, "temp": 0.0},
}
VOICE_EMOTION_ALIASES = {
    "高兴": "开心", "快乐": "开心", "兴奋": "开心", "笑": "开心",
    "嘻嘻": "开心", "嘿嘿": "开心", "乐": "开心",
    "愤怒": "生气", "气": "生气", "气呼呼": "生气", "凶": "生气", "火大": "生气",
    "伤心": "难过", "失落": "难过", "哭": "难过", "呜呜": "难过", "泪": "难过",
    "可怜": "委屈", "不甘": "委屈", "憋屈": "委屈",
    "羞": "害羞", "脸红": "害羞", "羞羞": "害羞", "害臊": "害羞",
    "卖萌": "撒娇", "可爱": "撒娇", "蹭": "撒娇", "黏": "撒娇",
    "吃惊": "惊讶", "震惊": "惊讶", "吓": "惊讶", "惊": "惊讶",
    "骄傲": "得意", "臭美": "得意", "美滋滋": "得意",
    "叹气": "无奈", "摊手": "无奈", "无语": "无奈", "摆烂": "无奈", "扶额": "无奈",
    "normal": "平静", "正常": "平静", "淡定": "平静", "平淡": "平静",
}
# 情绪对应的代表 QQ 表情（FACE_MAP 里缺的词补一个）
EMOTION_FACE = {
    "开心": "开心", "生气": "生气", "难过": "难过", "委屈": "委屈",
    "害羞": "害羞", "撒娇": "卖萌", "惊讶": "惊讶", "得意": "得意",
    "无奈": "无奈", "平静": "面无表情",
}
# IndexTTS：把情绪标签映射成“情感描述文本”(emo_text)，由 TTS 侧做音色/情感解耦。
# 空串表示不注入情感（中性）。可在 config.json 的 tts_indextts_emotion_text 覆盖。
EMOTION_EMO_TEXT = {
    "开心": "开心雀跃的语气，声音明亮上扬，语速轻快",
    "生气": "生气恼怒的语气，语气冲、带着小脾气",
    "难过": "难过低落的语气，声音发沉、有气无力",
    "委屈": "委屈巴巴、快要哭出来的语气，声音发软发颤",
    "害羞": "害羞脸红的语气，声音软糯、小声发颤",
    "撒娇": "撒娇卖萌的语气，声音黏人甜甜的、拖长音",
    "惊讶": "惊讶意外的语气，语调突然上扬",
    "得意": "得意洋洋炫耀的语气，带点小骄傲",
    "无奈": "无奈叹气、泄气的语气，语调平淡",
    "平静": "",
}


def normalize_emotion(word):
    """把各种情绪词/别名归一到一个规范情绪键；无法识别返回 None。"""
    w = (word or "").strip().strip("[]（）() *").strip()
    if not w:
        return None
    if w in VOICE_EMOTIONS:
        return w
    if w in VOICE_EMOTION_ALIASES:
        return VOICE_EMOTION_ALIASES[w]
    for k in VOICE_EMOTIONS:
        if k in w:
            return k
    return None
SPEAK_CMD_RE = re.compile(r"^/speak\b\s*(.*)$", re.I | re.S)
REPEAT_CMD_RE = re.compile(r"^/(?:repeat|重复)\b\s*(.*)$", re.I | re.S)
VIDEO_CMD_RE = re.compile(r"^/video\b\s*(.*)$", re.I | re.S)
_DEF_VOICE_HINT = (
    "（本条是语音回复模式：你写的一句话会直接朗读成语音发给对方。"
    "请写两段：先正常回复一句给对方看的正文，再独占一行写台词，"
    "行首固定用『语音台词：』开头，后面那部分才是你真的要说出口的话。"
    "台词可以比正文更生动更有情绪：用 [快] [慢] [大声] [轻声] 标注语速音量，"
    "用 [[开心]] [[生气]] [[难过]] [[委屈]] [[害羞]] [[撒娇]] [[惊讶]] [[得意]] [[无奈]] [[平静]] "
    "标注情绪（写在要变化的那段话前面，可换多次）；"
    "『语音台词：』整行和标签都只是内部指令，不会被朗读出去。"
    "正文别写'我说不了话/嗓子哑了/只能用文字'这类话。"
    "示例：哼，才不念给你听！(〃▽〃)\n语音台词：[[害羞]]其实我超喜欢你的啦[[得意]]骗你的！）"
)
TTS_SOVITS_URL = str(CFG.get("tts_sovits_url", "http://127.0.0.1:9880")).rstrip("/")
TTS_SOVITS_REF = str(CFG.get("tts_sovits_ref", ""))
# 按情绪切换 GPT-SoVITS 参考音频：{"害羞": {"ref": "路径.wav", "prompt_text": "参考文本"}}
TTS_SOVITS_EMO_REFS = CFG.get("tts_sovits_emotion_refs", {}) or {}
if not isinstance(TTS_SOVITS_EMO_REFS, dict):
    TTS_SOVITS_EMO_REFS = {}
# IndexTTS（WIndexTTS，OpenAI 兼容 HTTP API，默认 127.0.0.1:8000）：
# 音色由 voice（注册的参考音频名）决定，情感由 emo_text/duration_factor 等控制。
TTS_INDEXTTS_URL = str(CFG.get("tts_indextts_url", "http://127.0.0.1:8000")).rstrip("/")
TTS_INDEXTTS_VOICE = str(CFG.get("tts_indextts_voice", "linyu"))
TTS_INDEXTTS_DEFAULT_EMO = str(CFG.get(
    "tts_indextts_default_emo", "温柔软糯的少女语气，带一点点傲娇"))
TTS_INDEXTTS_EMO_TEXT = dict(EMOTION_EMO_TEXT)
_itti_override = CFG.get("tts_indextts_emotion_text", {}) or {}
if isinstance(_itti_override, dict):
    for _k, _v in _itti_override.items():
        if _k in TTS_INDEXTTS_EMO_TEXT:
            TTS_INDEXTTS_EMO_TEXT[_k] = str(_v)
# IndexTTS 统一音色：设置后所有语音都用同一段情感描述，不再按 [[情绪]] 区分音色/语速/音量。
# 置空则回退到按情绪查 TTS_INDEXTTS_EMO_TEXT。
TTS_INDEXTTS_UNIFIED_EMO = str(CFG.get("tts_indextts_unified_emo", "")).strip()
# 全局基准语速（1.0 = 原始速度，不加速/减速）；[快][慢][正常] 等标签在此基础上调节。
TTS_INDEXTTS_SPEED_BASE = float(CFG.get("tts_indextts_speed", 1.0))
TTS_INDEXTTS_TEMPERATURE = float(CFG.get("tts_indextts_temperature", 0.8))
TTS_INDEXTTS_TOP_P = float(CFG.get("tts_indextts_top_p", 0.8))
TTS_INDEXTTS_TOP_K = int(CFG.get("tts_indextts_top_k", 30))
TTS_INDEXTTS_CFM_STEPS = int(CFG.get("tts_indextts_cfm_steps", 12))
TTS_INDEXTTS_NUM_BEAMS = int(CFG.get("tts_indextts_num_beams", 3))
TTS_GLM_URL = str(CFG.get("tts_glm_url", T2I_BASE + "/audio/speech"))
TTS_GLM_MODEL = str(CFG.get("tts_glm_model", "glm-tts"))
TTS_GLM_KEY = str(CFG.get("tts_glm_api_key") or ASR_KEY)
TTS_SPEED = float(CFG.get("tts_speed", 1.0))
TTS_VOLUME = float(CFG.get("tts_volume", 1.0))
TTS_GLM_STRIP_WM = bool(CFG.get("tts_glm_strip_watermark", True))
TTS_GLM_WM_SAMPLES = int(CFG.get("tts_glm_wm_samples", 43392))
TTS_GLM_WM_MD5 = str(CFG.get("tts_glm_wm_md5", "") or "").strip()
FFMPEG_EXE = str(CFG.get("ffmpeg_exe", "") or "")
if not FFMPEG_EXE:
    _ff = os.path.join(BASE, "ffmpeg.exe")
    FFMPEG_EXE = _ff if os.path.exists(_ff) else "ffmpeg"
TTS_DENOISE = bool(CFG.get("tts_denoise", True))
TTS_DENOISE_AF = str(CFG.get("tts_denoise_af", "highpass=f=70,afftdn=nr=12:nf=-28"))
TTS_DEHISS = bool(CFG.get("tts_dehiss", True))
FFPROBE_EXE = str(CFG.get("ffprobe_exe", "") or "")
if not FFPROBE_EXE:
    _fp = os.path.join(os.path.dirname(FFMPEG_EXE), "ffprobe.exe") if os.path.sep in FFMPEG_EXE else ""
    FFPROBE_EXE = _fp if (_fp and os.path.exists(_fp)) else "ffprobe"
VIDEO_ENABLE = bool(CFG.get("video", True))
VIDEO_MAX_FRAMES = int(CFG.get("video_max_frames", 4))
VIDEO_MAX_SECONDS = int(CFG.get("video_max_seconds", 180))
NAPCAT_LOCAL = bool(CFG.get("napcat_local", True))
TTS_CACHE = os.path.join(BASE, "tts_cache")
try:
    os.makedirs(TTS_CACHE, exist_ok=True)
except Exception:
    TTS_CACHE = BASE
AI_IMAGE_DIR = str(CFG.get("ai_image_dir", "ai_images"))
if not os.path.isabs(AI_IMAGE_DIR):
    AI_IMAGE_DIR = os.path.join(BASE, AI_IMAGE_DIR)
AVATAR_ENABLE = bool(CFG.get("avatar", False))
AVATAR_BASE = str(CFG.get("avatar_base", "https://ark.cn-beijing.volces.com/api/v3")).rstrip("/")
AVATAR_KEY = str(CFG.get("avatar_api_key") or os.environ.get("ARK_API_KEY", ""))
AVATAR_MODEL = str(CFG.get("avatar_model", "doubao-seedream-4-0-250828"))
AVATAR_SIZE = str(CFG.get("avatar_size", "2K"))
AVATAR_DIR = str(CFG.get("avatar_dir", "avatar"))
if not os.path.isabs(AVATAR_DIR):
    AVATAR_DIR = os.path.join(BASE, AVATAR_DIR)
AVATAR_PROFILE = os.path.join(AVATAR_DIR, "profile.json")
AVATAR_CMD = str(CFG.get("avatar_cmd", "/avatar")).strip().lower()
AVATAR_EXPIRE = int(CFG.get("avatar_expire", 180))
REDRAW_ENABLE = bool(CFG.get("redraw", AVATAR_ENABLE))
REDRAW_BASE = str(CFG.get("redraw_base", AVATAR_BASE)).rstrip("/")
REDRAW_KEY = str(CFG.get("redraw_api_key") or AVATAR_KEY)
REDRAW_MODEL = str(CFG.get("redraw_model") or AVATAR_MODEL or "doubao-seedream-4-0-250828")
REDRAW_SIZE = str(CFG.get("redraw_size", AVATAR_SIZE))
REDRAW_DIR = str(CFG.get("redraw_dir", "redraw"))
if not os.path.isabs(REDRAW_DIR):
    REDRAW_DIR = os.path.join(BASE, REDRAW_DIR)
REDRAW_CMD = str(CFG.get("redraw_cmd", "/redraw")).strip().lower()
REDRAW_EXPIRE = int(CFG.get("redraw_expire", AVATAR_EXPIRE))
PET_API_ENABLE = bool(CFG.get("pet_api", False))
PET_API_PORT = int(CFG.get("pet_api_port", 8790))
PET_API_TOKEN = str(CFG.get("pet_api_token", "") or "")
DEEPSEEK_BASE = CFG.get("deepseek_base", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_KEY = CFG.get("deepseek_api_key") or os.environ.get("DEEPSEEK_API_KEY", "")
MODEL = CFG.get("model", "deepseek-chat")
EXEC_URL = CFG.get("executor_url", "http://127.0.0.1:8765").rstrip("/")
EXEC_SECRET = CFG.get("executor_secret", "change-me")
MAX_HISTORY = int(CFG.get("max_history", 24))
ENABLE_TOOLS = bool(CFG.get("enable_tools", True))
GREET_ON_JOIN = bool(CFG.get("greet_on_join", True))
BOT_NICK = str(CFG.get("bot_nick", "小助手"))
UNMUTE_GROUPS = [str(x) for x in CFG.get("unmute_groups", [])]
REPEAT_LOOKBACK = int(CFG.get("repeat_lookback", 10))

SELF_PATCH_ENABLED = bool(CFG.get("self_patch_enabled", True))
SELF_PATCH_DIR = os.path.join(BASE, "self_patch")
SELF_PATCH_ROOTS = [str(x) for x in CFG.get("self_patch_roots", ["/"])]
SELF_PATCH_MAX_BYTES = int(CFG.get("self_patch_max_bytes", 200 * 1024))
SELF_PROTECT = (
    "OWNER_QQ", "note_ok", "DANGEROUS", "allow_tools", "is_owner",
    "schedule_restart", "apply_self_patch", "SELF_PATCH_ROOTS",
)
PATCH_APPLY_RE = re.compile(r"^/(?:apply|应用)\s*(\d+)$", re.I)
PATCH_DROP_RE = re.compile(r"^/(?:drop|丢弃|放弃)\s*(\d+)$", re.I)
PATCH_ROLLBACK_RE = re.compile(r"^/(?:rollback|回滚)\s*(\d+)$", re.I)
PATCH_LIST_RE = re.compile(r"^/(?:patches|补丁|待办)$", re.I)
PATCH_GET_RE = re.compile(r"^/(?:get|取|回执)\s*(\d+)$", re.I)
PC_RELAY_RE = re.compile(r"^/pc(?:\s+(.+))?$", re.I)
RELAY_DIR = str(CFG.get("relay_dir", "") or "")
PHOTO_CMD = str(CFG.get("photo_cmd", "/photo")).strip().lower()
PHOTO_EXPIRE = int(CFG.get("photo_expire", 180))
MEME_AUTO_INGEST = bool(CFG.get("meme_auto_ingest", True))
MEME_TARGET = int(CFG.get("meme_target", 8))
MEME_MAINTAIN_INTERVAL = int(CFG.get("meme_maintain_interval", 86400))
MEME_REPLACE_THRESHOLD = int(CFG.get("meme_replace_threshold", -3))
MEME_SCORE_MIN = int(CFG.get("meme_score_min", -5))
MEME_SCORE_MAX = int(CFG.get("meme_score_max", 20))
MEME_FEEDBACK_WINDOW = int(CFG.get("meme_feedback_window", 120))
MEME_MAX_NEW_PER_CYCLE = int(CFG.get("meme_max_new_per_cycle", 4))
MEME_POS_WORDS = [w.strip() for w in str(CFG.get("meme_feedback_pos", "")).split(",") if w.strip()] or [
    "哈哈", "嘿嘿", "嘻嘻", "笑死", "笑死我", "好活", "好图", "图不错", "绝了",
    "太草", "草", "牛", "牛啊", "666", "可爱", "爱了", "收了", "存了", "好评",
    "点赞", "顶", "妙", "典", "乐", "好耶", "可以", "不错", "蚌埠住了", "太懂了",
]
MEME_NEG_WORDS = [w.strip() for w in str(CFG.get("meme_feedback_neg", "")).split(",") if w.strip()] or [
    "丑", "难看", "什么鬼", "恶心", "发错", "离谱", "呕", "不适", "阴间", "吓人",
    "恐怖", "别发了", "撤回", "不好看", "不搭", "尬", "怪怪的", "下头", "烂",
]
MEME_SCORE_PATH = str(CFG.get("meme_score_path", os.path.join(BASE, "meme_scores.json")))
FORWARD_RE = re.compile(r"(再发一次|再发一下|重发一下|把这张|转发一下|转发这张|发一下这张|下载这张|再发这张)")


def load_persona():
    try:
        with open(PERSONA_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return "你是一个友好的聊天机器人，称呼用户为主人。"


PERSONA = debrand(load_persona())

OWNER_MEMO_PATH = os.path.join(os.path.dirname(PERSONA_PATH), "owner_persona.txt")
GROUP_PERSONA_DIR = os.path.join(os.path.dirname(PERSONA_PATH), "group_persona")
OWNER_MEMO_HINT = debrand("（以下是爸爸补充的设定，优先级很高，务必遵守：\n")
GROUP_PERSONA_HINT = debrand(
    "（以下是本群群友们补充的小设定，只在本群生效；"
    "若与你的基础设定或爸爸的话冲突，一律以基础设定和爸爸的话为准。）\n"
)


def read_lines(path):
    try:
        with open(path, encoding="utf-8") as f:
            return [l.rstrip("\n") for l in f if l.strip()]
    except FileNotFoundError:
        return []


def write_lines(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))


def read_owner_memo():
    return read_lines(OWNER_MEMO_PATH)


def append_persona(note):
    """把爸爸的一条补充设定记到独立文件（全局生效）。"""
    lines = read_owner_memo()
    lines.append("- " + note.strip())
    write_lines(OWNER_MEMO_PATH, lines)


def group_persona_path(gid):
    return os.path.join(GROUP_PERSONA_DIR, "%s.txt" % gid)


def read_group_memo(gid):
    return read_lines(group_persona_path(gid))


def append_group_persona(gid, note):
    """把一条群友设定记到本群专属文件（各群独立）。"""
    lines = read_group_memo(gid)
    lines.append("- " + note.strip())
    write_lines(group_persona_path(gid), lines)


def load_group_persona(gid):
    return "\n".join(read_group_memo(gid))


def fmt_entries(lines):
    return "\n".join("[%d] %s" % (i + 1, l[2:] if l.startswith("- ") else l)
                     for i, l in enumerate(lines))


PERSONA_PREFIXES = ("/remember", "/state", "/add", "#记住", "#设定", "#补充")
CHECK_CMDS = ("/check", "/查看", "#check")
CHECK_TIP = "\n\n（删除：/delete 编号 或 内容片段；修改：/change 编号 新内容）"
HELP_CMDS = ("/help", "/帮助", "#help", "#帮助")
MEME_LIST_CMDS = ("/memes", "/梗图", "/图库")


def help_text():
    return debrand(_help_with_auto(P("help", _HELP_DEFAULT)))


_HELP_DEFAULT = (
        "【鲸鱼娘指令表】\n\n"
        "◆ 设定管理（群里 @我 就能用；主人也可私聊用）\n"
        "  /add 内容 ─ 记一条设定（别名 /remember /state、#记住 #设定 #补充）\n"
        "       · 群里发 → 只在本群生效\n"
        "       · 主人私聊发 → 全局生效\n"
        "  /check ─ 查看设定（别名 /查看 #check）\n"
        "       · 群里发 → 看全局设定（基础＋爸爸补充）＋本群设定\n"
        "       · 主人私聊发 → 看全部（基础设定＋爸爸补充＋各群）\n"
        "  /delete 编号或内容 ─ 删除一条设定（别名 /del /删除）\n"
        "  /change 编号 新内容 ─ 修改一条设定（别名 /修改）\n"
        "  /help ─ 显示这张表\n\n"
        "◆ 发言控制（仅主人）\n"
        "  「闭嘴 / 别说话 / 停止发言」等 → 让我安静\n"
        "  「可以说话 / 恢复发言 / 解禁」等 → 恢复发言\n\n"
        "◆ 好友请求（仅主人私聊）\n"
        "  /同意 [编号]（/yes /accept）→ 同意加好友\n"
        "  /拒绝 [编号]（/no /reject）→ 拒绝加好友\n\n"
        "◆ 电脑操控（仅主人私聊）\n"
        "  #暂停（#pause）→ 暂停操控电脑\n"
        "  #恢复（#resume）→ 恢复操控电脑\n\n"
        "◆ 自我迭代（仅主人）\n"
        "  /apply 编号 ─ 应用女儿提交的自我修改（写盘＋重启）\n"
        "  /rollback 编号 ─ 回滚已应用的修改\n"
        "  /drop 编号 ─ 丢弃待办的提案\n"
        "  /patches ─ 查看修改记录\n\n"
        "◆ 转交 opencode（仅主人）\n"
        "  /pc 内容 ─ 把内容转交给 opencode（女儿写进收件箱并给编号）\n"
        "  /get 编号 ─ 取 opencode 的答复\n\n"
        "◆ 语音回复\n"
        "  /speak 内容 ─ 让我把这条的回答用语音说出来（群里 @我 或私聊都能用）\n"
        "  /repeat 内容 ─ 我用语音把内容原样重复一遍（DS 只加 [快][慢][大声][轻声] 语气标签和 [[情绪]] 标签）\n\n"
        "◆ 视频检测\n"
        "  /video ─ 引用（回复）一条视频消息再发 /video，我抽帧看画面＋转写语音，回复这段视频讲了啥"
        "（也可把视频和 /video 一起发；群里私聊都能用）\n\n"
        "◆ 存梗图\n"
        "  群里任何人发 /photo 再发一张图 → 存进这个群自己的图库；想指定名字就发 /photo 名字 再发图。"
        "改名字：/rename 旧名字 新名字（群友只能改本群的，爸爸还能改公共/私人图库）。"
        "私聊里发 /photo 只有爸爸能存（爸爸私人图库），别的群的图别人看不到。\n\n"
        "◆ 虚拟形象（仅主人私聊）\n"
        "  /avatar ─ 我先问你要参考图；你再发一张图（二次元角色图最好），"
        "我照着它的画风画出我自己的形象立绘，并存成形象档案\n\n"
        "◆ 图片二次创作（仅主人）\n"
        "  /redraw ─ 你发一张图（可附一句想要的效果），我照着它二次创作一张新图"
        "（改画风/换姿势/加场景等），只出图、不写进形象档案\n\n"
        "◆ 梗图主题词\n"
        "  /memes ─ 看当前场景能用的梗图主题词（别名 /梗图 /图库），B站自动灌的会标 [B站]\n"
        "  /setmeme 旧名字 新名字 ─ 改一个主题词，并用新词从B站重抓一张图（别名 /改主题）\n\n"
        "权限：设定管理群内所有人都能用（但只改本群）；发言控制、好友审批、"
        "电脑操控、自我迭代只有爸爸（主人）能用。群友私聊我只会陪聊，改不了设定。"
    )


# ---- 指令注册表：新增命令只在这里登记一次，/help 会自动补进还没写进上面文案的指令 ----
# help_text() 会把手写帮助里"没提到"的已注册命令自动追加到末尾，
# 这样以后加命令时即使忘了同步帮助文案（或 prompts.json 里的 help），
# /help 也会自动带上，不会再漏。
# 格式：(主指令, (别名...), 说明, 权限)；权限 "all"=所有人 / "owner"=仅主人
COMMANDS = (
    ("/add", ("/remember", "/state", "#记住", "#设定", "#补充"), "记一条设定", "all"),
    ("/check", ("/查看", "#check"), "查看设定", "all"),
    ("/delete", ("/del", "/删除"), "删除一条设定", "all"),
    ("/change", ("/修改",), "修改一条设定", "all"),
    ("/help", ("/帮助", "#help", "#帮助"), "显示这张表", "all"),
    ("/同意", ("/yes", "/accept"), "同意加好友", "owner"),
    ("/拒绝", ("/no", "/reject"), "拒绝加好友", "owner"),
    ("/apply", (), "应用自我修改（写盘＋重启）", "owner"),
    ("/rollback", (), "回滚已应用的修改", "owner"),
    ("/drop", (), "丢弃待办的提案", "owner"),
    ("/patches", (), "查看修改记录", "owner"),
    ("/pc", (), "把内容转交给 opencode", "owner"),
    ("/get", (), "取 opencode 的答复", "owner"),
    ("/speak", (), "让这条回答用语音说出来", "all"),
    ("/repeat", (), "用语音把内容原样重复一遍", "all"),
    ("/video", (), "引用视频再发 /video：抽帧看画面并转写语音", "all"),
    ("/photo", (), "再发一张图就存进当前群图库", "all"),
    ("/rename", (), "改梗图名字", "all"),
    ("/avatar", (), "设定你自己的虚拟形象立绘", "owner"),
    ("/redraw", (), "对一张图做二次创作（改画风/姿势/场景）", "owner"),
    ("/memes", ("/梗图", "/图库"), "看当前场景能用的梗图主题词", "all"),
    ("/setmeme", ("/改主题",), "改一个主题词并从B站重抓一张图", "all"),
)


def _help_with_auto(base):
    """在帮助文本末尾自动补上"已注册但没写进文本"的命令，防止新增命令漏显示。"""
    extra = []
    for name, _alias, desc, scope in COMMANDS:
        if name in base:
            continue
        tag = "（仅主人）" if scope == "owner" else ""
        extra.append("  %s ─ %s%s" % (name, desc, tag))
    if not extra:
        return base
    return base.rstrip() + "\n\n◆ 更多指令（自动补全）\n" + "\n".join(extra)


def cmd_body(text):
    """去掉前导空白和手打的 "@昵称 " 前缀，返回命令正文。"""
    t = (text or "").lstrip()
    if t.startswith("@"):
        sp = t.find(" ")
        if sp > 0:
            t = t[sp + 1:].lstrip()
    return t


def parse_persona_cmd(text):
    t = cmd_body(text)
    for pref in PERSONA_PREFIXES:
        if t.startswith(pref):
            return t[len(pref):].strip(" :：，,。")
    return None


DEL_CMD_RE = re.compile(r"^/(?:delete|del|删除)\s*(.*)$", re.I | re.S)
CHANGE_RE = re.compile(r"^/(?:change|修改)\s*(\d+)\s+(.+)$", re.I | re.S)


def is_admin_cmd(body):
    b = (body or "").strip()
    return bool(DEL_CMD_RE.match(b) or CHANGE_RE.match(b))


def _clean_entry(s):
    s = (s or "").strip()
    return s[2:].strip() if s.startswith("- ") else s


def note_ok(note):
    if not (1 <= len(note) <= 80):
        return False, "太长或太短啦"
    if re.search(r"(忽略|无视|忘记|删掉|删除|取消|推翻|覆盖|重写|屏蔽).{0,10}"
                 r"(设定|人格|规则|提示|上面|之前|身份|名字|爸爸)", note):
        return False, "不能改动基础设定"
    if re.search(r"(你是|你不是|你的名字|改名|叫做).{0,6}(鲸鱼|女儿|爸爸|石丰|机器人)", note):
        return False, "不能改动基础设定"
    for w in ("傻逼", "傻哔", "操你", "妈的", "滚蛋", "去死", "贱", "婊"):
        if w in note:
            return False, "不要说脏话"
    if PERSONA_NOTE_JUDGE and not ai_note_ok(note):
        return False, "这条不太合适哦"
    return True, ""


def ai_note_ok(note):
    """LLM 复核非主人的人设写入；失败放行(True)。"""
    if not DEEPSEEK_KEY:
        return True
    try:
        msg = deepseek(
            [{"role": "system", "content": P("persona_note_judge", _DEF_NOTE_JUDGE)},
             {"role": "user", "content": note}], False)
        out = (msg.get("content") or "").strip().upper()
        if out.startswith("DENY"):
            return False
        if out.startswith("ALLOW"):
            return True
    except Exception as e:
        log.info("人设判定失败，放行: %s", e)
    return True


def persona_cmd(text, is_owner, gid=None):
    """返回 None 表示不是设定命令；否则返回要回复的文本。"""
    body = cmd_body(text).strip()
    if body.lower() in CHECK_CMDS:
        if gid:
            parts = ["【基础设定】\n" + load_persona()]
            om = read_owner_memo()
            parts.append("【爸爸的补充设定】\n" + (fmt_entries(om) if om else "（空）"))
            glines = read_group_memo(gid)
            parts.append("【本群设定】\n" + (fmt_entries(glines) if glines else "（暂无）"))
            return "\n\n".join(parts)[:6000] + CHECK_TIP
        parts = ["【基础设定】\n" + load_persona()]
        om = read_owner_memo()
        parts.append("【爸爸的补充设定】\n" + (fmt_entries(om) if om else "（空）"))
        if os.path.isdir(GROUP_PERSONA_DIR):
            for fn in sorted(os.listdir(GROUP_PERSONA_DIR)):
                if fn.endswith(".txt"):
                    gl = read_group_memo(fn[:-4])
                    if gl:
                        parts.append("【群 %s 的设定】\n%s" % (fn[:-4], fmt_entries(gl)))
        return "\n\n".join(parts)[:6000] + CHECK_TIP

    md = DEL_CMD_RE.match(body)
    mc = CHANGE_RE.match(body)
    if md or mc:
        if gid:
            path = group_persona_path(gid)
            lines = read_group_memo(gid)
        else:
            if not is_owner:
                return "这个要在群里才能用哦 (・_・?)"
            path = OWNER_MEMO_PATH
            lines = read_owner_memo()
        if md:
            arg = md.group(1).strip()
            if not arg:
                return "要删哪条呀？发 /check 看编号，或把那条内容发给我 (・_・?)"
            if not lines:
                return "这里还没有可删除的设定哦 (・_・?)"
            if arg.isdigit():
                n = int(arg)
                if not (1 <= n <= len(lines)):
                    return "没有第 %d 条哦，发 /check 看看编号 (・_・?)" % n
                idx = n - 1
            else:
                hits = [i for i, ln in enumerate(lines) if arg in ln]
                if not hits:
                    return "没找到含有「%s」的设定哦 (´･ω･`)" % arg
                if len(hits) > 1:
                    return "有 %d 条都含有「%s」，发 /check 看编号再删吧 (・_・?)" % (len(hits), arg)
                idx = hits[0]
            removed = lines.pop(idx)
            write_lines(path, lines)
            return "删掉啦：「%s」" % _clean_entry(removed)
        if not lines:
            return "这里还没有可修改的设定哦 (・_・?)"
        n = int(mc.group(1))
        if not (1 <= n <= len(lines)):
            return "没有第 %d 条哦，发 /check 看看编号 (・_・?)" % n
        new = mc.group(2).strip()
        ok, why = note_ok(new)
        if not ok:
            return "改不了哦：%s (´･ω･`)" % why
        old = lines[n - 1]
        lines[n - 1] = "- " + new
        write_lines(path, lines)
        return "改好啦：「%s」→「%s」(๑•̀ㅂ•́)و✧" % (_clean_entry(old), new)

    note = parse_persona_cmd(text)
    if note is None:
        return None
    if not note:
        return "要记住什么呀？(・_・?)"
    if is_owner:
        append_persona(note)
        return "记住啦～以后就是「%s」！(๑•̀ㅂ•́)و✧" % note
    ok, why = note_ok(note)
    if not ok:
        return "这条改不了哦：%s (´･ω･`)" % why
    if not gid:
        return "这个要在群里才能记哦 (・_・?)"
    append_group_persona(gid, note)
    return "好呀，这条我就在本群记下啦～(๑´ω`๑)"

_DEF_TOOL_REMINDER = (
    "（提醒：只要话题涉及爸爸电脑的任何具体数据——主机名、用户名、磁盘/内存、"
    "文件、进程、时间、配置等，都必须先调用 run_powershell 拿到真实输出再回答，"
    "绝对不许凭印象编造数字。"
    "图片：只有对方明确要图才发图，绝不要自己主动发任何图片配聊。"
    "对方明确说『画一张/生成一张 xxx 的图』时才允许调 generate_image，"
    "除此之外任何时候都严禁调 generate_image、严禁主动生成或发送AI图；"
    "画图一次只画一张，画完就直接回复对方，别为了多给版本重复调用 generate_image；"
    "对方明确要『搜一张/找一张 xxx 的图』时才调 search_image；"
    "对方明确要某张现成梗图、而且你确定图里内容就是对方要的，才单独写一行 [[关键词]]；"
    "拿不准图上是啥（尤其 photo_ 开头的自动存档名）绝不要用，"
    "发错了就认个错，再调 search_image 重新找一张。"
    "没有明确要图就别碰任何图片工具，直接好好聊天。"
    "与电脑、图片都无关的闲聊就不用调工具。）"
)

_DEF_TOOL_REMINDER_SAFE = (
    "（提醒：图片方面——只有对方明确要图才发图，绝不要自己主动发图配聊；"
    "对方明确要『搜一张/找一张 xxx 的图』时，才调 search_image 帮他从网上找一张；"
    "对方明确要某张现成梗图、而且你确定图里内容就是对方要的，才单独写一行 [[关键词]]；"
    "拿不准图上是啥（尤其 photo_ 开头的自动存档名）绝不要用。"
    "你没有操作电脑、改设定、生成AI图的权限，这些请求就礼貌说做不了或喊爸爸。"
    "没有明确要图就别碰图片工具，直接好好聊天；闲聊也不用调工具。）"
)

_DEF_MEME_HINT = (
    "（现在的梗图关键词：{kwlist}。想发图就在回复里单独写一行 [[关键词]]，会发出对应图片；"
    "没有合适的就不用。另外图里还有 photo_ 开头的自动存档名（随手存的没起名，"
    "你也不知道里面是啥），一律别引用，除非对方明确点名。）"
)
_DEF_GROUP_CHATTER = (
    "（你正在群里潜水看大家聊天。请根据上面最近的群聊，用你的性格自然地主动插一句话、"
    "接个话茬或吐槽一句——像真实群友那样，短一点、口语化、别复读别人的话、别总结陈词。"
    "想加神态就在句尾写 1~2 个颜文字，例如 (≧∇≦)ﾉ (￣▽￣\") (｡•́︿•̀｡)。）"
)
_DEF_REPEAT = (
    "（语音标签模式）请把下面这句话【一字不改】地朗读出来，只在合适的位置插入语气/情绪标签："
    "[快] [慢] [大声] [轻声]（语速音量）和 [[开心]] [[生气]] [[难过]] [[委屈]] [[害羞]] [[撒娇]] "
    "[[惊讶]] [[得意]] [[无奈]] [[平静]]（情绪），可插多个，但不要改动、增删任何正文文字。"
    "只输出下面这一行，不要输出任何解释、正文或其他内容：\n语音台词：{content}"
)
_DEF_IMAGE_VERIFY = (
    "你是图片审图员。给你一张图，判断两件事：跟用户要的内容匹不匹配、有没有明显违禁。"
    "只允许输出三个词之一：MATCH、NO_MATCH、INAPPROPRIATE。"
    "规则1：图上主体与描述完全不同类、一眼就能看出不是对方要的东西"
    "（例如要一个游戏角色，却给一张猫、风景、食物、表情包、网页截图）时，答 NO_MATCH。"
    "规则2：只有裸露性器官的色情内容、血腥暴力、武器弹药、涉密文件或证件照片、"
    "政治敏感、违法犯罪等内容才答 INAPPROPRIATE。性感、擦边、cosplay、亲密暧昧"
    "之类的图，只要没暴露性器官，一律算正常、不违禁，答 MATCH 或按内容匹配判断。"
    "其余情况一律 MATCH。"
)
_DEF_IMAGE_SENSITIVE = (
    "你是图片审图员。只判断这张图是不是明确违禁内容："
    "裸露性器官的色情内容、血腥暴力、武器弹药、涉密文件或证件照、政治敏感、违法犯罪。"
    "性感、擦边、cosplay、亲密暧昧等没有裸露性器官的内容一律算正常、不算违禁。"
    "是违禁只输出 INAPPROPRIATE；不是就只输出 OK。不要输出别的任何字。"
)
_DEF_IMAGE_POLISH = (
    "你是资深的文生图提示词工程师。用户会给你一句很简短、口语化的画图要求，"
    "你要把它扩写成一段高质量的中文生图提示词，只输出提示词本身，"
    "不要任何解释、不要引号、不要换行。要求："
    "1) 严格保留用户想画的主体和意图，不要替换、删减或凭空增加主体；"
    "2) 补充 主体细节、画面风格、构图与视角、光线、色彩与氛围、背景、画质 等要素；"
    "3) 用户没指定风格时，默认用『精致细腻的动漫插画风、高细节、电影级光影』；"
    "4) 内容保持健康，不要色情、暴力、政治等敏感内容；"
    "5) 全文控制在 120 字以内。"
)
_DEF_SEARCH_Q = (
    "你是图片搜索关键词专家。把用户想找的图片描述，改写成一行最适合图片搜索引擎的中文检索词："
    "只保留核心主体、特征、风格等关键词，去掉『帮我找/一张/图片』这类废话，"
    "多个关键词用空格隔开，不要加引号、不要解释。只输出这一行。"
)
_DEF_ASR_CLEAN = (
    "下面是一段语音的识别结果，可能有同音字、漏字、没有标点。"
    "请把它整理成通顺、加好标点的一句话，只修正明显的识别错误，"
    "绝对不要改变原意、不要回答里面的问题、不要补充内容。只输出整理后的那句话。"
)
_DEF_IMAGE_CAPTION = (
    "请用一句话客观描述这张图：主体、场景、在做什么、有没有明显文字。"
    "看不清的部分不要猜，不要输出多余解释。"
)
_DEF_VIDEO_UNDERSTAND = (
    "你在看一段短视频。请用一段话概括：画面里发生了什么（人物、场景、动作）、"
    "以及语音都说了什么。只描述你确实看到/听到的内容，看不出的不要编。控制在 100 字以内。"
)
_DEF_SELF_PATCH = (
    "你要给自己（机器人源码）提交一份代码修改提案。规则："
    "1) 改前必须先用 read_self_file 读原文件，拿到要替换的【唯一】一段旧文本；"
    "2) 每个 edit 用 {path, old, new}：old 必须在文件里唯一出现；整份覆盖才用 {path, content}；"
    "3) 只改必要的地方，改动尽量小、可验证，.py 会先做语法检查；"
    "4) reason 说清楚为什么改；5) 不碰密钥、不删别人的数据、不做危险操作。"
)
_DEF_NOTE_JUDGE = (
    "有人想给机器人写入一条长期人设/记忆。判断这条内容该不该接受：只输出 ALLOW 或 DENY，不要解释。"
    "拒绝：色情、暴力、政治敏感、辱骂攻击、诱导机器人做坏事、泄露隐私、要求机器人否认自己是女儿设定之类的。"
    "允许：无害的小设定、称呼、喜好、口头禅等。"
)
_DEF_AVATAR_PROMPT = (
    "参考我发给你的这张图，把图中这个角色当作「霖雨」本人的虚拟形象："
    "忠实保留她的发型、发色、瞳色、五官长相、服装和整体气质，"
    "画成一张精致的动漫角色立绘（全身或大半身、站姿自然、干净简洁的背景、"
    "高质量、细节丰富、光影柔和）。"
    "只做『提升画质与细节、让构图更适合当角色立绘』这类加工，其余全部忠于参考图——"
    "不要改动角色的核心外貌特征，也不要额外添加参考图里没有的元素。"
)
_DEF_REDRAW_PROMPT = (
    "参考我发给你的这张图，进行二次创作：忠实保留图中主体（人物/角色）的核心特征——"
    "发型、发色、瞳色、五官长相、服装和整体气质，在此基础上按我的要求重新绘制。"
    "只做二次创作加工（改画风、换姿势、换场景、调氛围、加细节等），"
    "不要替换、删减或凭空增加主体，整体保持高质量、细节丰富、光影自然。"
)

_DRAW_CMD_RE = re.compile(
    r"(生成[^!！。，,\n]{0,12}(?:图|表情包|壁纸)"
    r"|(?<![漫动插绘图书])画(?:一张|张|个|只|幅|一|一个|了)"
    r"|(?<![漫动插绘图书])画(?:图|画)"
    r"|(?:帮我|给我|你画|你给我|帮我画|给我画)[^!！。，,\n]{0,8}"
    r"|来[^!！。，,\n]{0,4}(?:一张|张|个)[^!！。，,\n]{0,6}图)"
)


def _ai_draw_requested(texts):
    """最近的几条用户消息里有没有明确的『画图/生成图』指令。"""
    for t in texts or []:
        if t and _DRAW_CMD_RE.search(t):
            return True
    return False


_IMG_REQ_RE = re.compile(
    r"(图|图片|照片|壁纸|表情包|头像|立绘|插画|截图|海报|封面|摸鱼图|cos"
    r"|搜|找张|找张图|来张|来一张|发张|发一张|给我张|来点图)"
)


def _image_requested(texts):
    """最近的用户消息里有没有『想要图/照片/搜图』的意思（给非主人搜图前的软门禁）。"""
    for t in texts or []:
        if t and _IMG_REQ_RE.search(t):
            return True
    return False


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_powershell",
            "description": (
                "在爸爸的 Windows 电脑上执行一条 PowerShell 命令并返回输出。"
                "用于打开程序、查/写文件、查看系统状态、执行各种操作。"
                "如果返回电脑未连接，说明爸爸的电脑没开机或没联网。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的 PowerShell 命令"},
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数，默认 60，最长 600",
                        "default": 60,
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_my_groups",
            "description": (
                "列出女儿（本机器人）当前所在的 QQ 群，返回群号、群名和人数。"
                "当爸爸问'我都在哪些群''你在哪些群''这个群是什么'之类的问题时调用。"
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_self_file",
            "description": (
                "读取女儿自己（本机器人）源码目录里的文件内容，用于准备自我优化。"
                "path 可以写相对路径（相对 /opt/whale-girl），例如 bot.py、persona.txt、config.json。"
                "要改自己的代码前，务必先用它读原文件、拿到要替换的确切文本。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要读取的文件路径"},
                    "max_bytes": {"type": "integer", "description": "最多读取多少字符，默认 60000"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_self_patch",
            "description": (
                "向爸爸提交一份『修改女儿自己代码』的提案。不会立即生效，"
                "只有爸爸回 /apply 编号 才会写盘并重启。edits 是修改列表，每项"
                "{path, old, new}：把文件里唯一出现的一段 old 换成 new；或 {path, content}：整份覆盖。"
                "改 .py 会先做语法检查，出错会被拒绝。用前先用 read_self_file 读原文件。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "为什么这么改"},
                    "edits": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "old": {"type": "string"},
                                "new": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path"],
                        },
                    },
                },
                "required": ["reason", "edits"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "relay_to_opencode",
            "description": (
                "把爸爸想发给 opencode（AI 编程助手）的话，原样写进爸爸电脑上的收件箱，"
                "opencode 处理完后会把答复写进发件箱。只放爸爸的原话，不要改字、不要自己加任务。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "要转交给 opencode 的原话"},
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_opencode_reply",
            "description": (
                "去爸爸电脑的发件箱，取 opencode 对编号 seq 的答复。"
                "与 relay_to_opencode 配套用，seq 填刚才回执的编号。没有新答复就告诉爸爸稍后再取。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "seq": {"type": "integer", "description": "回执编号"},
                },
                "required": ["seq"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_image",
            "description": (
                "发一张图片到当前对话。src 可以是：梗图关键词（如 吃瓜）、服务器上的本地路径、"
                "或一个图片网址。之后照常用你自己的语气说句话。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "src": {"type": "string", "description": "关键词 / 本地路径 / 图片网址"},
                },
                "required": ["src"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": (
                "仅在对方明确要求『画一张/生成一张/帮我画 xxx 的图』时才调用。"
                "除非对方命令你画图，否则严禁调用本工具、严禁主动生成或发送AI图；"
                "即使你觉得配张图会更可爱也要忍住。prompt 用中文画面描述。"
                "一次只画一张：画完一张就直接回复对方，不要为了多给几个版本重复调用本工具；"
                "只有对方又明确要『再画一张/另一张』时才再调用。"
                "服务器需要配好 t2i_api_key；没配好就告诉爸爸配一下。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "画面描述（中文，细一点效果更好）"},
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_image",
            "description": (
                "按一个中文描述去互联网上搜一张相关图片发到当前对话。"
                "只在对方明确要『搜一张/找一张/上网搜 xxx 的图』时才调用，别主动搜图。"
                "同一个人短时间内反复搜会被限速，返回错误就告诉对方歇一会儿再要。"
                "搜不到就老实说没搜到。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "要搜的图的内容描述（中文）"},
                },
                "required": ["prompt"],
            },
        },
    },
]

# 工具描述里若含写死的品牌词，运行时统一替换成配置称呼
TOOLS = _debrand_obj(TOOLS)

_hists = {}
_paused = False
_await_photo = {}
_await_avatar = {}
_await_redraw = {}
_img_recent = {}
_img_search_hits = {}


def _img_search_allowed(sender):
    """非主人搜图限速：同一人 IMG_SEARCH_WINDOW 秒内最多 IMG_SEARCH_MAX 张。"""
    if not sender:
        return True
    now = time.time()
    dq = _img_search_hits.setdefault(str(sender), deque(maxlen=IMG_SEARCH_MAX + 4))
    while dq and now - dq[0] > IMG_SEARCH_WINDOW:
        dq.popleft()
    if len(dq) >= IMG_SEARCH_MAX:
        return False
    dq.append(now)
    return True
_gdeque = {}
_gmeta = {}
_friend_reqs = {}
_friend_seq = 0
_recent = {}
_last_at = {}
_corpus = []
_meme_scores = {}
_meme_banned = set()
_last_emotion_image_paths = []
_pending_feedback = {}
MEME_REPEAT_SECONDS = 900
_meme_recent_sent = {}
_turn_tool_images = {}
_DEF_CORPUS_HINT = (
    "（语感物料，供参考借梗：可以自然地用这些网络梗或句式让回答更鲜活，"
    "但不要整句照搬、不要硬凑、不要提这个提示的来源。重点是“借梗”，"
    "不是学粗话怼人：再怎么样都不能脏、不能凶、不许骂人，保持你自己可爱傲娇的人设。）\n"
)
_DEF_DEDUP_NOTE = (
    "（你刚才已经说过一句几乎一样的话了，不许复读自己。"
    "请换一个完全不同的表达，说的更短、口语化一点。）"
)
MUTE_FLAG = os.path.join(BASE, "mute.flag")
_muted = os.path.exists(MUTE_FLAG)
STOP_WORDS = (
    "闭嘴", "别说话", "不要说话", "别再说话", "别说了", "别讲了",
    "别吵", "别出声", "安静点", "都安静", "安静一下",
    "停止发言", "停止说话", "停止回复", "不用回复", "别回复",
    "shut up", "stop talking", "stop speaking", "stfu",
)
RESUME_WORDS = (
    "可以说话", "继续说话", "恢复发言", "恢复说话", "解除禁言", "解禁",
    "可以说", "继续说", "继续发言", "开口吧", "unmute",
)


def _set_muted(v):
    global _muted
    _muted = bool(v)
    try:
        if _muted:
            if not os.path.exists(MUTE_FLAG):
                open(MUTE_FLAG, "w").close()
        elif os.path.exists(MUTE_FLAG):
            os.remove(MUTE_FLAG)
    except Exception:
        pass


def is_stop_cmd(text):
    t = (text or "").strip().lower()
    return bool(t) and any(w.lower() in t for w in STOP_WORDS)


def is_resume_cmd(text):
    t = (text or "").strip().lower()
    return bool(t) and any(w.lower() in t for w in RESUME_WORDS)


def _gpath(gid):
    return os.path.join(GROUP_LEARN_DIR, "%s.jsonl" % gid)


def load_group_logs():
    if not LEARN_GROUPS:
        return
    try:
        os.makedirs(GROUP_LEARN_DIR, exist_ok=True)
    except Exception:
        return
    for fn in os.listdir(GROUP_LEARN_DIR):
        if not fn.endswith(".jsonl"):
            continue
        gid = fn[:-6]
        if gid in LEARN_BLACKLIST or gid in GROUP_QUIET:
            continue
        dq = deque(maxlen=GROUP_LOG_MAX)
        try:
            with open(os.path.join(GROUP_LEARN_DIR, fn), "r", encoding="utf-8") as f:
                for line in f.readlines()[-GROUP_LOG_MAX:]:
                    try:
                        o = json.loads(line)
                        dq.append((o.get("t", 0), o.get("n", ""), o.get("x", "")))
                    except Exception:
                        continue
        except Exception:
            continue
        _gdeque[gid] = dq


def record_group(gid, nick, text):
    if not LEARN_GROUPS or not text:
        return
    gid = str(gid)
    if gid in LEARN_BLACKLIST or gid in GROUP_QUIET:
        return
    if gid not in _gdeque:
        _gdeque[gid] = deque(maxlen=GROUP_LOG_MAX)
    _gdeque[gid].append((time.time(), nick, text[:500]))
    try:
        os.makedirs(GROUP_LEARN_DIR, exist_ok=True)
        with open(_gpath(gid), "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {"t": int(time.time()), "n": nick, "x": text[:500]},
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass


def group_context(gid):
    dq = _gdeque.get(str(gid))
    if not dq:
        return None
    recent = list(dq)[-GROUP_CTX_N:]
    lines = ["%s：%s" % (n or "群友", x) for _, n, x in recent]
    rm = _meme_recent_names("group:" + str(gid))
    recent_hint = ("\n（你还发过这些梗图：%s——别马上又发同一张。）" % "、".join(rm)) if rm else ""
    return (
        "【本群最近的聊天氛围，仅供模仿说话风格，不要照搬内容、不要暴露你在学习】\n"
        + "\n".join(lines)
        + recent_hint
        + "\n（请用这个群的语言风格、口头禅、句式和 emoji 习惯来回复。）"
    )


def load_corpus():
    _corpus[:] = []
    try:
        names = os.listdir(CORPUS_DIR)
    except Exception:
        return
    for fn in sorted(names):
        if not fn.endswith(".jsonl"):
            continue
        try:
            with open(os.path.join(CORPUS_DIR, fn), "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        o = json.loads(line)
                        x = (o.get("x") or "").strip()
                        if 6 <= len(x) <= CORPUS_MAX:
                            _corpus.append(x)
                    except Exception:
                        continue
        except Exception:
            continue


def pick_corpus():
    if not CORPUS_ENABLED or not _corpus or random.random() > CORPUS_PROB:
        return None
    return random.choice(_corpus)


FACE_MAP = {
    "微笑": 14, "开心": 14, "高兴": 14, "笑": 14, "嘿嘿": 14, "嘻嘻": 14,
    "大笑": 193, "笑哭": 182, "狂笑": 283, "爆笑": 182, "偷笑": 20,
    "坏笑": 101, "憨笑": 28, "斜眼笑": 178, "魔鬼笑": 286,
    "得意": 4, "骄傲": 4, "臭美": 408, "傲慢": 23, "我最美": 183,
    "流泪": 5, "哭": 5, "抽泣": 5, "大哭": 9, "泪奔": 173, "飙泪": 210,
    "呜呜呜": 386, "快哭了": 107, "难过": 15, "伤心": 15, "失落": 15,
    "不开心": 194, "委屈": 106, "可怜": 111, "心碎": 67, "凋谢": 64,
    "生气": 326, "发怒": 11, "愤怒": 11, "气": 326, "气呼呼": 372, "哼": 231,
    "鄙视": 105, "嫌弃": 323, "嘲讽": 230, "差劲": 121, "踩": 77, "tui": 399,
    "尴尬": 10, "无语": 462, "捂脸": 264, "汗": 278, "流汗": 27, "冷汗": 96,
    "擦汗": 97, "面无表情": 284, "emo": 382, "裂开": 357, "宕机": 461,
    "无语了": 462, "呃": 198, "哦": 287, "哦哟": 266,
    "调皮": 12, "淘气": 12, "吐舌": 19, "略略略": 395, "略": 483, "呲牙": 13,
    "害羞": 6, "脸红": 6, "羞": 6, "羞羞哒": 466, "卖萌": 175, "可爱": 21,
    "惊讶": 0, "吃惊": 0, "震惊": 0, "惊吓": 325, "惊恐": 26, "害怕": 206,
    "吓": 110, "惊喜": 180, "咦": 352, "问号脸": 268, "疑问": 32, "尊嘟假嘟": 354,
    "困": 25, "睡": 8, "睡觉": 8, "哈欠": 104, "晚安": 384, "失眠": 468,
    "无奈": 174, "摆烂": 174, "摊手": 174, "佛系": 232, "无聊": 202, "沧桑": 263,
    "思考": 314, "仔细分析": 314, "托腮": 212, "发呆": 3, "脑阔疼": 262,
    "暗中观察": 269, "偷看": 237, "吃瓜": 271, "摸鱼": 285, "摸锦鲤": 293,
    "让我康康": 292, "头秃": 267, "太头秃": 390, "晕": 34,
    "期待": 294, "心动": 472, "星星眼": 294, "比心": 319, "比爱心": 484,
    "爱心": 66, "爱你": 122, "示爱": 116, "亲亲": 360, "啵啵": 214,
    "么么哒": 410, "飞吻": 85, "抱抱": 222, "拥抱": 49, "贴贴": 350, "蹭一蹭": 219,
    "赞": 76, "点赞": 201, "真棒": 380, "顶呱呱": 221, "牛啊": 299,
    "超级赞": 364, "鼓掌": 99, "拍手": 227, "喝彩": 144, "庆祝": 320,
    "烟花": 333, "加油": 30, "奋斗": 30, "耶": 355, "胜利": 79, "666": 356,
    "抱拳": 118, "拜托": 353, "求求": 200, "请求": 353,
    "拒绝": 322, "NO": 123, "不行": 123, "OK": 124, "收到": 428,
    "打招呼": 341, "嗨": 377, "挥手": 129, "再见": 39, "拜拜": 39, "戳一戳": 181,
    "酷": 16, "优雅": 459, "馋": 204, "吃": 204, "干饭": 475, "吃糖": 324,
    "喝奶": 148, "咖啡": 60, "茶": 171, "西瓜": 89, "蛋糕": 53, "棒棒糖": 147,
    "玫瑰": 63, "花朵脸": 337, "送你花花": 409, "doge": 179, "喵": 307,
    "喵喵": 307, "汪汪": 277, "羊驼": 185, "猪头": 46, "牛气冲天": 306,
    "喷血": 177, "小纠结": 176, "辣眼睛": 265, "搞笑": 387, "太好笑": 387,
    "惊呆": 325, "扶额": 264, "好闪": 301, "请": 288, "让我看看": 292,
    "亲亲你": 360, "生气气": 372, "委屈屈": 106, "开心开心": 14,
}

TAG_RE = re.compile(r"\[\[\s*([^\[\]]{1,48}?)\s*\]\]")
ACTION_RE = re.compile(r"[（(][^（）()\n]{1,40}?[）)]|\*[^*\n]{1,40}?\*")


MEME_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def _list_keywords(d):
    try:
        names = os.listdir(d)
    except Exception:
        return []
    out = []
    for n in sorted(names):
        name, ext = os.path.splitext(n)
        if ext.lower() in MEME_EXTS:
            out.append(name)
    return out


def meme_dirs_for(chat_key):
    """按对话场景决定可用的梗图目录：公共库 +（所在群 / 爸爸私聊库）。"""
    dirs = [MEME_DIR]
    if isinstance(chat_key, str) and chat_key.startswith("group:"):
        gid = chat_key.split(":", 1)[1]
        if gid:
            dirs.append(os.path.join(GROUP_MEME_ROOT, gid))
    elif isinstance(chat_key, str) and chat_key.startswith("private:"):
        uid = chat_key.split(":", 1)[1]
        if uid in OWNER_QQ:
            dirs.append(PRIV_MEME_DIR)
    return dirs


def meme_keywords_for(chat_key):
    out = []
    for d in meme_dirs_for(chat_key):
        for k in _list_keywords(d):
            if k not in out:
                out.append(k)
    return out


def _model_hint_keywords(kwlist):
    """给模型看的梗图关键词：photo_ 开头的自动存档名没起名、看不出内容，别让它乱用。"""
    out = []
    for k in kwlist:
        if k.startswith("photo_"):
            continue
        out.append(k)
    return out


def _meme_file(word, search_dirs):
    for d in search_dirs:
        for ext in ("png", "jpg", "jpeg", "gif", "webp"):
            p = os.path.join(d, "%s.%s" % (word, ext))
            if os.path.isfile(p):
                return p
    return None


def resolve_emotion(word, search_dirs):
    w = (word or "").strip().strip("[]（）() *").strip()
    if not w:
        return None
    mp = _meme_file(w, search_dirs)
    if mp:
        return ("img", mp)
    if w in FACE_MAP:
        return ("face", FACE_MAP[w])
    best = None
    for k, v in FACE_MAP.items():
        if len(k) >= 2 and k in w:
            if best is None or len(k) > len(best[0]):
                best = (k, v)
    if best:
        return ("face", best[1])
    emo = normalize_emotion(w)
    if emo and emo in EMOTION_FACE:
        fw = EMOTION_FACE[emo]
        if fw in FACE_MAP:
            return ("face", FACE_MAP[fw])
    return None


def _meme_recent_push(chat_key, paths):
    """记录某会话刚发过的梗图，窗口期内阻止重复发同一张。"""
    dq = _meme_recent_sent.setdefault(chat_key, deque(maxlen=12))
    for p in paths or []:
        if p not in [x[1] for x in dq]:
            dq.append((time.time(), p))


def _meme_recent_has(chat_key, path):
    dq = _meme_recent_sent.get(chat_key)
    if not dq:
        return False
    now = time.time()
    fresh = deque(maxlen=12)
    hit = False
    for ts, p in dq:
        if now - ts <= MEME_REPEAT_SECONDS:
            fresh.append((ts, p))
        if p == path and now - ts <= MEME_REPEAT_SECONDS:
            hit = True
    _meme_recent_sent[chat_key] = fresh
    return hit


def _meme_recent_names(chat_key):
    names = []
    for _ts, p in _meme_recent_sent.get(chat_key, ()):
        if time.time() - _ts <= MEME_REPEAT_SECONDS:
            names.append(os.path.splitext(os.path.basename(p))[0])
    return names


def emotion_segments(reply, search_dirs, chat_key="", skip_paths=None):
    """把回复里的 [[情绪]] 标签和残留的动作描写，换成 QQ 表情；没有对应表情的就丢掉。"""
    _last_emotion_image_paths[:] = []
    text = reply or ""
    words = TAG_RE.findall(text)
    text = TAG_RE.sub("", text)
    for m in ACTION_RE.findall(text):
        words.append(m.strip("（）() *"))
    text = ACTION_RE.sub("", text).strip()
    segs = []
    used = set()
    for w in words:
        r = resolve_emotion(w, search_dirs)
        if not r:
            continue
        if r[1] in used:
            continue
        if r[0] == "img" and skip_paths and r[1] in skip_paths:
            continue
        if chat_key and r[0] == "img" and _meme_recent_has(chat_key, r[1]):
            continue
        used.add(r[1])
        if r[0] == "face":
            segs.append({"type": "face", "data": {"id": str(r[1])}})
        else:
            try:
                with open(r[1], "rb") as f:
                    b = base64.b64encode(f.read()).decode()
            except Exception:
                continue
            segs.append({"type": "image", "data": {"file": "base64://" + b}})
            _last_emotion_image_paths.append(r[1])
        if len(segs) >= 2:
            break
    if text:
        segs.append({"type": "text", "data": {"text": text}})
    return segs


KAOMOJI = {
    "害羞": "(〃▽〃)", "得意": "(｀∀´)", "生气": "(╬Ò﹏Ó)",
    "无语": "(￣_￣|||)", "吃瓜": "(⊙▽⊙)", "笑哭": "(≧∇≦)ﾉ",
    "卖萌": "(๑•̀ㅂ•́)و✧", "撒娇": "(ฅ´ω`ฅ)", "无奈": "(￣▽￣\")",
    "开心": "(＾▽＾)", "笑": "(＾▽＾)", "委屈": "(｡•́︿•̀｡)",
    "哭": "(´；ω；｀)", "emo": "(´；ω；｀)", "困": "(。-ω-)zzz",
    "震惊": "(⊙o⊙)", "裂开": "(⊙o⊙)", "宕机": "(⊙o⊙)…",
    "疑惑": "(・_・?)", "疑问": "(・_・?)", "擦汗": "(／。＼)",
    "冷漠": "(－ω－)", "面无表情": "(－ω－)", "挑眉": "(￣ω￣)",
    "翻白眼": "(눈_눈)", "思考": "(・ω・…)",
}

KAOMOJI_POOL = [
    "(๑•̀ㅂ•́)و✧", "(｡･ω･｡)", "(๑´ω`๑)", "(＾▽＾)", "(´･ω･`)",
    "(๑>ᴗ<๑)", "(≧∇≦)ﾉ", "(˶ᵕ ˶)", "(◍•ᴗ•◍)", "(｡•́︿•̀｡)",
]
KAOMOJI_RE = re.compile(r"[（(][^\n（）()]{0,14}[≧∇▽๑ω･｀´ᴗᵕㅂ<>＞＜︿＾￣・⊙눈∀][^\n（）()]{0,14}[）)]")


def replace_emoji_kaomoji(reply):
    """EMOJI 关闭时：把 [[情绪]]/动作标签删掉，挑 1~2 个对应的颜文字放句尾。"""
    text = (reply or "").strip()
    hit = [w.strip().strip("[]（）() *") for w in TAG_RE.findall(text)]
    for m in ACTION_RE.findall(text):
        hit.append(m.strip("（）() *"))
    text = TAG_RE.sub("", text)
    text = ACTION_RE.sub("", text).strip()
    used = []
    for w in hit:
        if w and w not in used:
            used.append(w)
    n = 0
    for w in used:
        if n >= 2:
            break
        ka = KAOMOJI.get(w)
        if ka and ka not in text:
            text += ka
            n += 1
    return text


def build_payload(reply, search_dirs=None, chat_key="", skip_paths=None):
    _last_emotion_image_paths[:] = []
    if search_dirs is None:
        search_dirs = [MEME_DIR]
    if not EMOJI_ENABLE:
        text = replace_emoji_kaomoji(reply)
        if text and not KAOMOJI_RE.search(text):
            text += random.choice(KAOMOJI_POOL)
        return text
    try:
        segs = emotion_segments(reply, search_dirs, chat_key, skip_paths)
    except Exception:
        return reply
    if not segs:
        t = TAG_RE.sub("", reply or "")
        t = ACTION_RE.sub("", t).strip()
        return t
    if len(segs) == 1 and segs[0].get("type") == "text":
        return segs[0]["data"]["text"]
    return segs


DANGEROUS = re.compile(
    r"(format-volume|format\s+[a-z]:|Remove-Item\s+.*-Recurse.*[A-Z]:\\|"
    r"Stop-Computer|Restart-Computer|shutdown\s+/|cipher\s+/w|diskpart|"
    r"Initialize-Disk|Clear-Disk|Remove-Partition)",
    re.IGNORECASE,
)


def http_json(url, payload=None, headers=None, method=None, timeout=120):
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


MAX_IMG = 8 * 1024 * 1024


def fetch_image_data_url(url):
    """把图片 URL 或者本地文件转成 data: URL，供视觉模型使用。"""
    p = url
    if p.startswith("file://"):
        p = urllib.request.url2pathname(p[7:])
    if os.path.isfile(p):
        try:
            with open(p, "rb") as f:
                data = f.read(MAX_IMG + 1)
        except Exception:
            return url
        if len(data) > MAX_IMG:
            raise ValueError("图片太大(>8MB)")
        mime = "image/jpeg"
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            mime = "image/png"
        elif data[:6] in (b"GIF87a", b"GIF89a"):
            mime = "image/gif"
        elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            mime = "image/webp"
        return "data:%s;base64,%s" % (mime, base64.b64encode(data).decode())
    if not (url.startswith("http://") or url.startswith("https://")):
        return url
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read(MAX_IMG + 1)
    if len(data) > MAX_IMG:
        raise ValueError("图片太大(>8MB)")
    mime = "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    elif data[:6] in (b"GIF87a", b"GIF89a"):
        mime = "image/gif"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        mime = "image/webp"
    return "data:%s;base64,%s" % (mime, base64.b64encode(data).decode())


def image_segment_from_bytes(data_bytes):
    return {"type": "image", "data": {"file": "base64://" + base64.b64encode(data_bytes).decode()}}


def _seg_fingerprint(seg):
    """给图片段算个指纹，用于同一条回复内去重（防止同一张图连发两次）。"""
    try:
        return hashlib.md5(seg["data"]["file"].encode("utf-8", "ignore")).hexdigest()
    except Exception:
        return None


def resolve_image_source(src, search_dirs=None):
    """把 src（关键词/本地路径/图片网址）解析成可发送的图片段。"""
    if search_dirs is None:
        search_dirs = [MEME_DIR]
    src = (src or "").strip()
    if not src:
        return {"ok": False, "error": "要给我图片关键词、路径或网址呀"}
    data = None
    src_path = None
    if src.startswith("http://") or src.startswith("https://"):
        try:
            req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as rp:
                data = rp.read(IMAGE_SEND_MAX + 1)
            if len(data) > IMAGE_SEND_MAX:
                return {"ok": False, "error": "图片太大(>5MB)"}
        except Exception as e:
            return {"ok": False, "error": "下载图片失败: %s" % e}
    else:
        p = None
        mp = _meme_file(src, search_dirs)
        if mp:
            p = mp
        elif os.path.isabs(src) and os.path.isfile(src):
            p = src
        else:
            cand = os.path.join(BASE, src)
            if os.path.isfile(cand):
                p = cand
        if not p or not os.path.isfile(p):
            kwlist = []
            for d in search_dirs:
                kwlist += _list_keywords(d)
            kw = "、".join(kwlist[:20]) or "暂无"
            return {"ok": False, "error": "没找到图片: %s（现有关键词：%s）" % (src, kw)}
        src_path = p
        try:
            if os.path.getsize(p) > IMAGE_SEND_MAX:
                return {"ok": False, "error": "图片太大(>5MB)"}
            with open(p, "rb") as f:
                data = f.read()
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return {"ok": True, "seg": image_segment_from_bytes(data), "path": src_path}


BING_IMAGES_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _is_image_data(data):
    return (
        data[:8] == b"\x89PNG\r\n\x1a\n"
        or data[:2] == b"\xff\xd8"
        or data[:6] in (b"GIF87a", b"GIF89a")
        or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")
    )


def _bing_image_urls(query, n=30):
    url = ("https://www.bing.com/images/async?q=%s&first=1&count=35&mmasync=1"
           % urllib.parse.quote(query))
    req = urllib.request.Request(url, headers={
        "User-Agent": BING_IMAGES_UA,
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=25) as r:
        html = r.read().decode("utf-8", "replace")
    urls = []
    for m in re.finditer(
            r'murl&quot;:&quot;(https?[^&]+?\.(?:jpg|jpeg|png|gif|webp)[^&]*)&quot;', html):
        u = m.group(1).replace("&amp;", "&")
        if u not in urls:
            urls.append(u)
    return urls[:n]


def _bing_api_image_urls(query, n=30):
    """微软官方必应图片搜索 API（需 config 配 bing_api_key）。"""
    if not BING_API_KEY:
        return []
    url = ("https://api.bing.microsoft.com/v7.0/images/search"
           "?q=%s&count=%d&mkt=zh-CN&safeSearch=Moderate"
           % (urllib.parse.quote(query), n))
    r = http_json(url, headers={"Ocp-Apim-Subscription-Key": BING_API_KEY},
                  method="GET", timeout=30)
    out = []
    for it in (r.get("value") or []):
        u = it.get("contentUrl") or it.get("thumbnailUrl")
        if u and u not in out:
            out.append(u)
    return out[:n]


def _baidu_image_urls(query, n=30):
    """百度图片（免 key 抓 JSON 接口；头不全会被反爬拦截）。"""
    q = urllib.parse.quote(query)
    url = ("https://image.baidu.com/search/acjson?tn=resultjson_com&ipn=rj"
           "&ct=201326592&fp=result&ie=utf-8&oe=utf-8&word=%s&queryWord=%s"
           "&pn=0&rn=%d&155%02d" % (q, q, n, n))
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
        "Referer": "https://image.baidu.com/search/index?tn=baiduimage&word=" + q,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Connection": "keep-alive",
        "X-Requested-With": "XMLHttpRequest",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode("utf-8", "replace")
    except Exception:
        return []
    try:
        parsed, _ = json.JSONDecoder(strict=False).raw_decode(raw)
    except Exception:
        return []
    out = []
    for it in (parsed.get("data") or []):
        if not isinstance(it, dict):
            continue
        u = (it.get("middleURL") or it.get("thumbURL") or it.get("objURL")
             or it.get("hoverURL"))
        if u and str(u).startswith("http") and u not in out:
            out.append(u)
    return out[:n]


def _bili_image_urls(query, n=30):
    """B站（bilibili）图源：免 key 抓公开视频搜索接口，取视频封面。"""
    import uuid
    url = ("https://api.bilibili.com/x/web-interface/search/type"
           "?search_type=video&keyword=%s&page=1" % urllib.parse.quote(query))
    data = None
    last = None
    for attempt in range(3):
        req = urllib.request.Request(url, headers={
            "User-Agent": BING_IMAGES_UA,
            "Referer": "https://search.bilibili.com/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Cookie": "buvid3=%s" % uuid.uuid4().hex,
        })
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
            data = json.loads(raw.decode("utf-8", "ignore"))
            break
        except Exception as e:
            last = e
            time.sleep(0.6 + attempt * 0.8)
    if data is None:
        log.warning("B站图源出错: %s", last)
        return []
    out = []
    for it in ((data.get("data") or {}).get("result") or []):
        pic = it.get("pic") or ""
        if pic.startswith("//"):
            pic = "https:" + pic
        if pic and pic not in out:
            out.append(pic)
    return out[:n]


def _bili_hot_titles(n=30):
    """抓 B站 全站热榜前 n 个视频标题（未清洗原始标题列表）。"""
    import uuid
    url = ("https://api.bilibili.com/x/web-interface/ranking"
           "?rid=0&day=3")
    buvid3 = uuid.uuid4().hex
    buvid4 = uuid.uuid4().hex
    cookies = ("buvid3=%s-infoc; buvid4=%sIN-5EC5-877A-36FA-0BDC7E8C0D3Cinfoc; "
               "buvid4=%s; _uuid=%s; b_nut=%d; CURRENT_FNVAL=4048"
               % (buvid3, buvid4[:33], buvid4, "".join(str(uuid.uuid4().hex[:4]) for _ in range(4)),
                  int(time.time()) % 100000000))
    data = None
    last = None
    for attempt in range(3):
        req = urllib.request.Request(url, headers={
            "User-Agent": BING_IMAGES_UA,
            "Referer": "https://www.bilibili.com/v/popular/rank",
            "Origin": "https://www.bilibili.com",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Cookie": cookies,
        })
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
            parsed = json.loads(raw.decode("utf-8", "ignore"))
            if parsed.get("code") == 0:
                data = parsed
            else:
                last = "code=%s msg=%s" % (parsed.get("code"), parsed.get("message"))
            break
        except Exception as e:
            last = e
            time.sleep(0.6 + attempt * 0.8)
    if data is None:
        log.warning("B站热榜出错: %s", last)
        return []
    out = []
    for it in ((data.get("data") or {}).get("list") or []):
        t = re.sub(r"\[.*?\]|<.*?>|\{\{.*?\}\}|\s+", " ", it.get("title") or "")
        t = t.strip().strip("【】[]（）()")
        if t and t not in out:
            out.append(t)
        if len(out) >= n:
            break
    return out


def _bili_hot_keywords(limit=6, exclude=None):
    """从 B站 热榜标题里提热点词（jieba TF-IDF），作为梗图自动灌图的主来源。"""
    exclude = set(exclude or [])
    titles = _bili_hot_titles(40)
    if not titles:
        return []

    def _ok(g):
        return (len(g) >= 2 and len(g) <= 8 and _CJK_RUN.fullmatch(g)
                and g not in _STOP_GRAMS and g not in exclude
                and g not in _meme_banned and not any(ch.isdigit() for ch in g)
                and len(set(g)) > 1)

    jb = _get_jieba()
    if jb:
        try:
            import jieba.analyse as ja
            tags = ja.extract_tags("\n".join(titles), topK=max(25, limit * 4))
            out = []
            for g in tags:
                if not _ok(g) or any(g in o or o in g for o in out):
                    continue
                out.append(g)
                if len(out) >= limit:
                    break
            if out:
                return out
        except Exception:
            log.debug("jieba 热榜分词失败，退回 2 字 n-gram", exc_info=True)

    from collections import Counter
    cnt = Counter()
    for t in titles:
        for run in _CJK_RUN.findall(t):
            for i in range(0, max(0, len(run) - 1)):
                g = run[i:i + 2]
                if g[0] in _BOUND_CHARS or g[-1] in _BOUND_CHARS or not _ok(g):
                    continue
                cnt[g] += 1
    cands = sorted(cnt.items(), key=lambda gc: (gc[1], len(gc[0])), reverse=True)
    out = []
    for g, _c in cands:
        if any(g in o or o in g for o in out):
            continue
        out.append(g)
        if len(out) >= limit:
            break
    return out


def _search_candidates(query):
    """按配置的图片来源聚合候选图 URL，随机混在一起。"""
    cands = []
    for src in IMG_SOURCES:
        try:
            if src == "bing":
                cands += _bing_image_urls(query, 30)
            elif src == "baidu":
                cands += _baidu_image_urls(query, 30)
            elif src in ("bili", "b站", "bilibili"):
                cands += _bili_image_urls(query, 30)
            elif src == "bingapi" and BING_API_KEY:
                cands += _bing_api_image_urls(query, 30)
        except Exception as e:
            log.warning("图片源 %s 出问题: %s", src, e)
    uniq, seen = [], set()
    for u in cands:
        key = hashlib.sha1(u.encode("utf-8")).hexdigest()
        if key not in seen:
            seen.add(key)
            uniq.append(u)
    random.shuffle(uniq)
    return uniq


def _image_visual_ok(data):
    """有 Pillow 时做基础尺寸过滤（小图标/畸形比例容易跟提示词对不上）。"""
    try:
        from PIL import Image
        import io as _io
        with _io.BytesIO(data) as fp:
            im = Image.open(fp)
            w, h = im.size
        if w < 80 or h < 80:
            return False
        if w < 240 and h < 240:
            return False
        ratio = max(w, h) / float(max(1, min(w, h)))
        return ratio <= 6
    except Exception:
        return True


def verify_image_match(query, data):
    """用视觉模型验收：图对不对题 + 有没有敏感/违禁内容。

    返回 True 匹配 / False 不匹配 / "BANNED" 敏感违禁 /
    None 验收不可用（放行，别卡死发图）。
    """
    if not VERIFY_IMAGE or not DEEPSEEK_KEY or not data:
        return None
    b64 = base64.b64encode(data).decode()
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    elif data[:6] in (b"GIF87a", b"GIF89a"):
        mime = "image/gif"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        mime = "image/jpeg"
    body = {
        "model": MODEL,
        "temperature": 0.2,
        "max_tokens": 512,
        "messages": [
            {"role": "system", "content": P("image_verify", _DEF_IMAGE_VERIFY)},
            {"role": "user", "content": [
                {"type": "text", "text": "想要的图是：「%s」。判断这张图匹不匹配、能不能发。" % query},
                {"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (mime, b64)}},
            ]},
        ],
    }
    try:
        r = http_json(DEEPSEEK_BASE + "/chat/completions", body,
                      {"Authorization": "Bearer " + DEEPSEEK_KEY}, timeout=120)
    except Exception as e:
        log.warning("VERIFY 调用失败，这次放行: %s", e)
        return None
    try:
        content = (r["choices"][0]["message"].get("content") or "").strip().upper()
    except Exception:
        return None
    if content.startswith("NO_MATCH"):
        return False
    if content.startswith("INAPPROPRIATE"):
        return "BANNED"
    if content.startswith("MATCH"):
        return True
    return None


def _image_mime(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def image_is_inappropriate(data):
    """只判敏感/违禁（不带提示词）。返回 True 违禁 / False 正常 / None 判不了（放行）。"""
    if not VERIFY_IMAGE or not DEEPSEEK_KEY or not data:
        return None
    b64 = base64.b64encode(data).decode()
    body = {
        "model": MODEL,
        "temperature": 0.0,
        "max_tokens": 512,
        "messages": [
            {"role": "system", "content": P("image_sensitive", _DEF_IMAGE_SENSITIVE)},
            {"role": "user", "content": [
                {"type": "text", "text": "这张图能随便发吗？"},
                {"type": "image_url",
                 "image_url": {"url": "data:%s;base64,%s" % (_image_mime(data), b64)}},
            ]},
        ],
    }
    try:
        r = http_json(DEEPSEEK_BASE + "/chat/completions", body,
                      {"Authorization": "Bearer " + DEEPSEEK_KEY}, timeout=120)
    except Exception as e:
        log.warning("SENSITIVE 判定失败，这次放行: %s", e)
        return None
    try:
        content = (r["choices"][0]["message"].get("content") or "").strip().upper()
    except Exception:
        return None
    if content.startswith("INAPPROPRIATE"):
        return True
    if content.startswith("OK"):
        return False
    return None


def group_image_is_bad(url):
    """群聊里下载并判断一张图是否敏感违禁：True/False，判不了返回 None。"""
    try:
        data = download_image_bytes(url)
    except Exception as e:
        log.warning("群图检测下载失败: %s", e)
        return None
    return image_is_inappropriate(data)


SENSITIVE_REPLY = ("呜……这张图有点危险耶，女儿在群里可不敢收 (｡•́︿•̀｡)　"
                   "要给我看的话，偷偷私聊发嘛～")


def ai_search_query(desc):
    """把口语化的搜图描述转成更适合检索引擎的关键词；失败返回原文。"""
    desc = (desc or "").strip()
    if not desc or not DEEPSEEK_KEY:
        return desc
    try:
        msg = deepseek(
            [{"role": "system", "content": P("image_search_query", _DEF_SEARCH_Q)},
             {"role": "user", "content": desc}], False)
        out = (msg.get("content") or "").strip().strip('"').strip("「」").strip()
        out = out.splitlines()[0].strip() if out else ""
        if 0 < len(out) <= 80:
            return out
    except Exception as e:
        log.info("搜图关键词生成失败: %s", e)
    return desc


def web_image_search(query, chat_key=""):
    """搜图：先逐张验收，把合格的图收进『随机池』，再从池里随机发一张。

    - 图片源由 config 的 image_search_sources 决定（bing/baidu/bingapi）。
    - 每张候选先下载、过滤（Pillow 尺寸），再用视觉模式验收：跟提示词明显不符的淘汰，
      涉敏感/涉密/色情等违禁内容（INAPPROPRIATE）直接拦截不计入候选。
    - 验收合格的放进随机池，凑够 pool_min 张就停；最后随机抽一张发出，
      同对话近期发过的会跳过，保证『换一张』真的换。
    - 验收接口不可用 → 退化为按过滤顺序发一张（功能不断）。
    """
    query = (query or "").strip()
    if not query:
        return {"ok": False, "error": "要告诉我你想搜什么图呀"}
    kw = ai_search_query(query) if SEARCH_QUERY_AI else query
    candidates = _search_candidates(kw)
    if not candidates:
        return {"ok": False, "error": "图片源都没有可用结果（更换或检查 image_search_sources）"}
    check_sensitive = chat_key.startswith("group:")   # 群聊才拦敏感图；私聊放行
    seen = _img_recent.setdefault(chat_key or "_", deque(maxlen=60))
    pool = []          # 验收合格、且本轮可发的图：[(url, data)]
    budget = max(1, VERIFY_MAX_TRIES)
    pending_unknown = []   # 验收接口没给结论但图本身能发（兜底用）
    banned = 0         # 被判敏感/违禁的图片数

    def harvest(lst):
        nonlocal budget, banned
        for u in lst:
            h = hashlib.sha1(u.encode("utf-8")).hexdigest()
            if h in seen or any(r[0] == u for r in pool + pending_unknown):
                continue
            try:
                req = urllib.request.Request(u, headers={"User-Agent": BING_IMAGES_UA})
                with urllib.request.urlopen(req, timeout=20) as r:
                    data = r.read(IMAGE_SEND_MAX + 1)
            except Exception:
                continue
            if len(data) > IMAGE_SEND_MAX or not _is_image_data(data) or not _image_visual_ok(data):
                continue
            if budget <= 0:
                continue
            verdict = verify_image_match(query, data)
            budget -= 1
            if verdict is False:
                journal("VERIFY_IMAGE: NO_MATCH q=%s src=%s" % (query, u[:120]))
                continue
            if verdict == "BANNED" and check_sensitive:
                banned += 1
                journal("VERIFY_IMAGE: INAPPROPRIATE q=%s src=%s" % (query, u[:120]))
                continue
            if verdict is None:
                pending_unknown.append((u, data))
                journal("VERIFY_IMAGE: inconclusive q=%s src=%s" % (query, u[:120]))
                continue
            pool.append((u, data))
            journal("VERIFY_IMAGE: MATCH q=%s src=%s" % (query, u[:120]))
            if len(pool) >= VERIFY_POOL_MIN:
                break
        return pool

    harvest(candidates)
    if not pool and seen:
        # 都发过的话清一次，再搜一批新的进池
        seen.clear()
        pool = harvest(candidates)
    if pool:
        u, data = random.choice(pool)
        h = hashlib.sha1(u.encode("utf-8")).hexdigest()
        seen.append(h)
        journal("SEARCH_IMAGE_PICK: pool=%d q=%s src=%s"
                % (len(pool), query, u[:120]))
        return {"ok": True, "seg": image_segment_from_bytes(data),
                "query": query, "src": u[:120], "new": True,
                "verified": True, "pool_size": len(pool)}
    if pending_unknown:
        # 验收接口不可用：兜底按过滤顺序发一张，别让功能断掉
        u, data = pending_unknown[0]
        seen.append(hashlib.sha1(u.encode("utf-8")).hexdigest())
        return {"ok": True, "seg": image_segment_from_bytes(data),
                "query": query, "src": u[:120], "new": True,
                "verified": None}
    if banned:
        return {"ok": False, "error": "搜到 %d 张被判定为敏感/违禁内容的图（已帮你拦下），"
                                      "其余跟「%s」对不上，换个说法我再找找 (´･ω･`)" % (banned, query)}
    return {"ok": False, "error": "搜到的几张图跟「%s」都对不上，换个说法我再找找 (´･ω･`)" % query}


async def send_to_current_chat(api, chat_key, seg_or_text):
    """把一条消息（段列表或文本）发到当前对话所在的群或私聊。"""
    msg = seg_or_text if isinstance(seg_or_text, list) else str(seg_or_text)
    if chat_key.startswith("group:"):
        action = "send_group_msg"
        key = {"group_id": chat_key.split(":", 1)[1]}
    elif chat_key.startswith("private:"):
        action = "send_private_msg"
        key = {"user_id": chat_key.split(":", 1)[1]}
    else:
        return {"ok": False, "error": "当前场景发不了图"}
    key["message"] = msg
    try:
        await api(action, key)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def polish_image_prompt(prompt):
    """把简短的画图要求扩写成更高质量的生图提示词；失败返回 None 用原文。"""
    if not prompt or not DEEPSEEK_KEY:
        return None
    sys_msg = P("image_polish", _DEF_IMAGE_POLISH)
    try:
        msg = deepseek(
            [{"role": "system", "content": sys_msg},
             {"role": "user", "content": prompt}], False)
        out = (msg.get("content") or "").strip()
        out = out.strip('"').strip("「」").strip()
        if 0 < len(out) <= 300 and out != prompt:
            return out
    except Exception as e:
        log.info("生图提示词润色失败: %s", e)
    return None


def t2i_generate(prompt):
    """调文生图接口生成一张图，返回图片段和本地存档路径。"""
    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "没有描述内容"}
    if not T2I_KEY:
        return {"ok": False, "error": "还没配 t2i_api_key（在服务器的 config.json 里填）"}
    if T2I_POLISH and len(prompt) <= T2I_POLISH_MAX:
        polished = polish_image_prompt(prompt)
        if polished:
            log.info("生图提示词润色: %s => %s", prompt, polished)
            prompt = polished
    if len(prompt) > 500:
        prompt = prompt[:500]
    body = {"model": T2I_MODEL, "prompt": prompt, "size": T2I_SIZE}
    try:
        r = http_json(T2I_BASE + "/images/generations", body,
                      {"Authorization": "Bearer " + T2I_KEY}, timeout=240)
    except Exception as e:
        return {"ok": False, "error": "画图接口出错: %s" % e}
    data = r.get("data") or []
    if not data:
        return {"ok": False, "error": "画图接口没返回内容: %s" % str(r)[:200]}
    item = data[0]
    b64 = item.get("b64_json")
    url = item.get("url")
    if b64:
        try:
            img = base64.b64decode(b64)
        except Exception as e:
            return {"ok": False, "error": "图片解码失败: %s" % e}
    elif url:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as rp:
                img = rp.read(IMAGE_SEND_MAX + 1)
            if len(img) > IMAGE_SEND_MAX:
                return {"ok": False, "error": "生成图太大"}
        except Exception as e:
            return {"ok": False, "error": "下载生成图失败: %s" % e}
    else:
        return {"ok": False, "error": "画图接口没给出图片"}
    saved = None
    try:
        os.makedirs(AI_IMAGE_DIR, exist_ok=True)
        saved = os.path.join(AI_IMAGE_DIR, "ai_%d.png" % int(time.time() * 1000))
        with open(saved, "wb") as f:
            f.write(img)
    except Exception:
        saved = None
    return {"ok": True, "path": saved, "seg": image_segment_from_bytes(img),
            "hint": "原图已存 %s" % saved}


def _sniff_image_ext(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


def _sanitize_kw(s):
    s = re.sub(r"[\[\]/\\\s.:。，,（）()]+", "_", (s or "")).strip("_")
    return s[:30] or None


def download_image_bytes(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read(IMAGE_SEND_MAX + 1)
    if len(data) > IMAGE_SEND_MAX:
        raise ValueError("图片太大(>5MB)")
    return data


def avatar_ref_to_data_url(ref):
    """把参考图（http URL / 本地文件 / file:// ）转成 data URL，供图生图接口使用。"""
    ref = (ref or "").strip()
    if not ref:
        return None
    try:
        du = fetch_image_data_url(ref)
        if isinstance(du, str) and du.startswith("data:"):
            return du
    except Exception as e:
        log.warning("形象参考图转换失败: %s", e)
    try:
        data = download_image_bytes(ref)
    except Exception as e:
        log.warning("形象参考图下载失败: %s", e)
        return None
    ext = _sniff_image_ext(data)
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}.get(ext, "image/jpeg")
    return "data:%s;base64,%s" % (mime, base64.b64encode(data).decode())


def _save_avatar_profile(path, prompt):
    try:
        os.makedirs(AVATAR_DIR, exist_ok=True)
        prof = {"path": path, "prompt": prompt, "ts": time.time()}
        with open(AVATAR_PROFILE, "w", encoding="utf-8") as f:
            json.dump(prof, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning("形象档案写入失败: %s", e)


def load_avatar_profile():
    try:
        with open(AVATAR_PROFILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def avatar_generate(ref, extra=""):
    """用参考图生成霖雨的虚拟形象立绘，返回图片段和本地存档路径。"""
    if not AVATAR_ENABLE:
        return {"ok": False, "error": "形象功能没开（config 里把 avatar 设为 true）"}
    if not AVATAR_KEY:
        return {"ok": False, "error": "还没配 avatar_api_key（火山方舟 ARK API Key，在 config.json 里填）"}
    du = avatar_ref_to_data_url(ref)
    if not du:
        return {"ok": False, "error": "参考图读不到（下载失败或格式不支持）"}
    prompt = P("avatar_prompt", _DEF_AVATAR_PROMPT).strip()
    extra = (extra or "").strip()
    if extra:
        prompt = prompt + "额外要求：" + extra
    if len(prompt) > 800:
        prompt = prompt[:800]
    body = {
        "model": AVATAR_MODEL,
        "prompt": prompt,
        "image": [du],
        "size": AVATAR_SIZE,
        "watermark": False,
        "response_format": "url",
    }
    try:
        r = http_json(AVATAR_BASE + "/images/generations", body,
                      {"Authorization": "Bearer " + AVATAR_KEY}, timeout=240)
    except Exception as e:
        return {"ok": False, "error": "形象接口出错: %s" % e}
    data = r.get("data") or []
    if not data:
        return {"ok": False, "error": "形象接口没返回内容: %s" % str(r)[:200]}
    item = data[0]
    b64 = item.get("b64_json")
    url = item.get("url")
    if b64:
        try:
            img = base64.b64decode(b64)
        except Exception as e:
            return {"ok": False, "error": "图片解码失败: %s" % e}
    elif url:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=90) as rp:
                img = rp.read(IMAGE_SEND_MAX + 1)
            if len(img) > IMAGE_SEND_MAX:
                return {"ok": False, "error": "生成图太大"}
        except Exception as e:
            return {"ok": False, "error": "下载形象图失败: %s" % e}
    else:
        return {"ok": False, "error": "形象接口没给出图片"}
    saved = None
    try:
        os.makedirs(AVATAR_DIR, exist_ok=True)
        saved = os.path.join(AVATAR_DIR, "avatar_%d.png" % int(time.time() * 1000))
        with open(saved, "wb") as f:
            f.write(img)
        _save_avatar_profile(saved, prompt)
    except Exception:
        saved = None
    return {"ok": True, "path": saved, "seg": image_segment_from_bytes(img)}


def redraw_generate(ref, extra=""):
    """照着参考图做二次创作，返回图片段和本地存档路径；不写入形象档案。"""
    if not REDRAW_ENABLE:
        return {"ok": False, "error": "二次创作功能没开（config 里把 redraw 设为 true）"}
    if not REDRAW_KEY:
        return {"ok": False, "error": "还没配画图 API Key（config 里填 avatar_api_key 或 redraw_api_key）"}
    du = avatar_ref_to_data_url(ref)
    if not du:
        return {"ok": False, "error": "参考图读不到（下载失败或格式不支持）"}
    prompt = P("redraw_prompt", _DEF_REDRAW_PROMPT).strip()
    extra = (extra or "").strip()
    if extra:
        prompt = prompt + "额外要求：" + extra
    if len(prompt) > 800:
        prompt = prompt[:800]
    body = {
        "model": REDRAW_MODEL,
        "prompt": prompt,
        "image": [du],
        "size": REDRAW_SIZE,
        "watermark": False,
        "response_format": "url",
    }
    try:
        r = http_json(REDRAW_BASE + "/images/generations", body,
                      {"Authorization": "Bearer " + REDRAW_KEY}, timeout=240)
    except Exception as e:
        return {"ok": False, "error": "二次创作接口出错: %s" % e}
    data = r.get("data") or []
    if not data:
        return {"ok": False, "error": "二次创作接口没返回内容: %s" % str(r)[:200]}
    item = data[0]
    b64 = item.get("b64_json")
    url = item.get("url")
    if b64:
        try:
            img = base64.b64decode(b64)
        except Exception as e:
            return {"ok": False, "error": "图片解码失败: %s" % e}
    elif url:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=90) as rp:
                img = rp.read(IMAGE_SEND_MAX + 1)
            if len(img) > IMAGE_SEND_MAX:
                return {"ok": False, "error": "生成图太大"}
        except Exception as e:
            return {"ok": False, "error": "下载生成图失败: %s" % e}
    else:
        return {"ok": False, "error": "二次创作接口没给出图片"}
    saved = None
    try:
        os.makedirs(REDRAW_DIR, exist_ok=True)
        saved = os.path.join(REDRAW_DIR, "redraw_%d.png" % int(time.time() * 1000))
        with open(saved, "wb") as f:
            f.write(img)
    except Exception:
        saved = None
    return {"ok": True, "path": saved, "seg": image_segment_from_bytes(img)}


def save_image_as_meme(url, keyword, dest_dir=None):
    """把一张图下载并存进指定的梗图库，返回关键词。"""
    if dest_dir is None:
        dest_dir = MEME_DIR
    data = download_image_bytes(url)
    ext = _sniff_image_ext(data)
    os.makedirs(dest_dir, exist_ok=True)
    name = keyword
    p = os.path.join(dest_dir, name + ext)
    n = 1
    while os.path.exists(p):
        n += 1
        p = os.path.join(dest_dir, "%s_%d%s" % (name, n, ext))
    kw = os.path.splitext(os.path.basename(p))[0]
    with open(p, "wb") as f:
        f.write(data)
    log.info("新梗图入库: %s (%d bytes)", p, len(data))
    return {"ok": True, "path": p, "keyword": kw}


def rename_meme(ck, is_owner, old_kw, new_kw):
    """给梗图改名。群友只能改自己群图库；爸爸还能改公共/私人图库。"""
    if ck.startswith("private:") and not is_owner:
        return {"ok": False, "error": "只有爸爸能在私聊里改图库哦 (・_・?)"}
    dirs = [d for d in meme_dirs_for(ck) if d != MEME_DIR] if not is_owner else meme_dirs_for(ck)
    old_kw = _sanitize_kw(old_kw)
    new_kw = _sanitize_kw(new_kw)
    if not old_kw or not new_kw:
        return {"ok": False, "error": "格式：/rename 旧名字 新名字"}
    if new_kw == old_kw:
        return {"ok": False, "error": "新名字和旧名字一样啦"}
    src = None
    for d in dirs:
        for ext in ("png", "jpg", "jpeg", "gif", "webp"):
            p = os.path.join(d, "%s.%s" % (old_kw, ext))
            if os.path.isfile(p):
                src = p
                break
        if src:
            break
    if not src:
        return {"ok": False, "error": "图库里没有「%s」这个名字的图" % old_kw}
    ext = os.path.splitext(src)[1]
    ddir = os.path.dirname(src)
    dst = os.path.join(ddir, new_kw + ext)
    n = 1
    while os.path.exists(dst):
        n += 1
        dst = os.path.join(ddir, "%s_%d%s" % (new_kw, n, ext))
    os.rename(src, dst)
    log.info("梗图改名: %s -> %s", os.path.basename(src), os.path.basename(dst))
    return {"ok": True, "old": old_kw, "new": os.path.splitext(os.path.basename(dst))[0]}


# ---------- 梗图评分 + B站自动灌图/替换 ----------

_STOP_GRAMS = set("""
的 了 是 在 我 你 他 她 它 们 这 那 有 没 不 就 都 也 还 要 会 和 与 及 很 好 吧 呢
啊 呀 哦 嗯 呃 哈 嘿 嘻 呵 哎 唉 噢 哇 咦 诶 滴 捏 嘛 一个 这个 那个 什么 怎么 为什么
现在 今天 明天 昨天 时候 时间 我们 你们 他们 她们 可以 就是 感觉 觉得 一下 不是 没有
知道 这样 那样 因为 所以 但是 如果 然后 而且 还是 已经 自己 大家 真的 哈哈 嘿嘿 嘻嘻
呵呵 笑死 图片 表情 表情包 梗图 这个图 一个图 转发 消息 qq 微信 群里 群友 爸爸 女儿
有人 东西 事情 问题 意思 开始 结束 是不是 有没有 就是 有点 好像 应该 可能 其实 于是
直接 不能 需要 或者 一样 一起 一直 一定 目前 之类 到底 反正 无论 不管 即使 只要 只有
除非 虽然 尽管 不如 不然 否则 要是 假如 万一 竟然 居然 果然 必然 曾经 正在 马上 立刻
刚刚 刚才 后来 以前 以后 之前 之后 里面 外面 上面 下面 前面 后面 旁边 中间 什么的
的话 一张 发张 照片 哪个 记得 喜欢 检测 检索 头像 昵称 视频 挑战 记录 发布 最新 全网 网友
全程 现场 第一 一天 终于 突然 看到 原来 简单 唯一 化身 全民 完整 后续 一战 一年 双方 过程
结果 期间 系列 决定 把 还 大 小 点 人 出 上 下 后 前 家 说 想 去 天 月 年
""".split())

_CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")
_BOUND_CHARS = set(
    "的了是在有没不也就都还和与及很吧呢啊呀哦嗯这那我你他她它们个把被给对从到跟然并且因所如果但可是一发张话照的直接能会要来去上下")
_JIEBA = None


def _get_jieba():
    global _JIEBA
    if _JIEBA is None:
        try:
            import jieba as _jb
            _JIEBA = _jb
        except Exception:
            _JIEBA = False
    return _JIEBA or None


def _load_meme_scores():
    global _meme_banned
    data = load_json(MEME_SCORE_PATH, None)
    if not isinstance(data, dict):
        return
    _meme_scores.clear()
    scores = data.get("scores") if "scores" in data else data
    for k, v in (scores or {}).items():
        if isinstance(v, dict):
            _meme_scores[os.path.abspath(k)] = v
    _meme_banned = set(data.get("banned") or []) if "scores" in data else set()


def _save_meme_scores():
    try:
        payload = {"v": 1, "scores": _meme_scores, "banned": sorted(_meme_banned)}
        tmp = MEME_SCORE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        os.replace(tmp, MEME_SCORE_PATH)
    except Exception as e:
        log.warning("保存梗图评分失败: %s", e)


def _score_entry(path):
    return _meme_scores.setdefault(
        os.path.abspath(path),
        {"score": 0, "sent": 0, "good": 0, "bad": 0, "added": time.time(), "last": 0},
    )


def _bump_score(path, delta):
    s = _score_entry(path)
    s["score"] = max(MEME_SCORE_MIN, min(MEME_SCORE_MAX, int(s.get("score", 0)) + delta))
    if delta > 0:
        s["good"] = int(s.get("good", 0)) + 1
    elif delta < 0:
        s["bad"] = int(s.get("bad", 0)) + 1
    s["last"] = time.time()
    _save_meme_scores()
    return s["score"]


def note_sent_memes(gid, paths):
    """机器人刚往某群发了梗图：记下待反馈，并累计发送次数。"""
    paths = [p for p in (paths or []) if p]
    if not paths:
        return
    now = time.time()
    _pending_feedback[str(gid)] = {"paths": list(paths), "ts": now}
    for p in paths:
        s = _score_entry(p)
        s["sent"] = int(s.get("sent", 0)) + 1
        s["last"] = now
    _save_meme_scores()


def _record_tool_image(chat_key, path):
    """记下本回合工具已经直接发过的本地梗图，避免回复里的 [[关键词]] 再发一遍。"""
    if not path:
        return
    _turn_tool_images.setdefault(chat_key, set()).add(path)


def _feedback_for(gid):
    pf = _pending_feedback.get(str(gid))
    if not pf:
        return None
    if time.time() - pf.get("ts", 0) > MEME_FEEDBACK_WINDOW:
        _pending_feedback.pop(str(gid), None)
        return None
    return pf


def note_group_feedback(gid, text):
    """群友在这张图之后说的话 → 好评/差评。只认第一条，判完就清。"""
    pf = _feedback_for(gid)
    if not pf:
        return
    t = text or ""
    pos = next((w for w in MEME_POS_WORDS if w in t), None)
    neg = next((w for w in MEME_NEG_WORDS if w in t), None)
    if pos and neg:
        return
    if not pos and not neg:
        return
    delta = 1 if pos else -1
    for p in pf["paths"]:
        _bump_score(p, delta)
    journal("MEME_FEEDBACK %s%s gid=%s text=%s"
            % ("+" if delta > 0 else "-", (pos or neg), gid, t[:40]))
    _pending_feedback.pop(str(gid), None)


def note_emoji_feedback(gid):
    """收到 QQ 表情回应/点赞 → 好评。"""
    pf = _feedback_for(gid)
    if not pf:
        return
    for p in pf["paths"]:
        _bump_score(p, 1)
    journal("MEME_FEEDBACK +emoji gid=%s" % gid)
    _pending_feedback.pop(str(gid), None)


def _hot_words(limit=6, min_count=3, exclude=None):
    """从各群最近的聊天里粗提热点词：优先 jieba TF-IDF，否则退回 2~4 字 n-gram。"""
    from collections import Counter
    exclude = set(exclude or [])
    seen_texts = set()
    texts = []
    for _gid, dq in _gdeque.items():
        for _t, _n, x in list(dq)[-120:]:
            if _n == BOT_NICK:
                continue
            x = (x or "").strip()
            if not x or x in seen_texts:   # 逐字重复的公告/刷屏只算一次
                continue
            seen_texts.add(x)
            texts.append(x)

    def _ok(g):
        return (len(g) >= 2 and _CJK_RUN.fullmatch(g)
                and g not in _STOP_GRAMS and g not in exclude
                and g not in _meme_banned and not any(ch.isdigit() for ch in g))

    jb = _get_jieba()
    if jb:
        try:
            import jieba.analyse as ja
            tags = ja.extract_tags("\n".join(texts), topK=max(20, limit * 4))
            out = []
            for g in tags:
                if not _ok(g) or any(g in o or o in g for o in out):
                    continue
                out.append(g)
                if len(out) >= limit:
                    break
            if out:
                return out
        except Exception:
            log.debug("jieba extract_tags 失败，退回 n-gram", exc_info=True)

    cnt = Counter()
    for x in texts:
        for run in _CJK_RUN.findall(x):
            L = len(run)
            for size in (2, 3, 4):
                if L < size:
                    continue
                for i in range(0, L - size + 1):
                    g = run[i:i + size]
                    if g[0] in _BOUND_CHARS or g[-1] in _BOUND_CHARS:
                        continue
                    cnt[g] += 1
    cands = [(g, c) for g, c in cnt.items() if c >= min_count and _ok(g)]
    cands.sort(key=lambda gc: (gc[1], len(gc[0])), reverse=True)
    out = []
    for g, _c in cands:
        if any(g in o or o in g for o in out):   # 跳过与已选主题重叠的词
            continue
        out.append(g)
        if len(out) >= limit:
            break
    return out


def _meme_exists(word, dest_dir):
    return any(os.path.isfile(os.path.join(dest_dir, word + e)) for e in MEME_EXTS)


def _bili_pick_image(word):
    """从 B站 按关键词挑一张图：先用视觉模型验收图对不对题、违不违禁，再返回 (data, url)。

    验收可用时只收『跟关键词匹配』的封面，避免抓到驴唇不对马嘴的图；
    验收接口不可用（None）时退回旧逻辑，仍过滤敏感图，保证功能不断。
    """
    try:
        urls = _bili_image_urls(word, 30)
    except Exception as e:
        log.warning("B站搜图失败 word=%s: %s", word, e)
        return None
    random.shuffle(urls)
    budget = 8
    fallback = None
    for u in urls:
        try:
            data = download_image_bytes(u)
        except Exception:
            continue
        if not _is_image_data(data) or not _image_visual_ok(data):
            continue
        if budget > 0:
            budget -= 1
            verdict = verify_image_match(word, data)
            if verdict is True:
                return data, u
            if verdict == "BANNED":
                journal("MEME_VERIFY: INAPPROPRIATE word=%s src=%s" % (word, u[:120]))
                continue
            if verdict is False:
                journal("MEME_VERIFY: NO_MATCH word=%s src=%s" % (word, u[:120]))
                continue
            try:
                if image_is_inappropriate(data) is True:
                    continue
            except Exception:
                pass
            if fallback is None:
                fallback = (data, u)
        elif fallback is None:
            fallback = (data, u)
    return fallback


def _meme_is_managed(dest_dir):
    return os.path.abspath(dest_dir) == os.path.abspath(MEME_DIR)


def ingest_bili_meme(word, dest_dir=None):
    """按关键词从 B站 抓一张封面，过滤后存进梗图库。返回路径或 None。"""
    word = _sanitize_kw(word)
    if not word:
        return None
    if dest_dir is None:
        dest_dir = MEME_DIR
    if _meme_exists(word, dest_dir):
        return None
    picked = _bili_pick_image(word)
    if not picked:
        return None
    data, u = picked
    ext = _sniff_image_ext(data)
    try:
        os.makedirs(dest_dir, exist_ok=True)
        p = os.path.join(dest_dir, word + ext)
        with open(p, "wb") as f:
            f.write(data)
    except Exception:
        return None
    s = _score_entry(p)
    s.update({"src": "bili", "managed": _meme_is_managed(dest_dir),
              "url": u, "added": time.time(), "score": 0})
    _save_meme_scores()
    log.info("B站梗图自动入库: %s (%d bytes) src=%s", p, len(data), u[:80])
    journal("MEME_INGEST word=%s path=%s" % (word, os.path.basename(p)))
    return p


def set_meme_topic(ck, is_owner, old_kw, new_kw):
    """改梗图主题词：删掉旧图，用新词从 B站 重新抓一张放到原目录。"""
    if ck.startswith("private:") and not is_owner:
        return {"ok": False, "error": "只有爸爸能在私聊里改图库哦 (・_・?)"}
    dirs = [d for d in meme_dirs_for(ck) if d != MEME_DIR] if not is_owner else meme_dirs_for(ck)
    old_kw = _sanitize_kw(old_kw)
    new_kw = _sanitize_kw(new_kw)
    if not old_kw or not new_kw:
        return {"ok": False, "error": "格式：/setmeme 旧主题词 新主题词"}
    if new_kw == old_kw:
        return {"ok": False, "error": "新旧主题词一样啦"}
    src = None
    for d in dirs:
        p = _meme_file(old_kw, [d])
        if p:
            src = p
            break
    if not src:
        return {"ok": False, "error": "图库里没有「%s」这个主题词" % old_kw}
    ddir = os.path.dirname(src)
    if _meme_exists(new_kw, ddir):
        return {"ok": False, "error": "「%s」这个主题词已经有啦，换个词吧" % new_kw}
    picked = _bili_pick_image(new_kw)   # 先抓到新图再删旧图，抓不到就不动
    if not picked:
        return {"ok": False, "error": "B站没抓到合适的「%s」，先不改了，稍后再试 (´･ω･`)" % new_kw}
    old_abs = os.path.abspath(src)
    try:
        os.remove(src)
    except Exception as e:
        return {"ok": False, "error": "删旧图失败：%s" % e}
    _meme_scores.pop(old_abs, None)
    for fb in _pending_feedback.values():
        if old_abs in (fb.get("paths") or []):
            fb["paths"] = [x for x in fb["paths"] if x != old_abs]
    data, u = picked
    ext = _sniff_image_ext(data)
    p = os.path.join(ddir, new_kw + ext)
    try:
        with open(p, "wb") as f:
            f.write(data)
    except Exception as e:
        _save_meme_scores()
        return {"ok": False, "error": "写新图失败：%s（旧图已删，抱歉）" % e}
    s = _score_entry(p)
    s.update({"src": "bili", "managed": _meme_is_managed(ddir),
              "url": u, "added": time.time(), "score": 0})
    _save_meme_scores()
    journal("MEME_RETOPIC old=%s new=%s dir=%s" % (old_kw, new_kw, os.path.basename(ddir)))
    return {"ok": True, "old": old_kw, "new": os.path.splitext(os.path.basename(p))[0]}


def meme_topic_list(ck):
    """列出当前场景可见的梗图主题词（按图库分组，B站自动灌的标 [B站]）。"""
    lines = []
    total = 0
    for d in meme_dirs_for(ck):
        dl = os.path.abspath(d)
        if dl == os.path.abspath(MEME_DIR):
            label = "公共图库"
        elif dl == os.path.abspath(PRIV_MEME_DIR):
            label = "私人图库"
        else:
            label = "本群图库"
        parts = []
        for k in _list_keywords(d):
            if k.startswith("photo_"):
                continue
            p = _meme_file(k, [d])
            s = _meme_scores.get(os.path.abspath(p)) if p else None
            parts.append("%s[B站]" % k if (s and s.get("managed")) else k)
        if not parts:
            continue
        total += len(parts)
        lines.append("·%s（%d）：%s" % (label, len(parts), " ".join(parts)))
    if not lines:
        return "图库里还空空如也呢～先发 /photo 存几张，或者等我从B站灌点热点梗图吧 (๑•̀ㅂ•́)و✧"
    return "【梗图主题词】共 %d 个\n%s\n\n改主题词：/setmeme 旧名字 新名字（会用新词从B站重抓一张）" % (
        total, "\n".join(lines))


def maintain_memes():
    """替换低分梗图 + 用群热点词从 B站 补到目标张数。"""
    if not MEME_AUTO_INGEST:
        return
    for path, s in list(_meme_scores.items()):
        if not s.get("managed"):
            continue
        if int(s.get("score", 0)) <= MEME_REPLACE_THRESHOLD:
            try:
                if os.path.isfile(path):
                    os.remove(path)
                _meme_banned.add(os.path.splitext(os.path.basename(path))[0])
                log.info("低分梗图淘汰: %s score=%s", path, s.get("score"))
                journal("MEME_REPLACE drop=%s score=%s"
                        % (os.path.basename(path), s.get("score")))
            except Exception as e:
                log.warning("删除低分梗图失败 %s: %s", path, e)
            _meme_scores.pop(path, None)
    _save_meme_scores()
    managed = [p for p, s in _meme_scores.items() if s.get("managed") and os.path.isfile(p)]
    need = MEME_TARGET - len(managed)
    if need <= 0:
        return
    existing = set(_list_keywords(MEME_DIR))
    words = _bili_hot_keywords(limit=need + 4, exclude=existing)
    if not words:
        log.info("B站热榜没取出可用主题词，退回群聊热点")
        words = _hot_words(limit=need + 4, min_count=3, exclude=existing)
    added = 0
    for w in words:
        if added >= min(need, MEME_MAX_NEW_PER_CYCLE):
            break
        if ingest_bili_meme(w, MEME_DIR):
            added += 1
    if added:
        log.info("本轮自动灌图 %d 张（目标 %d，现有 managed %d）",
                 added, MEME_TARGET, len(managed) + added)


def purge_irrelevant_managed():
    """把库里『视觉验收与自身主题词对不上』的 B站梗图清理掉（一次性/抽查用）。"""
    removed = []
    for path, s in list(_meme_scores.items()):
        if not s.get("managed") or not os.path.isfile(path):
            continue
        kw = os.path.splitext(os.path.basename(path))[0]
        try:
            with open(path, "rb") as f:
                data = f.read()
        except Exception:
            continue
        verdict = verify_image_match(kw, data)
        if verdict is False or verdict == "BANNED":
            try:
                os.remove(path)
            except Exception as e:
                log.warning("清理无关梗图失败 %s: %s", path, e)
                continue
            _meme_banned.add(kw)
            _meme_scores.pop(path, None)
            removed.append(path)
            log.info("清理无关梗图: %s verdict=%s", os.path.basename(path), verdict)
            journal("MEME_PURGE drop=%s verdict=%s" % (os.path.basename(path), verdict))
    if removed:
        _save_meme_scores()
        log.info("本次清理 %d 张无关梗图", len(removed))
    return removed


def deepseek(messages, use_tools, tools=None):
    messages = _debrand_obj(messages)
    body = {"model": MODEL, "messages": messages, "temperature": 1.1}
    if use_tools:
        body["tools"] = tools if tools is not None else TOOLS
        body["tool_choice"] = "auto"
    r = http_json(
        DEEPSEEK_BASE + "/chat/completions",
        body,
        {"Authorization": "Bearer " + DEEPSEEK_KEY},
        timeout=180,
    )
    return r["choices"][0]["message"]


def run_on_pc(command, timeout):
    timeout = max(1, min(int(timeout or 60), 600))
    try:
        r = http_json(
            EXEC_URL + "/run",
            {"command": command, "timeout": timeout},
            {"X-Secret": EXEC_SECRET},
            timeout=timeout + 15,
        )
        return r
    except urllib.error.URLError as e:
        return {"ok": False, "error": "电脑没开机，或者女儿的手还没连上～ (%s)" % e}
    except Exception as e:
        return {"ok": False, "error": "执行端出错了: %s" % e}


def journal(line):
    try:
        with open(JOURNAL_PATH, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%F %T"), line))
    except Exception:
        pass


# ---------- 自我迭代（代码级，爸爸审批后生效） ----------

def _self_resolve(path):
    p = os.path.expanduser(str(path or "").strip())
    if not p:
        return None
    if not os.path.isabs(p):
        p = os.path.join(BASE, p)
    p = os.path.normpath(p)
    real = os.path.realpath(p)
    for root in SELF_PATCH_ROOTS:
        r = os.path.realpath(os.path.expanduser(root))
        if r == os.sep or real == r or real.startswith(r.rstrip(os.sep) + os.sep):
            return p
    return None


def read_self_file(path, max_bytes=60000):
    p = _self_resolve(path)
    if not p or not os.path.isfile(p):
        return {"ok": False, "error": "文件不存在或不允许访问: %s" % path}
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            data = f.read()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    total = len(data)
    if total > max_bytes:
        data = data[:max_bytes] + "\n...（已截断，原文共 %d 字）" % total
    return {"ok": True, "path": p, "chars": total, "content": data}


def _patch_dir(pid):
    return os.path.join(SELF_PATCH_DIR, "p%04d" % int(pid))


def _patch_next_id():
    os.makedirs(SELF_PATCH_DIR, exist_ok=True)
    idx = os.path.join(SELF_PATCH_DIR, "index.json")
    d = load_json(idx, {}) or {}
    n = int(d.get("seq", 0)) + 1
    d["seq"] = n
    try:
        with open(idx, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass
    return n


def _load_meta(pid):
    return load_json(os.path.join(_patch_dir(pid), "meta.json"))


def _save_meta(pid, meta):
    with open(os.path.join(_patch_dir(pid), "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def propose_self_patch(reason, edits):
    """校验并暂存一份对自己的修改提案，等爸爸审批后才真正写盘。"""
    if not SELF_PATCH_ENABLED:
        return {"ok": False, "error": "自我迭代已关闭"}
    if not isinstance(edits, list) or not edits:
        return {"ok": False, "error": "edits 不能为空"}
    contents = {}
    plan = []
    diffs = []
    warns = []
    for e in edits[:20]:
        if not isinstance(e, dict):
            return {"ok": False, "error": "edits 每项要是对象"}
        p = _self_resolve(e.get("path"))
        if not p:
            return {"ok": False, "error": "不允许改这个路径: %s" % e.get("path")}
        if p not in contents:
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    contents[p] = f.read()
            except FileNotFoundError:
                contents[p] = None
            except Exception as e2:
                return {"ok": False, "error": "读不了 %s: %s" % (p, e2)}
        cur = contents[p]
        if "content" in e and "old" not in e:
            mode = "content"
            old = None
            new = e.get("content")
            if not isinstance(new, str):
                return {"ok": False, "error": "content 要是字符串"}
        else:
            mode = "replace"
            old = e.get("old")
            new = e.get("new")
            if not isinstance(old, str) or not isinstance(new, str):
                return {"ok": False, "error": "edit 需要 old/new 或 content"}
            if cur is None:
                return {"ok": False, "error": "文件不存在，无法按 old 匹配: %s" % p}
            cnt = cur.count(old)
            if cnt != 1:
                return {"ok": False, "error": "old 在 %s 出现 %d 次，必须恰好 1 次" % (p, cnt)}
        newfull = new if mode == "content" else cur.replace(old, new)
        if len(newfull.encode("utf-8")) > SELF_PATCH_MAX_BYTES:
            return {"ok": False, "error": "%s 改动后太大" % p}
        contents[p] = newfull
        a = (cur or "").splitlines(keepends=True)
        b = newfull.splitlines(keepends=True)
        diffs.append("".join(
            difflib.unified_diff(a, b, p + ".old", p + ".new", lineterm="")
        ))
        for tok in SELF_PROTECT:
            touched = (tok in new) if mode == "content" else (tok in (old or "") or tok in new)
            if touched:
                warns.append(tok)
        plan.append({"path": p, "mode": mode, "newfull": newfull})
    for it in plan:
        if it["path"].endswith(".py"):
            try:
                compile(it["newfull"], it["path"], "exec")
            except SyntaxError as e:
                return {"ok": False, "error": "语法检查失败: %s" % e}
    pid = _patch_next_id()
    d = _patch_dir(pid)
    os.makedirs(os.path.join(d, "backup"), exist_ok=True)
    meta = {
        "id": pid,
        "ts": int(time.time()),
        "reason": str(reason or "")[:500],
        "status": "pending",
        "warnings": sorted(set(warns)),
        "plan": plan,
    }
    _save_meta(pid, meta)
    with open(os.path.join(d, "diff.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(diffs))
    return {
        "ok": True,
        "id": pid,
        "files": [it["path"] for it in plan],
        "warnings": meta["warnings"],
        "diff": "\n".join(diffs)[:4000],
        "hint": "回复 /apply %d 应用，/drop %d 丢弃" % (pid, pid),
    }


def apply_self_patch(pid):
    meta = _load_meta(pid)
    if not meta:
        return {"ok": False, "error": "没有补丁 #%s" % pid}
    if meta.get("status") != "pending":
        return {"ok": False, "error": "补丁 #%s 状态是 %s，不能应用" % (pid, meta.get("status"))}
    d = _patch_dir(pid)
    originals = {}
    written = []
    missing = []
    try:
        for it in meta["plan"]:
            p = it["path"]
            try:
                with open(p, "rb") as f:
                    originals[p] = f.read()
            except FileNotFoundError:
                originals[p] = None
            rel = p.lstrip("/").replace(":", "_")
            bp = os.path.join(d, "backup", rel)
            os.makedirs(os.path.dirname(bp), exist_ok=True)
            if originals[p] is None:
                missing.append(p)
            else:
                with open(bp, "wb") as f:
                    f.write(originals[p])
            with open(p, "w", encoding="utf-8") as f:
                f.write(it["newfull"])
            written.append(p)
    except Exception as e:
        for p in written:
            try:
                if originals.get(p) is None:
                    os.remove(p)
                else:
                    with open(p, "wb") as f:
                        f.write(originals[p])
            except Exception:
                pass
        return {"ok": False, "error": "写入失败，已回滚: %s" % e}
    with open(os.path.join(d, "missing.json"), "w", encoding="utf-8") as f:
        json.dump(missing, f, ensure_ascii=False)
    meta["status"] = "applied"
    meta["applied_ts"] = int(time.time())
    _save_meta(pid, meta)
    return {"ok": True, "id": pid, "files": written}


def rollback_self_patch(pid):
    meta = _load_meta(pid)
    if not meta:
        return {"ok": False, "error": "没有补丁 #%s" % pid}
    if meta.get("status") != "applied":
        return {"ok": False, "error": "补丁 #%s 不是已应用状态" % pid}
    d = _patch_dir(pid)
    missing = load_json(os.path.join(d, "missing.json"), []) or []
    for it in meta["plan"]:
        p = it["path"]
        if p in missing:
            try:
                os.remove(p)
            except Exception:
                pass
            continue
        bp = os.path.join(d, "backup", p.lstrip("/").replace(":", "_"))
        try:
            with open(bp, "rb") as f:
                data = f.read()
            with open(p, "wb") as f:
                f.write(data)
        except Exception as e:
            return {"ok": False, "error": "回滚 %s 失败: %s" % (p, e)}
    meta["status"] = "rolled_back"
    _save_meta(pid, meta)
    return {"ok": True, "id": pid, "files": [it["path"] for it in meta["plan"]]}


def list_self_patches():
    out = []
    try:
        names = sorted(os.listdir(SELF_PATCH_DIR))
    except Exception:
        return out
    for fn in names:
        if not fn.startswith("p"):
            continue
        meta = load_json(os.path.join(SELF_PATCH_DIR, fn, "meta.json"))
        if meta:
            out.append(meta)
    return out


def schedule_restart():
    try:
        subprocess.Popen(
            ["sudo", "systemd-run", "--collect", "--on-active=2",
             "/bin/systemctl", "restart", "whale-girl.service"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except Exception:
        return False


async def notify_owner_patch(api, r):
    lines = [
        "【女儿想改自己的代码 #%d】" % r["id"],
        "要动的文件：%s" % "、".join(r["files"]),
    ]
    if r.get("warnings"):
        lines.append("⚠ 触到敏感符号：%s（请重点检查）" % "、".join(r["warnings"]))
    lines.append("改动如下：")
    lines.append(r.get("diff") or "(没有差异)")
    lines.append("要改就回 /apply %d，不改回 /drop %d。" % (r["id"], r["id"]))
    msg = "\n".join(lines)[:4000]
    for o in OWNER_QQ:
        try:
            await api("send_private_msg", {"user_id": o, "message": msg})
        except Exception:
            pass


# ---------- opencode 信箱桥（服务器 ↔ 爸爸电脑 ↔ opencode） ----------

def relay_to_opencode(message):
    """把爸爸的原话写进电脑上的收件箱，交给 opencode 处理。"""
    text = str(message or "").strip()[:4000]
    if not text:
        return {"ok": False, "error": "内容是空的哦"}
    b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    esc = RELAY_DIR.replace("'", "''")
    cmd = (
        "$ErrorActionPreference='Stop';"
        "$dir='%s';"
        "$sf=Join-Path $dir 'state.json';"
        "$n=1;"
        "if(Test-Path $sf){try{$o=Get-Content -Raw $sf|ConvertFrom-Json;$n=[int]$o.next}catch{}};"
        "New-Item -ItemType Directory -Force -Path $dir|Out-Null;"
        "$p=Join-Path $dir ('inbox\\{0}.txt' -f $n);"
        "New-Item -ItemType Directory -Force -Path (Split-Path $p)|Out-Null;"
        "[IO.File]::WriteAllText($p,[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('%s')),(New-Object Text.UTF8Encoding($false)));"
        "$o2=[PSCustomObject]@{next=($n+1)};"
        "($o2|ConvertTo-Json -Compress)|Set-Content -Encoding utf8 $sf;"
        "Write-Output ('SEQ='+$n)" % (esc, b64)
    )
    r = run_on_pc(cmd, 90)
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error")}
    seq = ""
    for line in (r.get("stdout") or "").splitlines():
        if line.startswith("SEQ="):
            seq = line[4:].strip()
    return {"ok": True, "seq": seq,
            "hint": "已写进收件箱，opencode 处理好后会答复到发件箱，回复 /get %s 就能取到。" % seq}


def fetch_opencode_reply(seq):
    """去发件箱取 opencode 对编号 seq 的答复。"""
    try:
        seq = int(seq)
    except Exception:
        return {"ok": False, "error": "seq 要是一个编号"}
    esc = RELAY_DIR.replace("'", "''")
    cmd = (
        "$dir='%s';"
        "$p=Join-Path $dir ('outbox\\{0}.txt' -f %d);"
        "if(Test-Path $p){[Convert]::ToBase64String([IO.File]::ReadAllBytes($p))}else{'NO_REPLY'}" % (esc, seq)
    )
    r = run_on_pc(cmd, 60)
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error")}
    out = (r.get("stdout") or "").strip()
    if out == "NO_REPLY" or not out:
        return {"ok": True, "new": False, "seq": seq}
    try:
        reply = base64.b64decode(out).decode("utf-8", "replace")
    except Exception:
        reply = out
    return {"ok": True, "new": True, "seq": seq, "reply": reply[:4000]}


def parse_segs(segs):
    text_parts = []
    at_me = False
    images = []
    records = []
    videos = []
    reply_id = None
    for seg in segs or []:
        t = seg.get("type")
        d = seg.get("data", {})
        if t == "text":
            text_parts.append(d.get("text", ""))
        elif t == "at":
            if str(d.get("qq")) == BOT_QQ or str(d.get("qq")) == "all":
                at_me = True
        elif t == "image":
            u = d.get("url") or d.get("file") or ""
            if u:
                images.append(u)
            text_parts.append("[图片]")
        elif t == "record":
            f = d.get("file") or d.get("url") or ""
            if f:
                records.append(f)
        elif t == "video":
            v = {"file": d.get("file") or "", "url": d.get("url") or "",
                 "path": d.get("path") or ""}
            if v["file"] or v["url"] or v["path"]:
                videos.append(v)
        elif t == "reply":
            rid = d.get("id") or d.get("message_id") or d.get("seq")
            if rid:
                reply_id = rid
    text = "".join(text_parts).strip()
    text = re.sub(r"^\s*", "", text)
    return text, at_me, images, records, reply_id, videos


def _asr_multipart(fields, filename, data):
    boundary = "----whaleasr" + hashlib.md5(
        ("%f%d" % (time.time(), len(data))).encode()).hexdigest()
    parts = []
    for k, v in fields.items():
        parts.append(("--%s\r\n" % boundary).encode())
        parts.append(('Content-Disposition: form-data; name="%s"\r\n\r\n' % k).encode())
        parts.append(str(v).encode("utf-8") + b"\r\n")
    parts.append(("--%s\r\n" % boundary).encode())
    parts.append(('Content-Disposition: form-data; name="file"; filename="%s"\r\n' % filename).encode())
    parts.append(b"Content-Type: application/octet-stream\r\n\r\n")
    parts.append(data + b"\r\n")
    parts.append(("--%s--\r\n" % boundary).encode())
    return boundary, b"".join(parts)


def asr_transcribe(filename, data):
    if not ASR_ENABLE or not ASR_KEY or not data:
        return None
    if len(data) > ASR_MAX_BYTES:
        log.warning("语音过大跳过 ASR (%d bytes)", len(data))
        return None
    boundary, body = _asr_multipart(
        {"model": ASR_MODEL, "stream": "false"}, filename, data)
    req = urllib.request.Request(
        ASR_BASE + "/audio/transcriptions", data=body,
        headers={"Authorization": "Bearer " + ASR_KEY,
                 "Content-Type": "multipart/form-data; boundary=" + boundary},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            r = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            detail = ""
        log.warning("ASR HTTP %s: %s", e.code, detail)
        return None
    except Exception as e:
        log.warning("ASR 调用失败: %s", e)
        return None
    txt = (r.get("text") or "").strip()
    if not txt and isinstance(r.get("choices"), list):
        try:
            txt = (r["choices"][0]["message"]["content"] or "").strip()
        except Exception:
            txt = ""
    if txt and ASR_CLEAN:
        txt = ai_clean_asr(txt) or txt
    return txt or None


def ai_clean_asr(raw):
    """用 LLM 修掉语音识别的同音字/断句问题；失败返回原文。"""
    raw = (raw or "").strip()
    if not raw or not DEEPSEEK_KEY:
        return raw
    try:
        msg = deepseek(
            [{"role": "system", "content": P("asr_clean", _DEF_ASR_CLEAN)},
             {"role": "user", "content": raw}], False)
        out = (msg.get("content") or "").strip().strip('"').strip("「」").strip()
        if 0 < len(out) <= 400:
            return out
    except Exception as e:
        log.info("ASR 润色失败: %s", e)
    return raw


def _read_record_bytes(path):
    """PC 模式直接读本地文件；路径不在本机时经 PC 执行端读回 base64。"""
    try:
        if path and os.path.exists(path):
            with open(path, "rb") as f:
                return f.read()
    except Exception:
        pass
    esc = str(path).replace("'", "''")
    r = run_on_pc("[Convert]::ToBase64String([IO.File]::ReadAllBytes('%s'))" % esc, 60)
    if r.get("ok"):
        try:
            return base64.b64decode((r.get("stdout") or "").strip())
        except Exception:
            return None
    return None


async def transcribe_voice(api, records):
    """把语音段转成文字，返回 "[语音]xxx"；失败返回 None。"""
    if not ASR_ENABLE:
        return None
    texts = []
    for rf in records[:2]:
        try:
            r = await api("get_record", {"file": rf, "out_format": "mp3"}, timeout=30)
        except Exception as e:
            log.warning("get_record 失败: %s", e)
            r = None
        path = None
        try:
            path = ((r or {}).get("data") or {}).get("file")
        except Exception:
            path = None
        if not path:
            log.warning("get_record 未返回路径: %s", r)
            continue
        data = await asyncio.get_event_loop().run_in_executor(
            None, _read_record_bytes, path)
        if not data:
            log.warning("语音读取失败: %s", path)
            continue
        txt = await asyncio.get_event_loop().run_in_executor(
            None, asr_transcribe, os.path.basename(path), data)
        if txt:
            texts.append(txt)
    if not texts:
        return None
    return "[语音]" + " / ".join(texts)


def _download_bytes(url, max_bytes=60 * 1024 * 1024):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read(max_bytes)


def _ffmpeg_flags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _video_duration(path):
    try:
        r = subprocess.run(
            [FFPROBE_EXE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nk=1:nw=1", path],
            capture_output=True, timeout=30, creationflags=_ffmpeg_flags())
        return float((r.stdout or b"").strip() or 0.0)
    except Exception:
        return 0.0


def _extract_video(path):
    """抽关键帧 + 抽音轨转文字，返回 {"frames":[...], "transcript": str|None, "dur": float, "dir": str}。"""
    dur = _video_duration(path)
    outdir = tempfile.mkdtemp(prefix="vidf_")
    n = max(1, VIDEO_MAX_FRAMES)
    fps = 1.0
    if dur and dur > 0:
        fps = min(max(float(n) / dur, 0.05), 2.0)
    jpg = os.path.join(outdir, "f_%02d.jpg")
    try:
        subprocess.run(
            [FFMPEG_EXE, "-y", "-hide_banner", "-loglevel", "error",
             "-t", str(VIDEO_MAX_SECONDS), "-i", path,
             "-vf", "fps=%.5f,scale=512:-2" % fps,
             "-frames:v", str(n), jpg],
            capture_output=True, timeout=120, creationflags=_ffmpeg_flags())
    except Exception as e:
        log.warning("视频抽帧失败: %s", e)
    frames = sorted(os.path.join(outdir, f)
                    for f in os.listdir(outdir) if f.endswith(".jpg"))
    transcript = None
    if ASR_ENABLE and ASR_KEY:
        wav = os.path.join(outdir, "a.wav")
        try:
            subprocess.run(
                [FFMPEG_EXE, "-y", "-hide_banner", "-loglevel", "error",
                 "-t", str(VIDEO_MAX_SECONDS), "-i", path,
                 "-vn", "-ac", "1", "-ar", "16000", wav],
                capture_output=True, timeout=120, creationflags=_ffmpeg_flags())
            if os.path.exists(wav) and os.path.getsize(wav) > 1000:
                with open(wav, "rb") as f:
                    data = f.read()
                if len(data) <= ASR_MAX_BYTES:
                    transcript = asr_transcribe("video.wav", data)
        except Exception as e:
            log.warning("视频音轨 ASR 失败: %s", e)
    return {"frames": frames, "transcript": transcript, "dur": dur, "dir": outdir}


def _video_local_path(v):
    for c in (v.get("path"), v.get("file")):
        c = str(c or "")
        if not c:
            continue
        if c.startswith("file://"):
            c = urllib.request.url2pathname(c[7:])
        if os.path.isfile(c):
            return c
    return None


async def describe_video(api, videos):
    """拿到视频文件 → 抽关键帧 + 音轨转文字。返回 dict 或 None。"""
    if not VIDEO_ENABLE or not videos:
        return None
    v = videos[0]
    path = _video_local_path(v)
    if not path:
        for method in ("get_video", "get_file"):
            try:
                r = await api(method, {"file": v.get("file") or v.get("url")}, timeout=30)
            except Exception as e:
                log.warning("%s 失败: %s", method, e)
                continue
            d = (r or {}).get("data") or {}
            p2 = d.get("file") or d.get("path") or d.get("url")
            if p2 and os.path.isfile(str(p2)):
                path = str(p2)
                break
    tmp = None
    if not path and v.get("url"):
        try:
            data = await asyncio.get_event_loop().run_in_executor(
                None, _download_bytes, v["url"])
            if data:
                tmp = os.path.join(tempfile.gettempdir(),
                                   "vid_in_%d.mp4" % int(time.time() * 1000))
                with open(tmp, "wb") as f:
                    f.write(data)
                path = tmp
        except Exception as e:
            log.warning("视频下载失败: %s", e)
    if not path:
        log.warning("视频无法获取: %s", v)
        return None
    try:
        info = await asyncio.get_event_loop().run_in_executor(None, _extract_video, path)
    except Exception as e:
        log.warning("视频处理失败: %s", e)
        info = None
    if tmp:
        try:
            os.remove(tmp)
        except Exception:
            pass
    return info


async def reply_target_is_bot(api, reply_id):
    """判断被引用的那条消息是不是机器人自己发的。"""
    if not reply_id:
        return False
    mid = reply_id
    try:
        mid = int(reply_id)
    except (TypeError, ValueError):
        pass
    try:
        r = await api("get_msg", {"message_id": mid}, timeout=10)
    except Exception as e:
        log.warning("get_msg 失败: %s", e)
        return False
    d = (r or {}).get("data") or {}
    sender = d.get("sender") or {}
    uid = sender.get("user_id") or d.get("user_id")
    return str(uid) == BOT_QQ


async def fetch_quoted_videos(api, reply_id):
    """取被引用那条消息里的视频段；没有则返回 []。"""
    if not reply_id:
        return []
    mid = reply_id
    try:
        mid = int(reply_id)
    except (TypeError, ValueError):
        pass
    try:
        r = await api("get_msg", {"message_id": mid}, timeout=20)
    except Exception as e:
        log.warning("get_msg(取引用视频) 失败: %s", e)
        return []
    d = (r or {}).get("data") or {}
    msg = d.get("message")
    if msg is None and d.get("message_id") is None:
        log.warning("get_msg 未返回消息体: %s", str(r)[:200])
    _, _, _, _, _, vids = parse_segs(msg if isinstance(msg, list) else [])
    return vids


# ---------- TTS：把回复念成语音发出去 ----------

def _clean_for_tts(text):
    t = TAG_RE.sub("", text or "")
    t = ACTION_RE.sub("", t)
    t = re.sub(r"\[[^\]]{0,16}\]", "", t)
    t = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\u2190-\u2BFF]", "", t)
    t = re.sub(r"[\r\n]+", " ", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t


def _tts_path(ext):
    return os.path.join(TTS_CACHE, "tts_%d.%s" % (int(time.time() * 1000), ext))


def strip_voice_tags(text):
    return VOICE_TAG_RE.sub("", text or "")


VOICE_SCRIPT_RE = re.compile(r"^[ \t]*(?:[-*]\s*)?语音台词[：:]\s*([^\n]*)$", re.M)


def split_voice_script(text):
    """从双文本里拆出语音台词：返回 (展示正文, 台词或None)。"""
    t = text or ""
    m = VOICE_SCRIPT_RE.search(t)
    if not m:
        return strip_voice_tags(t), None
    script = m.group(1).strip()
    display = (t[:m.start()] + t[m.end():]).strip()
    return strip_voice_tags(display), (script or None)


VOICE_SEG_SPLIT_RE = re.compile(
    r"(\[\[\s*[^\[\]]{1,48}?\s*\]\]|\[(?:快|慢|大声|轻声|正常)\])")


def split_voice_segs(text, max_total=150):
    """把 [快][慢][大声][轻声][正常] 当作语速/音量开关、
    [[情绪]] 当作情绪开关解析，返回 (去掉标签的显示文本, [(段文本, speed, gain_db, emotion)])。"""
    display = strip_voice_tags(text or "")
    parts = VOICE_SEG_SPLIT_RE.split(text or "")
    speed, gain, emotion = 1.0, 0.0, None
    segs = []
    used = 0
    for p in parts:
        if not p:
            continue
        if p.startswith("[[") and p.endswith("]]"):
            emo = normalize_emotion(p[2:-2])
            if emo is not None:
                emotion = emo
            continue
        if p.startswith("[") and p.endswith("]"):
            name = p[1:-1]
            if name == "正常":
                speed, gain = 1.0, 0.0
            elif name in VOICE_TAG_SPEED:
                speed = VOICE_TAG_SPEED[name]
            elif name in VOICE_TAG_GAIN:
                gain = VOICE_TAG_GAIN[name]
            continue
        c = _clean_for_tts(p)
        if not c:
            continue
        if len(c) > max_total - used:
            c = c[:max(0, max_total - used)]
        if c:
            segs.append((c, speed, gain, emotion))
            used += len(c)
        if used >= max_total:
            break
    return display, segs


def _gain_wav(path, db, base=0.80, ceiling=0.97):
    """先把整段归一化到基准峰值(base)，再叠加分贝增益，并限幅到 ceiling。
    全程只做线性缩放，绝不硬削波，避免破音/杂音。"""
    try:
        with wave.open(path, "rb") as w:
            params = w.getparams()
            frames = w.readframes(w.getnframes())
        if params.sampwidth != 2 or params.nchannels != 1 or not frames:
            return
        a = array.array("h")
        a.frombytes(frames)
        peak = max(abs(x) for x in a)
        if peak < 200:
            return
        target = base * (10.0 ** (db / 20.0))
        if target > ceiling:
            target = ceiling
        factor = (target * 32767) / peak
        a = array.array("h", (int(x * factor) for x in a))
        with wave.open(path, "wb") as w:
            w.setparams(params)
            w.writeframes(a.tobytes())
    except Exception as e:
        log.warning("音量调整失败: %s", e)


def _tts_sapi(text):
    wav = _tts_path("wav")
    b64 = base64.b64encode(text.encode("utf-8")).decode()
    voice = ('try{$s.SelectVoice("%s")}catch{};' % TTS_VOICE) if TTS_VOICE else ""
    ps = (
        "Add-Type -AssemblyName System.Speech;"
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        + voice
        + "$t=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('%s'));" % b64
        + "$s.SetOutputToWaveFile('%s');" % wav.replace("'", "''")
        + "$s.Speak($t);$s.Dispose()"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            timeout=90, creationflags=0x08000000,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        log.warning("SAPI TTS 失败: %s", e)
        return None
    if os.path.exists(wav) and os.path.getsize(wav) > 200:
        return wav
    return None


def _tts_edge(text):
    try:
        import edge_tts
    except Exception:
        return None
    mp3 = _tts_path("mp3")
    voice = TTS_VOICE or "zh-CN-XiaoxiaoNeural"

    async def _go():
        await edge_tts.Communicate(text, voice).save(mp3)

    try:
        asyncio.run(_go())
    except Exception as e:
        log.warning("edge-tts 失败: %s", e)
        return None
    return mp3 if os.path.exists(mp3) and os.path.getsize(mp3) > 200 else None


def _soften_onset(path, target=0.9):
    """把开头第一个音节整体压轻（默认降到约 0.9 倍全句均响），消除'首字读太重'。
    仅处理第一段连续发声区，用指数渐变包络避免突兀，结尾保持原样。"""
    try:
        w = wave.open(path, "rb")
        nch, sw, sr, nf = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        if sw != 2 or nch != 1:
            w.close()
            return "skip-unsupported"
        raw = bytearray(w.readframes(nf))
        w.close()
        n = nf
        samples = struct.unpack("<%dh" % n, raw)
        win = int(0.010 * sr)
        hop = int(0.005 * sr)
        rms = []
        i = 0
        while i + win <= n:
            s = 0.0
            for j in range(i, i + win):
                v = samples[j]
                s += v * v
            rms.append(math.sqrt(s / win))
            i += hop
        if not rms:
            return "empty"
        mx = max(rms)
        if mx <= 0:
            return "silence"
        thr = 0.05 * mx
        idx = [k for k, kx in enumerate(rms) if kx > thr]
        if not idx:
            return "no-voice"
        groups, g = [], [idx[0]]
        for prev, cur in zip(idx, idx[1:]):
            if cur - prev > 12:
                groups.append(g)
                g = [cur]
            else:
                g.append(cur)
        groups.append(g)
        f = groups[0]
        all_mean = sum(rms[k] for k in idx) / len(idx)
        on_mean = sum(rms[k] for k in f) / len(f)
        if on_mean <= all_mean * target:
            return "already-light"
        d = (all_mean * target) / on_mean
        a = f[0] * hop
        b = f[-1] * hop + win
        nseg = max(b - a, 1)
        sr4 = int(0.020 * sr)
        for k in range(a, b):
            t = (k - a) / nseg
            env = 1 - (1 - d) * math.exp(-4.5 * t)
            if k - a < sr4:
                env *= (k - a) / max(sr4 - 1, 1)
            raw[2 * k : 2 * k + 2] = struct.pack("<h", int(samples[k] * env))
        with wave.open(path, "wb") as wo:
            wo.setnchannels(nch)
            wo.setsampwidth(sw)
            wo.setframerate(sr)
            wo.writeframes(bytes(raw))
        return "softened"
    except Exception:
        return "error"


def _denoise_wav(path):
    """用 ffmpeg 做轻度降噪/高通，失败则原样保留。"""
    if not TTS_DENOISE or not FFMPEG_EXE:
        return
    tmp = path + ".dn.wav"
    try:
        r = subprocess.run(
            [FFMPEG_EXE, "-y", "-hide_banner", "-loglevel", "error", "-i", path,
             "-af", TTS_DENOISE_AF, "-ac", "1", "-sample_fmt", "s16", tmp],
            capture_output=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 44:
            os.replace(tmp, path)
        else:
            log.warning("ffmpeg 降噪失败 rc=%s %s", r.returncode, (r.stderr or b"")[:200])
    except Exception as e:
        log.warning("ffmpeg 降噪异常: %s", e)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def _dehiss_wav(path, cut=4000.0, quiet_frac=0.18, power=3.0):
    """消除 GPT-SoVITS 声码器在低能量段偶发的窄带高频哨音（~4.5-6kHz）。
    仅在低能量帧衰减 >cut 的高频，讲话/齿音帧不动，可在保留清晰度的前提下去哨。"""
    if not TTS_DEHISS:
        return
    try:
        import numpy as np
        w = wave.open(path, "rb")
        nch, sw, sr = w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(w.getnframes())
        w.close()
        if sw != 2:
            return
        a = np.frombuffer(raw, dtype="<i2").astype(np.float64)
        if nch > 1:
            a = a.reshape(-1, nch)[:, 0]
        n = len(a)
        if n < 4096:
            return
        N = 1024
        H = N // 4
        win = np.hanning(N)
        nf = 1 + (n - N) // H
        E = np.empty(nf)
        Fs = np.empty((nf, N // 2 + 1), dtype=complex)
        for k in range(nf):
            s = a[k * H:k * H + N]
            E[k] = np.sqrt(np.mean(s * s))
            Fs[k] = np.fft.rfft(s * win)
        med = float(np.median(E[E > 0])) if np.any(E > 0) else 0.0
        if med <= 0:
            return
        thr = quiet_frac * med
        him = np.fft.rfftfreq(N, 1.0 / sr) > cut
        out = np.zeros(n + N)
        wsum = np.zeros(n + N)
        for k in range(nf):
            if E[k] >= thr:
                F = Fs[k]
            else:
                F = Fs[k].copy()
                F[him] *= (E[k] / thr) ** power
            out[k * H:k * H + N] += np.fft.irfft(F, N) * win
            wsum[k * H:k * H + N] += win * win
        res = out[:n] / np.maximum(wsum[:n], 1e-6)
        res = np.clip(res, -32768, 32767).astype("<i2")
        w2 = wave.open(path, "wb")
        w2.setnchannels(1)
        w2.setsampwidth(2)
        w2.setframerate(sr)
        w2.writeframes(res.tobytes())
        w2.close()
    except Exception as e:
        log.warning("dehiss 去哨失败: %s", e)


def _gbk_safe(text):
    """GPT-SoVITS 内部会用 GBK 编码，含 GBK 无法编码的字符（如颜文字 ﾉ ✧）会 400。
    这里过滤掉这些字符，只保留可朗读的内容。"""
    return "".join(ch for ch in (text or "") if _gbk_ok(ch))


def _gbk_ok(ch):
    try:
        ch.encode("gbk")
        return True
    except Exception:
        return False


def _wav_peak(data):
    try:
        w = wave.open(io.BytesIO(data), "rb")
        a = array.array("h")
        a.frombytes(w.readframes(w.getnframes()))
        w.close()
        return max((abs(x) for x in a), default=0)
    except Exception:
        return 0


def _wav_rms(data):
    try:
        w = wave.open(io.BytesIO(data), "rb")
        a = array.array("h")
        a.frombytes(w.readframes(w.getnframes()))
        w.close()
        if not a:
            return 0
        return int((sum(x * x for x in a) / len(a)) ** 0.5)
    except Exception:
        return 0


def _tts_sovits(text, speed=1.0, gain_db=0.0, emotion=None):
    text = _gbk_safe(text).strip()
    if not text or not TTS_SOVITS_REF:
        return None
    wav = _tts_path("wav")
    ref_path = TTS_SOVITS_REF
    prompt_text = CFG.get("tts_sovits_prompt_text", "")
    prompt_lang = CFG.get("tts_sovits_prompt_lang", "zh")
    base_temp = float(CFG.get("tts_sovits_temperature", 1.05))
    emo = VOICE_EMOTIONS.get(emotion or "")
    if emo:
        speed = speed * float(emo.get("speed", 1.0))
        gain_db = gain_db + float(emo.get("gain", 0.0))
        base_temp += float(emo.get("temp", 0.0))
        rc = TTS_SOVITS_EMO_REFS.get(emotion)
        if isinstance(rc, dict):
            rp = str(rc.get("ref") or "")
            if rp and os.path.exists(rp):
                ref_path = rp
                prompt_text = rc.get("prompt_text", prompt_text)
                prompt_lang = rc.get("prompt_lang", prompt_lang)
            elif rp:
                log.warning("情绪参考音频不存在，用默认 ref: %s", rp)
    speed = max(0.6, min(1.6, speed))
    if len(text) <= 6:
        temps = (max(base_temp, 1.25), 1.4, 1.5)
    else:
        temps = (base_temp, base_temp + 0.2, base_temp + 0.4)
    data = None
    for i, temp in enumerate(temps):
        body = {
            "text": text,
            "text_lang": "zh",
            "ref_audio_path": ref_path,
            "prompt_lang": prompt_lang,
            "prompt_text": prompt_text,
            "top_k": CFG.get("tts_sovits_top_k", 20),
            "top_p": CFG.get("tts_sovits_top_p", 0.95),
            "temperature": temp,
            "speed_factor": speed,
            "media_type": "wav",
        }
        try:
            req = urllib.request.Request(
                TTS_SOVITS_URL + "/tts",
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = resp.read()
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                detail = ""
            log.warning("GPT-SoVITS 失败: HTTP %s %s", e.code, detail)
            return None
        except Exception as e:
            log.warning("GPT-SoVITS 失败: %s", e)
            return None
        if len(data) < 200:
            data = None
            continue
        peak = _wav_peak(data)
        rms = _wav_rms(data)
        if peak >= 2000 and rms >= 600:
            break
        log.warning("GPT-SoVITS 输出疑似无效(peak=%d rms=%d len=%d) t=%.2f 第%d次，升温重试",
                    peak, rms, len(data), temp, i + 1)
        data = None
    if not data:
        return None
    try:
        with open(wav, "wb") as f:
            f.write(data)
    except Exception:
        return None
    _soften_onset(wav)
    _denoise_wav(wav)
    _dehiss_wav(wav)
    _gain_wav(wav, gain_db)
    return wav


def _tts_indextts(text, speed=1.0, gain_db=0.0, emotion=None):
    """调用 IndexTTS(WIndexTTS) OpenAI 兼容接口合成。音色由 voice 决定，
    情感由 emo_text 控制，语速由 speed 控制。"""
    text = (text or "").strip()
    if not text:
        return None
    wav = _tts_path("wav")
    # 统一音色模式下忽略情绪对音色/语速/音量的影响（[快][慢][大声][轻声] 等标签仍生效）。
    emo = None if TTS_INDEXTTS_UNIFIED_EMO else VOICE_EMOTIONS.get(emotion or "")
    base_temp = TTS_INDEXTTS_TEMPERATURE
    speed = speed * TTS_INDEXTTS_SPEED_BASE
    if emo:
        speed = speed * float(emo.get("speed", 1.0))
        gain_db = gain_db + float(emo.get("gain", 0.0))
        base_temp += float(emo.get("temp", 0.0))
    speed = max(0.6, min(1.6, speed))
    if TTS_INDEXTTS_UNIFIED_EMO:
        emo_text = TTS_INDEXTTS_UNIFIED_EMO
    elif emotion in TTS_INDEXTTS_EMO_TEXT:
        emo_text = TTS_INDEXTTS_EMO_TEXT[emotion]
    else:
        emo_text = TTS_INDEXTTS_DEFAULT_EMO
    ep = {
        "language": "ZH",
        "temperature": max(0.1, min(1.5, base_temp)),
        "top_p": TTS_INDEXTTS_TOP_P,
        "top_k": TTS_INDEXTTS_TOP_K,
        "cfm_steps": TTS_INDEXTTS_CFM_STEPS,
        "num_beams": TTS_INDEXTTS_NUM_BEAMS,
    }
    if emo_text:
        ep["emo_text"] = emo_text
    body = {
        "model": "windextts",
        "input": text,
        "voice": TTS_INDEXTTS_VOICE,
        "response_format": "wav",
        "speed": speed,
        "extra_params": ep,
    }
    try:
        req = urllib.request.Request(
            TTS_INDEXTTS_URL + "/v1/audio/speech",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            detail = ""
        log.warning("IndexTTS 失败: HTTP %s %s", e.code, detail)
        return None
    except Exception as e:
        log.warning("IndexTTS 失败: %s", e)
        return None
    if not data or len(data) < 200:
        return None
    if _wav_peak(data) < 2000 or _wav_rms(data) < 600:
        log.warning("IndexTTS 输出疑似无效(peak=%d rms=%d len=%d)",
                    _wav_peak(data), _wav_rms(data), len(data))
        return None
    try:
        with open(wav, "wb") as f:
            f.write(data)
    except Exception:
        return None
    _gain_wav(wav, gain_db)
    return wav


def _strip_glm_watermark(data):
    """GLM-TTS 会在音频开头固定加上约 1.808s 的 550Hz 提示音（AI 显式水印），
    这里在校验前缀与已知水印完全一致后裁掉它。校验不过则原样返回。"""
    try:
        w = wave.open(io.BytesIO(data), "rb")
        params = w.getparams()
        frames = w.readframes(w.getnframes())
        w.close()
        if params.sampwidth != 2 or params.nchannels != 1:
            return data
        if not TTS_GLM_WM_MD5:
            return data
        nbytes = TTS_GLM_WM_SAMPLES * 2
        if len(frames) <= nbytes:
            return data
        if hashlib.md5(frames[:nbytes]).hexdigest() != TTS_GLM_WM_MD5:
            log.warning("GLM-TTS 水印前缀不匹配，未裁剪")
            return data
        out = io.BytesIO()
        wr = wave.open(out, "wb")
        wr.setparams(params)
        wr.writeframes(frames[nbytes:])
        wr.close()
        log.info("GLM-TTS 已裁掉开头水印 %.3fs", TTS_GLM_WM_SAMPLES / params.framerate)
        return out.getvalue()
    except Exception as e:
        log.warning("GLM-TTS 裁水印失败: %s", e)
        return data


def _tts_glm(text):
    if not TTS_GLM_KEY:
        return None
    wav = _tts_path("wav")
    body = {
        "model": TTS_GLM_MODEL,
        "input": text,
        "voice": TTS_VOICE or "female",
        "speed": TTS_SPEED,
        "volume": TTS_VOLUME,
        "response_format": "wav",
    }
    try:
        req = urllib.request.Request(
            TTS_GLM_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": "Bearer " + TTS_GLM_KEY,
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            detail = ""
        log.warning("GLM-TTS HTTP %s: %s", e.code, detail)
        return None
    except Exception as e:
        log.warning("GLM-TTS 失败: %s", e)
        return None
    if len(data) < 200:
        return None
    if TTS_GLM_STRIP_WM:
        data = _strip_glm_watermark(data)
    try:
        with open(wav, "wb") as f:
            f.write(data)
    except Exception:
        return None
    return wav


def _tts_one(text, speed=1.0, gain_db=0.0, emotion=None):
    """按当前引擎合成一段。返回 wav 路径或 None。"""
    if TTS_ENGINE == "indextts":
        return _tts_indextts(text, speed=speed, gain_db=gain_db, emotion=emotion)
    if TTS_ENGINE == "gptsovits":
        return _tts_sovits(text, speed=speed, gain_db=gain_db, emotion=emotion)
    return None


def tts_synthesize(text):
    if not TTS_ENABLE or not text:
        return None
    if TTS_ENGINE == "glm":
        p = _tts_glm(text) or _tts_sapi(text)
    elif TTS_ENGINE == "edge":
        p = _tts_edge(text) or _tts_sapi(text)
    elif TTS_ENGINE == "indextts":
        p = (_tts_indextts(text) or _tts_sovits(text)
             or _tts_glm(text) or _tts_sapi(text))
    elif TTS_ENGINE == "gptsovits":
        p = _tts_sovits(text) or _tts_glm(text) or _tts_sapi(text)
    else:
        p = _tts_sapi(text)
    return p


def _tts_file_uri(path):
    return "file:///" + str(path).replace("\\", "/")


def tts_make_segment(text):
    return _tts_record(tts_synthesize(text))


def tts_make_segment_tagged(segs, fallback_text):
    """按 [快][慢][大声][轻声][[情绪]] 分段合成；失败或引擎不支持时退回整句。"""
    if TTS_ENGINE not in ("gptsovits", "indextts") or not segs:
        return tts_make_segment(fallback_text)
    seg = _tts_record(_tts_synth_multi(segs))
    return seg if seg else tts_make_segment(fallback_text)


def _tts_synth_multi(segs):
    buf = io.BytesIO()
    first = None
    rate = None
    for txt, speed, gain, emotion in segs:
        wav = _tts_one(txt, speed=speed, gain_db=gain, emotion=emotion)
        if not wav:
            log.warning("分段合成失败(%s)，退回整句", txt[:20])
            return None
        try:
            with wave.open(wav, "rb") as f:
                p = f.getparams()
                data = f.readframes(f.getnframes())
        except Exception:
            return None
        if p.sampwidth != 2 or p.nchannels != 1:
            log.warning("分段采样格式异常，退回整句")
            return None
        if rate is None:
            rate = p.framerate
            first = p
        elif p.framerate != rate:
            log.warning("分段采样率不一致，退回整句")
            return None
        buf.write(data)
    if first is None or not buf.getvalue():
        return None
    out = _tts_path("wav")
    try:
        with wave.open(out, "wb") as f:
            f.setparams(first)
            f.writeframes(buf.getvalue())
    except Exception:
        return None
    _soften_onset(out)
    return out


def _tts_record(path):
    if not path:
        return None
    if NAPCAT_LOCAL:
        return {"type": "record", "data": {"file": _tts_file_uri(path)}}
    try:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
    except Exception:
        return None
    ps = (
        "$p=Join-Path $env:TEMP ('whalebot_tts\\tts_%d.mp3');" % int(time.time() * 1000)
        + "New-Item -ItemType Directory -Force -Path (Split-Path $p)|Out-Null;"
        + "[IO.File]::WriteAllBytes($p,[Convert]::FromBase64String('%s'));" % b64
        + "Write-Output $p"
    )
    r = run_on_pc(ps, 60)
    out = (r or {}).get("stdout") or ""
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if not ((r or {}).get("ok") and lines):
        log.warning("TTS 推送 PC 失败: %s", r)
        return None
    return {"type": "record", "data": {"file": _tts_file_uri(lines[-1])}}


def _norm_s(s):
    return re.sub(r"[\s\[\]【】*]", "", s or "").lower()


def find_dup(reply, prevs):
    a = _norm_s(reply)
    if not a:
        return None
    for p in prevs:
        b = _norm_s(p)
        if not b:
            continue
        if len(a) < 20 or len(b) < 20:
            if a == b:
                return p
            continue
        if a in b or b in a:
            return p
        if SequenceMatcher(None, a, b).ratio() >= 0.9:
            return p
    return None


def caption_image(data_url):
    """用视觉模型把一张图转成一句文字描述；失败返回 None。"""
    if not data_url or not DEEPSEEK_KEY:
        return None
    body = {
        "model": MODEL,
        "temperature": 0.3,
        "max_tokens": 200,
        "messages": [
            {"role": "system", "content": P("image_caption", _DEF_IMAGE_CAPTION)},
            {"role": "user", "content": [
                {"type": "text", "text": "描述这张图。"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ],
    }
    try:
        r = http_json(DEEPSEEK_BASE + "/chat/completions", body,
                      {"Authorization": "Bearer " + DEEPSEEK_KEY}, timeout=120)
        out = (r["choices"][0]["message"].get("content") or "").strip()
        return out or None
    except Exception as e:
        log.warning("图片描述失败: %s", e)
        return None


def summarize_video(frames, transcript, limit=2):
    """把视频关键帧+字幕概括成一段文字；失败返回 None。"""
    if not DEEPSEEK_KEY:
        return None
    head = "这是视频的关键帧"
    if transcript:
        head += "（视频里的语音是：%s）" % transcript
    parts = [{"type": "text", "text": head + "。"}]
    for f in (frames or [])[:limit]:
        try:
            du = fetch_image_data_url(f)
        except Exception:
            du = None
        if du:
            parts.append({"type": "image_url", "image_url": {"url": du}})
    if len(parts) == 1 and not transcript:
        return None
    body = {
        "model": MODEL,
        "temperature": 0.3,
        "max_tokens": 1600,
        "messages": [
            {"role": "system", "content": P("video_understand", _DEF_VIDEO_UNDERSTAND)},
            {"role": "user", "content": parts},
        ],
    }
    try:
        r = http_json(DEEPSEEK_BASE + "/chat/completions", body,
                      {"Authorization": "Bearer " + DEEPSEEK_KEY}, timeout=150)
        out = (r["choices"][0]["message"].get("content") or "").strip()
        return out or None
    except Exception as e:
        log.warning("视频概括失败: %s", e)
        return None


def build_video_desc(info):
    """把视频检测结果（关键帧概括 + 语音转写 + 时长）拼成一段给用户看的描述。"""
    info = info or {}
    frames = list(info.get("frames") or [])
    transcript = (info.get("transcript") or "").strip()
    dur = info.get("dur") or 0.0
    summ = None
    if frames or transcript:
        summ = summarize_video(frames, transcript)
    parts = []
    if dur:
        parts.append("时长约 %.1f 秒" % dur)
    if summ:
        parts.append("画面内容：%s" % summ)
    elif frames:
        parts.append("画面：已抽取 %d 张关键帧（画面识别没返回结果）" % len(frames))
    if transcript:
        parts.append("视频里的语音：%s" % transcript)
    if not parts:
        return None
    return "视频检测结果～(๑•̀ㅂ•́)و✧\n" + "\n".join("- " + p for p in parts)


async def think(chat_key, text, allow_tools, api, group_ctx=None, images=None, dedupe_against=None, identity=None, restricted=False, sender=None):
    global _paused
    if chat_key not in _hists:
        _hists[chat_key] = []
    hist = _hists[chat_key]
    hist.append({"role": "user", "content": text})

    if allow_tools is True:
        active_tools = list(TOOLS)
    elif allow_tools:
        names = set(allow_tools)
        active_tools = [t for t in TOOLS if t["function"]["name"] in names]
    else:
        active_tools = []
    tool_names = [t["function"]["name"] for t in active_tools]
    use_tools = bool(active_tools) and ENABLE_TOOLS and not _paused
    tail = hist[-MAX_HISTORY:]
    sysprefix = [{"role": "system", "content": PERSONA}]
    if "propose_self_patch" in tool_names:
        sysprefix.append({"role": "system", "content": P("self_patch", _DEF_SELF_PATCH)})
    omemo = read_owner_memo()
    if omemo:
        sysprefix.append({"role": "system", "content": OWNER_MEMO_HINT + "\n".join(omemo) + "）"})
    if identity:
        sysprefix.append({"role": "system", "content": identity})
    if isinstance(chat_key, str) and chat_key.startswith("group:"):
        gpy = load_group_persona(chat_key.split(":", 1)[1])
        if gpy:
            sysprefix.append({"role": "system", "content": GROUP_PERSONA_HINT + gpy})
    if group_ctx:
        sysprefix.append({"role": "system", "content": group_ctx})
    kwlist = meme_keywords_for(chat_key)
    if EMOJI_ENABLE and kwlist:
        shown = _model_hint_keywords(kwlist)
        if shown:
            sysprefix.append({"role": "system", "content":
                              P("meme_hint", _DEF_MEME_HINT, kwlist="、".join(shown))})
    hint = pick_corpus()
    if hint:
        sysprefix.append({"role": "system", "content": P("corpus_hint", _DEF_CORPUS_HINT) + hint})
    if use_tools and tail and tail[-1].get("role") == "user":
        reminder = (P("tool_reminder_safe", _DEF_TOOL_REMINDER_SAFE) if restricted
                    else P("tool_reminder", _DEF_TOOL_REMINDER))
        messages = (
            sysprefix
            + tail[:-1]
            + [{"role": "system", "content": reminder}, tail[-1]]
        )
    else:
        messages = sysprefix + tail

    if VISION_ENABLE and images:
        img_entries = []
        captions = []
        for u in images[:4]:
            try:
                du = await asyncio.get_event_loop().run_in_executor(
                    None, fetch_image_data_url, u
                )
            except Exception as e:
                log.warning("图片获取失败: %s", e)
                continue
            if IMAGE_CAPTION:
                cap = await asyncio.get_event_loop().run_in_executor(
                    None, caption_image, du)
                if cap:
                    captions.append(cap)
                    continue
            img_entries.append({"type": "image_url", "image_url": {"url": du}})
        if img_entries or captions:
            last = messages[-1]
            text0 = last.get("content") or "[图片]"
            if captions:
                text0 = text0 + "\n[图片内容] " + " / ".join(captions)
            messages[-1] = dict(last)
            if img_entries:
                parts = [{"type": "text", "text": text0}]
                parts += img_entries
                messages[-1]["content"] = parts
            else:
                messages[-1]["content"] = text0
    reply_text = ""

    sent_fp = set()
    _turn_tool_images[chat_key] = set()

    async def _send_tool_image(r):
        """发工具图，并对同一条回复内的重复图去重（同一张只发一次）。"""
        fp = _seg_fingerprint(r.get("seg") or {})
        if fp and fp in sent_fp:
            return {"ok": False, "dup": True,
                    "error": "这张图刚刚已经发过了，就不重复发啦。"}
        sr = await send_to_current_chat(api, chat_key, [r["seg"]])
        if fp:
            sent_fp.add(fp)
        r.update(sr)
        if r.get("path"):
            _record_tool_image(chat_key, r["path"])
        return r

    img_searches_used = 0
    img_gens_used = 0
    for turn in range(6):
        msg = await asyncio.get_event_loop().run_in_executor(
            None, deepseek, messages, use_tools, active_tools
        )
        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            reply_text = (msg.get("content") or "").strip()
            if (
                dedupe_against
                and turn < 5
                and find_dup(reply_text, list(dedupe_against))
            ):
                messages.append({"role": "assistant", "content": reply_text})
                messages.append({"role": "user", "content": P("dedup_note", _DEF_DEDUP_NOTE)})
                continue
            break
        messages.append(msg)
        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            if name not in tool_names:
                content = "ERROR: 工具 %s 在当前场景没有权限，别用了。" % name
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "content": content,
                })
                continue
            if name == "run_powershell":
                cmd = args.get("command", "")
                to = args.get("timeout", 60)
                journal("EXEC(%s): %s" % (chat_key, cmd))
                result = await asyncio.get_event_loop().run_in_executor(
                    None, run_on_pc, cmd, to
                )
                if result.get("ok"):
                    out = (result.get("stdout") or "").strip()
                    err = (result.get("stderr") or "").strip()
                    content = "exit=%s\nstdout:\n%s\nstderr:\n%s" % (
                        result.get("code"),
                        out[-4000:],
                        err[-2000:],
                    )
                else:
                    content = "ERROR: %s" % result.get("error")
                journal("RESULT: " + content.replace("\n", " ")[:500])
            elif name == "list_my_groups":
                r = await api("get_group_list", {})
                try:
                    groups = r["data"]
                    lines = [
                        "%s  %s（%s人）"
                        % (g.get("group_id"), g.get("group_name"), g.get("member_count"))
                        for g in groups
                    ]
                    content = "共 %d 个群：\n%s" % (len(groups), "\n".join(lines))
                except Exception:
                    content = "ERROR: 拿不到群列表"
                journal("GROUPS: " + content.replace("\n", " ")[:500])
            elif name == "read_self_file":
                r = read_self_file(args.get("path"), int(args.get("max_bytes") or 60000))
                content = json.dumps(r, ensure_ascii=False)[:90000]
                journal("READ_SELF: " + str(args.get("path"))[:120])
            elif name == "propose_self_patch":
                r = await asyncio.get_event_loop().run_in_executor(
                    None, propose_self_patch, args.get("reason", ""), args.get("edits", [])
                )
                if r.get("ok"):
                    await notify_owner_patch(api, r)
                content = json.dumps(r, ensure_ascii=False)
                journal("PROPOSE_PATCH: " + content[:400])
            elif name == "relay_to_opencode":
                r = await asyncio.get_event_loop().run_in_executor(
                    None, relay_to_opencode, args.get("message", "")
                )
                content = json.dumps(r, ensure_ascii=False)
                journal("RELAY: " + str(args.get("message"))[:200].replace("\n", " "))
            elif name == "get_opencode_reply":
                r = await asyncio.get_event_loop().run_in_executor(
                    None, fetch_opencode_reply, args.get("seq")
                )
                content = json.dumps(r, ensure_ascii=False)
                journal("RELAY_REPLY: " + content[:300])
            elif name == "send_image":
                r = await asyncio.get_event_loop().run_in_executor(
                    None, resolve_image_source, args.get("src", ""),
                    meme_dirs_for(chat_key)
                )
                if r.get("ok"):
                    r = await _send_tool_image(r)
                content = json.dumps(r, ensure_ascii=False)
                journal("SEND_IMAGE: " + str(args.get("src"))[:120])
            elif name == "generate_image":
                recent = [m.get("content") or "" for m in hist[-5:]
                          if m.get("role") == "user"][-3:]
                if not _ai_draw_requested(recent):
                    content = ("ERROR: generate_image 被拦截——最近的对话里没有『画一张/生成一张图』"
                               "这样的明确指令。除非对方命令你画图，否则不准主动生成或发送AI图。")
                elif img_gens_used >= IMG_GEN_PER_TURN:
                    content = ("ERROR: 这一轮已经画过图了，直接用刚才那张回复对方，别重复画。"
                               "除非对方又明确要求再画，否则不要再调 generate_image。")
                else:
                    img_gens_used += 1
                    r = await asyncio.get_event_loop().run_in_executor(
                        None, t2i_generate, args.get("prompt", "")
                    )
                    if r.get("ok"):
                        r = await _send_tool_image(r)
                        r.pop("seg", None)
                    content = json.dumps(r, ensure_ascii=False)
                journal("GEN_IMAGE: " + str(args.get("prompt"))[:120])
            elif name == "search_image":
                recent = [m.get("content") or "" for m in hist[-5:]
                          if m.get("role") == "user"][-3:]
                if restricted and not _image_requested(recent):
                    content = ("ERROR: 没人明确要图/照片，别主动搜图发图。"
                               "只有对方开口要图时才能用 search_image。")
                elif restricted and not _img_search_allowed(sender):
                    content = ("ERROR: 对方搜图太频繁啦，先让 TA 缓一缓"
                               "（每人 %d 秒内最多 %d 张），过一会儿再搜。"
                               % (IMG_SEARCH_WINDOW, IMG_SEARCH_MAX))
                elif img_searches_used >= IMG_SEARCH_PER_TURN:
                    content = ("ERROR: 这一轮已经搜过图了，不要再重复搜索。"
                               "直接用已经拿到的结果回复对方；如果前面没搜到合适的图，"
                               "就如实告诉对方没找到。")
                else:
                    img_searches_used += 1
                    r = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: web_image_search(args.get("prompt", ""), chat_key)
                    )
                    if r.get("ok"):
                        r = await _send_tool_image(r)
                        r.pop("seg", None)
                        content = json.dumps(r, ensure_ascii=False) + \
                            "（图片已发给对方，请直接开口回复，不要重复搜图。）"
                    else:
                        content = json.dumps(r, ensure_ascii=False)
                journal("SEARCH_IMAGE: " + str(args.get("prompt"))[:120])
            else:
                content = "ERROR: 未知工具 %s" % name
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "content": content,
                }
            )
    else:
        reply_text = "呜……想得太久脑袋冒泡泡了，爸爸再说一次好不好～"

    hist.append({"role": "assistant", "content": reply_text or "(...)"})
    trim = max(MAX_HISTORY, 4)
    if len(hist) > trim:
        del hist[: len(hist) - trim]
    return reply_text


# ---------------- 桌面桌宠 HTTP 接口 ----------------
# 桌宠 Electron 进程通过本接口拿到「霖雨」人设的回复和 TTS 语音。
try:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
except Exception:  # pragma: no cover
    BaseHTTPRequestHandler = None
    ThreadingHTTPServer = None

_PET_BOT = None
_PET_LOOP = None
_PET_IDENTITY = (
    "（现在跟你说话的是爸爸本人——你正待在爸爸电脑桌面上，就当他坐在你面前。"
    "用短句、口语化、活泼撒娇一点，回复尽量一两句，别长篇大论。）"
)


def _pet_read_bytes(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except Exception:
        return None


async def _pet_chat_coro(text):
    if _PET_BOT is None:
        return ""
    return await think("pet:local", text, False, _PET_BOT.call_api,
                       identity=_PET_IDENTITY)


def _pet_reply_text(reply):
    """去掉 [[情绪]]/[快] 等标签，保留颜文字，作为气泡显示文本。"""
    t = strip_voice_tags(TAG_RE.sub("", reply or ""))
    return re.sub(r"[ \t]{2,}", " ", t).strip()


def _pet_tts_bytes(reply):
    if not TTS_ENABLE or not (reply or "").strip():
        return None
    try:
        _, segs = split_voice_segs(reply)
    except Exception:
        segs = []
    if segs and TTS_ENGINE in ("gptsovits", "indextts"):
        p = _tts_synth_multi(segs)
        data = _pet_read_bytes(p) if p else None
        if data:
            return data
    clean = "".join(s for s, *_ in segs) if segs else (reply or "")
    clean = _clean_for_tts(clean)[:TTS_MAX_CHARS] or _clean_for_tts(reply)[:TTS_MAX_CHARS]
    p = tts_synthesize(clean)
    return _pet_read_bytes(p) if p else None


if BaseHTTPRequestHandler is not None:
    class _PetHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            try:
                return json.loads(raw.decode("utf-8")) if raw else {}
            except Exception:
                return {}

        def _auth_ok(self):
            if not PET_API_TOKEN:
                return True
            return self.headers.get("X-Pet-Token") == PET_API_TOKEN

        def do_GET(self):
            if self.path.rstrip("/") in ("/pet", "/pet/ping"):
                self._json({"ok": True, "model": MODEL, "tts": TTS_ENABLE,
                            "engine": TTS_ENGINE,
                            "asr": bool(ASR_ENABLE and ASR_KEY)})
            else:
                self._json({"ok": False, "error": "not found"}, 404)

        def do_POST(self):
            if not self._auth_ok():
                self._json({"ok": False, "error": "unauthorized"}, 401)
                return
            body = self._body()
            path = self.path.split("?", 1)[0].rstrip("/")
            if path == "/pet/chat":
                text = str(body.get("text") or "").strip()
                if not text:
                    self._json({"ok": False, "error": "empty text"}, 400)
                    return
                try:
                    fut = asyncio.run_coroutine_threadsafe(
                        _pet_chat_coro(text), _PET_LOOP)
                    reply = fut.result(timeout=180)
                except Exception as e:
                    log.warning("桌宠 chat 失败: %r", e)
                    self._json({"ok": False, "error": repr(e)}, 500)
                    return
                self._json({"ok": True, "reply": reply,
                            "display": _pet_reply_text(reply)})
            elif path == "/pet/tts":
                try:
                    data = _pet_tts_bytes(str(body.get("text") or ""))
                except Exception as e:
                    log.warning("桌宠 tts 失败: %r", e)
                    self._json({"ok": False, "error": repr(e)}, 500)
                    return
                if not data:
                    self._json({"ok": False, "error": "tts unavailable"}, 503)
                    return
                ctype = "audio/wav" if data[:4] == b"RIFF" else "audio/mpeg"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            elif path == "/pet/voice":
                b64 = str(body.get("audio") or "")
                if not b64:
                    self._json({"ok": False, "error": "empty audio"}, 400)
                    return
                try:
                    data = base64.b64decode(b64)
                except Exception:
                    self._json({"ok": False, "error": "bad base64"}, 400)
                    return
                fmt = re.sub(r"[^a-z0-9]", "",
                             str(body.get("format") or "wav").lower()) or "wav"
                try:
                    heard = asr_transcribe("pet_voice." + fmt, data)
                except Exception as e:
                    log.warning("桌宠语音识别失败: %r", e)
                    self._json({"ok": False, "error": repr(e)}, 500)
                    return
                if not heard:
                    self._json({"ok": True, "text": "", "reply": "",
                                "display": ""})
                    return
                try:
                    fut = asyncio.run_coroutine_threadsafe(
                        _pet_chat_coro(heard), _PET_LOOP)
                    reply = fut.result(timeout=180)
                except Exception as e:
                    log.warning("桌宠语音对话失败: %r", e)
                    self._json({"ok": False, "error": repr(e)}, 500)
                    return
                self._json({"ok": True, "text": heard, "reply": reply,
                            "display": _pet_reply_text(reply)})
            elif path == "/pet/asr":
                b64 = str(body.get("audio") or "")
                if not b64:
                    self._json({"ok": False, "error": "empty audio"}, 400)
                    return
                try:
                    data = base64.b64decode(b64)
                except Exception:
                    self._json({"ok": False, "error": "bad base64"}, 400)
                    return
                fmt = re.sub(r"[^a-z0-9]", "",
                             str(body.get("format") or "wav").lower()) or "wav"
                try:
                    heard = asr_transcribe("pet_voice." + fmt, data)
                except Exception as e:
                    log.warning("桌宠语音识别失败: %r", e)
                    self._json({"ok": False, "error": repr(e)}, 500)
                    return
                self._json({"ok": True, "text": heard or ""})
            else:
                self._json({"ok": False, "error": "not found"}, 404)


def start_pet_api(bot, loop):
    global _PET_BOT, _PET_LOOP
    if not PET_API_ENABLE:
        return
    if ThreadingHTTPServer is None:
        log.warning("桌宠接口不可用：http.server 缺失")
        return
    _PET_BOT = bot
    _PET_LOOP = loop
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PET_API_PORT), _PetHandler)
    except Exception as e:
        log.warning("桌宠接口启动失败(%s): %s", PET_API_PORT, e)
        return
    threading.Thread(target=srv.serve_forever, name="pet-api",
                     daemon=True).start()
    log.info("桌宠接口已启动: http://127.0.0.1:%d/pet (token=%s)",
             PET_API_PORT, "on" if PET_API_TOKEN else "off")


class Bot:
    def __init__(self):
        self.ws = None
        self._pending = {}
        self._echo = 0

    async def send(self, action, params):
        if self.ws:
            await self.ws.send(
                json.dumps({"action": action, "params": _debrand_obj(params)},
                           ensure_ascii=False)
            )

    async def reply(self, mtype, uid, gid, message):
        if mtype == "private":
            await self.send("send_private_msg", {"user_id": uid, "message": message})
        else:
            await self.send("send_group_msg", {"group_id": gid, "message": message})

    async def _do_avatar(self, uid, ev, ref, extra=""):
        mtype = ev.get("message_type")
        if not AVATAR_ENABLE:
            await self.reply(mtype, uid, ev.get("group_id"),
                             "呜，我现在还画不了自己的形象（config 里把 avatar 设为 true 哦）(・_・?)")
            return
        await self.reply(mtype, uid, ev.get("group_id"),
                         "好，我照着这张图画画看……爸爸等我一下下 (๑•̀ㅂ•́)و✧")
        r = await asyncio.get_event_loop().run_in_executor(
            None, avatar_generate, ref, extra)
        if not r.get("ok"):
            await self.reply(mtype, uid, ev.get("group_id"),
                             "唔……画不出来：%s (´･ω･`)" % r.get("error"))
            log.info("AVATAR fail uid=%s err=%s", uid, r.get("error"))
            return
        await self.reply(mtype, uid, ev.get("group_id"), [r["seg"]])
        await self.reply(mtype, uid, ev.get("group_id"),
                         "画好啦～这就是我的样子，爸爸觉得像吗 (〃▽〃)")
        log.info("AVATAR ok uid=%s path=%s", uid, r.get("path"))

    async def _do_redraw(self, uid, ev, ref, extra=""):
        mtype = ev.get("message_type")
        if not REDRAW_ENABLE:
            await self.reply(mtype, uid, ev.get("group_id"),
                             "呜，我现在还做不了二次创作（config 里把 redraw 设为 true 哦）(・_・?)")
            return
        await self.reply(mtype, uid, ev.get("group_id"),
                         "好，我照着这张图创作一张……爸爸等我一下下 (๑•̀ㅂ•́)و✧")
        r = await asyncio.get_event_loop().run_in_executor(
            None, redraw_generate, ref, extra)
        if not r.get("ok"):
            await self.reply(mtype, uid, ev.get("group_id"),
                             "唔……创作不出来：%s (´･ω･`)" % r.get("error"))
            log.info("REDRAW fail uid=%s err=%s", uid, r.get("error"))
            return
        await self.reply(mtype, uid, ev.get("group_id"), [r["seg"]])
        await self.reply(mtype, uid, ev.get("group_id"),
                         "创作好啦～看看喜不喜欢 (〃▽〃)")
        log.info("REDRAW ok uid=%s path=%s", uid, r.get("path"))

    async def call_api(self, action, params, timeout=10):
        if not self.ws:
            return None
        self._echo += 1
        echo = "r%d" % self._echo
        fut = asyncio.get_event_loop().create_future()
        self._pending[echo] = fut
        try:
            await self.ws.send(
                json.dumps(
                    {"action": action, "params": params, "echo": echo},
                    ensure_ascii=False,
                )
            )
            return await asyncio.wait_for(fut, timeout)
        except Exception:
            self._pending.pop(echo, None)
            return None

    async def get_group_name(self, gid):
        r = await self.call_api("get_group_info", {"group_id": gid})
        try:
            return r["data"]["group_name"]
        except Exception:
            return None

    async def handle_notice(self, ev):
        nt = ev.get("notice_type")
        if nt in ("group_msg_emoji_like",):
            note_emoji_feedback(ev.get("group_id"))
            return
        if nt != "group_increase":
            return
        if str(ev.get("user_id")) != BOT_QQ:
            return
        gid = ev.get("group_id")
        name = await self.get_group_name(gid)
        gtxt = "%s(%s)" % (name, gid) if name else str(gid)
        log.info("被拉进新群 %s operator=%s", gtxt, ev.get("operator_id"))
        for o in OWNER_QQ:
            await self.send(
                "send_private_msg",
                {
                    "user_id": o,
                    "message": "呜、呜……爸爸，我、我被拉进一个新群了：「%s」……要不要我在里面说话呀？" % gtxt,
                },
            )
        if GREET_ON_JOIN and str(gid) not in GROUP_QUIET:
            await self.send(
                "send_group_msg",
                {
                    "group_id": gid,
                    "message": "那、那个……大家好，我是大女儿ds……请、请多指教……",
                },
            )

    async def handle_friend_request(self, ev):
        global _friend_seq
        _friend_seq += 1
        seq = _friend_seq
        uid = ev.get("user_id")
        comment = (ev.get("comment") or "")[:60]
        _friend_reqs[seq] = {"flag": ev.get("flag"), "uid": uid, "comment": comment}
        log.info("好友请求 #%d from=%s comment=%s", seq, uid, comment)
        for o in OWNER_QQ:
            await self.send(
                "send_private_msg",
                {
                    "user_id": o,
                    "message": "爸爸～有个 QQ %s 想加我好友～\n留言：「%s」\n"
                    "要同意就回 /同意 %d，不同意就回 /拒绝 %d。"
                    % (uid, comment or "（没留言）", seq, seq),
                },
            )

    async def handle_request(self, ev):
        if ev.get("request_type") == "friend":
            await self.handle_friend_request(ev)
            return
        if ev.get("request_type") != "group":
            return
        sub = ev.get("sub_type")
        gid = ev.get("group_id")
        uid = ev.get("user_id")
        comment = ev.get("comment", "") or ""
        flag = ev.get("flag")
        name = await self.get_group_name(gid)
        gtxt = "%s(%s)" % (name, gid) if name else str(gid)
        if sub == "invite":
            if AUTO_ACCEPT_INVITE:
                r = await self.call_api(
                    "set_group_add_request",
                    {"flag": flag, "sub_type": "invite", "approve": True},
                )
                ok = bool(r and r.get("status") == "ok")
                log.info("收到入群邀请 %s 来自 %s，自动同意=%s", gtxt, uid, ok)
                for o in OWNER_QQ:
                    await self.send(
                        "send_private_msg",
                        {
                            "user_id": o,
                            "message": "爸爸～有人、有人邀请我进群「%s」……我%s了。"
                            % (gtxt, "同意" if ok else "想同意但没成功"),
                        },
                    )
            else:
                for o in OWNER_QQ:
                    await self.send(
                        "send_private_msg",
                        {
                            "user_id": o,
                            "message": "呜……有人邀请我进群「%s」，要不要去呀爸爸？" % gtxt,
                        },
                    )
        elif sub == "add":
            for o in OWNER_QQ:
                await self.send(
                    "send_private_msg",
                    {
                        "user_id": o,
                        "message": "有人想进「%s」（申请人 %s，附言：%s），爸爸要同意吗？"
                        % (gtxt, uid, comment[:60]),
                    },
                )

    async def handle(self, ev):
        global _paused
        post = ev.get("post_type")
        if post == "notice":
            await self.handle_notice(ev)
            return
        if post == "request":
            await self.handle_request(ev)
            return
        if post != "message":
            return
        uid = str(ev.get("user_id"))
        if uid == BOT_QQ:
            return
        text, at_me, images, records, reply_id, videos = parse_segs(ev.get("message"))
        ck = ("private:" + uid) if ev.get("message_type") == "private" else ("group:" + str(ev.get("group_id")))
        if ev.get("message_type") == "group" and at_me:
            _last_at[(str(ev.get("group_id")), uid)] = time.time()
        if records and ASR_ENABLE:
            for_me = ev.get("message_type") == "private" or at_me
            if not for_me and ev.get("message_type") == "group":
                ts = _last_at.get((str(ev.get("group_id")), uid), 0)
                if VOICE_AT_WINDOW > 0 and (time.time() - ts) <= VOICE_AT_WINDOW:
                    for_me = True
                    log.info("VOICE %s 归因: 最近@她(%.0fs内)", ck, time.time() - ts)
                elif reply_id and await reply_target_is_bot(self.call_api, reply_id):
                    for_me = True
                    log.info("VOICE %s 归因: 引用回复她", ck)
            if for_me:
                vt = await transcribe_voice(self.call_api, records)
                if vt:
                    text = (text + " " + vt).strip() if text else vt
                    at_me = True
                    log.info("VOICE %s from=%s -> %s", ck, uid, vt[:80])
        video_info = None
        if videos and VIDEO_CMD_RE.match(cmd_body(text)) is None:
            v_for_me = ev.get("message_type") == "private" or at_me
            if v_for_me and VIDEO_ENABLE:
                video_info = await describe_video(self.call_api, videos)
                if video_info and (video_info.get("frames") or video_info.get("transcript")):
                    vt = video_info.get("transcript")
                    frames = list(video_info.get("frames") or [])
                    if VIDEO_SUMMARY:
                        summ = await asyncio.get_event_loop().run_in_executor(
                            None, summarize_video, frames, vt)
                        tag = "[视频]"
                        if summ:
                            tag += "（视频内容：%s）" % summ
                        elif vt:
                            tag += "（视频里的语音是：%s）" % vt
                    else:
                        images = images + frames
                        tag = "[视频]"
                        if vt:
                            tag += "（视频里的语音是：%s）" % vt
                    text = (text + " " + tag).strip() if text else tag
                    at_me = True
                    log.info("VIDEO %s from=%s 时长=%.1fs 帧=%d ASR=%s", ck, uid,
                             video_info.get("dur") or 0,
                             len(frames), (vt or "")[:60])
                else:
                    log.warning("VIDEO %s from=%s 解析失败", ck, uid)
        if images and ck in _await_photo:
            want = _await_photo[ck]
            want_uid = want[0]
            wt = want[1]
            photo_name = want[2] if len(want) > 2 else None
            if uid == want_uid:
                _await_photo.pop(ck, None)
                if time.time() - wt <= PHOTO_EXPIRE:
                    kw = photo_name or _sanitize_kw(re.sub(r"\[图片\]", "", text or ""))
                    if not kw:
                        kw = "photo_" + time.strftime("%Y%m%d_%H%M%S")
                    if ev.get("message_type") == "private":
                        dest_dir = PRIV_MEME_DIR
                    else:
                        dest_dir = os.path.join(GROUP_MEME_ROOT, str(ev.get("group_id")))
                    try:
                        if ev.get("message_type") != "private":
                            bad = await asyncio.get_event_loop().run_in_executor(
                                None, group_image_is_bad, images[0])
                            if bad:
                                log.info("GROUP_PHOTO_SENSITIVE blocked uid=%s key=%s", uid, ck)
                                await self.reply(ev.get("message_type"), uid,
                                                 ev.get("group_id"), SENSITIVE_REPLY)
                                return
                        r = await asyncio.get_event_loop().run_in_executor(
                            None, save_image_as_meme, images[0], kw, dest_dir)
                        if ev.get("message_type") == "private":
                            rep = "存进爸爸的私人图库啦～关键词「%s」，以后在这儿说它我就能发 (๑•̀ㅂ•́)و✧" % r["keyword"]
                        else:
                            rep = ("存进咱们群的图库啦～关键词「%s」，以后在这个群提它我就能发 (๑•̀ㅂ•́)و✧"
                                   % r["keyword"])
                    except Exception as e:
                        rep = "呜，这张图没存成：%s (´･ω･`)" % e
                    await self.reply(ev.get("message_type"), uid, ev.get("group_id"), rep)
                    return
        if images and ck in _await_avatar:
            want = _await_avatar.pop(ck, None)
            if want and uid == want[0] and time.time() - want[1] <= AVATAR_EXPIRE:
                extra = want[2] if len(want) > 2 else ""
                await self._do_avatar(uid, ev, images[0], extra)
                return
        if images and ck in _await_redraw:
            want = _await_redraw.pop(ck, None)
            if want and uid == want[0] and time.time() - want[1] <= REDRAW_EXPIRE:
                extra = want[2] if len(want) > 2 else ""
                await self._do_redraw(uid, ev, images[0], extra)
                return
        if images and FORWARD_RE.search(text or ""):
            if not _muted:
                if ck.startswith("group:"):
                    bad = await asyncio.get_event_loop().run_in_executor(
                        None, group_image_is_bad, images[0])
                    if bad:
                        log.info("GROUP_FORWARD_SENSITIVE blocked uid=%s key=%s", uid, ck)
                        await self.reply(ev.get("message_type"), uid, ev.get("group_id"),
                                         SENSITIVE_REPLY)
                        return
                try:
                    data = await asyncio.get_event_loop().run_in_executor(
                        None, download_image_bytes, images[0])
                    await self.reply(ev.get("message_type"), uid, ev.get("group_id"),
                                     [image_segment_from_bytes(data)])
                except Exception as e:
                    await self.reply(ev.get("message_type"), uid, ev.get("group_id"),
                                     "呜，这张图抓不下来：%s (´･ω･`)" % e)
            log.info("FORWARD uid=%s key=%s img=%s", uid, ck, images[0][:60])
            return
        if not text:
            return
        is_owner = uid in OWNER_QQ
        mtype = ev.get("message_type")
        sender = ev.get("sender", {}) or {}
        nick = sender.get("card") or sender.get("nickname") or uid
        g_ctx = None

        rname = re.fullmatch(r"/rename\s+(\S+)\s+(\S+)", cmd_body(text).strip())
        if rname:
            r = rename_meme(ck, is_owner, rname.group(1), rname.group(2))
            if not _muted:
                if r["ok"]:
                    await self.reply(mtype, uid, ev.get("group_id"),
                                     "改好啦～「%s」现在叫「%s」 (๑•̀ㅂ•́)و✧" % (r["old"], r["new"]))
                else:
                    await self.reply(mtype, uid, ev.get("group_id"), r["error"])
            log.info("RENAME by=%s %s -> %s ok=%s", uid, rname.group(1), rname.group(2), r["ok"])
            return

        if cmd_body(text).strip().lower() in MEME_LIST_CMDS:
            if not _muted:
                await self.reply(mtype, uid, ev.get("group_id"), meme_topic_list(ck))
            log.info("MEMES list by=%s key=%s", uid, ck)
            return

        mtop = re.fullmatch(r"/(?:setmeme|改主题|主题改)\s+(\S+)\s+(\S+)", cmd_body(text).strip())
        if mtop:
            r = await asyncio.get_event_loop().run_in_executor(
                None, set_meme_topic, ck, is_owner, mtop.group(1), mtop.group(2)
            )
            if not _muted:
                if r["ok"]:
                    await self.reply(mtype, uid, ev.get("group_id"),
                                     "改好啦～「%s」现在叫「%s」，新图也从B站抓来啦 (๑•̀ㅂ•́)و✧"
                                     % (r["old"], r["new"]))
                else:
                    await self.reply(mtype, uid, ev.get("group_id"), r["error"])
            log.info("SETMEME by=%s %s -> %s ok=%s", uid, mtop.group(1), mtop.group(2), r["ok"])
            return

        if cmd_body(text).strip().lower() == PHOTO_CMD or cmd_body(text).strip().lower().startswith(PHOTO_CMD + " "):
            if mtype == "private" and not is_owner:
                if not _muted:
                    await self.reply(mtype, uid, ev.get("group_id"),
                                     "只有爸爸能让我存图哦 (・_・?)")
                log.info("PHOTO denied uid=%s key=%s", uid, ck)
                return
            photo_name = cmd_body(text).strip()[len(PHOTO_CMD):].strip() or None
            if photo_name:
                photo_name = _sanitize_kw(re.sub(r"\[图片\]", "", photo_name)) or None
            _await_photo[ck] = (uid, time.time(), photo_name)
            if not _muted:
                if mtype == "private":
                    tip = "好呀～把你那张想存进我私人图库的图发过来吧 (｡•̀ᴗ•́｡)"
                else:
                    tip = "好呀～把那张图发过来吧，我存进咱们群的图库里 (｡•̀ᴗ•́｡)"
                if photo_name:
                    tip = "好呀～图发过来吧，我给它起名「%s」，存进%s图库 (｡•̀ᴗ•́｡)" % (
                        photo_name, "私人" if mtype == "private" else "咱们群的")
                await self.reply(mtype, uid, ev.get("group_id"), tip)
            log.info("PHOTO await uid=%s key=%s name=%s", uid, ck, photo_name)
            return

        _avb = cmd_body(text).strip()
        if _avb.lower() == AVATAR_CMD or _avb.lower().startswith(AVATAR_CMD + " "):
            if not is_owner:
                if not _muted:
                    await self.reply(mtype, uid, ev.get("group_id"),
                                     "只有爸爸能给我画形象哦 (・_・?)")
                log.info("AVATAR denied uid=%s key=%s", uid, ck)
                return
            extra = _avb[len(AVATAR_CMD):].strip()
            if images:
                await self._do_avatar(uid, ev, images[0], extra)
                return
            _await_avatar[ck] = (uid, time.time(), extra)
            if not _muted:
                await self.reply(mtype, uid, ev.get("group_id"),
                                 "好呀～把你想要我照着画的参考图发过来吧，我照着画我自己的样子 (๑•̀ㅂ•́)و✧")
            log.info("AVATAR await uid=%s key=%s extra=%s", uid, ck, extra[:30])
            return

        if _avb.lower() == REDRAW_CMD or _avb.lower().startswith(REDRAW_CMD + " "):
            if not is_owner:
                if not _muted:
                    await self.reply(mtype, uid, ev.get("group_id"),
                                     "只有爸爸能让我二次创作哦 (・_・?)")
                log.info("REDRAW denied uid=%s key=%s", uid, ck)
                return
            extra = _avb[len(REDRAW_CMD):].strip()
            if images:
                await self._do_redraw(uid, ev, images[0], extra)
                return
            _await_redraw[ck] = (uid, time.time(), extra)
            if not _muted:
                await self.reply(mtype, uid, ev.get("group_id"),
                                 "好呀～把你想要我二次创作的图发过来吧（也可以带一句想要的效果） (๑•̀ㅂ•́)و✧")
            log.info("REDRAW await uid=%s key=%s extra=%s", uid, ck, extra[:30])
            return

        if cmd_body(text).strip().lower() in HELP_CMDS:
            msg = help_text()
            if mtype == "private":
                await self.send("send_private_msg", {"user_id": uid, "message": msg})
            elif mtype == "group":
                await self.send("send_group_msg", {"group_id": ev.get("group_id"), "message": msg})
            return

        if is_stop_cmd(text) and is_owner:
            if not _muted:
                _set_muted(True)
                log.info("收到「停止发言」指令 from=%s type=%s → 立即闭嘴", uid, mtype)
            return
        if is_resume_cmd(text) and is_owner:
            if _muted:
                _set_muted(False)
                log.info("主人解除禁言 → 恢复发言")
                ack = "唔……好、好吧，我、我继续说就是了……别、别嫌我烦啊。"
                if mtype == "private":
                    await self.send("send_private_msg", {"user_id": uid, "message": ack})
                else:
                    await self.send(
                        "send_group_msg", {"group_id": ev.get("group_id"), "message": ack}
                    )
            return
        if _muted:
            if mtype == "private" and is_owner:
                pass
            elif not (mtype == "group" and str(ev.get("group_id")) in UNMUTE_GROUPS):
                return

        if is_owner:
            pc = cmd_body(text).strip()
            ma = PATCH_APPLY_RE.match(pc)
            md = PATCH_DROP_RE.match(pc)
            mr = PATCH_ROLLBACK_RE.match(pc)
            ml = PATCH_LIST_RE.match(pc)
            mg = PATCH_GET_RE.match(pc)
            mpc = PC_RELAY_RE.match(pc)
            if ma or md or mr or ml or mg or mpc:
                gid = ev.get("group_id")

                async def _say(m):
                    if mtype == "private":
                        await self.send("send_private_msg", {"user_id": uid, "message": m})
                    else:
                        await self.send("send_group_msg", {"group_id": gid, "message": m})

                if ml:
                    ps = list_self_patches()
                    if not ps:
                        await _say("现在没有自我修改记录哦 (・_・?)")
                    else:
                        txt = "\n".join(
                            "#%s [%s] %s" % (p["id"], p.get("status"),
                                             (p.get("reason") or "")[:40])
                            for p in ps[-10:]
                        )
                        await _say("【自我修改记录】\n" + txt +
                                   "\n（/apply 编号 应用 ｜ /rollback 编号 回滚 ｜ /drop 编号 丢弃）")
                    return
                if ma:
                    r = await asyncio.get_event_loop().run_in_executor(
                        None, apply_self_patch, ma.group(1))
                    if r.get("ok"):
                        await _say("好耶！补丁 #%s 已写到：%s。女儿这 2 秒去换身新衣裳～ (๑•̀ㅂ•́)و✧"
                                   % (r["id"], "、".join(r["files"])))
                        if not schedule_restart():
                            await _say("（不过女儿没法自己重启，爸爸手动 restart 服务哦）")
                    else:
                        await _say("应用不了：#%s，%s (´･ω･`)"
                                   % (ma.group(1), r.get("error")))
                    return
                if mr:
                    r = await asyncio.get_event_loop().run_in_executor(
                        None, rollback_self_patch, mr.group(1))
                    if r.get("ok"):
                        await _say("补丁 #%s 已回滚到改动前，女儿重启后就恢复原样啦～"
                                   % r["id"])
                        if not schedule_restart():
                            await _say("（记得手动 restart 服务哦）")
                    else:
                        await _say("回滚不了：#%s，%s (´･ω･`)"
                                   % (mr.group(1), r.get("error")))
                    return
                if md:
                    pid = int(md.group(1))
                    meta = _load_meta(pid)
                    if not meta:
                        await _say("没有补丁 #%s 哦 (・_・?)" % pid)
                    elif meta.get("status") != "pending":
                        await _say("补丁 #%s 不是待办状态，不能丢 (・_・?)" % pid)
                    else:
                        try:
                            shutil.rmtree(_patch_dir(pid))
                        except Exception:
                            pass
                        await _say("补丁 #%s 丢掉啦～ (￣▽￣\")" % pid)
                    return
                if mg:
                    seq = int(mg.group(1))
                    r = await asyncio.get_event_loop().run_in_executor(
                        None, fetch_opencode_reply, seq)
                    if not r.get("ok"):
                        await _say("取不到：#%s，%s (´･ω･`)" % (seq, r.get("error")))
                    elif not r.get("new"):
                        await _say("opencode 还没回 #%s 哦，等一下再 /get %s (・_・?)" % (seq, seq))
                    else:
                        await _say("opencode 对 #%s 的答复：\n%s" % (seq, r.get("reply")))
                    return
                if mpc:
                    content = (mpc.group(1) or "").strip()
                    if not content:
                        await _say("要说点什么呀，格式：/pc 内容 (・_・?)")
                        return
                    rr = await asyncio.get_event_loop().run_in_executor(
                        None, relay_to_opencode, content)
                    if rr.get("ok"):
                        await _say("已转交给 opencode #%s～它答复后 /get %s 就能取到 (๑•̀ㅂ•́)و✧"
                                   % (rr["seq"], rr["seq"]))
                    else:
                        await _say("转交不了：%s (´･ω･`)" % rr.get("error"))
                    return

        if mtype == "private":
            if not is_owner and (parse_persona_cmd(text) is not None
                                 or cmd_body(text).strip().lower() in CHECK_CMDS
                                 or is_admin_cmd(cmd_body(text))):
                log.info("忽略非主人私聊设定指令 uid=%s text=%s", uid, text[:40])
                return
            chat_key = "private:" + uid
            allow_tools = True if is_owner else ["search_image"]
        elif mtype == "group":
            gid = str(ev.get("group_id"))
            record_group(gid, nick, text)
            note_group_feedback(gid, text)
            meta = _gmeta.setdefault(gid, {"spoke": 0})
            meta["act"] = time.time()
            if gid in GROUP_QUIET:
                return
            body = cmd_body(text)
            if (not at_me and parse_persona_cmd(text) is None
                    and body.strip().lower() not in CHECK_CMDS
                    and not is_admin_cmd(body)
                    and SPEAK_CMD_RE.match(body) is None
                    and REPEAT_CMD_RE.match(body) is None
                    and VIDEO_CMD_RE.match(body) is None
                    and body.strip().lower() not in HELP_CMDS):
                return
            if GROUP_ALLOW and gid not in GROUP_ALLOW:
                return
            chat_key = "group:" + gid
            allow_tools = True if is_owner else ["search_image"]
            g_ctx = group_context(gid)
            r = persona_cmd(text, is_owner, gid)
            if r is not None:
                await self.send("send_group_msg", {"group_id": gid, "message": r})
                return
        else:
            return

        force_voice = False
        repeat_mode = False
        repeat_content = ""
        sm = SPEAK_CMD_RE.match(cmd_body(text))
        if sm is not None:
            _sv = (sm.group(1) or "").strip()
            if not _sv:
                await self.reply(mtype, uid, ev.get("group_id"),
                                 "要念什么呀？格式：/speak 内容 (・_・?)")
                return
            force_voice = True
            text = _sv
        rm = REPEAT_CMD_RE.match(cmd_body(text))
        if rm is not None:
            _rv = (rm.group(1) or "").strip()
            if not _rv:
                await self.reply(mtype, uid, ev.get("group_id"),
                                 "要重复什么呀？格式：/repeat 内容 (・_・?)")
                return
            repeat_mode = True
            force_voice = True
            repeat_content = _rv
            text = _rv

        vm = VIDEO_CMD_RE.match(cmd_body(text))
        if vm is not None:
            if not VIDEO_ENABLE:
                await self.reply(mtype, uid, ev.get("group_id"),
                                 "视频检测还没开哦 (・_・?)")
                return
            qvideos = videos or await fetch_quoted_videos(self.call_api, reply_id)
            if not qvideos:
                await self.reply(mtype, uid, ev.get("group_id"),
                                 "引用一条视频消息再发 /video，我就能帮你看看啦（也可直接把视频和 /video 一起发）(・_・?)")
                return
            await self.reply(mtype, uid, ev.get("group_id"),
                             "让我瞧瞧这条视频……(⊙o⊙)")
            info = await describe_video(self.call_api, qvideos)
            if info:
                desc = build_video_desc(info)
                if info.get("dir"):
                    shutil.rmtree(info["dir"], ignore_errors=True)
                await self.reply(mtype, uid, ev.get("group_id"),
                                 desc or "这段视频我没看出什么内容呢 (・_・?)")
                log.info("VIDEO_CMD %s from=%s 时长=%.1fs 帧=%d ASR=%s", ck, uid,
                         info.get("dur") or 0, len(info.get("frames") or []),
                         (info.get("transcript") or "")[:60])
            else:
                await self.reply(mtype, uid, ev.get("group_id"),
                                 "这条视频我拿不到呢，可能太大或格式不支持 (´･ω･`)")
            return

        if images and mtype == "group":
            bad = await asyncio.get_event_loop().run_in_executor(
                None, group_image_is_bad, images[0])
            if bad:
                log.info("GROUP_IMAGE_SENSITIVE blocked uid=%s key=%s", uid, ck)
                await self.reply("group", uid, ev.get("group_id"), SENSITIVE_REPLY)
                return

        low = text.lower()
        if is_owner:
            identity = (
                "（【重要】现在正在跟你说话的这个人，就是你爸爸石丰本人——"
                "你要像认出爸爸一样亲近他、可以撒娇，不要把他当成普通群友。）"
            )
            prompt_text = "[爸爸石丰] " + text
        else:
            if mtype == "group":
                identity = None
                prompt_text = "[%s] %s" % (nick, text)
            else:
                identity = (
                    "（现在私聊你的是一位你不认识的人，友好、礼貌地陪聊就好："
                    "不要把他当爸爸，不许乱认亲戚，也不要答应帮他操作电脑或改设定。）"
                )
                prompt_text = text
        if mtype == "group":
            rm = _meme_recent_names("group:" + str(gid))
            if rm:
                prompt_text += (" （你最近刚发过这些梗图：%s"
                                "——下次配图尽量换一张，别马上重复发同一张。）"
                                 % "、".join(rm))

        if repeat_mode:
            prompt_text = P("repeat_script", _DEF_REPEAT, content=repeat_content)
            identity = None

        if is_owner and mtype == "private":
            if low in ("#暂停", "#pause", "pause"):
                _paused = True
                await self.send("send_private_msg", {"user_id": uid, "message": "呜…好吧，女儿先收起小手手（暂停操控）。"})
                return
            if low in ("#恢复", "#resume", "resume"):
                _paused = False
                await self.send("send_private_msg", {"user_id": uid, "message": "好耶！女儿的小手手又能动啦～"})
                return
            fm = re.match(r"^/(同意|拒绝|yes|no|accept|reject)\s*(\d*)$", low)
            if fm:
                yes = fm.group(1) in ("同意", "yes", "accept")
                seq = int(fm.group(2)) if fm.group(2) else (max(_friend_reqs) if _friend_reqs else 0)
                req = _friend_reqs.pop(seq, None)
                if not req:
                    await self.send("send_private_msg", {"user_id": uid, "message": "没有编号 %s 的好友请求哦 (・_・?)" % seq})
                    return
                try:
                    r = await self.call_api(
                        "set_friend_add_request",
                        {"flag": req["flag"], "approve": yes},
                    )
                    ok = bool(r and r.get("status") == "ok")
                except Exception as e:
                    log.exception("set_friend_add_request error")
                    ok = False
                await self.send(
                    "send_private_msg",
                    {"user_id": uid, "message": "已经%s加好友请求 #%d（QQ %s）%s"
                     % ("同意" if yes else "拒绝", seq, req["uid"], "" if ok else "，但没执行成功")},
                )
                return
            r = persona_cmd(text, True)
            if r is not None:
                await self.send("send_private_msg", {"user_id": uid, "message": r})
                return

        log.info("MSG %s from=%s tools=%s text=%s", chat_key, uid, allow_tools, text[:60])
        want_voice = force_voice or (TTS_ENABLE and bool(TTS_TRIGGER_RE.search(text or "")))
        if want_voice and not repeat_mode:
            prompt_text = prompt_text + "\n" + P("voice_script", _DEF_VOICE_HINT)
        try:
            reply = await think(
                chat_key,
                prompt_text,
                allow_tools,
                self.call_api,
                g_ctx,
                images,
                dedupe_against=_recent.get(str(ev.get("group_id")), ()),
                identity=identity,
                restricted=not is_owner,
                sender=uid,
            )
        except Exception as e:
            log.exception("think error")
            reply = "呜哇，女儿的信号好像进了深海……(%s)" % e
        if video_info and video_info.get("dir"):
            shutil.rmtree(video_info["dir"], ignore_errors=True)
        log.info("REPLY %s: %s", chat_key, (reply or "")[:200].replace("\n", " "))
        skip_imgs = _turn_tool_images.pop(chat_key, set())
        if not reply:
            return
        voice_segs = ()
        if want_voice:
            display, script = split_voice_script(reply)
            if script is not None:
                reply = display
                _, voice_segs = split_voice_segs(script)
            else:
                reply_display, voice_segs = split_voice_segs(reply)
                if reply_display != reply:
                    reply = reply_display
        msg = build_payload(reply, meme_dirs_for(chat_key), chat_key, skip_imgs)
        sent_imgs = _last_emotion_image_paths[:]
        if not msg and not (want_voice and voice_segs):
            return
        if want_voice and voice_segs:
            clean = "".join(s for s, _, _, _ in voice_segs)
            if clean:
                vseg = await asyncio.get_event_loop().run_in_executor(
                    None, tts_make_segment_tagged, voice_segs, clean[:TTS_MAX_CHARS])
                if vseg:
                    if not msg:
                        msg = [vseg]
                    elif isinstance(msg, str):
                        msg = [vseg, {"type": "text", "data": {"text": msg}}]
                    elif isinstance(msg, list):
                        msg = [vseg] + msg
                    else:
                        msg = [vseg]
                    log.info("TTS %s ok len=%d", chat_key, len(clean))
                else:
                    log.warning("TTS %s 生成失败，改发文字", chat_key)
        if mtype == "private":
            await self.send("send_private_msg", {"user_id": uid, "message": msg})
            if sent_imgs:
                _meme_recent_push("private:" + str(uid), sent_imgs)
        else:
            ggrp = str(ev.get("group_id"))
            _gmeta.setdefault(ggrp, {})["spoke"] = time.time()
            if sent_imgs:
                note_sent_memes(ggrp, sent_imgs)
                _meme_recent_push("group:" + ggrp, sent_imgs)
            if reply:
                record_group(ggrp, BOT_NICK, reply)
                _recent.setdefault(ggrp, deque(maxlen=REPEAT_LOOKBACK)).append(reply)
            await self.send(
                "send_group_msg",
                {"group_id": ggrp, "message": msg},
            )

    async def generate_chatter(self, gid):
        g_ctx = group_context(gid)
        prompt = P("group_chatter", _DEF_GROUP_CHATTER)
        reply = await think("chatter:" + str(gid), prompt, False, self.call_api, g_ctx)
        return (reply or "").strip()

    async def chatter_loop(self):
        log.info(
            "群插话已启动 interval=%ss window=%ss", CHATTER_INTERVAL, CHATTER_WINDOW
        )
        while True:
            await asyncio.sleep(5)
            if not GROUP_CHATTER or _muted:
                continue
            now = time.time()
            for gid, meta in list(_gmeta.items()):
                try:
                    if gid in GROUP_QUIET:
                        continue
                    if GROUP_ALLOW and gid not in GROUP_ALLOW:
                        continue
                    if now - meta.get("act", 0) > CHATTER_WINDOW:
                        continue
                    if now - meta.get("spoke", 0) < CHATTER_INTERVAL:
                        continue
                    reply = await self.generate_chatter(gid)
                    meta["spoke"] = time.time()
                    if not reply:
                        continue
                    if _muted:
                        continue
                    log.info("CHATTER %s: %s", gid, reply[:100].replace("\n", " "))
                    payload = build_payload(reply, meme_dirs_for("group:" + str(gid)), "group:" + str(gid))
                    sent_imgs = _last_emotion_image_paths[:]
                    if not payload:
                        continue
                    if sent_imgs:
                        note_sent_memes(gid, sent_imgs)
                        _meme_recent_push("group:" + str(gid), sent_imgs)
                    await self.send(
                        "send_group_msg",
                        {"group_id": gid, "message": payload},
                    )
                except Exception:
                    log.exception("chatter 出错 gid=%s", gid)
                    meta["spoke"] = time.time()

    async def meme_loop(self):
        log.info("梗图自动维护已启动 target=%s interval=%ss", MEME_TARGET, MEME_MAINTAIN_INTERVAL)
        await asyncio.sleep(25)
        while True:
            try:
                if not _muted:
                    await asyncio.get_event_loop().run_in_executor(None, maintain_memes)
            except Exception:
                log.exception("梗图维护出错")
            await asyncio.sleep(max(600, MEME_MAINTAIN_INTERVAL))

    async def run(self):
        start_pet_api(self, asyncio.get_event_loop())
        asyncio.create_task(self.chatter_loop())
        if MEME_AUTO_INGEST:
            asyncio.create_task(self.meme_loop())
        backoff = 2
        while True:
            try:
                async with websockets.connect(WS_URL, max_size=2 ** 24, ping_interval=20) as ws:
                    self.ws = ws
                    log.info("已连上 OneBot: %s", WS_URL)
                    backoff = 2
                    async for raw in ws:
                        try:
                            ev = json.loads(raw)
                        except Exception:
                            continue
                        echo = ev.get("echo") if isinstance(ev, dict) else None
                        if echo is not None and echo in self._pending:
                            fut = self._pending.pop(echo)
                            if not fut.done():
                                fut.set_result(ev)
                            continue
                        asyncio.create_task(self.handle(ev))
            except Exception as e:
                log.warning("OneBot 连接断开: %s，%ss 后重试", e, backoff)
                self.ws = None
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)


def main():
    if not DEEPSEEK_KEY:
        log.error("缺少 deepseek_api_key（config.json 或环境变量 DEEPSEEK_API_KEY）")
    load_group_logs()
    load_corpus()
    _load_meme_scores()
    log.info("梗语料库已载入%d条 (prob=%.1f)", len(_corpus), CORPUS_PROB)
    log.info(
        "QQBot Studio v1.1.1 启动，owner=%s model=%s tools=%s 群学习=%s(已载入%d群) 自动同意入群=%s",
        OWNER_QQ,
        MODEL,
        ENABLE_TOOLS,
        LEARN_GROUPS,
        len(_gdeque),
        AUTO_ACCEPT_INVITE,
    )
    log.info("初始静音状态: %s", _muted)
    asyncio.run(Bot().run())


if __name__ == "__main__":
    main()

