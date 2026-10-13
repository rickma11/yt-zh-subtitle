#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生产：字幕获取（yt-dlp 直连 YouTube，runner 干净 IP）。

策略（三层降级）：
  T1: yt-dlp 直取 zh-Hans/zh-CN 中文字幕 -> work/zh.srt（无需翻译）
  T2: yt-dlp 取 en/en_auto 源字幕 -> work/en.srt（待 finalize.py 经 Google gtx 翻译）
  T3: downsub 兜底（runner 通常不可达，仅快速探测，失败即终止）

产物契约与 finalize.py 对齐：work/zh.srt 或 work/en.srt + work/subs_meta.json
"""
import os
import sys
import json
import glob
import time
import subprocess

WORK = os.environ.get("OUTDIR", "work")
VID = os.environ.get("VID") or "KUf0x6FG7p0"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def log(*a):
    print("[subs]", *a, flush=True)


def run(cmd):
    log("exec:", " ".join(cmd[:5]) + (" ..." if len(cmd) > 5 else ""))
    return subprocess.run(cmd, capture_output=True, text=True)


def ytdlp_extract():
    url = f"https://www.youtube.com/watch?v={VID}"
    out = os.path.join(WORK, "yt_sub")
    cmd = [
        "yt-dlp", "--skip-download",
        "--write-subs", "--write-auto-subs",
        "--sub-langs", "zh-Hans,zh-CN,en",
        "--convert-subs", "srt",
        "--no-warnings", "--user-agent", UA,
        "-o", out, url,
    ]
    for attempt in range(3):
        try:
            r = run(cmd)
        except FileNotFoundError:
            log("FATAL 未找到 yt-dlp，请先 pip install yt-dlp")
            return []
        files = sorted(glob.glob(os.path.join(WORK, "yt_sub*.srt")))
        if files:
            return files
        tail = (r.stderr or "")[-400:].replace("\n", " ")
        log(f"yt-dlp 未产出字幕 (attempt {attempt + 1}/3) rc={r.returncode} | {tail}")
        if attempt < 2:
            time.sleep(12)
    return []


def classify(files):
    zh = [f for f in files if any(k in os.path.basename(f)
                                  for k in ("zh-Hans", "zh-CN", ".zh."))]
    en = [f for f in files if "en" in os.path.basename(f).lower()]
    return zh, en


def main():
    os.makedirs(WORK, exist_ok=True)
    meta = {"vid": VID, "zhTrack": False, "enTrack": False}

    files = ytdlp_extract()
    zh, en = classify(files)

    if zh:
        zh.sort(key=lambda f: -os.path.getsize(f))
        src = zh[0]
        subprocess.run(["cp", src, os.path.join(WORK, "zh.srt")], check=True)
        meta["zhTrack"] = True
        meta["zhBytes"] = os.path.getsize(src)
        log(f"T1 命中：zh 直取 {meta['zhBytes']}B -> zh.srt [{os.path.basename(src)}]")
    elif en:
        en.sort(key=lambda f: -os.path.getsize(f))
        src = en[0]
        subprocess.run(["cp", src, os.path.join(WORK, "en.srt")], check=True)
        meta["enTrack"] = True
        meta["enBytes"] = os.path.getsize(src)
        log(f"T2 源就绪：en {meta['enBytes']}B -> en.srt [{os.path.basename(src)}]（待翻译）")
    else:
        # T3: downsub 兜底（runner 通常不可达，仅快速探测 2 次）
        log("yt-dlp 无字幕轨，尝试 downsub 兜底（最多 2 次）...")
        ok = False
        for _ in range(2):
            rc = subprocess.run(["node", "scripts/downsub_prod.mjs"]).returncode
            if rc == 0 and (os.path.exists(os.path.join(WORK, "zh.srt")) or
                            os.path.exists(os.path.join(WORK, "en.srt"))):
                ok = True
                break
            time.sleep(8)
        if not ok:
            log("FATAL 无可用字幕轨（yt-dlp + downsub 均失败）")
            sys.exit(1)
        log("downsub 兜底成功")

    json.dump(meta, open(os.path.join(WORK, "subs_meta.json"), "w"),
              ensure_ascii=False, indent=1)
    log("DONE")


if __name__ == "__main__":
    main()
