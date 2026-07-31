# Intent-MGSV 服务器下载后处理与多人标注操作

本文档对应当前服务器目录：

```text
/data/users/ccw/intent_mgsv/repo/MGSV_preprocessor
```

目标流程：

```text
DouK 下载
-> 多片段听歌识曲
-> auto.py 预处理新增视频
-> 导入服务器数据库
-> 多人独立复标
-> 导出与合并
```

## 一、服务器环境

进入项目并激活 `mgsv_data`。推荐使用与本地一致的 Python 3.10，
不要把项目依赖安装到服务器 `base` 环境：

```bash
cd /data/users/ccw/intent_mgsv/repo/MGSV_preprocessor
conda create -n mgsv_data python=3.10 -y
conda activate mgsv_data
python -m pip install -r requirements_server.txt
```

如果 `mgsv_data` 已经存在，只执行 `conda activate mgsv_data`，不要重复创建。

服务器还需要系统命令：

```bash
ffmpeg -version
ffprobe -version
```

`auto.py` 还依赖 TensorFlow、BeatNet 和 TransNetV2。服务器首次运行
`auto.py` 前，应确认原 `mgsv_data` 环境中的这些依赖和模型权重已经迁移完成。

## 二、服务器路径配置

生成服务器自己的配置文件：

```bash
cp config/server.env.example config/server.env
```

确认其中路径与服务器一致，然后每次进入新终端执行：

```bash
source config/server.env
```

代码支持以下环境变量：

```text
MGSV_ROOT
MGSV_OUTPUT_DIR
MGSV_EXCEL
MGSV_DOUK_VOLUME
MGSV_SCAN_ROOT
MGSV_DOUK_DATA_EXCEL
MGSV_FULL_MUSIC_DIR
MGSV_FULL_SONGS_DIR
MGSV_ACR_TRACKING
MGSV_DB
ACRCLOUD_CONFIG_FILE
QQMUSIC_COOKIES_FILE
ARIA2C_EXE
MGSV_MUSIC_FALLBACK_SOURCES
```

`config/server.env`、`acrcloud_config.json`、QQ Cookie 和数据库都不应提交 Git。

## 三、自动识曲、下载、对齐与人工核验

完整流程分为机器预处理和人工核验两层：

```text
多窗口 ACRCloud 识曲
-> QQ 音乐搜索并校验歌名/歌手
-> 按 song_mid 或“标准化歌名+歌手”复用已有歌曲
-> 未命中时使用 yt-dlp + aria2c 下载
-> 针对当前视频重新计算 song_offset
-> 写入 ready_for_review / needs_review
-> 人工确认歌曲、offset 和 Genre
-> verified
```

复用只复用完整歌曲文件。每条视频始终单独重新对齐，不会复用其他视频的
`music_start` 或 `music_end`。

先确认服务器工具：

```bash
ffmpeg -version
ffprobe -version
aria2c --version
python -m yt_dlp --version
```

在 `config/server.env` 中配置：

```bash
export QQMUSIC_COOKIES_FILE="$MGSV_ROOT/qqmusic_cookies.txt"
export ARIA2C_EXE=aria2c
export MGSV_MUSIC_FALLBACK_SOURCES=youtube,bilibili
```

QQ Cookie 只会被复制到临时文件供下载进程使用，程序不会回写或裁剪原始
`qqmusic_cookies.txt`。

### 3.1 只识曲

先用 5 条数据验证：

```bash
python yt_dy_auto.py --limit 5 --retry-failed
```

确认输出正常后处理全部新增和旧版失败记录：

```bash
python yt_dy_auto.py --retry-failed
```

识曲默认行为：

- 跳过已经存在于 `MGSV_Master_Dataset.xlsx` 的视频；
- 在视频多个位置截取 15 秒音频；
- 单个候选达到高分时提前结束；
- 两个时间点识别为同一首歌时采用投票结果；
- 保存歌名、歌手、Genre 候选、置信度、采样位置和错误原因；
- 当前版本失败后，只有增加 `--retry-failed` 才会再次请求。

只有在明确需要重新识别历史数据时，才增加：

```bash
python yt_dy_auto.py --include-existing-dataset --retry-failed
```

识曲结果保存在：

```text
outputs/acrcloud_tracking.xlsx
```

### 3.2 一条命令完成机器预处理

先用 5 条验证自动下载和对齐：

```bash
python yt_dy_auto.py --limit 5 --retry-failed --download
```

确认正常后处理全部未完成记录：

```bash
python yt_dy_auto.py --retry-failed --download
```

`--download` 会继续处理追踪表中此前已经识曲成功、但尚未下载/对齐的记录。
因此先运行过不带 `--download` 的识曲命令，不需要重新识曲。

也可以单独重跑下载与对齐阶段：

```bash
python prepare_music_pipeline.py --retry-failed
```

自动结果保存在数据库 `music_preparations` 表，常见状态为：

```text
ready_for_review  # 对齐分较高，仍需人工最终确认
needs_review      # 对齐可用但置信度较低
search_failed     # 未找到可信候选
download_failed   # 候选存在但下载失败
alignment_failed  # 下载成功但未得到可靠 offset
needs_manual      # 人工判定歌曲错误
needs_realign     # 人工判定歌曲正确但 offset 错误
verified          # 已人工确认
```

### 3.3 启动歌曲与对齐核验页面

首次处理一批新数据时，应先完成第四、第五节的 `auto.py` 和数据库增量导入，
再启动核验页面。这样 owner annotation 已经带有分镜和基础字段，音乐核验只更新
歌曲、offset 与 Genre。

