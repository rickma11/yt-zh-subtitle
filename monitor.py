#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""监控 YouTube 频道更新。

- 解析 @handle -> channel_id（抓频道页的 browseId/externalId/channelId 字段）
- 拉 RSS（feeds/videos.xml，无需 API key）；RSS 失败时回退 /channel/<id>/videos 标签页
- 与维护表 video_log.json 比对，只挑“频道最新一条非 Short 且不在维护表中”的视频
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


# 注：原“今天自然日(北京时间)”发布时间过滤已弃用——@AuzioMF 的 RSS 常被限流返回 404，
# 而 /watch 取 uploadDate 又被 GitHub runner 的 IP 反爬挡掉，拿不到可靠时间。
# 现改用 video_log.json 维护表做“频道最新视频是否首次出现”的去重（见 select_update / main），
# 完全不依赖 published。


def select_update(entries, vlog, shorts_checked, handle):
    """从 entries（最新在前）里挑出‘最新一条非 Short 且不在维护表’的视频。

    返回该 entry；若没有符合条件的视频（全是 Short / 最新一条已在表中）返回 None。
    这正是用户要的判定：最新一条既不是维护表里最新的那条、也不是表里其他的 → 即更新。
    """
    newest_regular = None
    for e in entries:
        vid = e["video_id"]
        if is_short(vid, shorts_checked):
            print(f"[monitor] {handle}: 跳过 Short {vid} | {e['title'][:50]}")
            continue
        newest_regular = e  # entries 已按最新在前排序，第一条非 Short 即最新普通视频
        break
    if newest_regular is None:
        print(f"[monitor] {handle}: 未发现非 Short 视频，结束")
        return None
    vid = newest_regular["video_id"]
    if vid in vlog:
        rec = vlog[vid]
        d = rec.get("date") if isinstance(rec, dict) else rec
        print(f"[monitor] {handle}: 最新视频 {vid} 已在维护表中({d})，非更新，跳过")
        return None
    print(f"[monitor] {handle}: 检测到更新(最新未处理) {vid} | {newest_regular['title'][:60]}")
    return newest_regular


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
        chosen = select_update(entries, vlog, shorts_checked, handle)
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
