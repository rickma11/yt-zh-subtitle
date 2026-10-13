# -*- coding: utf-8 -*-
"""探针 v4-A：验证 downsub HTTP API 从 GitHub Actions IP 可用（无需浏览器）。
- get-info.downsub.com/{enc} 拿视频信息+字幕轨
- subtitle.downsub.com/srt/{enc} 直链下载 SRT（en_auto + zh-Hans）
"""
import json, base64, hashlib, os, sys, urllib.request

PASS = "zthxw34cdp6wfyxmpad38v52t3hsz6c5"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def evp(password: bytes, salt: bytes, klen=32, ilen=16):
    d, prev = b"", b""
    while len(d) < klen + ilen:
        prev = hashlib.md5(prev + password + salt).digest()
        d += prev
    return d[:klen], d[klen:klen + ilen]


def encode(obj, key=PASS) -> str:
    salt = os.urandom(8)
    k, iv = evp(key.encode(), salt)
    pt = json.dumps(obj, ensure_ascii=False).encode()
    pad = 16 - len(pt) % 16
    pt += bytes([pad]) * pad
    from Cryptodome.Cipher import AES
    ct = AES.new(k, AES.MODE_CBC, iv).encrypt(pt)
    ser = json.dumps({"ct": base64.b64encode(ct).decode(),
                      "iv": iv.hex(), "s": salt.hex()}, separators=(",", ":"))
    return base64.b64encode(ser.encode()).decode().replace("+", "-").replace("/", "_").replace("=", "")


def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/json, text/plain, */*",
        "Referer": "https://downsub.com/", "Origin": "https://downsub.com"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read()


def main():
    vid = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("VID", "KUf0x6FG7p0")
    print(f"[v4-A] video_id = {vid}")
    enc = encode(vid)
    st, ct, body = fetch(f"https://get-info.downsub.com/{enc}")
    data = json.loads(body.decode("utf-8"))
    print(f"[v4-A] get-info: status={st} state={data.get('state')} "
          f"title={str(data.get('title'))[:60]} duration={data.get('duration')}")
    subs = data.get("subtitles", [])
    autotrans = data.get("subtitlesAutoTrans", [])
    print(f"[v4-A] 人工/自动生成轨 {len(subs)} 条；自动翻译轨 {len(autotrans)} 条")
    codes = [t["code"] for t in subs]
    print(f"[v4-A] 自动生成轨 codes: {codes}")

    results = {"video_id": vid, "state": data.get("state"), "downloads": {}}
    targets = [("en_auto", subs), ("zh-Hans", autotrans)]
    for code, lst in targets:
        track = next((t for t in lst if t["code"] == code), None)
        if not track:
            print(f"[v4-A] {code}: 轨道不存在")
            results["downloads"][code] = "MISSING"
            continue
        url = f"https://subtitle.downsub.com/srt/{track['url']}"
        try:
            st2, ct2, b2 = fetch(url)
            text = b2.decode("utf-8", "replace")
            fn = f"v4_{code}.srt"
            open(fn, "w", encoding="utf-8").write(text)
            first = text.split("\n")[2] if len(text.split("\n")) > 2 else ""
            print(f"[v4-A] {code}: status={st2} bytes={len(b2)} 首行字幕: {first[:60]}")
            results["downloads"][code] = {"status": st2, "bytes": len(b2)}
        except Exception as e:
            print(f"[v4-A] {code}: ERROR {e}")
            results["downloads"][code] = f"ERROR: {e}"
    json.dump(results, open("v4_downsub_api_results.json", "w"), ensure_ascii=False, indent=1)
    print("[v4-A] DONE")


if __name__ == "__main__":
    main()
