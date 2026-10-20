#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生产：字幕获取（T3 —— faster-whisper 本地转写）。

背景：Actions runner 是数据中心 IP，yt-dlp 直连 YouTube 取字幕会被
"Sign in to confirm you're not a bot" 墙死（IP 层，带 cookie 也没用）；
downsub.com 在 runner 返回 HTTP 0（被墙）。因此字幕统一走 Whisper 对
「Step1 已下载的视频」做本地转写，完全不依赖 YouTube。

产物 work/en.srt 交给 finalize.py 经 Google gtx 翻译为中文并硬烧录。
"""
import os
import sys
import subprocess

WORK = os.environ.get("OUTDIR", "work")
VID = os.environ.get("VID") or ""
MODEL = os.environ.get("WHISPER_MODEL", "medium").strip() or "medium"

AUDIO = os.path.join(WORK, "audio.wav")
EN_SRT = os.path.join(WORK, "en.srt")

# EAFC/足球领域种子词库（whisper_prompt.txt，与脚本同目录），用作 initial_prompt
# 引导解码偏向领域词，减少 small/medium 对球员名、模式词的误听。
PROMPT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "whisper_prompt.txt")

# 减小噪声日志 + 避免 tokenizers 并行告警
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def log(*a):
    print("[whisper]", *a, flush=True)


def load_seed_prompt():
    try:
        with open(PROMPT_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:  # noqa: BLE001  缺词库不应阻断转写
        return ""


def extract_audio():
    video = os.path.join(WORK, "video.mp4")
    if not os.path.exists(video):
        log("FATAL 无 video.mp4（Step1 未产出）")
        sys.exit(1)
    cmd = ["ffmpeg", "-y", "-i", video, "-vn", "-ac", "1", "-ar", "16000",
           "-f", "wav", AUDIO]
    log("提取音频:", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if r.returncode != 0 or not os.path.exists(AUDIO):
        log("FATAL ffmpeg 提取音频失败:")
        log(r.stderr[-1500:])
        sys.exit(1)
    log("音频就绪:", os.path.getsize(AUDIO) // 1048576, "MB")


def fmt_ts(sec):
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms >= 1000:
        ms = 999
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def transcribe():
    try:
        from faster_whisper import WhisperModel
    except Exception as e:  # noqa: BLE001
        log("FATAL 无法 import faster_whisper:", e)
        sys.exit(1)
    log(f"加载模型 {MODEL} (cpu/int8) ...")
    model = WhisperModel(MODEL, device="cpu", compute_type="int8")
    seed = load_seed_prompt()
    log(f"转写中（CPU 可能需数分钟）... 模型={MODEL} 种子词库={len(seed)}字符")
    segments, info = model.transcribe(
        AUDIO, language="en", task="transcribe",
        vad_filter=True, beam_size=5,
        initial_prompt=seed or None,
    )
    n = 0
    with open(EN_SRT, "w", encoding="utf-8") as f:
        for seg in segments:
            text = (seg.text or "").strip()
            if not text:
                continue
            n += 1
            f.write(f"{n}\n{fmt_ts(seg.start)} --> {fmt_ts(seg.end)}\n{text}\n\n")
    if n == 0:
        log("FATAL Whisper 未产出任何字幕段")
        sys.exit(1)
    log(f"T3 转写完成：{n} 段 -> en.srt ({os.path.getsize(EN_SRT)//1024}KB)")


def main():
    os.makedirs(WORK, exist_ok=True)
    extract_audio()
    transcribe()
    log("DONE")


if __name__ == "__main__":
    main()
