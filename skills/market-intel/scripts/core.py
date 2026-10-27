#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""market-intel 公共基础层。

沿用 change-watch 的设计约定（保持同仓库一致，不另起炉灶）：
- 只用 Python 3 标准库（零第三方依赖），任意装了 Python 3 的机器都能跑；
- 不写死安装位置：脚本用自身路径反推技能目录，状态全部落在工作区（默认 ~/.market-intel/）；
- 源健康三态 ok / flapping / down（连续失败 >= 3 判 down；恢复只认 down -> ok）；
- 配置字段白名单校验：拼错的键直接报错退出，不允许"看起来在跑、其实没生效"；
- 时间统一 ISO 8601 本地时区（卡片展示北京时间）。
"""

import gzip
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime

DEFAULT_WS = os.path.join(os.path.expanduser("~"), ".market-intel")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 近似汇率：仅用于融资门槛比较，不求精确（可按需调整）
USD_CNY = 7.2

# 完整浏览器请求头：change-watch 实测过"请求头不像浏览器"会触发反爬（LinkedIn HTTP 999），勿简化
_BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/rss+xml;q=0.8,*/*;q=0.7",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
    "Connection": "close",
}


# ---------- 时间 ----------

def now_iso():
    """当前时间 ISO 8601（本地时区，秒级）。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_iso(text):
    """解析时间为 Unix 秒；解析不出返回 None（调用方按保守处理）。

    必须兼容两种真实形态（2026-10-27 修复）：
    - ISO 8601：`2026-10-27T10:34:24+08:00`（内部状态文件用）
    - RFC 2822：`Mon, 26 Oct 2026 18:11:43 GMT`（**RSS/Atom 的 published 原生格式**）
    此前只认 ISO，RSS 源的 published 全部解析成 None → windowed 源的「窗口滚动」
    判定（靠 oldest_cur / pub 比较）永久失效 → 滚动条目全被判「真的消失」进变更窗，
    每轮产出几十条 removed 噪音。全量首扫轮无 prev 快照，故长期潜伏未暴露。
    """
    if not text:
        return None
    raw = str(text).strip()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except Exception:
        pass
    # RFC 2822/822 回退（email.utils 是标准库，零依赖）
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(raw)
        if dt is not None:
            if dt.tzinfo is None:      # 无时区按 UTC（RSS 惯例）
                from datetime import timezone
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
    except Exception:
        pass
    return None


def hours_since(iso_text, now=None):
    """距某 ISO 时刻的小时数；时刻无效返回 None。"""
    ts = parse_iso(iso_text)
    if ts is None:
        return None
    return ((now if now is not None else time.time()) - ts) / 3600.0


# ---------- 文本与哈希 ----------

def norm(text):
    """压缩空白：差分与指纹前的统一归一化。"""
    return re.sub(r"\s+", " ", (text or "").strip())


def sha1(text):
    return hashlib.sha1((text or "").encode("utf-8", "replace")).hexdigest()


def norm_url(url):
    """URL 归一：去查询串/锚点与结尾斜杠（change-watch 同款语义）。"""
    url = (url or "").strip()
    url = re.sub(r"[?#].*$", "", url)
    return url.rstrip("/")


def strip_tags(html):
    """剥掉 HTML 标签取纯文本（先去 script/style，防把脚本内容当正文）。"""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return norm(text)


# ---------- 网络抓取 ----------

