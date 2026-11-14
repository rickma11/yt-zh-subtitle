#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中文字幕本地化润色：FC27/足球术语前后两层处理，贴合「欧啊欧」翻译习惯。

两层处理：
- preprocess()（译前）：把字幕原文中的英文 FC 术语按 OAO 官方术语库
  （OAO-evotrans built-in-dictionaries.json：六维/比赛风格/球员角色/化学卡/界面词
  共 269 条）预替换为中文标准译名，再送机翻——避免 Google/MyMemory 把
  Pace/Trickster/Poacher/Hunter 等术语直翻成「步伐/诡计者/偷猎者/猎人」。
  词边界 + 大小写不敏感匹配；多词术语优先；带上下文排除规则防普通义误伤
  （如 pace of the game 不替换）。
- polish()（译后）：把机翻生硬输出按 55 条校正表替换为圈内通行说法
  （步伐→速度、防守型中场→后腰、自定义战术→战术代码、周末联赛→周赛……），
  再做轻量口语化与标点规整。

原则：
- 只做高置信度替换，不改写句子结构，避免引入事实错误
- 幂等：重复执行结果一致
- 数据与逻辑分离：术语数据在 fc_terms.json，本文件只放规则
"""
import json
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_TERMS_FILE = os.path.join(_HERE, "fc_terms.json")

try:
    with open(_TERMS_FILE, encoding="utf-8") as f:
        _T = json.load(f)
    PRE_TERMS = _T.get("pre", {})            # 译前：英文术语 -> 中文标准译名（已按长度降序，大小写不敏感）
    PRE_TERMS_CS = _T.get("pre_cs", {})      # 译前：位置缩写等（区分大小写，防 cm/lb 普通缩写误伤）
    POST_TERMS = _T.get("post", {})          # 译后：机翻错译 -> 圈内习惯
    _SKIP = [(k, re.compile(p, re.IGNORECASE)) for k, p in _T.get("skip_context", [])]
except Exception:  # noqa: BLE001  术语文件缺失不应拖垮主翻译链
    PRE_TERMS, PRE_TERMS_CS, POST_TERMS, _SKIP = {}, {}, {}, []

# 预编译译前匹配式：词边界 + 大小写不敏感（数字/字母不粘连，防 pacemaker 误伤）
# 按英文词长度降序排序，保证 "pack weight" 先于 "pack"、"walkout animation" 先于 "walkout" 匹配
_PRE_COMPILED = sorted(
    [(re.compile(r"(?<![A-Za-z])" + re.escape(en) + r"(?![A-Za-z])", re.IGNORECASE),
      zh, en) for en, zh in PRE_TERMS.items()],
    key=lambda x: len(x[2]), reverse=True
)
# 位置缩写：词边界 + 区分大小写
# 注意：lookbehind 排除数字，避免 "60cm"（厘米）被当成位置缩写 cm（中场）误伤
_PRE_CS_COMPILED = [(re.compile(r"(?<![A-Za-z0-9])" + re.escape(en) + r"(?![A-Za-z0-9])"),
                     zh, en) for en, zh in PRE_TERMS_CS.items()]
# 译后校正：按 key 长度降序，保证长串（如「这一个L」）先于短串（如「是L」）命中，
# 避免短规则把长规则要匹配的上下文提前改坏
_POST_SORTED = sorted(POST_TERMS.items(), key=lambda kv: len(kv[0]), reverse=True)

# 口语化/清理规则（正则）
RULES = [
    (re.compile(r"1对1s"), "1对1"),
    (re.compile(r"极其(困难|难)"), "特别难"),
    # gtx 机翻 goal -> 「目标」：beautiful goal 译成「美丽的目标」，拉回「进球」
    (re.compile(r"(漂亮|美丽|精彩|伟大|绝妙|惊人)的目标"), r"\1的进球"),
    # 数字千分位：LLM 偶发写成全角逗号「2，500」，先转回半角。
    # 必须放在普通半角->全角规则之前；validate_zh 把「数字间全角逗号」列为硬失败，
    # 旧规则 (r",", "，") 会把 LLM 正确输出的 2,500 改成 2，500 再被硬失败掐断（2026-11-12 事故）。
    (re.compile(r"(?<=\d)，(?=\d)"), ","),
    # gtx 偶发半角标点 -> 全角；但保留数字间的千分位半角逗号
    (re.compile(r"(?<!\d),(?!\d)"), "，"),
    (re.compile(r";"), "；"),
    (re.compile(r"(?<!\d):(?!\d)"), "："),
]

# LLM 在长 system prompt（词表 + 20 条）下偶发对术语本身展开说明，
# 输出「如果你需要…请提供更具体的问题…我会尽力」之类套话。
# 这些不是字幕内容，必须在烧录前截掉；命中后保留套话前的最后一个完整句。
_LLM_META_MARKERS = (
    "请提供更具体的问题", "请提供更多信息", "我会尽力", "我会尽我所能",
    "根据提供的术语", "在术语锁定表中", "作为AI", "作为人工智能",
    "希望这对你有帮助", "希望对你有帮助",
)

# 日文假名 -> 中文音译兜底表。
# 根因（2026-11-14 事故）：LLM 偶发把人名音译成片假名（帕ブロ / 玛リア娜），
# validate_zh 把「日文字符」列为硬性失败会掐断整条流水线。这里在译后把假名
# 按音节音译成近似汉字（如 ブロ->布罗、リアナ->里阿娜），保证字幕里不再出现日文字符，
# 同时给出可读的中文音译。覆盖平/片假名 + 浊音/半浊音 + 小字 + 长音/促音。
_HIRA = "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをん"
_HIRA_ZH = "阿伊乌江奥卡基库凯科萨希苏塞索塔奇茨特托纳尼努涅诺哈希夫赫霍玛米穆梅莫亚尤约拉里鲁列罗瓦奥恩"
_KANA_MAP = {ch: z for ch, z in zip(_HIRA, _HIRA_ZH)}
# 浊音 / 半浊音：显式列出（假名码点非连续，不能靠 +1 推算）
_VOICED = {
    "か": "が", "き": "ぎ", "く": "ぐ", "け": "げ", "こ": "ご",
    "さ": "ざ", "し": "じ", "す": "ず", "せ": "ぜ", "そ": "ぞ",
    "た": "だ", "ち": "ぢ", "つ": "づ", "て": "で", "と": "ど",
    "は": "ば", "ひ": "び", "ふ": "ぶ", "へ": "べ", "ほ": "ぼ",
}
_SEMI = {"は": "ぱ", "ひ": "ぴ", "ふ": "ぷ", "へ": "ぺ", "ほ": "ぽ"}
for _b, _v in _VOICED.items():
    _KANA_MAP[_v] = _KANA_MAP[_b]          # 浊音 -> 同音节汉字
for _b, _s in _SEMI.items():
    _KANA_MAP[_s] = _KANA_MAP[_b]          # 半浊音 -> 同音节汉字
# 人名音译微调：ぶ/ブ 是 bu，中文音译用「布」更准确（帕ブロ→帕布罗）；
# 默认按基础 ふ(fu)→夫 会译成「夫」，这里显式覆盖。
_KANA_MAP["ぶ"] = "布"
_KANA_MAP["ブ"] = "布"
# 小字 / 长音 / 促音
_KANA_MAP.update({"ゃ": "亚", "ゅ": "尤", "ょ": "约", "っ": "", "ー": ""})
# 片假名：平假名码点 +0x60 映射到同一套汉字（含浊音/半浊音）
_KANA_MAP.update({chr(ord(k) + 0x60): v for k, v in _KANA_MAP.items() if k not in ("っ", "ー")})
_KANA_MAP.update({"ャ": "亚", "ュ": "尤", "ョ": "约", "ッ": "", "ー": ""})
_KANA_RE = re.compile(r"[\u3040-\u30ff]")


def _kana_to_hanzi(text):
    """把混入的中日假名按音节音译成近似汉字；不在表中的假名直接丢弃，确保输出零日文字符。"""
    if not _KANA_RE.search(text):
        return text
    return "".join(_KANA_MAP.get(ch, "") if _KANA_RE.match(ch) else ch for ch in text)


def _strip_llm_meta(text):
    """截掉 LLM 偶发输出的元说明套话；整段都是套话时退回「（听不清）」。"""
    hits = [text.index(m) for m in _LLM_META_MARKERS if m in text]
    if not hits:
        return text
    head = text[:min(hits)]
    idx = max(head.rfind("。"), head.rfind("！"), head.rfind("？"))
    if idx >= 0:
        head = head[:idx + 1]
    head = head.strip()
    return head or "（听不清）"


def preprocess(text):
    """译前处理：英文 FC 术语 -> OAO 标准中文译名（送机翻前调用）。"""
    if not text:
        return text
    # 先处理位置缩写（区分大小写），再处理通用术语（大小写不敏感）
    for rx, zh, en in _PRE_CS_COMPILED:
        if rx.search(text):
            text = rx.sub(lambda _m: zh, text)
    if not PRE_TERMS:
        return text
    # 已替换出的内容先存进占位符，避免后续短键二次命中长键的产物：
    # 例："rice with" -> "Declan Rice" 后，短键 "rice" 若再扫一遍会变成
    # "Declan Declan Rice"（2026-11-11 合并版落地时发现）。
    slots = []

    def stash(zh):
        slots.append(zh)
        return "\x00%d\x00" % (len(slots) - 1)

    for rx, zh, en in _PRE_COMPILED:
        if not rx.search(text):
            continue
        # 上下文排除：术语命中处若接普通义上下文（如 pace of the game）则跳过
        m = rx.search(text)
        tail = text[m.end():m.end() + 30]
        # 键名大小写不敏感比对（skip_context 历史条目大小写不统一）
        if any(k.lower() == en.lower() and p.search(tail) for k, p in _SKIP):
            continue
        text = rx.sub(lambda _m, z=zh: stash(z), text)
    for i, zh in enumerate(slots):
        text = text.replace("\x00%d\x00" % i, zh)
    return text


def polish(text):
    """译后处理：机翻错译 -> 圈内习惯说法 + 口语化 + 标点规整。"""
    if not text:
        return text
    for src, dst in _POST_SORTED:  # 按 key 长度降序，长串先命中
        if src in text:
            text = text.replace(src, dst)
    text = _strip_llm_meta(text)
    text = _kana_to_hanzi(text)   # 兜底：清除 LLM 偶发的片假名音译（如 帕ブロ / 玛リア娜）
    for pat, dst in RULES:
        text = pat.sub(dst, text)
    return text


if __name__ == "__main__":
    demo = [
        ("His pace is insane, 99 sprint speed and the trickster playstyle",
         "他的速度简直离谱，99冲刺速度还有诡术师比赛风格"),
        ("I run a poacher up top with a deep-lying playmaker behind him",
         "我前面放个禁区之王，身后配个拖后组织核心"),
        ("Hunter chemistry style gives him that finishing boost",
         "猎手化学风格能让他的临门一脚上一个档次"),
        ("Just play a simple through ball, don't force the finesse shot",
         "就直接送个直塞，别硬搓"),
        ("The pace of the game in weekend league is so slow",
         "周赛的比赛节奏太慢了"),
        ("Their defensive midfielder screens every pass, what a stopper",
         "他们的后腰能拦下每一脚传球，太拦截了"),
        ("My GK is a sweeper keeper and my CB has the aerial fortress playstyle",
         "我的门将是清道夫门将，我的中后卫有空中堡垒比赛风格"),
        ("He's 60cm taller and I still can't get the ball off him",
         None),  # cm 不应被误替换成中场
    ]
    ok = True
    for en, _expect in demo:
        pre = preprocess(en)
        print("原文:", en)
        print("预替换:", pre)
        print()
    # 大写缩写应替换、普通小写不应替换
    cs1 = preprocess("my GK and CDM link up well")
    cs2 = preprocess("the box is 60cm wide")
    print("缩写 GK/CDM ->", cs1)
    print("60cm 防误伤 ->", cs2)
    if "门将" not in cs1 or "后腰" not in cs1:
        print("  !! 缩写替换失败"); ok = False
    if "中场" in cs2:
        print("  !! cm 误伤"); ok = False
    # pace of the game 排除规则验证
    guard = preprocess("The pace of the game is slow")
    print("排除规则 pace of the game ->", guard)
    if "速度" in guard:
        print("  !! 排除规则失效")
        ok = False
    # 幂等性
    once = polish("他们的防守者，适当时机的技能移动可以使他们变得极其困难")
    twice = polish(once)
    print("润色一次:", once)
    print("幂等:", "OK" if once == twice else "FAIL")
    ok = ok and once == twice
    print("\nSELF-CHECK:", "PASS" if ok else "FAIL")
