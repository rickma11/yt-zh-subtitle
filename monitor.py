#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""监控 YouTube 频道更新。

- 解析 @handle -> channel_id（抓频道页的 browseId/externalId/channelId 字段）
- 拉 RSS（feeds/videos.xml，无需 API key）；RSS 失败时回退 /channel/<id>/videos 标签页
- 与 state.json 比对，输出未处理的新视频到 new_videos.json
- 过滤 YouTube Shorts（只处理普通长视频）
- 首次运行只取每个频道最新 1 期，避免历史 backlog 洪流
"""
import datetime
import json
import os
import re
import sys
import urllib.error
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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不跟随 3xx，用于识别 Short。"""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(newurl, code, msg, headers, fp)


def is_short(video_id, cache):
    """判断 video_id 是否为 YouTube Short。

    依据：GET https://www.youtube.com/shorts/<id>
      - 普通视频 -> 302 重定向到 /watch?v=<id>
      - Short     -> 直接 200 返回
    结果写入 cache（state["shorts_checked"]）避免重复探测；
    探测异常（429/5xx/超时）时返回 False（保留该视频、不缓存，下轮重试）——
    宁可漏过一个 Short，也不误伤正常视频。
    """
    if video_id in cache:
        return cache[video_id]
    url = f"https://www.youtube.com/shorts/{video_id}"
    try:
        opener = urllib.request.build_opener(_NoRedirect(), urllib.request.ProxyHandler({}))
        with opener.open(urllib.request.Request(url, headers=UA), timeout=20) as r:
            result = True  # 直接 200 -> Short
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "")
        if 300 <= e.code < 400 and "watch?v=" in loc:
            result = False
        elif e.code == 404:
            result = False
        else:
            print(f"[monitor] Short 探测异常 {video_id}: HTTP {e.code}"
                  f"（默认按非 Short 处理，不缓存，下轮重试）")
            return False
    except Exception as e:  # noqa: BLE001
        print(f"[monitor] Short 探测异常 {video_id}: {e}"
              f"（默认按非 Short 处理，不缓存，下轮重试）")
        return False
    cache[video_id] = result
    return result


# ---- 发布时间 / “今天自然日(北京时间)” 过滤 ----
# 用户按北京时间(UTC+8)的“自然日”判断“今天更新的视频”。用固定偏移，
# 不依赖 zoneinfo 数据文件（Windows/runner 都自带标准库即可）。
BEIJING = datetime.timezone(datetime.timedelta(hours=8))


def parse_published(s):
    """解析 RSS 的 published（ISO 8601，可能带 Z 或 +08:00 偏移）为带时区 datetime；失败返回 None。"""
    if not s or not str(s).strip():
        return None
    t = str(s).strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        return datetime.datetime.fromisoformat(t)
    except Exception:  # noqa: BLE001
        return None


def get_upload_date(vid):
    """从视频页 /watch 取 uploadDate（最可靠：YouTube 直接给出带原始时区偏移的精确时间）。

    用途：RSS 的 published 缺失/解析失败，以及 /videos-tab 回退路径（页面只给相对/本地化
    时间，按视频本地时区显示会踩时区坑）。失败返回 None，调用方据此保守跳过，避免误抓旧视频。
    """
    url = f"https://www.youtube.com/watch?v={vid}"
    try:
        html = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            urllib.request.Request(url, headers=UA), timeout=20).read().decode("utf-8", "ignore")
    except Exception as e:  # noqa: BLE001
        print(f"[monitor] 取发布时间失败 {vid}: {e}")
        return None
    m = re.search(r'"uploadDate"\s*:\s*"([^"]+)"', html)
    if not m:
        return None
    return parse_published(m.group(1))


def is_today(pub_dt):
    """pub_dt: 带时区 datetime（无时区则按北京时间解释）。判断是否落在北京时间‘今天’自然日。"""
    if pub_dt is None:
        return False
    if pub_dt.tzinfo is None:
        pub_bj = pub_dt.replace(tzinfo=BEIJING)
    else:
        pub_bj = pub_dt.astimezone(BEIJING)
    return pub_bj.date() == datetime.datetime.now(BEIJING).date()


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


