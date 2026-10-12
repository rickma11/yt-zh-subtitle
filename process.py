#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单视频端到端处理：中文字幕分层获取 -> 下载视频 -> ffmpeg 硬字幕烧录。

字幕分层（优先级从高到低，前一层成功就不走下一层）：
  T1 zh-direct  : YouTube 直接提供简体/繁体中文字幕（native 或 auto-translate 轨）
  T2 translated : 只有源语言字幕（优先 en）-> Google 翻译成中文
  T3 whisper    : 任何字幕都没有 -> 本地 faster-whisper 转写音频 -> 翻译

所有 yt-dlp 调用都会轮换 player_client（数据中心 IP 常被 bot 墙，换客户端有可能绕过）；
仍不行时提供 cookies（YT_COOKIES secret 或仓库内 cookies.txt）。

产物：<日期>_<频道>_<标题>.mp4（硬字幕）+ 同名 .zh.srt + meta.json
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata

import translate as tr

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
# PO token provider（bgutil）地址；workflow 里配了就对 web 系客户端附加 po_token
POT_URL = os.environ.get("PO_TOKEN_URL", "").strip()
_WEB_CLIENTS = ["web", "web_safari", "mweb"]
_OTHER_CLIENTS = ["default", "android", "android_vr", "tv", "tv_embedded"]
# player_client 轮换链：datacenter IP 下 default(visionos) 常被 bot 墙，逐个试；
# 有 POT 时 web 系提前（po_token 只对 web client 有效）
CLIENT_FALLBACKS = (_WEB_CLIENTS + _OTHER_CLIENTS) if POT_URL else \
    (["default"] + _WEB_CLIENTS + _OTHER_CLIENTS[1:])
ZH_DIRECT_LANGS = ["zh-Hans", "zh-Hans-CN", "zh-CN", "zh", "zh-Hant"]


def _pot_args(client):
    """PO token 的 client 前缀固定为 web，仅对 web 系客户端附加。"""
    if POT_URL and client in _WEB_CLIENTS:
        return ["--extractor-args", f"youtube:po_token=web+{POT_URL}"]
    return []


def log(*a):
    print(*a, flush=True)


# ---------- yt-dlp 封装（带 client 轮换 + 可选 cookies） ----------

def _base(work, cookies):
    b = [sys.executable, "-m", "yt_dlp", "--no-warnings", "--no-progress",
         "--user-agent", UA, "-P", work]
    if cookies:
        b += ["--cookies", cookies]
    return b


def ytdlp(args, work, cookies=None, clients=None, retries=1):
    """跑 yt-dlp，按 clients 依次轮换，任一成功返回 True。"""
    for client in (clients or ["default"]):
        ea = [] if client == "default" else \
            ["--extractor-args", f"youtube:player_client={client}"]
        ea += _pot_args(client)
        for i in range(retries):
            log(f"  $ yt-dlp[{client}] {' '.join(str(c) for c in args)[:110]}")
            r = subprocess.run(_base(work, cookies) + ea + args)
            if r.returncode == 0:
                return True
            log(f"    yt-dlp 失败 (client={client}, rc={r.returncode})")
            time.sleep(2 * (i + 1))
    return False


def ytdlp_capture(args, cookies=None, clients=None, timeout=300):
    """跑 yt-dlp 并捕获 stdout（用于 --dump-single-json）。成功返回 stdout。"""
    for client in (clients or ["default"]):
        ea = [] if client == "default" else \
            ["--extractor-args", f"youtube:player_client={client}"]
        ea += _pot_args(client)
        cmd = ([sys.executable, "-m", "yt_dlp", "--no-warnings", "--no-progress",
                "--user-agent", UA] + (["--cookies", cookies] if cookies else [])
               + ea + args)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            log(f"    dump 异常 (client={client}): {e}")
            continue
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout
        err = (r.stderr or "").strip().splitlines()
        if err:
            log(f"    [{client}] {err[-1][:150]}")
    return None


