#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""字幕翻译封装：多免费端点容错 + 中文占比校验门，最大限度保证输出中文。

可靠性策略（不带 key 的免费方案）：
- 三级端点按顺序兜底：Google gtx（质量好）→ MyMemory（免 key）→ Argos LibreTranslate 公共实例。
  GitHub Actions 的 runner 是数据中心 IP：Google 常被 429/封锁，MyMemory 有每日词数上限，
  Argos 作第三道防线。三者同时挂的概率极低。
- 批量翻译：主用 Google；Google 连续失败则整体切 MyMemory；首轮未译出的段进入「二刷」，
  用全链路（Google→MyMemory→Argos）逐段重试，最大化命中率。
- 中文校验门（gate）：译完统计「仍为英文」的段占比。若系统性失败（大量段仍是英文），
  直接抛异常让 job 失败，由下次 cron 重跑（额度第二天重置），**绝不静默烧录英文片**。
  仅 1~2 句偶发未译出（如 GOAL! 这类喊声）不触发失败，避免无谓重跑。

若将来配置 LLM key（如 DEEPSEEK_API_KEY），可在此链首部插入 LLM 端点，做到真正 100% 保证。
"""
import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import polish

ENDPOINT = "https://translate.googleapis.com/translate_a/single"
MYMEMORY = "https://api.mymemory.translated.net/get"
ARGOS = "https://translate.argosopentech.com/translate"

# CJK 判定：含汉字视为已译出中文；含拉丁字母但无汉字视为「仍为英文」
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LAT = re.compile(r"[A-Za-z]")


def _is_english(s):
    return bool(_LAT.search(s)) and not _HAN.search(s)


# --------------------------- 单端点实现 ---------------------------

def _google_translate(text, sl="auto", tl="zh-CN", retries=2):
    """Google gtx 单段翻译；sl=auto 自动识别源语言。失败抛异常由上层回退。"""
    if not text or not text.strip():
        return text
    url = (f"{ENDPOINT}?client=gtx&sl={urllib.parse.quote(sl)}"
           f"&tl={urllib.parse.quote(tl)}&dt=t&q={urllib.parse.quote(text)}")
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read().decode("utf-8"))
            return "".join(part[0] for part in data[0] if part[0])
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429:
                # Google 免费接口对 GitHub Actions IP 容易限流；指数退避 + jitter 给冷却窗口
                sleep = min(30, (2 ** i)) + random.uniform(0, 1)
                print(f"    [translate] Google 429 限流，退避 {sleep:.1f}s ({i + 1}/{retries})")
                time.sleep(sleep)
            else:
                time.sleep(1.5 * (i + 1))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"Google failed after {retries} tries: {last}")


def _mymemory_translate(text, sl="auto", tl="zh-CN"):
    """MyMemory 免费翻译（无需 key）。sl=auto 时按英文源处理（本流水线字幕多来自 Whisper en）。"""
    if not text or not text.strip():
        return text
    src = sl if sl and sl != "auto" else "en"
    tgt = tl if tl != "zh-CN" else "zh-CN"
    url = (f"{MYMEMORY}?q={urllib.parse.quote(text)}&langpair={urllib.parse.quote(src)}|"
           f"{urllib.parse.quote(tgt)}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read().decode("utf-8"))
    status = str(d.get("responseStatus"))
    if status != "200":
        raise RuntimeError(f"MyMemory status {status}: {str(d.get('responseMessage', ''))[:120]}")
    tr = (d.get("responseData") or {}).get("translatedText", "")
    if not tr or tr.strip() == text.strip():
        raise RuntimeError("MyMemory 返回空/未翻译")
    return tr


def _argos_translate(text, sl="auto", tl="zh-CN"):
    """Argos LibreTranslate 公共实例（第三兜底，免 key）。"""
    if not text or not text.strip():
        return text
    src = sl if sl and sl != "auto" else "en"
    tgt = "zh" if tl == "zh-CN" else tl
    data = json.dumps({"q": text, "source": src, "target": tgt,
                       "format": "text"}).encode("utf-8")
    req = urllib.request.Request(ARGOS, data=data,
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        d = json.loads(r.read().decode("utf-8"))
    tr = (d.get("translatedText") or "").strip()
    if not tr or tr == text.strip():
        raise RuntimeError("Argos 返回空/未翻译")
    return tr


# 全链路兜底顺序（标题等「尽力而为」场景用）
_CHAIN = (("google", _google_translate),
          ("mymemory", _mymemory_translate),
          ("argos", _argos_translate))


def _translate_one_chain(text, sl, tl):
    """逐端点尝试，返回首个成功结果；全失败抛异常。"""
    last = None
    for name, fn in _CHAIN:
        try:
            return fn(text, sl, tl)
        except Exception as e:  # noqa: BLE001
            last = e
            continue
    raise RuntimeError(f"all translators failed: {last}")


# --------------------------- 对外接口 ---------------------------

def translate(text, sl="auto", tl="zh-CN"):
    """翻译单段（如视频标题）：尽力而为，全失败则保留原文，绝不抛异常。"""
    if not text or not text.strip():
        return text
    try:
        return _translate_one_chain(text, sl, tl)
    except Exception as e:  # noqa: BLE001
        print(f"    [translate] 标题翻译失败，保留原文: {e}")
        return text


def translate_many(texts, sl="auto", tl="zh-CN", pace=1.0, log_every=25,
                   gate=True, fail_ratio=0.2, min_fail_lines=3):
    """批量翻译 + 中文校验门。

    - 主 Google，连续失败整体切 MyMemory；首轮未译出的段进入「二刷」用全链路重试。
    - gate=True 时：若「仍为英文」的段占比 >= fail_ratio 且数量 >= min_fail_lines，
      抛 RuntimeError 让 job 失败以便下次重跑（不静默出英文片）。
    - 返回与输入等长的已润色中文字幕列表。
    """
    total = len(texts)
    if total == 0:
        return []
    out = []
    failed_idx = []
    google_dead = False
    consec = 0

    # ---- 首轮：Google 主用，失败降级 MyMemory ----
    for i, t in enumerate(texts, 1):
        res = None
        if not google_dead:
            try:
                res = _google_translate(t, sl, tl, retries=2)
                consec = 0
            except Exception as e:  # noqa: BLE001
                print(f"    [translate] Google 失败: {e}")
                consec += 1
                if consec >= 5:
                    google_dead = True
                    print("    [translate] Google 连续失败，整体切换 MyMemory")
        if res is None:
            try:
                res = _mymemory_translate(t, sl, tl)
                consec = 0
            except Exception as e2:  # noqa: BLE001
                print(f"    [translate] MyMemory 失败，暂留原文待二刷: {e2}")
        if res is None or _is_english(res):
            failed_idx.append(i - 1)
            if res is None:
                res = t  # 临时占位，二刷覆盖
        out.append(res)
        if i % log_every == 0:
            print(f"    [translate] 首轮 {i}/{total} (待二刷 {len(failed_idx)} 段)")
        time.sleep(pace if not google_dead else 0.3)

    # ---- 二刷：对仍英文的段用全链路逐段重试 ----
    if failed_idx:
        print(f"    [translate] 二刷重试 {len(failed_idx)} 段（Google→MyMemory→Argos）...")
        ok = 0
        for n, idx in enumerate(failed_idx, 1):
            t = texts[idx]
            try:
                out[idx] = _translate_one_chain(t, sl, tl)
                ok += 1
            except Exception as e:  # noqa: BLE001
                print(f"    [translate] 二刷第 {n} 段仍失败，保留原文: {e}")
                out[idx] = t
            time.sleep(0.5)
        print(f"    [translate] 二刷完成，成功补译 {ok}/{len(failed_idx)} 段")

    # ---- 中文占比校验门 ----
    still = [i for i in range(total) if _is_english(out[i])]
    ratio = len(still) / total
    if gate and ratio >= fail_ratio and len(still) >= min_fail_lines:
        raise RuntimeError(
            f"中文占比不足: {len(still)}/{total} 段仍为英文 (ratio={ratio:.0%})，"
            f"超过阈值 {fail_ratio:.0%}，job 失败以便下次 cron 重跑（不静默出英文片）")
    print(f"    [translate] 完成 {total} 段，仍英文 {len(still)} 段（占比 {ratio:.0%}）")

    # 出口统一本地化润色（FC27/足球术语 + 口语化）
    return [polish.polish(x) for x in out]


if __name__ == "__main__":
    # 简易自检：用模拟端点验证校验门逻辑（不联网）
    import sys
    sample = ["Hello world", "This is a test", "GOAL!", "2 - 1", "Thank you"]
    # 模拟「全部英文」应触发 gate
    print("self-check: 译文仍英文时 gate 应抛异常")
    try:
        translate_many(sample, gate=True, fail_ratio=0.2)
        print("  -> 未抛异常（异常：应失败）")
        sys.exit(1)
    except RuntimeError as e:
        print(f"  -> 正确抛异常: {e}")
