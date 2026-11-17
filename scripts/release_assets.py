#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 GitHub Release API 读回真实资源名与直链，输出可直接 eval 的 shell 变量。

背景（2026-11-17 事故）：推送里的 1080p 直链打不开（404）。
根因不是 URL 编码问题，而是「本地算出来的文件名」与「GitHub 实际存储的资源名」不一致：
GitHub 会规范化 release asset 名里的部分字符（实测 '!' -> '.'、'&' -> '.'，12 个真实资源
样本一致），而 workflow 用本地名拼 URL，拼出来的名字带 '!'，资源实际存成了 '.'，于是 404。

本脚本以 API 返回的 browser_download_url 为准，从根上消除「拼出来的名字 ≠ 实际资源名」
这一类问题——以后 GitHub 再改规范化规则也不会错。

用法（在 workflow 里）：
    eval "$(python3 scripts/release_assets.py --repo owner/name --tag auto-YYYYMMDD-xxx)"

输出（可 eval）：
    REAL=...        真实 1080p 资源名
    REAL_URL=...    真实 1080p 直链
    FULL_URL=...    真实完整版直链

鉴权：读环境变量 GITHUB_TOKEN / GH_TOKEN。取不到时输出空值（调用方退回旧拼接逻辑）。
"""
import argparse
import json
import os
import shlex
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"


def _token():
    for k in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_API_TOKEN"):
        v = os.environ.get(k, "").strip()
        if v:
            return v
    return ""


def fetch_release(repo, tag, timeout=30):
    """取 release 及其 assets；任何异常都返回 ('', [])（调用方有兜底）。"""
    url = f"{API}/repos/{repo}/releases/tags/{tag}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "yt-zh-subtitle",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
        return d.get("name", "") or "", (d.get("assets", []) or [])
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as e:
        print(f"[release_assets] 读不到 release 资源（{e}），返回空", file=sys.stderr)
        return "", []


def pick(assets):
    """挑出 1080p 精简版与完整版的真实名/直链。"""
    def nm(a):
        return a.get("name", "") or ""
    compact = [a for a in assets if nm(a).endswith("_1080p.mp4")]
    full = [a for a in assets if nm(a).endswith(".mp4") and not nm(a).endswith("_1080p.mp4")]
    real = nm(compact[0]) if compact else ""
    real_url = (compact[0].get("browser_download_url", "") if compact else "") or ""
    full_url = (full[0].get("browser_download_url", "") if full else "") or ""
    return real, real_url, full_url


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--tag", required=True, help="release tag")
    args = ap.parse_args()

    title, assets = fetch_release(args.repo, args.tag)
    real, real_url, full_url = pick(assets)
    print(f"TITLE={shlex.quote(title)}")
    print(f"REAL={shlex.quote(real)}")
    print(f"REAL_URL={shlex.quote(real_url)}")
    print(f"FULL_URL={shlex.quote(full_url)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