def cookies_path(work):
    """YT_COOKIES 环境变量（secret）优先，其次仓库里的 cookies.txt"""
    env = os.environ.get("YT_COOKIES")
    if env:
        p = os.path.join(work, "cookies.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(env)
        log("    使用 YT_COOKIES secret")
        return p
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
    if os.path.exists(p):
        log("    使用仓库内 cookies.txt")
        return p
    return None


# ---------- 元数据 ----------

def get_metadata(url, cookies):
    out = ytdlp_capture(["--dump-single-json", "--skip-download", url],
                        cookies=cookies, clients=CLIENT_FALLBACKS)
    if out:
        try:
            d = json.loads(out)
            return {
                "title": d.get("title") or "",
                "channel": d.get("channel") or d.get("uploader") or "",
                "duration": d.get("duration") or 0,
                "subtitles": list((d.get("subtitles") or {}).keys()),
                "auto_captions": list((d.get("automatic_captions") or {}).keys()),
            }
        except Exception as e:  # noqa: BLE001
            log("    dump-single-json 解析失败:", e)
    # oEmbed 兜底（免鉴权）
    try:
        import urllib.parse
        import urllib.request
        oe = "https://www.youtube.com/oembed?url=%s&format=json" % urllib.parse.quote(url, safe="")
        req = urllib.request.Request(oe, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        return {"title": d.get("title", ""), "channel": d.get("author_name", ""),
                "duration": 0, "subtitles": [], "auto_captions": []}
    except Exception as e:  # noqa: BLE001
        log("    oEmbed 也失败:", e)
    return {"title": url, "channel": "unknown", "duration": 0,
            "subtitles": [], "auto_captions": []}


# ---------- SRT 工具 ----------

def parse_srt(path):
    txt = open(path, encoding="utf-8", errors="ignore").read()
    res = []
    for b in re.split(r"\n\s*\n", txt.strip()):
        lines = [l.strip() for l in b.splitlines() if l.strip()]
        if len(lines) < 2:
            continue
        m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", lines[1])
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        res.append({"start": start, "end": end, "text": clean_text(" ".join(lines[2:]))})
    return [s for s in res if s["text"]]


def clean_text(t):
    t = re.sub(r"<[^>]+>", "", t)
    t = t.replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&")
    t = re.sub(r"&[a-z]+;", " ", t)
    t = re.sub(r">>+", "", t)
    t = re.sub(r"\[[^\]]{0,30}\]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def fmt_ts(t):
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t % 1) * 1000))
    if ms == 1000:
        ms = 0
        s += 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(segs, path):
    with open(path, "w", encoding="utf-8") as f:
        for i, s in enumerate(segs, 1):
            f.write(f"{i}\n{fmt_ts(s['start'])} --> {fmt_ts(s['end'])}\n{s['text']}\n\n")


# ---------- T1: 直接中文字幕 ----------

def tier_direct_zh(url, work, cookies):
    log("  [T1] 尝试直接获取中文字幕 (zh-Hans 等) ...")
    ytdlp(["--skip-download", "--write-subs", "--write-auto-subs",
           "--sub-langs", ",".join(ZH_DIRECT_LANGS),
           "--convert-subs", "srt", "-o", "sub.%(ext)s", url],
          work, cookies=cookies, clients=CLIENT_FALLBACKS)
    found = sorted(f for f in os.listdir(work)
                   if re.search(r"\.zh(-Hans|-Hant|-CN|-TW|-HK)?\.srt$", f))
    if not found:
        log("  [T1] 无中文字幕轨")
        return None
    pick = next((f for f in found if "Hans" in f or f.endswith(".zh.srt")), found[0])
    log(f"  [T1] 命中: {pick}")
    segs = parse_srt(os.path.join(work, pick))
    if len(segs) < 3:
        log(f"  [T1] 字幕过短 ({len(segs)} 段)，弃用")
        return None
    return segs, "zh-direct", pick


# ---------- T2: 源字幕 + 翻译 ----------

