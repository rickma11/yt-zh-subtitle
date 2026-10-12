#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""监控 YouTube 频道更新。

- 解析 @handle -> channel_id（抓频道页的 browseId/externalId/channelId 字段）
- 拉 RSS（feeds/videos.xml，无需 API key）
- 与 state.json 比对，输出未处理的新视频到 new_videos.json
- 首次运行只取每个频道最新 1 期，避免历史 backlog 洪流
"""
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(ROOT, "config.json")
STATE = os.path.join(ROOT, "state.json")
OUT = os.path.join(ROOT, "new_videos.json")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
NS = {
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
    "a": "http://www.w3.org/2005/Atom",
}


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return default


def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def resolve_channel_id(handle, cache):
    """@Name / UCxxxx / 完整 URL -> channel_id"""
    h = handle.strip()
    if re.fullmatch(r"UC[\w-]{22}", h):
        return h
    if h in cache:
        return cache[h]
    url = h if h.startswith("http") else "https://www.youtube.com/" + h.lstrip("@")
    html = http_get(url).decode("utf-8", "ignore")
    m = re.search(r'"(?:browseId|externalId|channelId)":"(UC[\w-]{22})"', html)
    if not m:
        raise RuntimeError(f"无法从频道页解析 channel_id: {handle}")
    cache[h] = m.group(1)
    return m.group(1)


def fetch_rss(channel_id):
    xml = http_get(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")
    root = ET.fromstring(xml)
    entries = []
    for e in root.findall("a:entry", NS):
        vid = e.findtext("yt:videoId", default="", namespaces=NS)
        title = e.findtext("a:title", default="", namespaces=NS)
        mg = e.find("media:group", NS)
        if mg is not None:
            t = mg.findtext("media:title", default=None, namespaces=NS)
            if t:
                title = t
        pub = e.findtext("a:published", default="", namespaces=NS)
        author = e.find("a:author/a:name", NS)
        author = author.text if author is not None else ""
        if vid:
            entries.append({
                "video_id": vid,
                "title": title or "(无标题)",
                "published": pub,
                "channel": author,
            })
    return entries


def main():
    cfg = load_json(CONFIG, {})
    state = load_json(STATE, {"channels": {}, "processed": {}, "resolved_ids": {}})
    resolved = state.setdefault("resolved_ids", {})
    # config 里预置的 ID 也纳入缓存
    for k, v in (cfg.get("resolved_channel_ids") or {}).items():
        resolved.setdefault(k, v)
    processed = state.setdefault("processed", {})
    channels_state = state.setdefault("channels", {})

    new_videos = []
    for handle in cfg.get("channels", []):
        try:
            cid = resolve_channel_id(handle, resolved)
        except Exception as e:  # noqa: BLE001
            print(f"[monitor] 解析失败 {handle}: {e}")
            continue
        try:
            entries = fetch_rss(cid)
        except Exception as e:  # noqa: BLE001
            print(f"[monitor] RSS 拉取失败 {handle} ({cid}): {e}")
            continue
        candidates = [e for e in entries if e["video_id"] not in processed]
        first_run = cid not in channels_state
        if first_run:
            candidates = candidates[:1]
            print(f"[monitor] {handle}: 首次运行，仅取最新 1 期")
        if not candidates:
            print(f"[monitor] {handle}: 无新视频")
            continue
        for e in candidates:
            print(f"[monitor] {handle}: 新视频 {e['video_id']} | "
                  f"{e['published'][:19]} | {e['title'][:60]}")
            new_videos.append({**e, "channel_id": cid, "channel_handle": handle})
    # 注意：channels_state 的"已初始化"标记由 run_batch 在处理成功后回写，
    # 不在 monitor 阶段写——否则处理失败也会吃掉 first_run 保护，导致下轮
    # 把整个 backlog 全部重跑（2026-10-12 实测教训：15 个视频每个被拦 3 分钟）。

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(new_videos, f, ensure_ascii=False, indent=2)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    print(f"[monitor] 共 {len(new_videos)} 个待处理视频 -> new_videos.json")

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write("has_new=%s\n" % ("true" if new_videos else "false"))


if __name__ == "__main__":
    sys.exit(main())
