#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生产：字幕获取（官方英文字幕优先，youtube-transcript-api）。

背景：原 get_subs.py 用 yt-dlp 直连 YouTube 取官方字幕，但在 GitHub
Actions runner（数据中心 IP）被 YouTube bot 墙死（带 cookie / player_client
轮换也无效），故改为 whisper_subs.py 本地转写。本脚本尝试用
youtube-transcript-api（走 timedtext 端点，与 yt-dlp 的 watch/innertube
端点不同）直拉官方 en 字幕，可能在 runner 上绕开 bot 墙。

契约（与 whisper_subs.py 完全一致，便于作为 drop-in 替身）：
  - 读环境变量 OUTDIR（默认 work）、VID
  - 成功 -> 写 OUTDIR/en.srt，退出码 0
  - 失败（被墙 / 视频无 en 字幕 / 网络异常）-> 退出码 1，
    由调用方回退到 whisper_subs.py 本地转录，流水线不中断。

SRT 格式与 whisper_subs.py 同构，下游 finalize.py / translate.py 无需改动。
"""
import os
import sys

WORK = os.environ.get("OUTDIR", "work")
VID = (os.environ.get("VID") or "").strip()
# 支持命令行覆盖：python fetch_official_subs.py <VID>
if len(sys.argv) > 1 and sys.argv[1].strip():
    VID = sys.argv[1].strip()

EN_SRT = os.path.join(WORK, "en.srt")


def log(*a):
    print("[official-subs]", *a, flush=True)


def fmt_ts(sec):
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms >= 1000:
        ms = 999
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def find_en(listing):
    """返回 (transcript, kind) 或 (None, reason)。kind 按 is_generated 判定，
    而非按调用方法——find_transcript 已能匹配 auto，必须以属性为准。"""
    try:
        t = listing.find_transcript(["en"])
    except Exception as e:
        reason = repr(e)[:160]
        # 列出可用语言，便于诊断
        try:
            avail = [f"{x.language_code}({'auto' if x.is_generated else 'manual'})"
                     for x in listing]
            log("可用字幕:", avail)
        except Exception:
            avail = []
        return None, f"无 en 字幕 | {reason} | avail={avail}"
    kind = "auto" if t.is_generated else "manual"
    return t, kind


def fetch():
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except Exception as e:  # noqa: BLE001
        log("FATAL 无法 import youtube_transcript_api（CI 需 pip install）:", e)
        return 1

    if not VID:
        log("FATAL 未提供 VID（env VID 或 argv[1]）")
        return 1

    api = YouTubeTranscriptApi()
    try:
        listing = api.list(VID)
    except Exception as e:  # noqa: BLE001
        log("FATAL list 失败（很可能 runner 被 bot 墙 / 视频不可用）:", repr(e)[:200])
        return 1

    transcript, kind = find_en(listing)
    if transcript is None:
        log("FATAL", kind)
        return 1

    try:
        snips = transcript.fetch()
    except Exception as e:  # noqa: BLE001
        log("FATAL fetch 失败:", repr(e)[:200])
        return 1

    n = 0
    with open(EN_SRT, "w", encoding="utf-8") as f:
        for i, s in enumerate(snips, 1):
            text = (getattr(s, "text", None) or "").strip()
            if not text:
                continue
            start = float(getattr(s, "start", 0) or 0)
            dur = float(getattr(s, "duration", 0) or 0)
            f.write(f"{i}\n{fmt_ts(start)} --> {fmt_ts(start + dur)}\n{text}\n\n")
            n += 1

    if n == 0:
        log("FATAL 官方字幕为空（0 段）")
        return 1

    size = os.path.getsize(EN_SRT)
    log(f"官方 en 字幕命中：{kind} | {n} 段 -> en.srt ({size // 1024}KB)")
    # 诊断：是否同时存在 zh 轨道（供未来 T1 免翻译路径参考，本脚本不写 zh.srt）
    try:
        zh = listing.find_transcript(["zh-Hans", "zh-CN", "zh"])
        log(f"提示：该视频存在 zh 轨道（{('auto' if zh.is_generated else 'manual')}），"
            f"未来可扩展为免翻译直接烧录")
    except Exception:
        pass
    return 0


def main():
    os.makedirs(WORK, exist_ok=True)
    sys.exit(fetch())


if __name__ == "__main__":
    main()
