#!/usr/bin/env python3
"""定期清理旧的自动发布 Release（含 git tag），避免仓库/Release 资产无限增长。

规则：
- 只清理 tag 形如 `auto-YYYYMMDD-<video_id>` 的 release（本流水线发布格式，见
  process_v2.yml: TAG="auto-${DATE}-${VIDEO_ID}"），其他 tag 一律不碰。
- tag 日期早于 (今天 - DAYS) 的 release 删除（release + git tag 一起删）。
- 保底：始终保留最近 KEEP_MIN 个可解析的 release，防止极端情况下误删光。
- 严禁清理 video_log.json / state.json：它们是去重维护表，删了会导致重复抓取。
  本脚本只操作 Release/tag，不修改仓库任何文件。

环境变量：
- DAYS     清理早于 N 天的 release（默认 3）
- DRY_RUN  true/1 时只列计划不真删（默认 false）
- KEEP_MIN 至少保留最近几个 release（默认 3）
"""
import json
import os
import re
import subprocess
import sys
from datetime import date, timedelta

TAG_RE = re.compile(r"^auto-(\d{8})-([\w-]{6,})$")
DAYS = int(os.environ.get("DAYS", "3"))
DRY = os.environ.get("DRY_RUN", "false").strip().lower() in ("1", "true", "yes")
KEEP_MIN = int(os.environ.get("KEEP_MIN", "3"))


def parse_tag_date(tag):
    """'auto-20261016-JlxwBkSW2zM' -> date(2026,10,16)；不匹配返回 None。"""
    m = TAG_RE.match(tag or "")
    if not m:
        return None
    y, mo, d = int(m.group(1)[:4]), int(m.group(1)[4:6]), int(m.group(1)[6:8])
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def plan_cleanup(tags, today, days=DAYS, keep_min=KEEP_MIN):
    """纯函数：给定 tag 列表，返回 (to_delete, to_keep, skipped)。

    - to_delete: 早于 cutoff 且不在"保底保留最近 keep_min 个"范围内的 tag
    - to_keep  : 保留的 tag
    - skipped  : 无法解析日期/不匹配发布格式的 tag（保守不动）
    """
    cutoff = today - timedelta(days=days)
    parsed = []   # (tag_date, tag)
    skipped = []
    for t in tags:
        d = parse_tag_date(t)
        if d is None:
            skipped.append(t)
        else:
            parsed.append((d, t))
    # 日期降序（新的在前）；同日期按 tag 名倒序，保证"最近"判定稳定
    parsed.sort(key=lambda x: (x[0], x[1]), reverse=True)
    keep_recent = {t for _, t in parsed[:keep_min]}
    to_delete, to_keep = [], []
    for d, t in parsed:
        if d < cutoff and t not in keep_recent:
            to_delete.append(t)
        else:
            to_keep.append(t)
    return to_delete, to_keep, skipped


def gh(args):
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[cleanup] gh {' '.join(args)} 失败: {r.stderr.strip()[:300]}")
        sys.exit(1)
    return r.stdout


def main():
    raw = gh(["release", "list", "--limit", "200", "--json", "tagName,createdAt"])
    releases = json.loads(raw or "[]")
    tags = [r["tagName"] for r in releases]
    print(f"[cleanup] 当前共 {len(tags)} 个 release；DAYS={DAYS} KEEP_MIN={KEEP_MIN} DRY_RUN={DRY}")
    print(f"[cleanup] cutoff = {date.today() - timedelta(days=DAYS)}（早于该日期的清理）")

    to_delete, to_keep, skipped = plan_cleanup(tags, date.today())

    if skipped:
        print(f"[cleanup] 跳过 {len(skipped)} 个非自动发布/不可解析 tag（不碰）: {skipped}")

    if not to_delete:
        print("[cleanup] 没有超过保留期的 release，无需清理。")
    for t in to_delete:
        if DRY:
            print(f"[cleanup][dry-run] 将删除 {t}")
        else:
            # --cleanup-tag 同时删除 release 与其 git tag
            gh(["release", "delete", t, "--cleanup-tag", "--yes"])
            print(f"[cleanup] 已删除 {t}（release + git tag）")

    print(f"[cleanup] 完成：删除 {0 if DRY else len(to_delete)}/{len(to_delete)} 个，"
          f"保留 {len(to_keep)} 个（含保底）。")
    if DRY and to_delete:
        print("[cleanup][dry-run] 以上为预演，未真实删除。")
    # 清理成功不应让 job 失败；列表如下仅供核对
    for t in to_keep:
        print(f"[cleanup]   保留: {t}")


if __name__ == "__main__":
    main()
