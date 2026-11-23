#!/usr/bin/env python3
"""推送幂等锁：查询远端 main 的 state.json，判断某 VID 是否已被【别的 run】标记处理。

退出码：
  0 —— 已处理（应跳过推送）
  1 —— 未处理（可以推送）
  2 —— 查询失败/参数缺失（放行，避免误伤正常推送；shell 里按“未处理”处理）

背景（2026-11-23 重复推送事故）：
  两道防线防御同一类 bug。第一道（refresh_state.py）让排队 run 选片时看到最新 state；
  本脚本是第二道：即使选片层失效、同一视频被重复处理并发布 Release，推送前再查一次
  远端 state，若发现 VID 已 processed（说明另一个 run 已处理、已推送过），就跳过本次
  推送，从用户视角杜绝“两条一模一样的内容”。
"""
import base64
import json
import os
import sys
import urllib.request

REPO = os.environ.get("GITHUB_REPOSITORY", "")
TOKEN = os.environ.get("GH_TOKEN", "")
VID = os.environ.get("VID", "")


def main():
    if not REPO or not VID:
        return 2
    url = f"https://api.github.com/repos/{REPO}/contents/state.json?ref=main"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "yt-zh-subtitle",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
        state = json.loads(base64.b64decode(data["content"]).decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"[already_processed] 查询失败，按未处理放行: {e}", file=sys.stderr)
        return 2
    processed = state.get("processed", state)
    if isinstance(processed, dict) and VID in processed:
        print(f"[already_processed] {VID} 已被处理，跳过推送（幂等锁）")
        return 0
    print(f"[already_processed] {VID} 未处理，允许推送")
    return 1


if __name__ == "__main__":
    sys.exit(main())
