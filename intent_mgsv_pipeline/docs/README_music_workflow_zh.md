# 音乐识别、下载、对齐与人工核验操作说明

## 1. 当前流水线

```text
DouK 下载视频
-> yt_dy_auto.py 多窗口 ACRCloud 识曲
-> QQ 音乐检索 / 下载或复用完整歌曲
-> 多窗口 Chroma 对齐
-> 7862 音乐核验（歌曲、offset、Genre）
-> 7860 主标注
```

`auto.py` 负责视频属性、BeatNet 节拍、TransNetV2 分镜和初始 Excel。它会优先
读取 SQLite 中已有的音乐准备结果，不再无条件重复计算另一套 offset：

1. 已人工核验：使用 `song_reviews.corrected_offset`。
2. 待人工核验：使用 `music_preparations.song_offset`。
3. 数据库没有可用音乐准备结果：才回退到旧版 subsequence-DTW。

Excel 中的 `alignment_source` 会标明 `human_verified`、`music_preparation` 或
`legacy_dtw_fallback`。

## 2. 自动识曲

`yt_dy_auto.py` 最多从视频的多个位置截取 15 秒音频。单窗口置信度足够高，或同一
歌曲在两个窗口获得一致结果时，才接受识曲答案。结果写入：

```text
outputs/acrcloud_tracking.xlsx
```

## 3. 自动完整歌曲准备与对齐

识曲成功后，系统按歌名和歌手搜索 QQ 音乐，最多尝试三个可信候选。已下载过的
同一 `song_mid` 或同名同歌手歌曲只复用文件，但每条视频都会独立重新计算 offset。

默认对齐从视频多个位置取窗口，以 Chroma 互相关寻找完整歌曲中的位置，再按多个
窗口对同一 offset 的一致程度投票。结果写入 SQLite 的 `music_preparations`。

## 4. 状态与去向

| 状态 | 含义 | 下一步 |
|---|---|---|
| `recognition_failed` | ACRCloud 未识别 | 重跑识曲，仍失败则人工识曲 |
| `search_failed` | 找不到可信歌曲候选 | 重跑或人工提供歌曲 |
| `download_failed` | 找到候选但下载失败 | 检查 Cookie/网络后重跑 |
| `alignment_failed` | 有歌曲但未找到可靠 offset | 重跑或人工调整 |
| `ready_for_review` | 自动对齐较可信 | 进入 7862 最终核验 |
| `needs_review` | 自动结果置信度较低 | 在 7862 仔细试听 |
| `needs_manual` | 人工判定歌曲错误 | 重跑后排除当前 QQ 候选 |
| `needs_realign` | 歌曲正确但对齐被拒绝 | 重跑对齐后回到 7862 |
| `verified` | 歌曲、offset、Genre 已确认 | 进入 7860 主标注 |

核验失败不会删除视频或歌曲文件。完整歌曲通常仍在 `outputs/full_songs/`，映射和
失败原因保留在 `music_preparations`、`songs`、`song_reviews` 和 `events` 表中。

## 5. 每批数据的推荐操作

```bash
source config/server.env
conda activate mgsv_data

python scripts/run_server_preprocessing.py --dry-run
python scripts/run_server_preprocessing.py
python scripts/audit_music_pipeline.py --db "$MGSV_DB" --limit 50
```

访问 `7862`：

- 歌曲正确且 offset 正确：填写 Genre 后确认。
- 歌曲正确但 offset 不准：先选“精细（推荐）”重新自动对齐；仍不准时直接修改
  offset，刷新试听，确认无误后提交。
- 歌曲错误：填写备注并点“歌曲错误”。然后运行：

```bash
python prepare_music_pipeline.py \
  --retry-failed \
  --include-existing-dataset
```

系统会排除刚被拒绝的 QQ `song_mid` 并尝试下一候选。

这里必须带 `--include-existing-dataset`：人工核验失败的视频通常已经进入主 Excel，
不加该参数会被当作历史数据跳过。

- 对齐点“稍后处理”后会成为 `needs_realign`，同样运行上述命令重新准备。

## 6. 当前尚未补齐的人工入口

服务器版 7862 当前还没有旧本地工具中的“粘贴 QQ 分享链接 / song_mid / 本地完整
歌曲并替换候选”功能。如果 `needs_manual` 在自动重试后仍失败，数据不会丢失，但
会停留在人工队列之外。下一项功能应当是在 7862 增加人工换歌入口，换歌后对当前
视频重新对齐并回到核验队列。不要直接修改正式 SQLite。
