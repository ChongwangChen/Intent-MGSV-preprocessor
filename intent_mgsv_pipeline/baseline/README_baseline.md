# Baseline 接入说明

这一板块记录如何把当前 Intent-MGSV 数据接入原 MGSV/MaDe 代码。

## 两条数据入口

### 1. 原 MGSV 兼容入口

路径：

```text
outputs/intent_mgsv_dataset/splits/mgsv_ec_format/
```

文件：

- `train_data.csv`
- `val_data.csv`
- `test_data.csv`

这些文件对齐原项目 `dataloaders/dataloader_MGSV_EC_feature.py` 的字段：

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

用途：

- 跑原始 MaDe baseline。
- 暂时只评估 grounding 区间预测。
- 不使用 `genre/emotion/style/usage_scene/sync_level` 等新标签。

注意：

- 当前数据完整歌曲最长约 381 秒。
- 原 MGSV 默认 `max_m_duration=240` 不够，建议设置为 `400`。

### 2. Intent-MGSV 富字段入口

路径：

```text
outputs/intent_mgsv_dataset/splits/
```

文件：

- `train.csv`
- `val.csv`
- `test.csv`
- `all.csv`

读取代码：

```text
intent_mgsv_pipeline/datasets/intent_mgsv_dataset.py
```

用途：

- 后续训练 Intent/Rhythm 扩展模型。
- 读取完整语义标签和节奏标签。
- 支持辅助任务，例如 `sync_level`, `genre`, `vocal_presence`, `emotion`, `style`, `usage_scene`。

## 推荐推进顺序

### Step 1: Baseline dry-run

先确认兼容 CSV 的区间标签合法：

```powershell
E:\Users\30993\miniconda3\envs\mgsv_data\python.exe E:\MGSV_preprocessor\intent_mgsv_pipeline\validation\dry_run_intent_mgsv_dataset.py --max-m-duration 400
```

### Step 2: Rich Dataset dry-run

确认新标签能被结构化读取：

```powershell
E:\Users\30993\miniconda3\envs\mgsv_data\python.exe E:\MGSV_preprocessor\intent_mgsv_pipeline\validation\dry_run_intent_row_dataset.py --max-m-duration 400
```

### Step 3: 原 MaDe baseline

在 `E:\Research\MGSV` 中使用以下 CSV：

```text
E:\MGSV_preprocessor\outputs\intent_mgsv_dataset\splits\mgsv_ec_format\train_data.csv
E:\MGSV_preprocessor\outputs\intent_mgsv_dataset\splits\mgsv_ec_format\val_data.csv
E:\MGSV_preprocessor\outputs\intent_mgsv_dataset\splits\mgsv_ec_format\test_data.csv
```

需要先解决原项目的特征文件输入：

- 视频特征：原代码期待 `vit_feature/{video_id}.pt` 和 `vit_mask/{video_id}.pt`
- 音乐特征：原代码期待 `ast_feature/{music_id}.pt` 和 `ast_mask/{music_id}.pt`

所以真正训练前，下一步应该是建立 `features/` 板块，生成或适配这四类 `.pt` 特征。

## 下一步待做

- [ ] 建立 `intent_mgsv_pipeline/features/`
- [ ] 明确视频特征提取方式：CLIP/ViT 或先用占位特征 dry-run
- [ ] 明确音乐特征提取方式：AST/MERT 或先用占位特征 dry-run
- [ ] 让原 MaDe dataloader 能读到当前 89 条数据的 `.pt` 特征
- [ ] 跑通一个最小训练/评估流程
