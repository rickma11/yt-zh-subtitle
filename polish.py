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
_PRE_COMPILED = [(re.compile(r"(?<![A-Za-z])" + re.escape(en) + r"(?![A-Za-z])", re.IGNORECASE),
                  zh, en) for en, zh in PRE_TERMS.items()]
# 位置缩写：词边界 + 区分大小写
_PRE_CS_COMPILED = [(re.compile(r"(?<![A-Za-z])" + re.escape(en) + r"(?![A-Za-z])"),
                     zh, en) for en, zh in PRE_TERMS_CS.items()]

# 口语化/清理规则（正则）
RULES = [
    (re.compile(r"1对1s"), "1对1"),
    (re.compile(r"极其(困难|难)"), "特别难"),
    # gtx 机翻 goal -> 「目标」：beautiful goal 译成「美丽的目标」，拉回「进球」
    (re.compile(r"(漂亮|美丽|精彩|伟大|绝妙|惊人)的目标"), r"\1的进球"),
    # gtx 偶发半角标点 -> 全角
    (re.compile(r","), "，"),
    (re.compile(r";"), "；"),
    (re.compile(r"(?<!\d):(?!\d)"), "："),
]


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
    for rx, zh, en in _PRE_COMPILED:
        if not rx.search(text):
            continue
        # 上下文排除：术语命中处若接普通义上下文（如 pace of the game）则跳过
        m = rx.search(text)
        tail = text[m.end():m.end() + 30]
        if any(k == en and p.search(tail) for k, p in _SKIP):
            continue
        text = rx.sub(lambda _m: zh, text)
    return text


def polish(text):
    """译后处理：机翻错译 -> 圈内习惯说法 + 口语化 + 标点规整。"""
    if not text:
        return text
    for src, dst in POST_TERMS.items():  # 已按 key 长度降序
        if src in text:
            text = text.replace(src, dst)
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
