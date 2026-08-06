# Intent-MGSV 初步代码范式任务蓝图

## 当前完成状态

- [x] 锁定当前数据字段问题和清理规则
- [x] 编写数据表清理脚本并生成干净版 Excel/CSV
- [x] 检查清理结果：字段、取值、缺失、路径是否合理
- [x] 生成 Intent-MGSV 的 train/val/test 数据划分
- [x] 对齐原 MGSV 代码需要的数据读取格式
- [x] 跑通最小 baseline 数据读取 dry-run
- [x] 实现新范式第一版评价指标：IoU + 起点偏差 + 节奏命中
- [x] 整理下一阶段模型扩展方案：Intent/Rhythm 接入 MaDe
- [x] 建立 `datasets/` 板块，提供 Intent-MGSV 富字段读取器
- [x] 建立 `baseline/` 板块，记录原 MGSV/MaDe 接入路径
- [x] 跑通富字段 Dataset dry-run
- [x] 定义 video/image/text 统一音乐 grounding 数据协议
- [x] 生成图片与文字配乐采集模板
- [x] 实现统一跨模态 RowDataset 与完整性检查
- [x] 支持一个图片样本包含有序多图，并从 DouK 图文集自动建表
- [x] 实现登录后的抖音浏览器链接采集、历史去重与每 20 条分批
- [x] 增加视频数据扩充漏斗审计报告
- [x] 将现有 121 条清洗视频转换为统一 manifest，并通过真实文件校验
- [ ] 将服务器后续新增正式导出增量合并到统一 manifest
- [ ] 建立按 group_id/music_id 防泄漏的跨模态 split

## 已生成文件

### 数据清理

- `intent_mgsv_pipeline/schema/clean_dataset_schema.py`
- `outputs/intent_mgsv_dataset/intent_mgsv_clean_all.xlsx`
- `outputs/intent_mgsv_dataset/intent_mgsv_clean_all.csv`
- `outputs/intent_mgsv_dataset/intent_mgsv_clean_ready.xlsx`
- `outputs/intent_mgsv_dataset/intent_mgsv_clean_ready.csv`

说明：

- `clean_all` 包含当前主表全部样本。
- `clean_ready` 只包含必填项完整、可进入训练/评估的样本。
- 已删除/废弃字段：`music_grounding_need`, `song_offset`, `song_start`, `song_end`。
- `sync_level` 已统一为 `0/1`。
- `music_start/music_end` 作为论文主 grounding 区间字段。

### 数据划分

- `intent_mgsv_pipeline/splits/prepare_intent_mgsv_dataset.py`
- `outputs/intent_mgsv_dataset/splits/all.csv`
- `outputs/intent_mgsv_dataset/splits/train.csv`
- `outputs/intent_mgsv_dataset/splits/val.csv`
- `outputs/intent_mgsv_dataset/splits/test.csv`

当前 ready 数据：

- total: 89
- train: 62
- val: 13
- test: 14
- sync_level=0: 55
- sync_level=1: 34

### 原 MGSV 兼容格式

- `outputs/intent_mgsv_dataset/splits/mgsv_ec_format/all_data.csv`
- `outputs/intent_mgsv_dataset/splits/mgsv_ec_format/train_data.csv`
- `outputs/intent_mgsv_dataset/splits/mgsv_ec_format/val_data.csv`
- `outputs/intent_mgsv_dataset/splits/mgsv_ec_format/test_data.csv`

这些文件对齐原 MGSV/MaDe dataloader 的 16 列格式：

- `video_id`
- `music_id`
- `video_start`
- `video_end`
- `music_start`
- `music_end`
- `music_total_duration`
- `video_segment_duration`
- `music_segment_duration`
- `music_path`
- `video_total_duration`
- `video_width`
- `video_height`
- `video_total_frames`
- `video_frame_rate`
- `video_category`

注意：

- `music_total_duration` 已从 `full_song_path` 重新读取完整歌曲时长。
- 原 MGSV 默认 `max_m_duration=240` 不适合当前数据，建议 baseline 使用 `max_m_duration=400`。

### Dry-run 与指标

