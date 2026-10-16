#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""字幕翻译封装：Google gtx（主） + MyMemory（备），双端容错。

背景：GitHub Actions runner 是数据中心 IP，Google 免费 gtx 端点经常被限流（429）甚至
整 IP 被墙，导致翻译全部失败、zh.srt 退回英文原文、烧录出来的字幕是英文。
为此：
- 主用 Google gtx（质量好）；失败（含 429）立即回退 MyMemory（免费、GitHub IP 不易被限流）。
- 运行中若 Google 连续失败，整体切到 MyMemory，避免逐段重试风暴拖垮 job 超时。
- 仅当双端都失败时，才保留该段英文原文（极少见）。
"""
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request

import polish

ENDPOINT = "https://translate.googleapis.com/translate_a/single"
MYMEMORY = "https://api.mymemory.translated.net/get"


def _google_translate(text, sl="auto", tl="zh-CN", retries=2):
    """Google gtx 单段翻译；sl=auto 自动识别源语言。失败抛异常由上层回退。"""
    if not text or not text.strip():
        return text
    url = (f"{ENDPOINT}?client=gtx&sl={urllib.parse.quote(sl)}"
           f"&tl={urllib.parse.quote(tl)}&dt=t&q={urllib.parse.quote(text)}")
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read().decode("utf-8"))
            return "".join(part[0] for part in data[0] if part[0])
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429:
                # Google 免费接口对 GitHub Actions IP 容易限流；指数退避 + jitter 给冷却窗口
                sleep = min(30, (2 ** i)) + random.uniform(0, 1)
                print(f"    [translate] Google 429 限流，退避 {sleep:.1f}s ({i+1}/{retries})")
                time.sleep(sleep)
            else:
                time.sleep(1.5 * (i + 1))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"Google failed after {retries} tries: {last}")


def _mymemory_translate(text, sl="auto", tl="zh-CN"):
    """MyMemory 免费翻译（无需 key）。sl=auto 时按英文源处理（本流水线字幕多来自 Whisper en）。"""
    if not text or not text.strip():
        return text
    src = sl if sl and sl != "auto" else "en"
    tgt = tl if tl != "zh-CN" else "zh-CN"
    url = (f"{MYMEMORY}?q={urllib.parse.quote(text)}&langpair={urllib.parse.quote(src)}|"
           f"{urllib.parse.quote(tgt)}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read().decode("utf-8"))
    status = str(d.get("responseStatus"))
    if status != "200":
        raise RuntimeError(f"MyMemory status {status}: {str(d.get('responseMessage',''))[:120]}")
    tr = (d.get("responseData") or {}).get("translatedText", "")
    if not tr or tr.strip() == text.strip():
        raise RuntimeError("MyMemory 返回空/未翻译")
    return tr


def translate(text, sl="auto", tl="zh-CN", retries=2):
    """翻译单段：主 Google，失败回退 MyMemory，双端均失败才抛异常。"""
    if not text or not text.strip():
        return text
    try:
        return _google_translate(text, sl, tl, retries)
    except Exception as e:  # noqa: BLE001
        print(f"    [translate] Google 失败，回退 MyMemory: {e}")
        try:
            return _mymemory_translate(text, sl, tl)
        except Exception as e2:  # noqa: BLE001
            raise RuntimeError(f"translate 双端均失败: {e} | {e2}")


def translate_many(texts, sl="auto", tl="zh-CN", pace=1.0, log_every=25):
    """批量翻译。主 Google，连续失败自动切 MyMemory；仅双端都失败才保留英文原文。"""
    out = []
    fails = 0
    google_dead = False
    consec = 0
    total = len(texts)
    for i, t in enumerate(texts, 1):
        res = None
        if not google_dead:
            try:
                res = _google_translate(t, sl, tl, retries=2)
                consec = 0
            except Exception as e:  # noqa: BLE001
                print(f"    [translate] Google 失败: {e}")
                consec += 1
                if consec >= 5:
                    google_dead = True
                    print("    [translate] Google 连续失败，整体切换 MyMemory")
        if res is None:
            try:
                res = _mymemory_translate(t, sl, tl)
                consec = 0
            except Exception as e2:  # noqa: BLE001
                fails += 1
                print(f"    [translate] 第 {i} 段双端失败，保留原文: {e2}")
                res = t
        out.append(res)
        if i % log_every == 0:
            print(f"    [translate] {i}/{total} (失败 {fails} 段, 当前源={'MyMemory' if google_dead else 'Google'})")
        time.sleep(pace if not google_dead else 0.3)
    print(f"    [translate] 完成 {total} 段，失败 {fails} 段")
    # 出口统一本地化润色（FC27/足球术语 + 口语化）
    return [polish.polish(x) for x in out]
