#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""监控 YouTube 频道更新。

- 解析 @handle -> channel_id（抓频道页的 browseId/externalId/channelId 字段）
- 拉 RSS（feeds/videos.xml，无需 API key）；RSS 失败时回退 /channel/<id>/videos 标签页
- 与维护表 video_log.json 比对，遍历 entries 挑出“第一条未处理(不在维护表)且发布在
  MAX_AGE_DAYS 天内”的普通视频（fall-through：若最新一条已在表中会往下找，不再整轮跳过）
  → 视为更新 → 输出到 new_videos.json
- 维护表记录每个已处理视频的 id + 处理日期（北京时间），持续累积，用于去重
  （不依赖被限流的 RSS published 时间）
- 过滤 YouTube Shorts（只处理普通长视频）
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(ROOT, "config.json")
STATE = os.path.join(ROOT, "state.json")
OUT = os.path.join(ROOT, "new_videos.json")
VIDEO_LOG_PATH = os.path.join(ROOT, "video_log.json")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
# YouTube cookies（可选，来自 secret YT_COOKIES）。@AuzioMF 这类频道的 RSS 常被 YouTube 限流
# 返回 404、/watch 取 uploadDate 也被 runner IP 反爬；带登录态 cookie 可显著提升命中率。
# secret 末尾常带换行符，必须strip，否则 urllib 报 "Invalid header value"。
COOKIE = (os.environ.get("YT_COOKIES", "") or "").replace("\r", "").replace("\n", "").strip()
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


