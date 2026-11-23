#!/usr/bin/env python3
"""选片前刷新维护表（state.json / video_log.json）到远端 main 最新版本。

背景（2026-11-23 重复推送事故）：
  cron-job.org 每小时触发一次 workflow_dispatch，但单次处理耗时约 72 分钟 > 60 分钟间隔。
  GitHub 的 workflow_dispatch 在【触发时刻】快照 head_sha：Run B(21:00) 触发时，
  Run A(20:00) 还没把 state 提交到 main，于是 Run B 锁定了旧 SHA。concurrency 排队
  让 Run B 等 Run A 跑完再执行，但 checkout 时用的仍是【旧 SHA】——state.json 里
  没有刚被标记的视频，monitor 于是又选中同一个视频，重复处理 + 重复推送
  （用户收到两条一模一样的内容，就是这么来的）。

修复：选片前先从 GitHub Contents API 读取 main 上最新的 state.json / video_log.json
  覆盖本地 checkout 的旧版本，让 monitor 看到最新的处理标记。
  任何失败都优雅降级（沿用 checkout 版本），绝不阻塞流程。
"""
import base64
import json
import os
import sys
import urllib.request

REPO = os.environ.get("GITHUB_REPOSITORY", "")
TOKEN = os.environ.get("GH_TOKEN", "")
FILES = ("state.json", "video_log.json")


def fetch_latest(path, timeout=20):
    url = f"https://api.github.com/repos/{REPO}/contents/{path}?ref=main"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "yt-zh-subtitle",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    return base64.b64decode(data["content"])


def main():
    if not REPO:
        print("[refresh_state] 无 GITHUB_REPOSITORY，跳过（沿用 checkout 版本）")
        return 0
    refreshed = 0
    for fn in FILES:
        try:
            raw = fetch_latest(fn)
            json.loads(raw.decode("utf-8"))  # 合法性校验，避免写入损坏文件
            with open(fn, "wb") as f:
                f.write(raw)
            refreshed += 1
            print(f"[refresh_state] {fn} 已从 main 刷新 ({len(raw)} bytes)")
        except Exception as e:  # noqa: BLE001 —— 任何异常都降级，不阻塞选片
            print(f"[refresh_state] {fn} 刷新失败，沿用 checkout 版本: {e}", file=sys.stderr)
    print(f"[refresh_state] 完成，刷新 {refreshed}/{len(FILES)} 个文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
