# ACRCloud 与 Shazam 识曲对照实验

## 目的

本实验用于判断 Shazam 是否适合作为 ACRCloud 失败后的低频兜底。实验不会修改：

- `outputs/acrcloud_tracking.xlsx`
- SQLite 标注数据库
- 已下载完整歌曲
- 人工核验状态

实验同时选取两组样本：

1. `control_success`：ACRCloud 已识别成功，用于检查 Shazam 是否识别一致。
2. `acr_failed`：ACRCloud 未识别，用于统计 Shazam 能额外挽救多少条。

默认两组各 10 条。正式接入前必须人工核验 Shazam 挽救的歌曲，不能直接把识曲结果
当作真值。

## 安装可选依赖

在服务器项目目录执行：

```bash
source config/server.env
conda activate mgsv_data
python -m pip install -r requirements_music_experiment.txt
```

ShazamIO 是开源的非官方 Shazam 客户端，可能受上游接口变化、区域网络和限流影响，
因此没有加入服务器的基础必装依赖。

## 运行 20 条对照实验

```bash
python scripts/compare_music_recognition.py \
  --success-samples 10 \
  --failed-samples 10
```

程序优先使用 DouK 单独下载的音乐文件，没有音乐文件时才使用视频音轨。结果保存在：

```text
outputs/server/music_recognition_experiments/
```

CSV 用于逐条人工核验，JSON 同时保存汇总指标和逐条结果。

提交给 Shazam 前，程序会用 ffmpeg 把输入统一转换为 20 秒单声道 WAV，避免特殊 MP3
封装造成 `SignatureError`。CSV 中的 `needs_manual_review=True` 表示该结果只能作为候选，
必须试听确认。

## 指标解释

| 指标 | 含义 |
|---|---|
| `control_hit_rate` | Shazam 对 ACRCloud 成功样本的命中比例 |
| `control_agreement_rate` | Shazam 与 ACRCloud 歌名、歌手一致的比例 |
| `rescue_rate` | ACRCloud 失败样本被 Shazam 识别的比例 |
| `error_rate` | Shazam 请求异常或超时比例 |

自动建议仅用于筛选方案：

- `recommend_fallback_integration`：指标达到预设门槛，可以开发正式兜底，但仍需人工核验。
- `promising_manual_review_only`：有额外命中，但可靠性不足，只在人工页面展示候选。
- `do_not_integrate_yet`：暂不接入正式流水线。

中文、英文译名、拼音和繁体名称可能实际指向同一首歌，因此字符串一致率只是保守指标。
是否接入应同时检查 CSV 中的人工核验结果。

## 全量正式流水线

对照实验结束后，现有正式流程仍按以下命令运行：

```bash
python scripts/run_server_preprocessing.py --dry-run
python scripts/run_server_preprocessing.py
```

该命令依次执行正式识曲和歌曲准备、`auto.py`、增量导入数据库、路径修复及标注服务
重启。Shazam 对照实验不会被这个命令自动调用。

## ACRCloud `code=3003`

`code=3003: requests limit exceeded` 表示账户请求次数额度已经耗尽，不是密钥过期，
也不是网络错误。新版 `yt_dy_auto.py` 会在第一次收到 3003 时立即停止 ACRCloud
识曲，不再把其余视频错误标记为 `recognition_failed`。

旧版本已经写入、且 `recognition_error` 包含 `code=3003` 的行，会在下次运行时自动
改为 `recognition_deferred_quota`，并保留到额度恢复或更换识曲后端时重新处理。

额度耗尽不妨碍以下工作继续进行：

- 已识曲歌曲的搜索、下载和自动对齐；
- `auto.py` 的视频分镜、节拍和基础预处理；
- 已进入数据库样本的人工标注。

若不准备升级 ACRCloud，可先运行本页的 Shazam 对照实验。Shazam 新命中目前只生成
人工候选，不会自动写入正式追踪表。
