#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""字幕翻译封装：多免费端点容错 + 中文占比校验门，最大限度保证输出中文。

可靠性策略（不带 key 的免费方案）：
- 两级端点按顺序兜底：Google gtx（质量好）→ MyMemory（免 key，带 email 参数 5 万字符/天）。
  GitHub Actions 的 runner 是数据中心 IP：Google 常被 429/封锁，MyMemory 兜底。
- 注：曾用的第三端 Argos（translate.argosopentech.com）域名已 DNS 失效、LibreTranslate 公共实例
  被 Cloudflare 403、Lingva 公共实例 403/500，均不可用，故不再保留死端点占位。
- 批量翻译：主用 Google；Google 连续失败则整体切 MyMemory；首轮未译出的段进入「二刷」，
  用全链路（Google→MyMemory）逐段重试，最大化命中率。
- 中文校验门（gate）：译完统计「仍为英文」的段占比。若系统性失败（大量段仍是英文），
  直接抛异常让 job 失败，由下次 cron 重跑（额度第二天重置），**绝不静默烧录英文片**。
  仅 1~2 句偶发未译出（如 GOAL! 这类喊声）不触发失败，避免无谓重跑。
- 标题等单段翻译：多轮重试（默认 5 轮，递增间隔），跨过 Google 429 短窗口。
- FC27 术语层（polish.py + fc_terms.json，数据源 OAO-evotrans 官方术语库）：
  译前把英文术语（六维/比赛风格/球员角色/化学卡/战术口语 273 条 + 位置缩写 13 条）
  预替换为「欧啊欧」标准译名，译后按 55 条校正表把机翻错译改成圈内习惯说法。

重要：所有日志一律走 stderr，stdout 只输出纯结果，
避免 workflow 中 `$(python ...)` 捕获时把日志混进通知内容。

