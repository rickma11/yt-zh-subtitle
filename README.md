# yt-zh-subtitle

每日监控 YouTube 频道更新 → 下载视频 → 中文字幕（三层降级）→ ffmpeg 硬字幕烧录 → 发布 GitHub Release → Server酱微信推送下载链接。

跑在 GitHub Actions 上（干净 IP，不受本机网络封锁影响），无需自己的服务器。

## 工作流程

```
cron (UTC 19-22 点 = 北京时间 03:00-05:00，每小时)
  └─ monitor.py   解析频道 -> 拉 RSS -> 与 state.json 比对，找出新视频
       └─ run_batch.py
            └─ process.py（每个视频）
                 ├─ T1 zh-direct  直接下载 YouTube 中文字幕（zh-Hans 等）
                 ├─ T2 translated 只有源语言字幕（优先 en）→ Google 翻译
                 └─ T3 whisper    无任何字幕 → 本地 faster-whisper 转写 → 翻译
                 ├─ 下载视频（≤1080p，多 player_client 回退）
                 └─ ffmpeg 烧录中文硬字幕（Noto Sans CJK SC）
            ├─ gh release create（mp4 + srt 作为资产，公开可下载）
            ├─ Server酱 推送（标题 + 下载链接）
            └─ state.json 标记已处理（失败不标记，下轮自动重试）
```

## 配置

### config.json

| 字段 | 说明 |
|---|---|
| `channels` | 监控的频道列表，支持 `@handle`、`UC` 开头频道 ID 或完整 URL |
| `translate_target` | 翻译目标语言，默认 `zh-CN` |
| `whisper_model` | T3 转写模型，`small`（默认，均衡）/ `base`（更快）/ `medium`（更准但慢） |
| `max_height` | 视频下载最高分辨率，默认 `1080` |
| `keep_releases` | Release 保留个数，超出自动删除（控存储），默认 `5` |

### Secrets（仓库 Settings → Secrets and variables → Actions）

| Secret | 必需 | 说明 |
|---|---|---|
| `SERVERCHAN_KEY` | 推荐 | Server酱 SendKey（sctapi.ftqq.com 注册）。不配则跳过微信推送，Release 仍正常生成 |
| `YT_COOKIES` | 可选 | YouTube cookies.txt 内容。下载被机器人验证挡住时用 |

## 手动触发 / 测试

Actions 页 → `yt-zh-subtitle` → Run workflow：
- 留空 `video_url`：走正常监控逻辑（首次运行每个频道只处理最新 1 期）
- 填 `video_url`：直接处理指定视频，忽略监控结果

本地跑（需要 Python 3.12+、ffmpeg、fonts-noto-cjk）：

```bash
pip install -r requirements.txt
python monitor.py                 # 生成 new_videos.json
python run_batch.py               # 处理 + Release + 推送
```

## 产物位置

- 每个视频一个 GitHub Release：`https://github.com/<owner>/yt-zh-subtitle/releases/tag/v-<video_id>`
- 资产：`YYYYMMDD_频道_标题.mp4`（中文字幕已烧录进画面）+ 同名 `.zh.srt`（字幕文件）

## 说明

- GitHub Actions 的 IP 与 YouTube 字幕接口的连通性会变化：能直取中文字幕时走 T1（最快最准），不行时自动降级，最终一定有中文字幕成品。
- 免费额度：公开仓库 Actions 不限时长；Release 存储超 1GB 会计费，`keep_releases` 会自动清理旧片。
- 失败不静默：处理失败的视频不写 state，下一轮 cron 自动重试；日志在 Actions run 里可查。

## ⚠️ 实测评估：GitHub Actions IP 被 YouTube bot 墙（2026-10-12）

对 `rickma11/yt-zh-subtitle` 的首次真实运行评估结论：

| 测试项 | 结果 |
|---|---|
| monitor 频道监控（RSS） | ✅ 正常，能发现新视频 |
| yt-dlp 播放器 API（取格式/字幕轨） | ❌ `Sign in to confirm you're not a bot`（Azure 数据中心 IP 被墙） |
| player_client 轮换（web/safari/mweb/android/tv 等 8 个） | ❌ 全部被拦（见第二次运行日志） |
| 直接下中文字幕（T1） | 无法评估——播放器 API 被墙，连字幕轨列表都取不到 |

**结论：GitHub 托管 runner 的 IP 被 YouTube 拦截，必须提供 cookies 才能下载。** yt-dlp 官方报错也明确要求 `--cookies`。

### 解决方案：配置 YT_COOKIES（必须）

1. Edge/Chrome 安装扩展 **「Get cookies.txt LOCALLY」**（Chrome 应用店可搜到）
2. 登录 YouTube（<https://www.youtube.com>），点扩展图标 → 导出 `cookies.txt`（Netscape 格式）
3. 用记事本打开，**全选复制内容**
4. 仓库 → Settings → Secrets and variables → Actions → New repository secret：
   - Name: `YT_COOKIES`
   - Value: 粘贴刚才复制的全部内容
5. 重新 Run workflow 即可

**维护**：YouTube 会话 cookies 一般有效 1-3 个月。当 Actions 日志再次出现 `Sign in to confirm you're not a bot` 时，重复上述导出即可（workflow 每小时会自动重试，换好 cookies 后无需其它操作）。

### 备选方案（不想用 cookies 时）

- **自托管 runner**：把本机注册为 Actions runner（`runs-on: [self-hosted, win]`），用本机 IP + 代理下载（本机代理出口 IP 实测可正常下载视频，仅字幕接口需 cookies），Whisper 兜底不受影响。
- **本地定时任务**：在本机直接跑 `monitor.py` + `run_batch.py`（Whisper 转写已实测可完成全片），成品再上传 GitHub Release 发链接。
