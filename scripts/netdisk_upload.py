#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
netdisk_upload.py —— 隔离测试：仅把一个【小文件】上传到国内网盘，完全不碰视频下载/合成流水线。
目的是用 ~30 秒验证「能不能上传」，而不是花 56 分钟重跑整条 pipeline。

用法:
  python netdisk_upload.py <provider> <filepath>
    provider:  quark | baidu | aliyun
    filepath:  要上传的本地文件（测试用 1KB 即可）

凭证从环境变量读取（由 GitHub Secret 注入，不进代码）:
  QUARK_COOKIE         夸克网盘登录 cookie（从浏览器 devtools 复制）
  BAIDU_BDUSS          百度网盘登录 BDUSS（从浏览器 cookie 复制）
  ALIYUN_REFRESH_TOKEN 阿里云盘 refresh_token（从浏览器/App 抓取）

说明: 三家网盘 API 均为非官方或受限接口，首次跑可能需按真实返回微调。
      本脚本只负责「尽力上传 + 把原始错误返回打印出来」，便于迭代。
"""
import os
import sys
import json
import hashlib
import urllib.request
import urllib.error

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _req(url, method="GET", data=None, headers=None, timeout=60, raw=False):
    headers = dict(UA, **(headers or {}))
    body = None
    if data is not None:
        if isinstance(data, (bytes, bytearray)):
            body = data
            headers.setdefault("Content-Type", "application/octet-stream")
        else:
            body = json.dumps(data).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(), r.status, dict(r.getheaders())
    except urllib.error.HTTPError as e:
        return e.read(), e.code, dict(e.headers)


def quark_upload(cookie, filepath):
    """夸克网盘：非官方 API。流程 = 建上传任务 -> 取预签名URL -> PUT文件 -> 提交。"""
    name = os.path.basename(filepath)
    data = open(filepath, "rb").read()
    size = len(data)
    md5 = hashlib.md5(data).hexdigest()
    headers = {"Cookie": cookie, "Content-Type": "application/json"}
    # 1) 建上传任务
    url = "https://drive-pc.quark.cn/1/clouddrive/file/uploadTask"
    payload = {
        "dir": "/", "filename": name, "size": size, "md5": md5,
        "type": "file", "objFrom": "pc", "uploadType": "NORMAL",
    }
    body, code, _ = _req(url, "POST", payload, headers)
    if code != 200:
        return False, f"建任务失败 HTTP {code}: {body[:300]}"
    j = json.loads(body)
    if j.get("status") != 200 and j.get("code") != 0:
        return False, f"建任务返回异常: {body[:300]}"
    task = j.get("data", {})
    upload_url = task.get("uploadUrl") or (task.get("upload_task_info", {}) or {}).get("uploadUrl")
    if not upload_url:
        return False, f"未拿到预签名上传URL: {body[:300]}"
    # 2) PUT 文件到预签名URL
    _, code2, _ = _req(upload_url, "PUT", data, {"Content-Type": "application/octet-stream"})
    if code2 >= 400:
        return False, f"PUT 文件失败 HTTP {code2}"
    # 3) 提交（部分接口需要 commit，有的直接成功）
    commit = task.get("commitUrl")
    if commit:
        _req(commit, "POST", None, headers)
    return True, f"夸克上传成功（任务ID={task.get('taskId','?')}）"


def baidu_upload(bduss, filepath):
    """百度网盘：个人 API（BDUSS cookie）。流程 = precreate -> 上传分片 -> create。"""
    if not bduss.startswith("BDUSS="):
        bduss = "BDUSS=" + bduss
    headers = {"Cookie": bduss, "Content-Type": "application/json"}
    name = os.path.basename(filepath)
    data = open(filepath, "rb").read()
    size = len(data)
    md5 = hashlib.md5(data).hexdigest()
    path = "/" + name  # 上传到网盘根目录
    # 1) precreate
    url = "https://pan.baidu.com/rest/2.0/xpan/file?method=precreate"
    payload = {
        "path": path, "size": size, "isdir": 0, "autoinit": 1,
        "block_list": json.dumps([md5]), "rtype": "3",
    }
    body, code, _ = _req(url, "POST", payload, headers)
    if code != 200:
        return False, f"precreate HTTP {code}: {body[:300]}"
    j = json.loads(body)
    if j.get("errno", 0) != 0:
        return False, f"precreate errno={j.get('errno')}: {body[:300]}"
    uploadid = j.get("uploadid")
    # 2) 上传分片（tmpfile 单分片）
    up_url = (f"https://c.pcs.baidu.com/rest/2.0/pcs/file?method=upload&type=tmpfile"
              f"&path={path}&uploadid={uploadid}&partseq=0")
    _, code2, _ = _req(up_url, "POST", data, {"Cookie": bduss, "Content-Type": "application/octet-stream"})
    if code2 >= 400:
        return False, f"上传分片 HTTP {code2}"
    # 3) create 提交
    url2 = "https://pan.baidu.com/rest/2.0/xpan/file?method=create"
    payload2 = {
        "path": path, "size": size, "isdir": 0, "uploadid": uploadid,
        "block_list": json.dumps([md5]), "rtype": "3",
    }
    body3, code3, _ = _req(url2, "POST", payload2, headers)
    if code3 != 200:
        return False, f"create HTTP {code3}: {body3[:300]}"
    j3 = json.loads(body3)
    if j3.get("errno", 0) != 0:
        return False, f"create errno={j3.get('errno')}: {body3[:300]}"
    return True, f"百度网盘上传成功: {j3.get('fs_id','?')}"


def aliyun_upload(refresh_token, filepath):
    """阿里云盘：需 refresh_token 换 access_token，再 create -> PUT -> complete。"""
    # 1) 换 token
    body, code, _ = _req("https://auth.aliyundrive.com/v2/account/token",
                         "POST", {"grant_type": "refresh_token", "refresh_token": refresh_token})
    if code != 200:
        return False, f"换 token HTTP {code}: {body[:300]}"
    t = json.loads(body)
    at = t.get("access_token")
    if not at:
        return False, f"无 access_token: {body[:300]}"
    auth = {"Authorization": f"Bearer {at}"}
    # 2) drive_id
    di, _, _ = _req("https://user.aliyundrive.com/v2/user/getDriveInfo", "POST", {}, auth)
    drive_id = json.loads(di).get("default_drive_id")
    # 3) 文件 hash + 上传
    data = open(filepath, "rb").read()
    size = len(data)
    sha1 = hashlib.sha1(data).hexdigest()
    pre_hash = hashlib.sha1(data[:min(size, 1024 * 1024)]).hexdigest()
    url = "https://api.aliyundrive.com/adrive/v2/file/createWithFolders"
    payload = {
        "drive_id": drive_id, "parent_file_id": "root",
        "name": os.path.basename(filepath), "type": "file", "size": size,
        "pre_hash": pre_hash, "content_hash": sha1, "content_hash_name": "sha1",
        "check_name_mode": "auto_rename",
    }
    cr, code4, _ = _req(url, "POST", payload, auth)
    if code4 != 200:
        return False, f"create HTTP {code4}: {cr[:300]}"
    c = json.loads(cr)
    if c.get("exist") or c.get("rapid_upload"):
        return True, f"阿里云盘秒传/已存在: {c.get('file_id')}"
    up = (c.get("part_info_list") or [{}])[0].get("upload_url")
    if not up:
        return False, f"无 upload_url: {cr[:300]}"
    _, code5, _ = _req(up, "PUT", data, {"Content-Type": "application/octet-stream"})
    if code5 >= 400:
        return False, f"PUT 文件 HTTP {code5}"
    comp, _, _ = _req("https://api.aliyundrive.com/v2/file/complete",
                      "POST", {"drive_id": drive_id, "file_id": c["file_id"],
                               "upload_id": c["upload_id"],
                               "part_info_list": [{"part_number": 1}]}, auth)
    return True, f"阿里云盘上传成功: {c.get('file_id')}"


def main():
    if len(sys.argv) < 3:
        print(json.dumps({"ok": False, "msg": "用法: netdisk_upload.py <quark|baidu|aliyun> <file>"}))
        sys.exit(2)
    provider, filepath = sys.argv[1], sys.argv[2]
    creds = {
        "quark": os.environ.get("QUARK_COOKIE", ""),
        "baidu": os.environ.get("BAIDU_BDUSS", ""),
        "aliyun": os.environ.get("ALIYUN_REFRESH_TOKEN", ""),
    }
    cred = creds.get(provider, "")
    if not cred:
        print(json.dumps({"ok": False, "msg": f"缺少 {provider} 凭证（环境变量未注入 Secret）"}))
        sys.exit(3)
    try:
        if provider == "quark":
            ok, msg = quark_upload(cred, filepath)
        elif provider == "baidu":
            ok, msg = baidu_upload(cred, filepath)
        elif provider == "aliyun":
            ok, msg = aliyun_upload(cred, filepath)
        else:
            ok, msg = False, f"未知 provider: {provider}"
    except Exception as e:
        ok, msg = False, f"异常: {e}"
    print(json.dumps({"ok": bool(ok), "msg": msg}, ensure_ascii=False))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
