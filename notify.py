#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Server酱 微信推送。环境变量 SERVERCHAN_KEY（SendKey）；未配置则跳过。"""
import json
import os
import urllib.parse
import urllib.request

TIER_DESC = {
    "zh-direct": "YouTube 原生中文字幕（直取）",
    "translated": "源语言字幕 + Google 翻译",
    "whisper": "本地 Whisper 转写 + Google 翻译",
    "whisper-zh": "本地 Whisper 转写（原生中文）",
}


def serverchan(title, desp):
    key = os.environ.get("SERVERCHAN_KEY", "").strip()
    if not key:
        print("[notify] 未配置 SERVERCHAN_KEY，跳过推送")
        return False
    url = f"https://sctapi.ftqq.com/{key}.send"
    data = urllib.parse.urlencode({"title": title, "desp": desp}).encode("utf-8")
    req = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            resp = json.loads(r.read().decode("utf-8"))
        print(f"[notify] Server酱 code={resp.get('code')} msg={resp.get('message')}")
        return resp.get("code") == 0
    except Exception as e:  # noqa: BLE001
        print(f"[notify] Server酱推送失败: {e}")
        return False


def build_desp(meta, release_url):
    return (
        f"**频道**：{meta.get('channel', '')}\n\n"
        f"**标题**：{meta.get('title', '')}\n\n"
        f"**发布日期**：{(meta.get('published') or '')[:10]}\n\n"
        f"**中字来源**：{TIER_DESC.get(meta.get('subtitle_tier', ''), meta.get('subtitle_tier', ''))}"
        f"（{meta.get('segments', 0)} 段）\n\n"
        f"**原视频**：{meta.get('url', '')}\n\n"
        f"**下载链接**：{release_url}\n\n"
        f"文件：`{meta.get('mp4', '')}`（硬字幕）+ `{meta.get('srt', '')}`（字幕文件）"
    )
