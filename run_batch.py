#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批处理编排：new_videos.json -> 逐视频处理 -> 建 Release -> Server酱推送 -> 回写 state -> 清理旧 Release。

支持环境变量：
  VIDEO_URL        直接处理指定视频（workflow_dispatch 用），设置后忽略监控结果
  SERVERCHAN_KEY   Server酱 SendKey
  GITHUB_REPOSITORY 由 Actions 自动注入，用于拼 Release 链接
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

import notify
import process

ROOT = os.path.dirname(os.path.abspath(__file__))
WORK_ROOT = os.path.join(ROOT, "work")
STATE = os.path.join(ROOT, "state.json")
NEW = os.path.join(ROOT, "new_videos.json")
CONFIG = os.path.join(ROOT, "config.json")


def log(*a):
    print(*a, flush=True)


def load_json(p, d):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return d


def extract_vid(url):
    m = re.search(r"(?:v=|youtu\.be/|shorts/)([\w-]{11})", url)
    return m.group(1) if m else None


def gh(args, **kw):
    return subprocess.run(["gh"] + args, capture_output=True, text=True, **kw)


def create_release(meta, repo, work):
    tag = f"v-{meta['video_id']}"
    mp4 = os.path.join(work, meta["mp4"])
    srt = os.path.join(work, meta["srt"])
    title = f"{(meta.get('published') or '')[:10]} {meta.get('title', meta['video_id'])}"
    notes = notify.build_desp(meta, f"https://github.com/{repo}/releases/tag/{tag}")
    r = gh(["release", "create", tag, mp4, srt, "--title", title, "--notes", notes])
    if r.returncode != 0:
        log("    release create 失败，尝试 upload --clobber:", r.stderr.strip()[:200])
        r2 = gh(["release", "upload", tag, mp4, srt, "--clobber"])
        if r2.returncode != 0:
            raise RuntimeError(f"Release 创建/上传失败: {r2.stderr.strip()[:300]}")
    return f"https://github.com/{repo}/releases/tag/{tag}"


def prune_releases(repo, keep):
    if keep <= 0:
        return
    r = gh(["release", "list", "--limit", "100", "--json", "tagName"])
    if r.returncode != 0:
        log("    prune: 列出 release 失败", r.stderr.strip()[:150])
        return
    try:
        tags = [x["tagName"] for x in json.loads(r.stdout or "[]")]
    except Exception:  # noqa: BLE001
        return
    for t in tags[keep:]:
        log(f"    prune: 删除旧 release {t}")
        gh(["release", "delete", t, "--cleanup-tag", "-y"])


def main():
    cfg = load_json(CONFIG, {})
    repo = os.environ.get("GITHUB_REPOSITORY", "rickma11/yt-zh-subtitle")
    state = load_json(STATE, {"channels": {}, "processed": {}, "resolved_ids": {}})
    processed = state.setdefault("processed", {})

    url_env = os.environ.get("VIDEO_URL", "").strip()
    if url_env:
        vid = extract_vid(url_env)
        videos = [{"video_id": vid, "title": "", "published": "", "channel": ""}] if vid else []
        log(f"[batch] 指定视频模式: {url_env}")
    else:
        videos = load_json(NEW, [])
        log(f"[batch] 监控模式: {len(videos)} 个待处理")

    if not videos:
        log("[batch] 无视频需要处理，退出")
        _out(False)
        return

    ok_any = False
    for v in videos:
        vid = v["video_id"]
        if vid in processed:
            log(f"[batch] {vid} 已处理过，跳过")
            continue
        try:
            meta = process.process_video(v, WORK_ROOT, cfg)
            work = os.path.join(WORK_ROOT, vid)
            meta["release_url"] = create_release(meta, repo, work)
            log(f"    Release: {meta['release_url']}")
            desp = notify.build_desp(meta, meta["release_url"])
            notify.serverchan(f"【YT中字】{(meta.get('title') or vid)[:40]}", desp)
            processed[vid] = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "release": meta["release_url"],
                "tier": meta.get("subtitle_tier"),
            }
            ok_any = True
            shutil.rmtree(work, ignore_errors=True)  # 省 runner 磁盘
        except Exception as e:  # noqa: BLE001
            log(f"[batch] {vid} 处理失败（不标记 processed，下轮重试）: {e}")

    prune_releases(repo, int(cfg.get("keep_releases", 5)))

    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    log(f"[batch] 完成, 成功={ok_any}")
    _out(ok_any)


def _out(published):
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write("published=%s\n" % ("true" if published else "false"))


if __name__ == "__main__":
    main()
