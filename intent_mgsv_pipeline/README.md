# Intent-MGSV Pipeline

这个目录用于承载论文新范式相关代码。它和现有的标注工具、自动预处理脚本分开，避免后续模型、数据、指标混在一起。

## 板块结构

### `schema/`

数据字段清理与标准化。

当前脚本：

- `clean_dataset_schema.py`

主要职责：

- 读取 `outputs/MGSV_Master_Dataset.xlsx`
- 删除/废弃重复字段：`music_grounding_need`, `song_offset`, `song_start`, `song_end`
- 统一 `sync_level` 为 `0/1`
- 生成 `clean_all` 和 `clean_ready`

### `splits/`

数据集划分和原 MGSV 兼容格式导出。

当前脚本：

- `prepare_intent_mgsv_dataset.py`

主要职责：

- 从 `intent_mgsv_clean_ready.xlsx` 生成 `train/val/test`
- 按 `sync_level` 分层抽样
- 生成原 MGSV/MaDe dataloader 可读的 16 列 CSV
- 重新读取完整歌曲时长，修正旧表中短音频时长残留的问题

### `validation/`

数据读取与标签合法性检查。

当前脚本：

- `dry_run_intent_mgsv_dataset.py`
- `dry_run_intent_row_dataset.py`

主要职责：

- 检查 `music_start/music_end` 是否合法
- 检查区间是否超出完整歌曲时长
- 模拟原 MGSV dataloader 的 center/width 标签计算
- 检查富字段 Dataset 是否能稳定解析 intent/rhythm 标签

### `datasets/`

新范式自己的数据读取层。

当前脚本：

- `intent_mgsv_dataset.py`

主要职责：

- 读取富字段 split CSV
- 结构化解析 `intent`, `rhythm`, `grounding`, `metadata`
- 作为后续模型 dataloader 的语义入口

### `baseline/`

原 MGSV/MaDe 接入说明。

当前文档：

- `README_baseline.md`

### `metrics/`

新范式评价指标。

当前脚本：

- `evaluate_intent_mgsv_metrics.py`

主要职责：

- Temporal IoU
- Start Error
- End Error
- Beat Hit Rate
- 后续扩展 Sync-aware Score、Intent Match Score

### `docs/`

路线图、任务蓝图和阶段记录。

当前文档：

- `README_pipeline.md`

## 当前推荐运行顺序

```powershell
E:\Users\30993\miniconda3\envs\mgsv_data\python.exe E:\MGSV_preprocessor\intent_mgsv_pipeline\schema\clean_dataset_schema.py
E:\Users\30993\miniconda3\envs\mgsv_data\python.exe E:\MGSV_preprocessor\intent_mgsv_pipeline\splits\prepare_intent_mgsv_dataset.py
E:\Users\30993\miniconda3\envs\mgsv_data\python.exe E:\MGSV_preprocessor\intent_mgsv_pipeline\validation\dry_run_intent_mgsv_dataset.py --max-m-duration 400
E:\Users\30993\miniconda3\envs\mgsv_data\python.exe E:\MGSV_preprocessor\intent_mgsv_pipeline\validation\dry_run_intent_row_dataset.py --max-m-duration 400
E:\Users\30993\miniconda3\envs\mgsv_data\python.exe E:\MGSV_preprocessor\intent_mgsv_pipeline\metrics\evaluate_intent_mgsv_metrics.py
```

## 未来预留板块

后续可以继续新增：

- `features/`: 视频/音乐/节奏/意图特征提取
- `models/`: Intent/Rhythm 扩展后的 MaDe 模型
- `training/`: 训练入口、loss、配置
- `experiments/`: 实验配置与结果管理
