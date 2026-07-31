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
```

`config/server.env`、`acrcloud_config.json`、QQ Cookie 和数据库都不应提交 Git。

## 三、多片段听歌识曲

新识曲程序默认只生成歌曲候选，不自动下载完整歌曲。

先用 5 条数据验证：

```bash
python yt_dy_auto.py --limit 5 --retry-failed
```

确认输出正常后处理全部新增和旧版失败记录：

```bash
python yt_dy_auto.py --retry-failed
```

默认行为：

- 跳过已经存在于 `MGSV_Master_Dataset.xlsx` 的视频；
- 在视频多个位置截取 15 秒音频；
- 单个候选达到高分时提前结束；
- 两个时间点识别为同一首歌时采用投票结果；
- 保存歌名、歌手、置信度、采样位置和错误原因；
- 旧算法留下的失败记录会自动使用新算法重试；
- 当前版本失败后，只有增加 `--retry-failed` 才会再次请求，避免浪费额度。

只有在明确需要重新识别历史数据时，才增加：

```bash
python yt_dy_auto.py --include-existing-dataset --retry-failed
```

结果保存在：

```text
outputs/acrcloud_tracking.xlsx
```

如果确实需要继续尝试自动下载搜索结果，可以增加：

```bash
python yt_dy_auto.py --retry-failed --download
```

论文数据建议先只识别候选，在标注页面人工核验后再下载 QQ 原曲。

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

数据库开始正式多人标注后，后续更新不能再使用 `--replace`：

```bash
python intent_mgsv_pipeline/server/import_excel_to_db.py \
  --input outputs/MGSV_Master_Dataset.xlsx \
  --db "$MGSV_DB" \
  --annotator-id owner
```

正式数据库需要定期备份。禁止用本地旧数据库覆盖服务器数据库。

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

## 七、本地改代码后更新服务器

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
  intent_mgsv_pipeline.validation.test_peer_annotation
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
