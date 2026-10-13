# 生产：字幕定稿 + 烧录
# 若 work/zh.srt 不存在（T1 未命中）：读 work/en.srt -> Google 翻译 -> FC27 润色 -> 生成 zh.srt
# 然后 ffmpeg 硬字幕烧录 -> work/output.mp4
import os, sys, re, html

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

    # 烧录
    video = os.path.join(WORK, "video.mp4")
    out = os.path.join(WORK, "output.mp4")
    if not os.path.exists(video):
        print("FATAL 无 video.mp4")
        sys.exit(1)
    srt_abs = os.path.abspath(zh_path).replace("\\", "/").replace(":", "\\:")
    vf = (f"subtitles='{srt_abs}':force_style='FontSize=15,"
          "FontName=Noto Sans CJK SC,"
          "PrimaryColour=&H00FFFFFF&,OutlineColour=&H00000000&,"
          "BackColour=&H80000000&,Outline=2,Shadow=1,Bold=1,MarginV=26'")
    print("[finalize] ffmpeg 烧录 ...")
    r = os.system(f'ffmpeg -y -i "{video}" -vf "{vf}" -c:v libx264 -preset medium -crf 20 -c:a copy "{out}"')
    if r != 0 or not os.path.exists(out):
        print("FATAL ffmpeg 失败")
        sys.exit(1)
    print(f"[finalize] DONE output.mp4 {os.path.getsize(out)/1048576:.1f}MB")


if __name__ == "__main__":
    main()