若将来配置 LLM key（如 DEEPSEEK_API_KEY），可在此链首部插入 LLM 端点，做到真正 100% 保证。
"""
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import polish

ENDPOINT = "https://translate.googleapis.com/translate_a/single"
MYMEMORY = "https://api.mymemory.translated.net/get"
# MyMemory 免费额度：匿名 5000 字符/天；带 email 参数提升到 50000 字符/天（官方规则，邮箱无需归属验证）
MYMEMORY_EMAIL = "ytzhbot@users.noreply.github.com"

# LLM 端点配置（OpenAI 兼容，通过环境变量配置）
# LLM_API_KEY: API 密钥（GitHub secrets 配置）
# LLM_BASE_URL: API 基础地址（默认 DeepSeek）
# LLM_MODEL: 模型名（默认 deepseek-chat）
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")

# CJK 判定：含汉字视为已译出中文；含拉丁字母但无汉字视为「仍为英文」
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LAT = re.compile(r"[A-Za-z]")


def _is_english(s):
    return bool(_LAT.search(s)) and not _HAN.search(s)


def _log(msg):
    """日志统一走 stderr：stdout 只留纯结果，防止污染调用方的 stdout 捕获。"""
    print(msg, file=sys.stderr, flush=True)


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
                _log(f"    [translate] Google 429 限流，退避 {sleep:.1f}s ({i + 1}/{retries})")
                time.sleep(sleep)
            else:
                time.sleep(1.5 * (i + 1))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"Google failed after {retries} tries: {last}")


def _mymemory_translate(text, sl="auto", tl="zh-CN"):
    """MyMemory 免费翻译（免 key，带 email 提升额度）。sl=auto 时按英文源处理。"""
    if not text or not text.strip():
        return text
    src = sl if sl and sl != "auto" else "en"
    tgt = tl if tl != "zh-CN" else "zh-CN"
    url = (f"{MYMEMORY}?q={urllib.parse.quote(text)}&langpair={urllib.parse.quote(src)}|"
           f"{urllib.parse.quote(tgt)}&email={urllib.parse.quote(MYMEMORY_EMAIL)}")
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


def _llm_translate(text, sl="auto", tl="zh-CN", retries=2):
    """LLM 翻译（OpenAI 兼容端点，通过环境变量配置）。

    无 API key 时抛异常，由上层跳过（优雅降级到 Google/MyMemory）。
    翻译链首位：LLM 质量最好，词序自然，球员名保留英文。
    """
    if not LLM_API_KEY:
        raise RuntimeError("LLM_API_KEY 未配置，跳过 LLM 端点")
    if not text or not text.strip():
        return text
    # 构造 OpenAI 兼容的 chat completions 请求
    url = f"{LLM_BASE_URL}/chat/completions"
    system_prompt = (
        "你是一个专业的 FC27/EAFC 足球游戏视频字幕翻译器。"
        "将英文字幕翻译成中文，要求：\n"
        "1. 贴合国内 FC/FUT 玩家习惯，术语用游戏圈标准叫法。\n"
        "2. 球员名、球队名保留英文原名（如 Saka、Stam、Chong 不译）。\n"
        "3. 口语化，符合主播吐槽开包的语气，不要书面生硬。\n"
        "4. 只输出译文，不加任何解释或标注。"
    )
    body = json.dumps({
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        "temperature": 0.3,
        "stream": False,
    }).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 502, 503, 504):
                sleep = min(10, (2 ** i)) + random.uniform(0, 1)
                _log(f"    [translate] LLM {e.code} 限流/错误，退避 {sleep:.1f}s ({i+1}/{retries})")
                time.sleep(sleep)
            else:
                time.sleep(1.5 * (i + 1))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"LLM failed after {retries} tries: {last}")


# 全链路兜底顺序（标题等「尽力而为」场景用）
# LLM 质量最好但需 key；无 key 时抛异常自动跳过，降级到 Google/MyMemory
_CHAIN = [("llm", _llm_translate),
          ("google", _google_translate),
          ("mymemory", _mymemory_translate)]


def _translate_one_chain(text, sl, tl):
    """逐端点尝试，返回首个成功结果；全失败抛异常。"""
    last = None
    for name, fn in _CHAIN:
        try:
            return fn(text, sl, tl)
        except Exception as e:  # noqa: BLE001
            _log(f"    [translate] {name} 端点失败: {e}")
            last = e
            continue
    raise RuntimeError(f"all translators failed: {last}")


# --------------------------- 对外接口 ---------------------------

def translate(text, sl="auto", tl="zh-CN", rounds=5):
    """翻译单段（如视频标题）：多轮全链重试，全失败则保留原文，绝不抛异常。

    多轮重试原因：标题翻译常发生在字幕翻译之后（Google 配额被消耗、限流窗口期），
    递增间隔（3/6/9/12s）可跨过 Google 429 的短时限流窗口。

    译前走 polish.preprocess（FC 术语预替换为 OAO 标准译名），
    译后走 polish.polish（圈内习惯校正），全失败返回的也是预替换后的文本。
    """
    if not text or not text.strip():
        return text
    text = polish.preprocess(text)
    for i in range(rounds):
        try:
            return polish.polish(_translate_one_chain(text, sl, tl))
        except Exception as e:  # noqa: BLE001
            _log(f"    [translate] 标题翻译第 {i + 1}/{rounds} 轮失败: {e}")
            if i < rounds - 1:
                time.sleep(3 * (i + 1))
    _log(f"    [translate] 标题翻译 {rounds} 轮全失败，保留原文")
    return polish.polish(text)


def translate_many(texts, sl="auto", tl="zh-CN", pace=1.0, log_every=25,
                   gate=True, fail_ratio=0.2, min_fail_lines=3):
    """批量翻译 + 中文校验门。

    - 主 Google，连续失败整体切 MyMemory；首轮未译出的段进入「二刷」用全链路重试。
    - gate=True 时：若「仍为英文」的段占比 >= fail_ratio 且数量 >= min_fail_lines，
      抛 RuntimeError 让 job 失败以便下次重跑（不静默出英文片）。
    - 返回与输入等长的已润色中文字幕列表（译前术语预替换 + 译后圈内习惯校正）。
    """
    total = len(texts)
    if total == 0:
        return []
    # 译前统一预替换 FC 术语（六维/比赛风格/角色/化学/战术口语 -> OAO 标准译名）
    texts = [polish.preprocess(t) for t in texts]
    out = []
    failed_idx = []
    google_dead = False
    consec = 0

    # ---- 首轮：LLM 主用（有 key 时），失败降级 Google→MyMemory ----
    for i, t in enumerate(texts, 1):
        res = None
        # LLM 优先（质量最好）
        if LLM_API_KEY:
            try:
                res = _llm_translate(t, sl, tl, retries=2)
                consec = 0
            except Exception as e:  # noqa: BLE001
                _log(f"    [translate] LLM 失败，降级 Google: {e}")
        if res is None and not google_dead:
            try:
                res = _google_translate(t, sl, tl, retries=2)
                consec = 0
            except Exception as e:  # noqa: BLE001
                _log(f"    [translate] Google 失败: {e}")
                consec += 1
                if consec >= 5:
                    google_dead = True
                    _log("    [translate] Google 连续失败，整体切换 MyMemory")
        if res is None:
            try:
                res = _mymemory_translate(t, sl, tl)
                consec = 0
            except Exception as e2:  # noqa: BLE001
                _log(f"    [translate] MyMemory 失败，暂留原文待二刷: {e2}")
        if res is None or _is_english(res):
            failed_idx.append(i - 1)
            if res is None:
                res = t  # 临时占位，二刷覆盖
        out.append(res)
        if i % log_every == 0:
            _log(f"    [translate] 首轮 {i}/{total} (待二刷 {len(failed_idx)} 段)")
        time.sleep(pace if not google_dead else 0.3)

    # ---- 二刷：对仍英文的段用全链路逐段重试 ----
    if failed_idx:
        _log(f"    [translate] 二刷重试 {len(failed_idx)} 段（Google→MyMemory）...")
        ok = 0
        for n, idx in enumerate(failed_idx, 1):
            t = texts[idx]
            try:
                out[idx] = _translate_one_chain(t, sl, tl)
                ok += 1
            except Exception as e:  # noqa: BLE001
                _log(f"    [translate] 二刷第 {n} 段仍失败，保留原文: {e}")
                out[idx] = t
            time.sleep(0.5)
        _log(f"    [translate] 二刷完成，成功补译 {ok}/{len(failed_idx)} 段")

    # ---- 中文占比校验门 ----
    still = [i for i in range(total) if _is_english(out[i])]
    ratio = len(still) / total
    if gate and ratio >= fail_ratio and len(still) >= min_fail_lines:
        raise RuntimeError(
            f"中文占比不足: {len(still)}/{total} 段仍为英文 (ratio={ratio:.0%})，"
            f"超过阈值 {fail_ratio:.0%}，job 失败以便下次 cron 重跑（不静默出英文片）")
    _log(f"    [translate] 完成 {total} 段，仍英文 {len(still)} 段（占比 {ratio:.0%}）")

    # 出口统一本地化润色（FC27/足球术语 + 口语化）
    return [polish.polish(x) for x in out]


if __name__ == "__main__":
    # 简易自检：用模拟端点验证校验门逻辑（不联网）
    sample = ["Hello world", "This is a test", "GOAL!", "2 - 1", "Thank you"]
    _orig_google, _orig_mm, _orig_chain = _google_translate, _mymemory_translate, _CHAIN
    _google_translate = lambda *a, **k: "Hola mundo"  # 模拟：译成非中文（西班牙语）
    _mymemory_translate = lambda *a, **k: "Bonjour"    # 模拟：译成非中文（法语）
    _CHAIN = (("mock_es", _google_translate), ("mock_fr", _mymemory_translate))
    # 模拟「全部英文」应触发 gate
    print("self-check: 译文仍英文时 gate 应抛异常")
    try:
        translate_many(sample, gate=True, fail_ratio=0.2)
        print("  -> 未抛异常（异常：应失败）")
        sys.exit(1)
    except RuntimeError as e:
        print(f"  -> 正确抛异常: {e}")
    finally:
        _google_translate, _mymemory_translate, _CHAIN = _orig_google, _orig_mm, _orig_chain
    print("self-check: PASS")