def fetch(url, timeout=30, retries=1):
    """抓取 URL 文本，带退避重试；全部失败抛最后一个异常（由调用方记为源失败）。"""
    last = None
    for attempt in range(max(1, retries + 1)):
        try:
            req = urllib.request.Request(url, headers=_BROWSER_HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                    data = gzip.decompress(data)
                return data.decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001 —— 统一记为源失败，不外溢
            last = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise last


# ---------- JSON 落盘（原子写） ----------

def jload(path, default, strict=False):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except Exception as exc:  # noqa: BLE001
        if strict:
            raise ValueError("状态文件读取失败，拒绝覆盖: %s" % path) from exc
        sys.stderr.write("[warn] 读取失败 %s: %s\n" % (path, exc))
        return default


def jdump(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def append_jsonl(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def replace_jsonl(path, rows):
    """完整写入临时文件后替换，避免重判中途留下半份流水。"""
    import tempfile
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".jsonl-", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def read_jsonl(path, strict=False):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, 1):
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception as exc:  # 查询可跳过坏行；写回前必须严格读取。
                    if strict:
                        raise ValueError("流水第 %d 行损坏，拒绝覆盖: %s" % (line_number, path)) from exc
                    continue
    return out


# ---------- 工作区 ----------

def workspace(override=None):
    """解析并初始化工作区目录，返回绝对路径。"""
    ws = os.path.abspath(override or DEFAULT_WS)
    for sub in ("state", "state/streams", "facts", "out", "log"):
        os.makedirs(os.path.join(ws, sub), exist_ok=True)
    return ws


# ---------- 源健康（三态） ----------

def update_health(health, key, ok, error, now_iso_text):
    """连续失败 >= 3 判 down；flapping 是中间态不告警；恢复只认 down -> ok。"""
    rec = health.setdefault(key, {"fails": 0, "status": "ok", "first_fail_at": None,
                                  "last_ok_at": None, "last_error": None})
    if ok:
        rec.update({"fails": 0, "status": "ok", "first_fail_at": None,
                    "last_ok_at": now_iso_text, "last_error": None})
    else:
        rec["fails"] = int(rec.get("fails", 0)) + 1
        rec["last_error"] = str(error)[:300]
        if rec["fails"] >= 3 and rec.get("status") != "down":
            rec["status"] = "down"
            rec["first_fail_at"] = now_iso_text
        elif rec["fails"] >= 1 and rec.get("status") == "ok":
            rec["status"] = "flapping"
    return rec


# ---------- 配置白名单校验 ----------

def check_keys(obj, allowed, where):
    """未知键直接中止：静默忽略会让配置漂移变成'看起来在跑其实没生效'（change-watch 铁律）。"""
    unknown = [k for k in obj.keys() if k not in allowed and not k.startswith("_")]
    if unknown:
        sys.stderr.write("[config-error] %s 含未知字段: %s\n（字段拼写错误会静默失效，必须点名报错）\n"
                         % (where, ", ".join(sorted(unknown))))
        raise SystemExit(1)


# ---------- 中文金额近似解析 ----------

def parse_amount_cny(text):
    """把中文融资金融文本近似解析为人民币元（float），解析不出返回 None。

    只用于门槛比较（min_amount_cny），不求精确：
    - 支持「N亿 / N千万 / N百万 / N万」+「美元/美金」（按 USD_CNY 近似折算）；
    - 模糊表述取保守下限：数千万/过千万=1000万，数百万=100万，上亿/过亿=1亿；
    - 「近亿元」「超2000万」等前缀不影响解析。
    """
    if not text:
        return None
    t = str(text).replace(",", "").replace(" ", "").replace("，", "")
    mult = {"亿": 1e8, "千万": 1e7, "百万": 1e6, "万": 1e4}
    val = None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(亿|千万|百万|万)", t)
    if m:
        val = float(m.group(1)) * mult[m.group(2)]
    elif re.search(r"数千万|过千万|千万级", t):
        val = 1e7
    elif re.search(r"数百万|百万级", t):
        val = 1e6
    elif re.search(r"上亿|过亿|近亿|超亿|亿级|亿元", t):
        val = 1e8
    if val is None:
        # 英文写法：$30B / $200M / $1.5billion / 500K 美元等（外刊源常见）
        m = re.search(r"[$￥€£]?(\d+(?:\.\d+)?)\s*"
                      r"(billion|b|million|m|thousand|k)(?![a-z])",
                      t, re.IGNORECASE)
        if m:
            unit = m.group(2).lower()
            scale = 1e9 if unit in ("billion", "b") else (
                1e6 if unit in ("million", "m") else 1e3)
            val = float(m.group(1)) * scale
            # 带美元符号或 billion/million 语境默认按美元折算（中文源写美元会走上面的分支）
            if "$" in t or re.search(r"dollar|usd", t, re.IGNORECASE):
                val *= USD_CNY
    if val is None:
        return None
    if re.search(r"美元|美金", t):
        val *= USD_CNY
    return val