def load_video_log():
    """读取维护表（视频id -> {date,title,channel}）。不存在/损坏返回空字典。"""
    try:
        with open(VIDEO_LOG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def save_video_log(data):
    with open(VIDEO_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    if COOKIE:
        req.add_header("Cookie", COOKIE)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不跟随 3xx，用于识别 Short。"""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(newurl, code, msg, headers, fp)


# YouTube 现已对所有视频的 /shorts/<id> 返回 200（不再 302 到 /watch），旧“200=Short”判定彻底失效。
# 改用视频时长判定：Shorts 时长 <= SHORT_MAX_SECONDS（秒）。从 /shorts/<id> 页面 HTML 的 player
# config 提取 lengthSeconds（首个匹配即主视频时长）。探测异常/拿不到时长时默认非 Short（不缓存，
# 下轮重试）——宁可漏过一个 Short，也不误伤正常视频。
SHORT_MAX_SECONDS = 60


def is_short(video_id, cache):
    """判断 video_id 是否为 YouTube Short（基于视频时长）。"""
    if video_id in cache:
        return cache[video_id]
    url = f"https://www.youtube.com/shorts/{video_id}"
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read().decode("utf-8", "ignore")
        m = re.search(r'"lengthSeconds"\s*:\s*"?(\d+)', html)
        if not m:
            print(f"[monitor] {video_id}: 未取得时长，默认按非 Short 处理（不缓存）")
            return False
        dur = int(m.group(1))
        result = dur <= SHORT_MAX_SECONDS
        print(f"[monitor] {video_id}: 时长 {dur}s -> {'Short' if result else '普通'}")
    except Exception as e:  # noqa: BLE001
        print(f"[monitor] Short 探测异常 {video_id}: {e}（默认按非 Short 处理，不缓存，下轮重试）")
        return False
    cache[video_id] = result
    return result


# 注：原“今天自然日(北京时间)”硬过滤曾因 RSS published 不可靠(被限流 404 / runner IP 反爬)
# 而弃用。现 RSS 已能稳定返回带时区的 published（实测正常），故在 select_update 内恢复一层
# “MAX_AGE_DAYS 天内”的年龄护栏作为安全网（专治频道把 2019 置顶老视频排 RSS 第一的异常），
# 同时保留 video_log.json 维护表做“首次出现”去重——两者互补，不完全依赖 published。


def _parse_rfc3339(s):
    """解析 RSS 的 published(如 2026-10-19T19:00:45+00:00) 为 aware datetime；失败返回 None。"""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


def select_update(entries, vlog, shorts_checked, handle, max_age_days=7):
    """从 entries（最新在前）里挑出第一条『未处理且发布在 N 天内』的普通视频。

    相比旧实现的两处关键修复：
    1. 遍历全部 entries（而非只看第一条）——若最新一条已在维护表（例如被 YouTube 置顶到
       RSS 首位的 2019 老视频），会 fall-through 继续往下找，直到命中第一条未处理的新片，
       不会再因“最新一条在表里”而整轮 return None、永远够不到下面的真新片。
    2. 年龄护栏：published 距今天(UTC)超过 max_age_days 的直接跳过，专治 RSS 把陈年置顶
       视频排第一的异常，避免把 2019 老片当新片处理。RSS 现能稳定返回可靠 published。
    /videos 兜底无 published 时退化为“只跳过已在表的”，不拦（保持兼容）。
    """
    now = datetime.now(timezone.utc)
    for e in entries:
        vid = e["video_id"]
        if vid in vlog:
            print(f"[monitor] {handle}: 跳过已处理 {vid} | {e['title'][:50]}")
            continue
        if is_short(vid, shorts_checked):
            print(f"[monitor] {handle}: 跳过 Short {vid} | {e['title'][:50]}")
            continue
        pub = e.get("published", "")
        dt = _parse_rfc3339(pub) if pub else None
        if dt is not None:
            age = (now - dt).days
            if age > max_age_days:
                print(f"[monitor] {handle}: 跳过过旧视频 {vid} (发布 {pub}, {age}天前, 超 {max_age_days}天)")
                continue
        print(f"[monitor] {handle}: 检测到更新(最新未处理) {vid} | {e['title'][:60]}")
        return e
    print(f"[monitor] {handle}: 无未处理的新视频（均已处理/过旧/Short）")
    return None


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


def _parse_rss(xml):
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


def fetch_rss(channel_id, retries=3):
    """拉频道 RSS，返回带精确 published(UTC) 的 entries。

    端点优先级（针对 @AuzioMF 这类 channel_id RSS 间歇 404 的频道做加固）：
      1) playlist_id=UU... 形式的“上传”播放列表 RSS（通常比 channel_id 端点稳定）
      2) channel_id=UC...（原端点）
      3) rss.youtube.com 域名的上述两种（部分网络/runner 对该域名更友好）
    全部候选都试一遍，整体重试 retries 次，以应对 YouTube 临时限流/间歇 404。
    只要任一端点返回非空 entries 即采用——RSS 是唯一带可靠发布时间的源，
    命中后“今天自然日”过滤才成立。
    """
    pl_id = "UU" + channel_id[2:]
    candidates = [
        f"https://www.youtube.com/feeds/videos.xml?playlist_id={pl_id}",
        f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
        f"https://rss.youtube.com/feeds/videos.xml?channel_id={channel_id}",
        f"https://rss.youtube.com/feeds/videos.xml?playlist_id={pl_id}",
    ]
    last_err = None
    for _ in range(retries):
        for url in candidates:
            try:
                xml = http_get(url)
                entries = _parse_rss(xml)
                if entries:
                    return entries
            except Exception as e:  # noqa: BLE001
                last_err = e
                continue
        time.sleep(2)
    raise last_err or RuntimeError("RSS 拉取失败")


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
        # ---- 维护表去重：只抓“最新一条非 Short 且不在维护表中”的视频 ----
        # 维护表 video_log.json 记录每个已处理视频的 id + 处理日期（北京时间），持续累积。
        # 逻辑：频道最新一条非 Short 视频，若不在维护表里（既不是表里最新的、也不是表里
        # 任何一条）→ 视为一次更新 → 抓取并写进表；否则跳过。
        # 不依赖 RSS 的 published（@AuzioMF 的 RSS 常被限流 404、/watch 取时间又被 runner
        # IP 反爬挡掉），改用“频道最新视频是否首次出现”判定更新，稳定可靠。
        vlog = load_video_log()
        max_age = cfg.get("max_age_days", 7)
        chosen = select_update(entries, vlog, shorts_checked, handle, max_age)
        if chosen is None:
            continue
        new_videos.append({**chosen, "channel_id": cid, "channel_handle": handle})

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
