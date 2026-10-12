#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批处理编排：new_videos.json -> 逐视频处理 -> 建 Release -> Server酱推送 -> 回写 state -> 清理旧 Release。

发布双通道（按可用性自动选择）：
  1. gh CLI      —— Actions 内使用，GH_TOKEN 由 GITHUB_TOKEN 自动注入
  2. REST API    —— 本机跑使用，token 依次取自 GH_TOKEN / GITHUB_TOKEN 环境变量、
                   或与本脚本同目录的 .gh_token.txt（内容为 GitHub PAT，仅需 public_repo 权限）
两者都不可用时降级为「仅本地产出」：成品复制到 out/ 目录，不上传、不推送。

支持环境变量：
  VIDEO_URL          直接处理指定视频（workflow_dispatch 用），设置后忽略监控结果
  SERVERCHAN_KEY     Server酱 SendKey
  GITHUB_REPOSITORY  默认 rickma11/yt-zh-subtitle
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

import notify
import process

ROOT = os.path.dirname(os.path.abspath(__file__))
WORK_ROOT = os.path.join(ROOT, "work")
STATE = os.path.join(ROOT, "state.json")
NEW = os.path.join(ROOT, "new_videos.json")
CONFIG = os.path.join(ROOT, "config.json")
TOKEN_FILE = os.path.join(ROOT, ".gh_token.txt")
OUT_DIR = os.path.join(ROOT, "out")


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


# ---------------------------------------------------------------- 发布通道

def local_token():
    """本机 token：环境变量优先，其次 .gh_token.txt。"""
    t = (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
    if t:
        return t
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:  # noqa: BLE001
        return ""


def gh_available():
    return bool(local_token()) and shutil.which("gh") is not None


def gh(args, **kw):
    env = dict(os.environ)
    # Actions 里 gh 认 GH_TOKEN；GITHUB_TOKEN 由 Actions 自动注入
    env["GH_TOKEN"] = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    return subprocess.run(["gh"] + args, capture_output=True, text=True, env=env, **kw)


def api(method, url, token, body=None, raw=None, timeout=120, retries=3):
    """GitHub REST 调用。body=dict 发 JSON；raw=bytes 发 octet-stream。返回 (status, parsed)。"""
    data = None
    ctype = "application/json"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    elif raw is not None:
        data = raw
        ctype = "application/octet-stream"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "yt-zh-subtitle",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if data is not None:
        headers["Content-Type"] = ctype
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            # GitHub 走直连，不经系统代理
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=timeout) as r:
                payload = r.read()
                try:
                    return r.status, json.loads(payload.decode("utf-8"))
                except Exception:  # noqa: BLE001
                    return r.status, payload
        except urllib.error.HTTPError as e:
            payload = e.read()
            try:
                return e.code, json.loads(payload.decode("utf-8"))
            except Exception:  # noqa: BLE001
                return e.code, payload
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"API 请求失败 {method} {url}: {last}")


def create_release(meta, repo, work):
    """创建 Release 并上传 mp4+srt。返回 release 页面 URL；无可用通道返回 None。"""
    if gh_available():
        return _create_release_gh(meta, repo, work)
    if local_token():
        return _create_release_api(meta, repo, work)
    log("    [release] 无 gh CLI 且无 token，跳过上传（本地保留产物）")
    return None


def _create_release_gh(meta, repo, work):
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


def _create_release_api(meta, repo, work):
    token = local_token()
    tag = f"v-{meta['video_id']}"
    title = f"{(meta.get('published') or '')[:10]} {meta.get('title', meta['video_id'])}"
    notes = notify.build_desp(meta, f"https://github.com/{repo}/releases/tag/{tag}")

    st, rel = api("POST", f"https://api.github.com/repos/{repo}/releases", token,
                  body={"tag_name": tag, "name": title, "body": notes,
                        "draft": False, "prerelease": False})
    if st == 422:  # 已存在 -> 复用
        st, rel = api("GET", f"https://api.github.com/repos/{repo}/releases/tags/{tag}", token)
        if st != 200:
            raise RuntimeError(f"Release 已存在但获取失败: HTTP {st}")
    elif st != 201:
        raise RuntimeError(f"Release 创建失败: HTTP {st} {str(rel)[:200]}")

    rel_id = rel["id"]
    for name in (meta["mp4"], meta["srt"]):
        path = os.path.join(work, name)
        # 先删同名资产（重跑场景）
        st, assets = api("GET",
                         f"https://api.github.com/repos/{repo}/releases/{rel_id}/assets?per_page=100",
                         token)
        for a in (assets if isinstance(assets, list) else []):
            if a.get("name") == name:
                api("DELETE",
                    f"https://api.github.com/repos/{repo}/releases/{rel_id}/assets/{a['id']}",
                    token)
        with open(path, "rb") as f:
            blob = f.read()
        log(f"    上传资产 {name} ({len(blob) / 1048576:.1f} MB) ...")
        st, _ = api("POST",
                    f"https://uploads.github.com/repos/{repo}/releases/{rel_id}/assets?name={name}",
                    token, raw=blob, timeout=900)
        if st != 201:
            raise RuntimeError(f"资产上传失败 {name}: HTTP {st}")
    return f"https://github.com/{repo}/releases/tag/{tag}"


def prune_releases(repo, keep):
    if keep <= 0:
        return
    if gh_available():
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
        return
    token = local_token()
    if not token:
        return
    st, rels = api("GET", f"https://api.github.com/repos/{repo}/releases?per_page=100", token)
    if st != 200 or not isinstance(rels, list):
        log("    prune: 列出 release 失败", str(rels)[:150])
        return
    for rel in rels[keep:]:
        log(f"    prune: 删除旧 release {rel.get('tag_name')}")
        api("DELETE", f"https://api.github.com/repos/{repo}/releases/{rel['id']}", token)


def keep_local_output(work, meta):
    """无发布通道时把成品复制到 out/ 保留。"""
    os.makedirs(OUT_DIR, exist_ok=True)
    for name in (meta["mp4"], meta["srt"], "meta.json"):
        src = os.path.join(work, name)
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(OUT_DIR, name))
    log(f"    产物已保留到 {OUT_DIR}")


# ---------------------------------------------------------------- 主流程

def main():
    cfg = load_json(CONFIG, {})
    repo = os.environ.get("GITHUB_REPOSITORY", "rickma11/yt-zh-subtitle")
    state = load_json(STATE, {"channels": {}, "processed": {}, "resolved_ids": {}})
    processed = state.setdefault("processed", {})
    channels_state = state.setdefault("channels", {})

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
            release_url = create_release(meta, repo, work)
            if release_url:
                meta["release_url"] = release_url
                log(f"    Release: {release_url}")
                desp = notify.build_desp(meta, release_url)
                notify.serverchan(f"【YT中字】{(meta.get('title') or vid)[:40]}", desp)
                shutil.rmtree(work, ignore_errors=True)  # 省 runner 磁盘
            else:
                keep_local_output(work, meta)
            processed[vid] = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "release": release_url,
                "tier": meta.get("subtitle_tier"),
            }
            # channels_state 标记"该频道已初始化"：只在处理成功后写，
            # 失败下轮仍受 first_run 保护（只取最新 1 期），不会重跑整个 backlog。
            cid = v.get("channel_id")
            if cid:
                channels_state[cid] = {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "last_video": vid,
                }
            ok_any = True
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
    sys.exit(main())
