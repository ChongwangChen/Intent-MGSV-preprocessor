# Intent-MGSV Training Notes

Date: 2026-07-24

## Current Data Snapshot

- Master rows: 123
- Ready rows: 121
- Train / val / test: 85 / 18 / 18
- sync_level=0: 74
- sync_level=1: 47

The user-facing annotation set was described as roughly 124 samples. The current
Excel file contains 123 rows, and the cleaned ready set contains 121 rows.

## New Code Blocks

- `intent_mgsv_pipeline/features/tabular_features.py`
  - Builds vocabularies for `genre`, `vocal_presence`, `emotion`, `style`, `usage_scene`, and `rhythm_category`.
  - Standardizes numeric metadata such as BPM, video duration, music duration, and match score.
  - Converts `music_start/music_end` into normalized `center/width` targets.

- `intent_mgsv_pipeline/models/tabular_baseline.py`
  - Defines a minimal MLP baseline.
  - Input: structured intent/rhythm/metadata features.
  - Output: normalized music grounding interval.

- `intent_mgsv_pipeline/training/train_tabular_baseline.py`
  - Trains the tabular baseline.
  - Exports validation/test predictions.
  - Reuses the existing metrics code for IoU, start/end error, and beat hit rate.

## Commands

Refresh the dataset outputs:

```powershell
E:\Users\30993\miniconda3\envs\mgsv_data\python.exe E:\MGSV_preprocessor\intent_mgsv_pipeline\schema\clean_dataset_schema.py
E:\Users\30993\miniconda3\envs\mgsv_data\python.exe E:\MGSV_preprocessor\intent_mgsv_pipeline\splits\prepare_intent_mgsv_dataset.py
```

Run validation:

```powershell
E:\Users\30993\miniconda3\envs\mgsv_data\python.exe E:\MGSV_preprocessor\intent_mgsv_pipeline\validation\dry_run_intent_mgsv_dataset.py --max-m-duration 400
E:\Users\30993\miniconda3\envs\mgsv_data\python.exe E:\MGSV_preprocessor\intent_mgsv_pipeline\validation\dry_run_intent_row_dataset.py --max-m-duration 400
```

Train the current minimal baseline:

```powershell
E:\Users\30993\miniconda3\envs\mgsv_data\python.exe E:\MGSV_preprocessor\intent_mgsv_pipeline\training\train_tabular_baseline.py --epochs 80 --batch-size 16
```

## Latest Experiment

Output directory:

```text
outputs/intent_mgsv_dataset/experiments/tabular_baseline
```

Generated files:

- `best_model.pt`
- `feature_config.json`
- `history.csv`
- `val_predictions.csv`
- `test_predictions.csv`
- `val_metrics.summary.json`
- `test_metrics.summary.json`
- `summary.json`

Best checkpoint:

- epoch: 7
- val_loss: 0.002988

Validation summary:

- rows: 18
- mean_temporal_iou: 0.1307
- mean_start_error: 24.4165
- mean_end_error: 25.1316

Test summary:

- rows: 18
- mean_temporal_iou: 0.1832
- mean_start_error: 31.4331
- mean_end_error: 35.3020

## Interpretation

This tabular baseline is only an engineering sanity check. It does not load video
or audio content, so low grounding IoU is expected. Its purpose is to prove that
the cleaned dataset, split files, feature encoder, model, training loop,
prediction export, and metrics pipeline all work end to end.

## Next Step

Build real multimodal features:

- video frame / shot-level features
- full-song audio features
- source-clip audio features
- beat and shot-point encodings
- intent-conditioned feature fusion

## 2026-07-24 Multimodal Fast Baseline

Implemented files:

- `features/extract_audio_features.py`
- `features/extract_video_features.py`
- `features/extract_video_stats_features.py`
- `datasets/feature_dataset.py`
- `models/multimodal_grounding_baseline.py`
- `training/train_multimodal_baseline.py`

Feature outputs:

```text
outputs/intent_mgsv_dataset/features_fast/audio
outputs/intent_mgsv_dataset/features_fast/video
```

Feature extraction status:

- audio unique songs: 117 ok, 0 failed
- video samples: 121 ok, 0 failed

Training command:

```powershell
E:\Users\30993\miniconda3\envs\mgsv_data\python.exe E:\MGSV_preprocessor\intent_mgsv_pipeline\training\train_multimodal_baseline.py --feature-root outputs\intent_mgsv_dataset\features_fast --epochs 40 --batch-size 8 --out-dir outputs\intent_mgsv_dataset\experiments\multimodal_baseline_fast --device cpu
```

Experiment output:

```text
outputs/intent_mgsv_dataset/experiments/multimodal_baseline_fast
```

Best checkpoint:

- epoch: 16
- val_loss: 0.02214

Validation summary:

- rows: 18
- mean_temporal_iou: 0.0935
- mean_start_error: 53.2514
- mean_end_error: 54.6868

Test summary:

- rows: 18
- mean_temporal_iou: 0.0802
- mean_start_error: 60.3000
- mean_end_error: 61.5287

Interpretation:

This is the first end-to-end multimodal pipeline. It reads cached video and
audio features, trains a video-query-to-music-timeline model, exports
predictions, and runs metrics. The current visual feature is a fast handcrafted
OpenCV feature, not a semantic video encoder, so weak IoU is expected.

Next model step:

- replace fast OpenCV video stats with pretrained visual features
- keep `feature_dataset.py` and `train_multimodal_baseline.py` unchanged where possible
- add shot/beat-aware auxiliary losses after the feature quality improves
