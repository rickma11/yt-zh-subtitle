#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Google 免费翻译端点封装（带重试/退避）。GitHub Actions IP 可直连，无需代理。"""
import json
import time
import urllib.parse
import urllib.request

ENDPOINT = "https://translate.googleapis.com/translate_a/single"


def translate(text, sl="auto", tl="zh-CN", retries=4):
    """翻译单段文本。sl=auto 自动识别源语言。失败抛异常由上层保底。"""
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
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"translate failed after {retries} tries: {last}")


def translate_many(texts, sl="auto", tl="zh-CN", pace=0.12, log_every=25):
    """批量翻译，失败段保底留原文，保证流水线不中断。"""
    out = []
    for i, t in enumerate(texts, 1):
        try:
            out.append(translate(t, sl=sl, tl=tl))
        except Exception as e:  # noqa: BLE001
            print(f"    [translate] 第 {i} 段失败，保留原文: {e}")
            out.append(t)
        if i % log_every == 0:
            print(f"    [translate] {i}/{len(texts)}")
        time.sleep(pace)
    return out