```bash
python -m intent_mgsv_pipeline.server.music_review_app \
  --db "$MGSV_DB" \
  --owner-id owner \
  --host 0.0.0.0 \
  --port 7862
```

页面同时展示原视频和按自动 offset 裁出的完整歌曲片段。核验者需要确认歌曲、
片段位置和 Genre；必要时直接修改 offset 或 Genre，然后点击
“歌曲和对齐均正确”。

只有完成最终核验，系统才会写入：

```text
song_verified=Yes
music_start=最终 offset
music_end=offset + 当前视频中的有效音乐时长
```

未点击最终确认的记录不会对第二标注者开放。

## 四、运行新增视频预处理

识曲完成后运行：

```bash
python auto.py
```

`auto.py` 现在从服务器环境变量读取 DouK 下载目录、元数据表、输出目录和
ACRCloud 追踪表，不再写死 Windows 路径。

新生成的记录会带入：

```text
song_title
song_artist
acr_confidence
recognition_votes
recognition_samples
recognition_sample_summary
recognition_error
recognition_version
```

运行前应备份：

```text
outputs/MGSV_Master_Dataset.xlsx
```

同一时间只能有一个 `auto.py` 进程写主表。

## 五、导入服务器数据库

第一次初始化数据库时：

```bash
python intent_mgsv_pipeline/server/import_excel_to_db.py \
  --input outputs/MGSV_Master_Dataset.xlsx \
  --db "$MGSV_DB" \
  --annotator-id owner \
  --replace
```

`--replace` 只允许在首次迁移、且数据库中还没有正式多人标注或自动歌曲准备结果时
使用；它会清空数据库中的旧记录。正式数据库严禁再次使用。

数据库开始正式多人标注后，后续更新不能再使用 `--replace`：

```bash
python intent_mgsv_pipeline/server/import_excel_to_db.py \
  --input outputs/MGSV_Master_Dataset.xlsx \
  --db "$MGSV_DB" \
  --annotator-id owner
```

正式数据库需要定期备份。禁止用本地旧数据库覆盖服务器数据库。

默认增量导入是“只新增、不覆盖”：已有的 owner annotation、音乐核验结果和标注
进度都会保留。只有经过备份并明确需要用 Excel 覆盖数据库时，才允许显式增加：

```bash
--update-existing
```

正常多人标注流程不要使用这个参数。

## 六、启动多人复标页面

启动服务器网页：

```bash
python -m intent_mgsv_pipeline.server.peer_annotation_app \
  --db "$MGSV_DB" \
  --host 0.0.0.0 \
  --port 7860
```

推荐放在 `tmux` 中运行：

```bash
tmux new -s mgsv-annotation
```

页面要求每位同学输入自己的稳定 ID，例如：

```text
annotator_b
annotator_c
```

不要把 `owner` 作为第二标注者 ID。

当前复标页面只要求重新标注：

```text
emotion
style
usage_scene
seg_scores_3
seg_scores_5
```

以下字段继承 owner 数据，不要求第二标注者重新填写：

```text
sync_level
shot_points_3
shot_points_5
vocal_presence
genre
song_verified
```

同一个视频可以被不同标注者同时领取。每位标注者的数据保存到独立数据库记录，
不会覆盖其他人的结果。刷新页面或重新登录时会继续该标注者尚未完成的当前样本。

分段评分未填完整，后端会拒绝完成并停留在当前视频。

页面会显示当前标注者自己的 `已完成/可领取总数`。第二标注者只能领取
`owner.status=completed` 且 `owner.song_verified=Yes` 的样本，所以机器任务刚结束、
但尚未人工核验的歌曲不会混入复标任务。

## 七、导出多人标注共识

直接从服务器数据库生成 owner 与第二标注者的共识结果：

```bash
python -m intent_mgsv_pipeline.server.export_consensus \
  --db "$MGSV_DB" \
  --owner-id owner \
  --peer-id annotator_b \
  --out outputs/server/MGSV_Master_Dataset.consensus.xlsx
```

只合并双方都已经 `completed` 的视频。合并规则：

```text
emotion/style/usage_scene -> 取并集
seg_scores_3/seg_scores_5 -> 对应分段取平均
vocal_presence/genre      -> 保留 owner
sync_level/shot_points    -> 保留 owner
```

同时生成分歧表和 JSON 汇总：

```text
MGSV_Master_Dataset.consensus.disagreements.xlsx
MGSV_Master_Dataset.consensus.summary.json
```

## 八、本地改代码后更新服务器

代码通过 GitHub 更新，数据不通过 Git。

本地建议为每次较大修改创建分支：

```bash
git switch -c codex/server-annotation-v1
git add auto.py yt_dy_auto.py requirements_server.txt config \
  intent_mgsv_pipeline
git commit -m "Add server recognition and peer annotation"
git push -u origin codex/server-annotation-v1
```

合并到 GitHub `main` 后，服务器执行：

```bash
cd /data/users/ccw/intent_mgsv/repo/MGSV_preprocessor
git status --short
git pull --ff-only origin main
source config/server.env
conda activate mgsv_data
python -m unittest \
  intent_mgsv_pipeline.validation.test_music_recognition \
  intent_mgsv_pipeline.validation.test_music_preparation \
  intent_mgsv_pipeline.validation.test_peer_annotation \
  intent_mgsv_pipeline.validation.test_server_import
```

测试通过后再重启标注服务。

不要使用 Xftp 覆盖服务器上的以下内容：

```text
outputs/
DouK-Source/Volume/
config/server.env
acrcloud_config.json
qqmusic_cookies.txt
```

如果服务器上的受 Git 管理代码有临时修改，应先执行 `git status` 和 `git diff`，
明确这些修改的来源，不能直接覆盖或丢弃。
