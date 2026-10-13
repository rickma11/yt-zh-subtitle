#!/usr/bin/env python3
# 上传文件到 123云盘（国内网盘），并返回分享直链。
# 需要环境变量 PAN123_CLIENT_ID / PAN123_CLIENT_SECRET（123云盘开放平台应用凭证）。
# 设计原则：任何失败都打印空字符串并退出，让 workflow 优雅跳过，绝不阻断主流程。
import os, sys

try:
    import requests
except ImportError:
    os.system("pip install --quiet requests")
    import requests

BASE = "https://open-api.123pan.com"


def log(*a):
    print("[pan123]", *a, file=sys.stderr)


def get_token(cid, sec):
    # 先试 POST JSON，失败回退 GET query（不同版本开放平台接口略有差异）
    try:
        r = requests.post(f"{BASE}/api/v1/access_token",
                          json={"clientID": cid, "clientSecret": sec}, timeout=30)
        r.raise_for_status()
        return r.json()["data"]["accessToken"]
    except Exception as e:
        log("POST token 失败，回退 GET:", e)
    try:
        r = requests.get(f"{BASE}/api/v1/access_token",
                         params={"clientID": cid, "clientSecret": sec}, timeout=30)
        r.raise_for_status()
        return r.json()["data"]["accessToken"]
    except Exception as e:
        log("GET token 也失败:", e)
        return None


def main():
    cid = os.environ.get("PAN123_CLIENT_ID")
    sec = os.environ.get("PAN123_CLIENT_SECRET")
    if not cid or not sec:
        log("缺少 PAN123_CLIENT_ID / PAN123_CLIENT_SECRET，跳过")
        return
    if len(sys.argv) < 2:
        log("用法: pan123_upload.py <文件路径>")
        return
    fpath = sys.argv[1]
    fname = os.path.basename(fpath)

    tok = get_token(cid, sec)
    if not tok:
        return

    # 上传（使用 pan123 SDK 处理分片/断点等细节）
    try:
        from pan123 import Pan123
        api = Pan123(cid, sec)
        api.refresh_access_token()
        file_id = api.upload(fpath, fname, 0)
        if isinstance(file_id, dict):
            file_id = file_id.get("fileId") or file_id.get("id")
        log("上传完成 file_id=", file_id)
    except Exception as e:
        log("SDK 上传失败:", e)
        return
    if not file_id:
        log("upload 返回空 file_id")
        return

    # 创建分享，拿直链
    try:
        h = {"Authorization": f"Bearer {tok}", "Platform": "open_platform",
             "Content-Type": "application/json"}
        r = requests.post(f"{BASE}/api/v1/share", headers=h, json={
            "fileIdList": [int(file_id)],
            "expireType": 0,
            "shareLinkType": 1,
            "shareName": "yt-zh-subtitle",
        }, timeout=30)
        r.raise_for_status()
        data = r.json().get("data", {})
        url = data.get("shareUrl") or ""
        log("分享直链:", url)
        print(url)
    except Exception as e:
        log("创建分享失败（文件已上传到你的123云盘，可手动分享）:", e)


if __name__ == "__main__":
    main()
