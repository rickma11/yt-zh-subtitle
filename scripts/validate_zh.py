#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""字幕交付前自检（硬性要求 #10）。

比对 work/en.srt 与 work/zh.srt，做两类检查：

【硬性失败项（exit 1，触发 CI 重跑）】—— 都是客观、可自动修复、零误报的：
  1. 段数 1:1：中文段数必须 == 英文段数（否则烧录时字幕错位）。
  2. 空段：中文不能有任何空译文（会烧出空白字幕）。
  3. 日文假名 / 片假名混入（合并版规则 2）：中文口播字幕不应出现日文字符。
  4. 全角逗号当千分位（如「1，400」）：规范 R12 要求半角逗号。
     —— 已在 polish.RULES 译后自动转半角，这里只做兜底拦截。

【软性警告项（仅报告，exit 0）】—— 提示人工复核，不阻断流水线：
  - 元说明泄漏：「直接翻译就是 / 请提供更 / 这字幕」等 LLM 偶发套话。
    已在 polish._strip_llm_meta 译后自动截断；残留仅提醒，**绝不硬失败吞片**
    （2026-11-12 事故：LLM 输出「请提供更具体的问题…我会尽力」被硬失败掐断全链）。
  - 半中半英：汉字紧贴长度>=3 的英文词（人名保留英文属合规，故仅提醒）。
  - 残留旧错词：合并版第 8 节禁用说法（周黑 / 冠军卡 / 无人机 / 中前卫 / 88+免费包 等）。
  - 「(待核)」「(听不清)」：提示人工对照卡面复核。
  - 非千分位半角标点（合并版规则 2）。

【严禁扩展硬失败词表】2026-11-10 事故：把「应该是」误列为元说明硬性失败，
正常口播「他应该是中锋」命中后掐断整条流水线（Release/推送/状态全跳过）。
任何日常口播高频词、任何可能在正常句中出现的词，只能进软性警告，绝不硬失败。

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

