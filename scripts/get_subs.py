#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生产：字幕获取（yt-dlp 直连 YouTube + YT_COOKIES 绕过 bot 挑战）。

YouTube 对数据中心 IP（GitHub Actions runner）会做 "Sign in to confirm you're not a bot"
挑战，直连必失败。两种绕过方式（process.py 已验证）：
  1) YT_COOKIES secret（优先）：把 cookie 文件内容写成 work/cookies.txt 传给 --cookies；
  2) player_client 轮换：tv / web / ios 等客户端对同一挑战的响应不同，逐一尝试。

字幕分层：
  T1: yt-dlp 直取 zh-Hans/zh-CN -> work/zh.srt（免翻译）
  T2: yt-dlp 取 en/en_auto -> work/en.srt（交 finalize.py 经 Google gtx 翻译）

注意：downsub 在 runner（Azure）返回 HTTP 0（被墙），故不再依赖它，统一走 yt-dlp。
"""
import os
import sys
import glob
import time
import json
import subprocess

WORK = os.environ.get("OUTDIR", "work")
VID = os.environ.get("VID") or "KUf0x6FG7p0"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# 代理处理：剔除沙箱注入的失效代理（runner 上无代理时为空操作），仅保留显式 YT_PROXY
_PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                   "http_proxy", "https_proxy", "all_proxy")
YT_PROXY = os.environ.get("YT_PROXY", "").strip()


def _clean_env():
    env = {k: v for k, v in os.environ.items() if k not in _PROXY_ENV_KEYS}
    if YT_PROXY:
        env["HTTP_PROXY"] = YT_PROXY
        env["HTTPS_PROXY"] = YT_PROXY
    return env


# player_client 轮换链：default 在数据中心 IP 常被 bot 墙，逐个试
CLIENT_FALLBACKS = ["default", "tv", "web", "web_safari", "mweb", "android", "tv_embedded"]


def cookies_path():
    """YT_COOKIES 环境变量（secret）优先，其次仓库里的 cookies.txt。"""
    env = os.environ.get("YT_COOKIES")
    if env:
        p = os.path.join(WORK, "cookies.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(env)
        log("使用 YT_COOKIES secret")
        return p
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
    if os.path.exists(p):
        log("使用仓库内 cookies.txt")
        return p
    return None


def log(*a):
    print("[subs]", *a, flush=True)


def ytdlp_extract():
    url = f"https://www.youtube.com/watch?v={VID}"
    cookies = cookies_path()
    out = os.path.join(WORK, "yt_sub")
    for client in CLIENT_FALLBACKS:
        ea = [] if client == "default" else \
            ["--extractor-args", f"youtube:player_client={client}"]
        cmd = [sys.executable, "-m", "yt_dlp", "--skip-download",
               "--write-subs", "--write-auto-subs",
               "--sub-langs", "zh-Hans,zh-CN,en",
               "--convert-subs", "srt", "--no-warnings", "--no-progress",
               "--user-agent", UA, "-P", WORK]
        if cookies:
            cmd += ["--cookies", cookies]
        cmd += ea + [url]
        for attempt in range(2):
            log(f"yt-dlp[{client}] attempt {attempt + 1}/2 ...")
            r = subprocess.run(cmd, capture_output=True, text=True,
                               errors="replace", env=_clean_env())
            for l in (r.stderr or "").splitlines():
                if l.strip():
                    log("   !", l[:200])
            files = sorted(glob.glob(os.path.join(WORK, "yt_sub*.srt")))
            if files:
                return files
            if attempt == 0:
                time.sleep(3)
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
        log("FATAL 无可用字幕轨（yt-dlp + cookies 均失败）")
        sys.exit(1)

    json.dump(meta, open(os.path.join(WORK, "subs_meta.json"), "w"),
              ensure_ascii=False, indent=1)
    log("DONE")


if __name__ == "__main__":
    main()
