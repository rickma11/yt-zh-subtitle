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