def pick_source_lang(meta):
    subs, autos = meta.get("subtitles", []), meta.get("auto_captions", [])
    if "en" in subs:
        return "en", "manual"
    if "en" in autos:
        return "en", "auto"
    for pref in ["en-US", "en-GB", "en-orig"]:
        if pref in subs:
            return pref, "manual"
    if subs:
        return sorted(subs)[0], "manual"
    if autos:
        return sorted(autos)[0], "auto"
    return None, None


def tier_translated(url, work, cookies, meta, tl):
    lang, kind = pick_source_lang(meta)
    if not lang:
        log("  [T2] 无任何源字幕")
        return None
    log(f"  [T2] 使用源字幕 {lang} ({kind}) -> 翻译为 {tl}")
    flag = "--write-auto-subs" if kind == "auto" else "--write-subs"
    ytdlp([flag, "--skip-download", "--sub-langs", lang,
           "--convert-subs", "srt", "-o", "src.%(ext)s", url],
          work, cookies=cookies, clients=CLIENT_FALLBACKS)
    cand = [f for f in os.listdir(work)
            if f.startswith("src.") and f.endswith(".srt")]
    if not cand:
        log("  [T2] 源字幕下载失败")
        return None
    segs = parse_srt(os.path.join(work, cand[0]))
    if len(segs) < 3:
        log(f"  [T2] 源字幕过短 ({len(segs)} 段)，弃用")
        return None
    if lang.startswith("zh"):
        log("  [T2] 源字幕本就是中文，无需翻译")
        return segs, "zh-direct", cand[0]
    sl = "en" if lang.startswith("en") else lang
    zh_texts = tr.translate_many([s["text"] for s in segs], sl=sl, tl=tl)
    for s, t in zip(segs, zh_texts):
        s["text"] = t
    return segs, "translated", cand[0]


# ---------- T3: Whisper ----------

def tier_whisper(url, work, cookies, model_name, tl):
    log("  [T3] 无字幕，下载音频走本地 Whisper 转写 ...")
    ytdlp(["-f", "bestaudio", "--extract-audio", "--audio-format", "wav",
           "-o", "audio.%(ext)s", url],
          work, cookies=cookies, clients=CLIENT_FALLBACKS, retries=2)
    wav = os.path.join(work, "audio.wav")
    if not os.path.exists(wav):
        cand = [f for f in os.listdir(work) if f.startswith("audio.")]
        if not cand:
            raise RuntimeError("音频下载失败（所有 client 均被拦）")
        wav = os.path.join(work, cand[0])
    from faster_whisper import WhisperModel
    model = WhisperModel(model_name, device="cpu", compute_type="int8",
                         cpu_threads=os.cpu_count() or 4)
    log(f"    转写中 (model={model_name}, cpu={os.cpu_count()}) ...")
    t0 = time.time()
    segments, info = model.transcribe(wav, vad_filter=True, beam_size=5)
    segs = []
    for s in segments:
        txt = clean_text(s.text)
        if txt:
            segs.append({"start": s.start, "end": s.end, "text": txt})
    log(f"    转写完成: {len(segs)} 段, 语言={info.language}, 耗时 {time.time()-t0:.0f}s")
    if len(segs) < 3:
        raise RuntimeError("Whisper 转写结果过短")
    lang = (info.language or "en")
    if lang.startswith("zh"):
        log("    转写即为中文，无需翻译")
        return segs, "whisper-zh", wav
    zh_texts = tr.translate_many([s["text"] for s in segs], sl=lang, tl=tl)
    for s, t in zip(segs, zh_texts):
        s["text"] = t
    return segs, "whisper", wav


# ---------- 视频下载 ----------

def download_video(url, work, cookies, max_height):
    fmt = f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b"
    ok = ytdlp(["-f", fmt, "--merge-output-format", "mp4",
                "-o", "video.%(ext)s", url],
               work, cookies=cookies, clients=CLIENT_FALLBACKS, retries=2)
    vids = [f for f in os.listdir(work)
            if f.startswith("video.") and f != "video.info.json"]
    if not ok or not vids:
        raise RuntimeError("视频下载失败（所有 client 均被拦）")
    log(f"    视频就绪: {vids[0]}")
    return os.path.join(work, vids[0])