# 元说明 / 对话残留（软警告：polish._strip_llm_meta 已在译后截断，残留仅提醒不吞片）
# 严禁加入「应该是」这类日常口播高频词：正常句如「他应该是中锋」
# 会被误判为元说明泄漏，硬性失败会掐断整条流水线（2026-11-10 事故根因）。
# 2026-11-23 质检：三支视频共 19 处元话语残留，这里补齐触发词，全部只警告。
_META_PATTERNS = [
    # —— 原有 ——
    "直接翻译就是", "这字幕", "字幕就一个字", "翻译就是",
    "这个词应该是", "字幕仅供参考", "请提供更",
    # —— 待命 / 引导话术 ——
    "请提供你需要翻译", "请提供需要翻译", "请提供完整句子", "如果后面有更多内容",
    # —— 过程 / 规则复述 / 词典释义 ——
    "根据术语锁定表", "根据你提供的术语", "对应的英文是", "保留英文标准拼写",
    "是人名，保留英文", "这句话可以翻译为", "以下是对应的简体中文翻译",
    "以下是对应的中文翻译", "译为：", "译文：",
    # —— 模型自述（中文）——
    "作为AI", "作为人工智能", "我会尽力", "我会尽我所能",
    # —— 模型自述（整段英文）——
    "expecting a response in English", "been provided with instructions to translate",
]
# 软性残留旧错词（提醒，不失败）—— 来自合并版规范第 8 节 18 项自检 + 附录 A
_RESIDUAL = [
    # 术语 banned（RTTK / 活动 / 卡包）
    "周黑", "旋转大保健", "旋风连击", "旋风淘汰", "锁包卡", "通往淘汰赛",
    "冠军卡", "冬日 wild card", "尖叫活动", "女冠军Z卡",
    # 位置 / 特性
    "后场发送者", "工具人", "拳击手", "中前卫", "回防", "中路属性",
    # 机制 / 卡包
    "快速设置", "空壳", "走出球场", "走出动画", "走大卡", "爆包",
    "打包", "组队挑战", "玩法加成", "操作损失值", "无人机",
    # 卡包名加号错误（合并版 R4 / A2）
    "88+免费包", "84+免费包", "85 免费包",
    # 评价词方向
    "坑惨了", "没煮熟", "双黄蛋",
    # —— 2026-11-23 质检新增：术语回退（报告规则二）——
    "传奇球星", "超级巨星", "明日之星", "疾风步", "敏锐的传球", "切入传球",
    "骑师", "卡姆", "冻结卡包", "KFC积分", "KFC 积分", "KFC点",
    "统计数据", "尖叫卡", "战役混合包", "活动组合", "活动混合刷新",
    # —— 人名音译（报告规则三：应保留英文）——
    "亚马尔", "姆巴佩", "罗德里", "库尔图瓦", "奥巴拉克", "本泽马",
    "卡塞米罗", "普利希奇", "马内", "奥利塞", "魔笛", "莫德里奇",
    "施洛特贝克", "施赖克尔", "玛罗尼亚", "玛罗娜", "玛丽安娜",
    "哈利·凯恩", "哈里·凯恩", "亚历克斯·斯科特·鲍姆思",
    "摩根·罗杰斯", "路易·斯凯利", "莱昂内尔·Messi", "马奎inhos",
    # —— 噪声硬猜（报告规则四：应回英文或标听不清）——
    "谷物", "旗帜", "骆驼", "鸽子", "佛陀", "村庄", "鲨鱼卡", "射门卡",
    "打击卡", "长怀疑动画", "铜墙铁壁", "边锋和公牛队", "加布里埃尔·银色",
]
# 半中半英检测：汉字 与 长度>=3 的拉丁词 直接相邻
_HALF = re.compile(r"[\u4e00-\u9fff][A-Za-z]{3,}|[A-Za-z]{3,}[\u4e00-\u9fff]")
# 日文假名 / 片假名（硬性：合并版规则 2）
_KANA = re.compile(r"[\u3040-\u30ff]")
# 全角逗号当千分位（硬性：合并版 R12）
_FULLWIDTH_COMMA_NUM = re.compile(r"\d，\d")
# 非千分位半角标点（软性）
_HALF_PUNCT = re.compile(r"(?<![0-9]),(?![0-9])|(?<![0-9]);(?![0-9])")


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
        # 空段硬失败豁免：若英文段本身也为空（whisper 偶发末尾空块），属对称空段，
        # 不算翻译缺失，跳过（2026-11-14 加固，避免误杀）。
        # 仅当「英文非空但中文空」才算真缺失。
        if not z:
            if not en[i]:
                continue
            errors.append(f"第 {i+1} 段为空译文（对应英文段非空，属翻译缺失）")
            continue
        # 软性：元说明泄漏（polish 已自动截断，残留仅提醒；绝不硬失败吞片）
        for pat in _META_PATTERNS:
            if pat in z:
                warns.append(f"第 {i+1} 段含元说明泄漏「{pat}」：{z[:40]}")
        # 4. 日文假名 / 片假名混入
        m = _KANA.search(z)
        if m:
            errors.append(f"第 {i+1} 段混入日文字符「{m.group()}」：{z[:40]}")
        # 5. 全角逗号当千分位
        if _FULLWIDTH_COMMA_NUM.search(z):
            errors.append(f"第 {i+1} 段全角逗号当千分位：{z[:40]}")
        # 软性：半中半英
        if _HALF.search(z):
            warns.append(f"第 {i+1} 段疑似半中半英：{z[:50]}")
        # 软性：非千分位半角标点
        if _HALF_PUNCT.search(z):
            warns.append(f"第 {i+1} 段疑似半角标点：{z[:50]}")
        # 软性：残留旧错词
        for w in _RESIDUAL:
            if w in z:
                warns.append(f"第 {i+1} 段残留旧错词「{w}」：{z[:50]}")
        if "(待核)" in z or "（待核）" in z:
            warns.append(f"第 {i+1} 段有人名未确认「待核」：{z[:50]}")
        if "(听不清)" in z or "（听不清）" in z:
            warns.append(f"第 {i+1} 段有整段听不清标记：{z[:50]}")

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
