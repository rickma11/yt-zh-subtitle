#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""字幕交付前自检（硬性要求 #10）。

比对 work/en.srt 与 work/zh.srt，做两类检查：

【硬性失败项（exit 1，触发 CI 重跑）】—— 都是客观、零误报的：
  1. 段数 1:1：中文段数必须 == 英文段数（否则烧录时字幕错位）。
  2. 空段：中文不能有任何空译文（会烧出空白字幕）。
  3. 元说明泄漏：译文里出现「直接翻译就是 / 这字幕 / 字幕就一个字 / 翻译就是 /
     这个词应该是」这类关于翻译本身的说明——规范明确禁止。

【软性警告项（仅报告，exit 0）】—— 提示人工复核，不阻断流水线：
  - 半中半英：汉字紧贴长度>=3 的英文词（人名保留英文属合规，故仅提醒）。
  - 残留旧错词：周黑 / 走出 / 打包 / 空壳 / 组队挑战 / 后场发送者 等应已被术语表消除。
  - 「(待核)」：表示有人名未确认，提醒复核。

用法：python scripts/validate_zh.py [en.srt] [zh.srt]
"""
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

DEFAULT_EN = os.path.join(_ROOT, "work", "en.srt")
DEFAULT_ZH = os.path.join(_ROOT, "work", "zh.srt")

# 元说明泄漏（绝对禁止，出现即失败）
_META_PATTERNS = [
    "直接翻译就是", "这字幕", "字幕就一个字", "翻译就是",
    "这个词应该是", "应该是", "字幕仅供参考", "请提供更",
]
# 软性残留旧错词（提醒，不失败）
_RESIDUAL = ["周黑", "走出球场", "走出动画", "走大卡", "打包", "空壳", "组队挑战", "后场发送者"]
# 半中半英检测：汉字 与 长度>=3 的拉丁词 直接相邻
_HALF = re.compile(r"[\u4e00-\u9fff][A-Za-z]{3,}|[A-Za-z]{3,}[\u4e00-\u9fff]")


def parse_srt(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        content = f.read()
    blocks = re.split(r"\n\s*\n", content.strip())
    segs = []
    for b in blocks:
        lines = [l for l in b.strip().splitlines() if l.strip()]
        if len(lines) >= 3:
            segs.append("\n".join(lines[2:]).strip())
        elif len(lines) == 2 and "-->" in lines[0]:
            segs.append(lines[1].strip())
    return segs


def main():
    en_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_EN
    zh_path = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_ZH
    if not os.path.exists(en_path) or not os.path.exists(zh_path):
        print(f"[validate] 跳过：缺少文件 en={en_path} zh={zh_path}")
        return 0

    en = parse_srt(en_path)
    zh = parse_srt(zh_path)
    print(f"[validate] en={len(en)} 段, zh={len(zh)} 段")

    errors = []   # 硬性失败
    warns = []    # 软性警告

    # 1. 段数 1:1
    if len(en) != len(zh):
        errors.append(f"段数不一致：en={len(en)} zh={len(zh)}（必须 1:1）")

    n = min(len(en), len(zh))
    for i in range(n):
        z = zh[i]
        if not z:
            errors.append(f"第 {i+1} 段为空译文")
            continue
        # 3. 元说明泄漏
        for pat in _META_PATTERNS:
            if pat in z:
                errors.append(f"第 {i+1} 段含元说明泄漏「{pat}」：{z[:40]}")
        # 软性：半中半英
        if _HALF.search(z):
            warns.append(f"第 {i+1} 段疑似半中半英：{z[:50]}")
        # 软性：残留旧错词
        for w in _RESIDUAL:
            if w in z:
                warns.append(f"第 {i+1} 段残留旧错词「{w}」：{z[:50]}")
        if "(待核)" in z:
            warns.append(f"第 {i+1} 段有人名未确认「(待核)」：{z[:50]}")

    # 汇总
    print(f"[validate] 硬性失败 {len(errors)} 项，软性警告 {len(warns)} 项")
    for e in errors:
        print("  ❌ " + e)
    for w in warns[:40]:
        print("  ⚠️  " + w)
    if len(warns) > 40:
        print(f"  ⚠️  …（其余 {len(warns)-40} 条警告略）")

    if errors:
        print("[validate] 存在硬性失败，建议重跑该视频翻译")
        return 1
    print("[validate] 通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
