# 生产：字幕定稿 + 烧录
# 若 work/zh.srt 不存在（T1 未命中）：读 work/en.srt -> Google 翻译 -> FC27 润色 -> 生成 zh.srt
# 然后 ffmpeg 硬字幕烧录，产出两个版本：
#   work/output.mp4         完整版（原始分辨率，音轨 copy，体积大，走 GitHub Release 桌面下载）
#   work/output_1080p.mp4   手机精简版（保持 1080p，单 pass 目标码率压到 ~300MB，便于移动端/网盘快速下载）
import os, sys, re, html, json, subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from translate import translate_many
from polish import polish

WORK = os.environ.get("OUTDIR", "work")
# x264 编码速度档：medium(慢/质好) -> veryfast(快 ~2x/字幕视频画质损失极小)。
# 全程 veryfast；精简版改为单 pass ABR（见 burn_target），兼顾速度与 ~300MB 预算。可用 env X264_PRESET 覆盖。
PRESET = os.environ.get("X264_PRESET") or "veryfast"


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
    cmd = f'ffmpeg -y -i "{video}" -vf "{vf}" -c:v libx264 -preset {PRESET} -crf {crf}'
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
    """保持 1080p（源更高则降到 1080），单 pass 目标码率把总体积压到 ~target_mb MB。

    为什么不用 2-pass：2-pass 仅为精确命中体积预算，但要多编码一遍。在 2 核 runner 上，
    长视频（如 45min 开包视频）会因「完整版1遍 + 1080p 2遍 = 3 遍重编码」逼近 job 120min
    超时（实测 run #27 卡死在 Step3 73min、撞超时）。单 pass 速度约快一倍，体积略超预算
    （手机/网盘仍可接受），质量足够。"""
    dur, h = probe(video)
    scale = "scale=-2:1080:flags=lanczos," if h > 1080 else ""
    vf = (f"{scale}subtitles='{srt_abs}':force_style='FontSize={fontsize},"
          "FontName=Noto Sans CJK SC,"
          "PrimaryColour=&H00FFFFFF&,OutlineColour=&H00000000&,"
          "BackColour=&H80000000&,Outline=2,Shadow=1,Bold=1,MarginV=26'")
    if dur > 0:
        # 单 pass ABR（非 2-pass）：按预算算 -b:v，加 maxrate/bufsize 约束波动，只编码一遍
        target_bytes = target_mb * 1024 * 1024
        audio_bytes = (audio_kbps * 1000 / 8) * dur
        video_bytes = target_bytes - audio_bytes - int(target_bytes * 0.02)
        video_kbps = max(400, int(video_bytes * 8 / dur / 1000))
        vmax = int(video_kbps * 1.5)
        vbuf = video_kbps * 2
        cmd = (f'ffmpeg -y -i "{video}" -vf "{vf}" -c:v libx264 -preset {PRESET} '
               f'-b:v {video_kbps}k -maxrate {vmax}k -bufsize {vbuf}k '
               f'-c:a aac -b:a {audio_kbps}k -ar 44100 "{out_path}"')
        print(f"[finalize] 单 pass 目标 ~{target_mb}MB / {dur:.0f}s，视频码率 ~{video_kbps}kbps")
    else:
        print("WARN 无法探测时长，单 pass CRF23 编码")
        cmd = (f'ffmpeg -y -i "{video}" -vf "{vf}" -c:v libx264 -preset {PRESET} '
               f'-crf 23 -c:a aac -b:a {audio_kbps}k -ar 44100 "{out_path}"')
    r = os.system(cmd)
    return r == 0 and os.path.exists(out_path)


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
        done = translate_many(texts, sl="en", log_every=25)
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

    # 1080p 手机精简版（保持 1080p，单 pass ABR 压到 ~300MB，移动端/网盘快速下载）
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