- `intent_mgsv_pipeline/validation/dry_run_intent_mgsv_dataset.py`
- `intent_mgsv_pipeline/validation/dry_run_intent_row_dataset.py`
- `intent_mgsv_pipeline/metrics/evaluate_intent_mgsv_metrics.py`
- `outputs/intent_mgsv_dataset/splits/metrics_sanity.csv`
- `outputs/intent_mgsv_dataset/splits/metrics_sanity.summary.json`

当前 sanity check：

- Temporal IoU: 1.0
- Start Error: 0.0
- End Error: 0.0
- Beat Hit Rate overall: 0.7361
- Beat Hit Rate sync_level=0: 0.5434
- Beat Hit Rate sync_level=1: 0.7653

### 富字段 Dataset

- `intent_mgsv_pipeline/datasets/intent_mgsv_dataset.py`
- `intent_mgsv_pipeline/baseline/README_baseline.md`

富字段 Dataset 会把每条样本拆成：

- `ids`
- `paths`
- `grounding`
- `rhythm`
- `intent`
- `metadata`

当前 dry-run：

- total: 89
- missing intent: 0
- missing paths: 0
- bad target: 0
- rows with visual rhythm points: 38
- rows with audio rhythm points: 88

## 下一阶段代码路线

### Stage 1: 原 MGSV baseline

目标：

使用 `mgsv_ec_format/train_data.csv`, `val_data.csv`, `test_data.csv` 跑通原 MaDe 的数据读取和训练/评估流程。

重点：

- 先不加入 intent/rhythm 新标签。
- 只复现 `Video + Music -> music_start/music_end`。
- 评估指标先用原 IoU，加上当前新增的 `Start Error`。
- 下一步需要建立 `features/` 板块，生成原 MaDe 需要的 `.pt` 视频/音乐特征。

### Stage 2: Intent 辅助监督

目标：

在原 MaDe 表征后增加辅助预测头，让模型学习人工标签。

可加入的辅助任务：

- `sync_level`: 二分类
- `vocal_presence`: 三分类
- `genre`: 多分类
- `emotion/style/usage_scene`: 多标签分类

建议先使用辅助 loss，不急着把 intent 作为输入条件。

### Stage 3: Rhythm-Adaptive Evaluation

目标：

把不同 sync_level 的评价重点区分开。

建议指标：

- Temporal IoU
- Start Error
- End Error
- Beat Hit Rate
- Sync-aware Score

基本思想：

- `sync_level=0`: 更重视整体区间和语义氛围。
- `sync_level=1`: 更重视节奏点/分镜点命中。

### Stage 4: Intent/Rhythm 条件建模

目标：

让模型显式接收创作意图和节奏信息。

可能结构：

- Video Encoder
- Music Encoder
- Intent Encoder
- Rhythm Encoder
- Fusion Module
- Segment Prediction Head
- Auxiliary Heads

输入从：

```text
Video + Music
```

扩展为：

```text
Video + Music + Intent + Rhythm
```

输出仍保留：

```text
music_start, music_end
```

同时可辅助输出：

```text
sync_level, genre, vocal_presence, intent compatibility score
```

## 常用命令

重新生成干净数据：

```powershell
E:\Users\30993\miniconda3\envs\mgsv_data\python.exe E:\MGSV_preprocessor\intent_mgsv_pipeline\schema\clean_dataset_schema.py
```

重新生成 split：

```powershell
E:\Users\30993\miniconda3\envs\mgsv_data\python.exe E:\MGSV_preprocessor\intent_mgsv_pipeline\splits\prepare_intent_mgsv_dataset.py
```

检查 baseline 标签：

```powershell
E:\Users\30993\miniconda3\envs\mgsv_data\python.exe E:\MGSV_preprocessor\intent_mgsv_pipeline\validation\dry_run_intent_mgsv_dataset.py --max-m-duration 400
```

检查富字段 Dataset：

```powershell
E:\Users\30993\miniconda3\envs\mgsv_data\python.exe E:\MGSV_preprocessor\intent_mgsv_pipeline\validation\dry_run_intent_row_dataset.py --max-m-duration 400
```

运行指标 sanity check：

```powershell
E:\Users\30993\miniconda3\envs\mgsv_data\python.exe E:\MGSV_preprocessor\intent_mgsv_pipeline\metrics\evaluate_intent_mgsv_metrics.py
```
