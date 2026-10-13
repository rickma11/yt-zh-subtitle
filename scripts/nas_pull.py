#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nas_pull.py —— 把 yt-zh-subtitle 处理好的视频主动拉取到本地 NAS / 电脑目录

设计：云端 pipeline 跑在 GitHub Actions，NAS 在内网、无公网 IP，无法被云端主动推送。
      因此采用「拉取」架构：本脚本定时运行，从 GitHub Release 拉取最新成品视频进 NAS 共享目录。

运行环境：
  - 群晖 DSM：控制面板 → 任务计划 → 新增 → 计划的任务 → 用户定义的脚本，
             命令行填：/usr/local/bin/python3 /volume1/scripts/nas_pull.py
             计划：每天 / 每小时（按你希望的频率）
  - Windows 本机：任务计划程序 → 运行 python nas_pull.py；OUTPUT_DIR 用 UNC 路径 \\NAS\共享\目录
             （或在「映射网络驱动器」里挂成 Z: 后填 Z:\yt-zh-subtitle）

可配置（环境变量）：
  NAS_OUTPUT_DIR   目标目录（必填）。DSM 例：/volume1/video/yt-zh-subtitle
                                  Win 例：\\\\NAS\\video\\yt-zh-subtitle  或  Z:\\yt-zh-subtitle
  PULL_QUALITY     compact=只拉 1080p 精简版(~300MB，默认) | full=只拉完整版(~564MB) | both=都拉
  HTTPS_PROXY/HTTP_PROXY  若 NAS/电脑需走代理访问 GitHub，可设；留空则直连
  GITHUB_TOKEN     可选，公开仓库 Release 下载无需 token；设了可提 API 速率上限

去重：脚本在 OUTPUT_DIR 写 .nas_pull_state.json 记录已拉取的 tag，重跑不会重复下载。
"""
import os
import sys
import json
import time
import urllib.request
import urllib.error

REPO = "rickma11/yt-zh-subtitle"
OUTPUT_DIR = os.environ.get("NAS_OUTPUT_DIR", "")
QUALITY = (os.environ.get("PULL_QUALITY") or "compact").lower()
PROXY = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
TOKEN = os.environ.get("GITHUB_TOKEN", "")

API = f"https://api.github.com/repos/{REPO}/releases?per_page=30"
STATE_FILE = os.path.join(OUTPUT_DIR, ".nas_pull_state.json")
UA = {"User-Agent": "nas-pull/1.0", "Accept": "application/vnd.github+json"}


def setup_proxy():
    if PROXY:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
        )
        urllib.request.install_opener(opener)
        print(f"[proxy] 使用代理 {PROXY}")


def http_json(url):
    req = urllib.request.Request(url, headers=UA)
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def download(url, path):
    req = urllib.request.Request(url, headers={"User-Agent": "nas-pull"})
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    total = 0
    with urllib.request.urlopen(req, timeout=1800) as r, open(path, "wb") as f:
        while True:
            buf = r.read(1024 * 1024)
            if not buf:
                break
            f.write(buf)
            total += len(buf)
    return total


def pick_assets(assets):
    """按 QUALITY 选要下载的 mp4 + 字幕 srt"""
    compact = next((a for a in assets if "1080p" in a["name"] and a["name"].endswith(".mp4")), None)
    full = next((a for a in assets if a["name"].endswith(".mp4")), None)
    srt = next((a for a in assets if a["name"].endswith(".srt")), None)
    want = []
    if QUALITY == "full":
        if full:
            want.append(full)
    elif QUALITY == "both":
        if compact:
            want.append(compact)
        if full and full["name"] != (compact and compact["name"]):
            want.append(full)
    else:  # compact (默认)
        if compact:
            want.append(compact)
        elif full:  # 没有精简版时回退完整版
            want.append(full)
    if srt:
        want.append(srt)
    return want


def main():
    if not OUTPUT_DIR:
        print("ERROR: 请设置环境变量 NAS_OUTPUT_DIR 指向 NAS/本地目标目录")
        sys.exit(2)
    setup_proxy()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    state = {}
    if os.path.exists(STATE_FILE):
        try:
            state = json.load(open(STATE_FILE, encoding="utf-8"))
        except Exception:
            state = {}

    print(f"[pull] 目标目录: {OUTPUT_DIR} | 质量: {QUALITY}")
    try:
        releases = http_json(API)
    except Exception as e:
        print(f"ERROR: 获取 Release 列表失败: {e}")
        sys.exit(3)

    pulled = 0
    for rel in releases:
        tag = rel.get("tag_name", "")
        if tag in state:
            continue
        assets = rel.get("assets", [])
        if not assets:
            continue
        targets = pick_assets(assets)
        if not targets:
            continue
        for a in targets:
            name = a["name"]
            dest = os.path.join(OUTPUT_DIR, name)
            if os.path.exists(dest):
                print(f"  [skip] 已存在 {name}")
                continue
            print(f"  [download] {tag} -> {name} ({a.get('size', 0)//1024//1024} MB)")
            try:
                sz = download(a["browser_download_url"], dest)
                print(f"  [ok] {name} 完成 {sz//1024//1024} MB")
            except Exception as e:
                print(f"  [fail] {name}: {e}")
                if os.path.exists(dest):
                    os.remove(dest)
                continue
        state[tag] = {"time": int(time.time()), "quality": QUALITY}
        json.dump(state, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        pulled += 1

    print(f"[done] 本次新增拉取 {pulled} 个 Release；已记录共 {len(state)} 个")


if __name__ == "__main__":
    main()
