# 生产：字幕定稿 + 烧录
# 若 work/zh.srt 不存在（T1 未命中）：读 work/en.srt -> Google 翻译 -> FC27 润色 -> 生成 zh.srt
# 然后 ffmpeg 硬字幕烧录，产出两个版本：
#   work/output.mp4         完整版（原始分辨率，音轨 copy，体积大，走 GitHub Release 桌面下载）
#   work/output_1080p.mp4   手机精简版（保持 1080p，2-pass 目标码率压到 ~300MB，便于移动端/网盘快速下载）
import os, sys, re, html, json, subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from translate import translate_many
from polish import polish

WORK = os.environ.get("OUTDIR", "work")


def parse_srt(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        content = f.read()
    blocks = re.split(r"\n\s*\n", content.strip())
    segs = []
    for b in blocks:
        lines = [l for l in b.strip().splitlines() if l.strip()]
        if len(lines) >= 3:
            segs.append({"idx": lines[0], "ts": lines[1], "text": "\n".join(lines[2:])})
        elif len(lines) == 2 and "-->" in lines[0]:
            segs.append({"idx": str(len(segs) + 1), "ts": lines[0], "text": lines[1]})
    return segs


def write_srt(segs, path):
    with open(path, "w", encoding="utf-8") as f:
        for i, s in enumerate(segs, 1):
            f.write(f"{i}\n{s['ts']}\n{s['text']}\n\n")


def burn(video, srt_abs, out_path, scale=None, fontsize=15, crf=20, audio="copy"):
    vf = (f"subtitles='{srt_abs}':force_style='FontSize={fontsize},"
          "FontName=Noto Sans CJK SC,"
          "PrimaryColour=&H00FFFFFF&,OutlineColour=&H00000000&,"
          "BackColour=&H80000000&,Outline=2,Shadow=1,Bold=1,MarginV=26'")
    if scale:
        vf = f"scale={scale}:flags=lanczos,{vf}"
    cmd = f'ffmpeg -y -i "{video}" -vf "{vf}" -c:v libx264 -preset medium -crf {crf}'
    if audio == "copy":
        cmd += " -c:a copy"
    else:
        cmd += f" -c:a aac -b:a {audio} -ar 44100"
    cmd += f' "{out_path}"'
    r = os.system(cmd)
    return r == 0 and os.path.exists(out_path)


def probe(video):
    """返回 (duration_seconds, height)"""
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=height", "-of", "json", video])
        d = json.loads(out)
        dur = float(d.get("format", {}).get("duration", 0) or 0)
        h = 0
        for s in d.get("streams", []):
            if s.get("codec_type") == "video" and s.get("height"):
                h = max(h, int(s["height"]))
        return dur, h
    except Exception as e:
        print(f"WARN probe 失败: {e}")
        return 0.0, 0


def burn_target(video, srt_abs, out_path, target_mb=300, fontsize=15, audio_kbps=128):
    """保持 1080p（源更高则降到 1080），用 2-pass 目标码率把总体积压到 ~target_mb MB。
    按视频时长动态算码率：10~15 分钟都贴近 300MB 预算。"""
    dur, h = probe(video)
    if dur <= 0:
        print("WARN 无法探测时长，回退单 pass CRF 编码")
        return burn(video, srt_abs, out_path, scale=None, fontsize=fontsize,
                    crf=24, audio=f"{audio_kbps}k")
    target_bytes = target_mb * 1024 * 1024
    audio_bytes = (audio_kbps * 1000 / 8) * dur
    # 预留 2% 容器/muxing 开销
    video_bytes = target_bytes - audio_bytes - int(target_bytes * 0.02)
    if video_bytes < target_bytes * 0.5:
        video_bytes = int(target_bytes * 0.9)
    video_kbps = max(300, int(video_bytes * 8 / dur / 1000))
    scale = "scale=-2:1080:flags=lanczos," if h > 1080 else ""
    vf = (f"{scale}subtitles='{srt_abs}':force_style='FontSize={fontsize},"
          "FontName=Noto Sans CJK SC,"
          "PrimaryColour=&H00FFFFFF&,OutlineColour=&H00000000&,"
          "BackColour=&H80000000&,Outline=2,Shadow=1,Bold=1,MarginV=26'")
    plog = "/tmp/passlog"
    vmax = int(video_kbps * 1.5)
    vbuf = video_kbps * 2
    cmd1 = (f'ffmpeg -y -i "{video}" -vf "{vf}" -c:v libx264 -preset medium '
            f'-b:v {video_kbps}k -maxrate {vmax}k -bufsize {vbuf}k '
            f'-pass 1 -passlogfile {plog} -f null /dev/null')
    cmd2 = (f'ffmpeg -y -i "{video}" -vf "{vf}" -c:v libx264 -preset medium '
            f'-b:v {video_kbps}k -maxrate {vmax}k -bufsize {vbuf}k '
            f'-pass 2 -passlogfile {plog} '
            f'-c:a aac -b:a {audio_kbps}k -ar 44100 "{out_path}"')
    print(f"[finalize] 2-pass 目标: {target_mb}MB / {dur:.0f}s -> 视频码率 ~{video_kbps}kbps")
    r1 = os.system(cmd1)
    if r1 != 0:
        print("WARN pass1 失败，回退单 pass CRF")
        return burn(video, srt_abs, out_path,
                    scale=(None if h <= 1080 else "-2:1080"),
                    fontsize=fontsize, crf=24, audio=f"{audio_kbps}k")
    r2 = os.system(cmd2)
    return r2 == 0 and os.path.exists(out_path)


def main():
    zh_path = os.path.join(WORK, "zh.srt")
    en_path = os.path.join(WORK, "en.srt")

    if not os.path.exists(zh_path):
        if not os.path.exists(en_path):
            print("FATAL 无 zh.srt 也无 en.srt")
            sys.exit(1)
        print("[finalize] T2：翻译 en.srt -> zh.srt ...")
        segs = parse_srt(en_path)
        print(f"  共 {len(segs)} 条")
        texts = [html.unescape(s["text"]).replace("\n", " ") for s in segs]
        done = translate_many(texts, log_every=25)
        polished = [polish(d) for d in done]
        for s, p in zip(segs, polished):
            s["text"] = p
        write_srt(segs, zh_path)
        print("[finalize] zh.srt 生成完毕")
    else:
        print("[finalize] T1：已存在 zh.srt，跳过翻译")

    video = os.path.join(WORK, "video.mp4")
    if not os.path.exists(video):
        print("FATAL 无 video.mp4")
        sys.exit(1)
    srt_abs = os.path.abspath(zh_path).replace("\\", "/").replace(":", "\\:")

    # 完整版
    print("[finalize] ffmpeg 烧录（完整版）...")
    ok1 = burn(video, srt_abs, os.path.join(WORK, "output.mp4"),
               scale=None, fontsize=15, crf=20, audio="copy")
    if not ok1:
        print("FATAL ffmpeg 完整版失败")
        sys.exit(1)
    full_mb = os.path.getsize(os.path.join(WORK, "output.mp4")) / 1048576
    print(f"[finalize] DONE 完整版 output.mp4 {full_mb:.1f}MB")

    # 1080p 手机精简版（保持 1080p，2-pass 压到 ~300MB，移动端/网盘快速下载）
    print("[finalize] ffmpeg 烧录（1080p 精简版，目标 ~300MB）...")
    ok2 = burn_target(video, srt_abs, os.path.join(WORK, "output_1080p.mp4"),
                      target_mb=300, fontsize=15, audio_kbps=128)
    if not ok2:
        print("WARN 1080p 精简版烧录失败，仅保留完整版")
    else:
        mb = os.path.getsize(os.path.join(WORK, "output_1080p.mp4")) / 1048576
        print(f"[finalize] DONE 1080p 精简版 output_1080p.mp4 {mb:.1f}MB")


if __name__ == "__main__":
    main()
