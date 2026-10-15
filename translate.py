#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Google 免费翻译端点封装（带重试/退避）。GitHub Actions IP 可直连，无需代理。"""
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request

import polish

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
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429:
                # Google 免费接口对 GitHub Actions IP 容易限流；
                # 遇到 429 用指数退避 + jitter，最大约 30s，给限流窗口冷却时间。
                sleep = min(30, (2 ** i)) + random.uniform(0, 1)
                print(f"    [translate] 429限流，退避 {sleep:.1f}s 后重试 ({i+1}/{retries})")
                time.sleep(sleep)
            else:
                time.sleep(1.5 * (i + 1))
        except Exception as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"translate failed after {retries} tries: {last}")


def translate_many(texts, sl="auto", tl="zh-CN", pace=1.0, log_every=25):
    """批量翻译，失败段保底留原文，保证流水线不中断。"""
    out = []
    fails = 0
    for i, t in enumerate(texts, 1):
        try:
            out.append(translate(t, sl=sl, tl=tl))
        except Exception as e:  # noqa: BLE001
            fails += 1
            print(f"    [translate] 第 {i} 段失败，保留原文: {e}")
            out.append(t)
        if i % log_every == 0:
            print(f"    [translate] {i}/{len(texts)} (失败 {fails} 段)")
        time.sleep(pace)
    print(f"    [translate] 完成 {len(texts)} 段，失败 {fails} 段")
    # 出口统一本地化润色（FC27/足球术语 + 口语化）
    return [polish.polish(t) for t in out]