def _unescape_json_str(s):
    """YouTube 把 page JSON 里的标题用 JSON 转义（如 \\u0026 表示 &）。用 json.loads 还原。"""
    try:
        return json.loads('"' + s + '"')
    except Exception:  # noqa: BLE001
        return s.replace("\\u0026", "&")


def fetch_videos_tab(channel_id):
    """RSS 兜底：直接抓 /channel/<id>/videos 标签页。

    适用场景：YouTube 对单个频道的 feeds/videos.xml 偶发返回 404/500（本机与 GH runner
    都遇到过，但 /videos 页面本身 200）。新 UI 每个视频块是
    lockupViewModel(contentId) + 紧随其后的 lockupMetadataViewModel(title)，页面顺序 = 最新在前。
    返回与 fetch_rss 同结构的 entries（published 兜底为空，因 tab 页只给相对时间）。
    """
    url = f"https://www.youtube.com/channel/{channel_id}/videos"
    html = http_get(url).decode("utf-8", "ignore")
    cid_pat = r'"contentId":"([\w-]{11})","contentType":"LOCKUP_CONTENT_TYPE_VIDEO"'
    title_pat = r'lockupMetadataViewModel":\{"title":\{"content":"((?:[^"\\]|\\.){1,200})"\}'
    cids = re.findall(cid_pat, html)
    titles = re.findall(title_pat, html)
    n = min(len(cids), len(titles))
    seen = set()
    entries = []
    for i in range(n):
        vid = cids[i]
        if not vid or vid in seen:
            continue
        seen.add(vid)
        title = _unescape_json_str(titles[i]) or "(无标题)"
        entries.append({
            "video_id": vid,
            "title": title,
            "published": "",
            "channel": "",
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
    shorts_checked = state.setdefault("shorts_checked", {})

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
            print(f"[monitor] RSS 拉取失败 {handle} ({cid}): {e} —— 回退 /videos 标签页")
            try:
                entries = fetch_videos_tab(cid)
            except Exception as e2:  # noqa: BLE001
                print(f"[monitor] /videos 回退也失败 {handle}: {e2}，跳过")
                continue
            if not entries:
                print(f"[monitor] /videos 标签页也为空 {handle}，跳过")
                continue
            print(f"[monitor] /videos 回退成功，取得 {len(entries)} 个视频")
        raw_candidates = [e for e in entries if e["video_id"] not in processed]
        first_run = cid not in channels_state
        # 三级过滤：
        #  1) 排除 Short（RSS 现在会把 Shorts 也放进 feed，没意义烧字幕）
        #  2) 只处理“今天自然日内（北京时间）更新”的视频——这是用户核心诉求，
        #     避免某天发布的视频被标记 done 后，又去抓更早的未处理旧片（如 pEmlhqOthHU）
        #  3) first_run 命中 1 个即停（每次只处理 1 期，靠每日多次 cron 顺次覆盖当天多期）
        candidates = []
        for e in raw_candidates:
            vid = e["video_id"]
            if is_short(vid, shorts_checked):
                print(f"[monitor] {handle}: 跳过 Short {vid} | {e['title'][:50]}")
                continue
            # 发布时间：优先用 RSS 的 published；缺失/回退路径无时间则取视频页 uploadDate
            pub = parse_published(e.get("published", "")) if e.get("published") else None
            if pub is None:
                pub = get_upload_date(vid)
            if pub is None:
                print(f"[monitor] {handle}: 无法获取 {vid} 发布时间，保守跳过（不抓旧视频）")
                continue
            if not is_today(pub):
                pstr = pub.astimezone(BEIJING).strftime("%Y-%m-%d %H:%M")
                print(f"[monitor] {handle}: 跳过非今天视频 {vid} | 发布 {pstr}(北京) | {e['title'][:40]}")
                continue
            candidates.append(e)
            if first_run:
                break  # 每次只取 1 个“今天的非 Short”视频，命中即停
        if first_run:
            print(f"[monitor] {handle}: 首次运行，仅取最新 1 期")
        if not candidates:
            print(f"[monitor] {handle}: 无新视频")
            continue
        for e in candidates:
            ptxt = (e.get("published") or "")[:19] or "（回退路径/无 published）"
            print(f"[monitor] {handle}: 新视频(今天) {e['video_id']} | {ptxt} | {e['title'][:60]}")
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