# ---------- 烧录 ----------

def burn(video_path, out_path):
    log("  ffmpeg 硬字幕烧录 ...")
    vf = ("subtitles=zh.srt:force_style='FontSize=15,"
          "FontName=Noto Sans CJK SC,"
          "PrimaryColour=&H00FFFFFF&,OutlineColour=&H00000000&,"
          "BackColour=&H80000000&,Outline=2,Shadow=1,Bold=1,MarginV=26'")
    r = subprocess.run(["ffmpeg", "-y", "-i", os.path.basename(video_path),
                        "-vf", vf, "-c:a", "copy", "-c:v", "libx264",
                        "-crf", "20", "-preset", "veryfast",
                        os.path.basename(out_path)],
                       cwd=os.path.dirname(video_path))
    if r.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError("ffmpeg 烧录失败")


# ---------- 文件名 ----------

def sanitize(name, limit=70):
    name = unicodedata.normalize("NFKC", name)
    name = "".join(c for c in name
                   if not (0x1F000 <= ord(c) <= 0x1FAFF
                           or 0x2600 <= ord(c) <= 0x27BF
                           or ord(c) in (0x2B50, 0xFE0F, 0x200D)))
    name = re.sub(r'[\\/:*?"<>|]', " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:limit] or "video"


# ---------- 主流程 ----------

def process_video(video, work_root, cfg):
    vid = video["video_id"]
    url = f"https://www.youtube.com/watch?v={vid}"
    work = os.path.join(work_root, vid)
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, exist_ok=True)
    ck = cookies_path(work)
    tl = cfg.get("translate_target", "zh-CN")
    t0 = time.time()

    log(f"\n===== 处理 {vid} | {video.get('title', '')[:60]} =====")
    meta = get_metadata(url, ck)
    log(f"  标题: {meta['title'][:70]}")
    log(f"  频道: {meta['channel']} | 时长: {meta.get('duration', 0)}s")
    log(f"  可用字幕: manual={meta['subtitles'][:8]} auto={meta['auto_captions'][:8]}")

    result = tier_direct_zh(url, work, ck)
    if not result:
        result = tier_translated(url, work, ck, meta, tl)
    if not result:
        result = tier_whisper(url, work, ck,
                              cfg.get("whisper_model", "small"), tl)
    segs, tier, src_file = result
    write_srt(segs, os.path.join(work, "zh.srt"))
    log(f"  字幕就绪: {len(segs)} 段 (tier={tier})")

    video_path = download_video(url, work, ck, cfg.get("max_height", 1080))
    date = (video.get("published") or "")[:10].replace("-", "") or \
        time.strftime("%Y%m%d", time.gmtime())
    base = sanitize(f"{date}_{meta['channel']}_{meta['title']}")
    out_mp4 = os.path.join(work, base + ".mp4")
    out_srt = os.path.join(work, base + ".zh.srt")
    burn(video_path, out_mp4)
    shutil.copyfile(os.path.join(work, "zh.srt"), out_srt)

    meta_out = {
        "video_id": vid,
        "url": url,
        "title": meta["title"],
        "channel": meta["channel"],
        "published": video.get("published", ""),
        "duration": meta.get("duration", 0),
        "subtitle_tier": tier,
        "subtitle_source": src_file,
        "segments": len(segs),
        "mp4": os.path.basename(out_mp4),
        "srt": os.path.basename(out_srt),
        "elapsed_sec": round(time.time() - t0, 1),
    }
    with open(os.path.join(work, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta_out, f, ensure_ascii=False, indent=2)
    log(f"  完成 (tier={tier}, {meta_out['elapsed_sec']}s): {base}.mp4")
    return meta_out


if __name__ == "__main__":
    cfg = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "config.json"), encoding="utf-8"))
    v = {"video_id": sys.argv[1], "title": "", "published": ""}
    root = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "work")
    print(json.dumps(process_video(v, root, cfg), ensure_ascii=False, indent=2))
