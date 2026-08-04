# 数据集扩充、图片/文字配乐与统一 Dataloader

## 一、当前阶段目标

当前优先级：

```text
继续扩大视频数据
-> 保证新增视频完整进入识曲、歌曲核验和标注漏斗
-> 小规模试采图片/文字配乐
-> 用统一 RowDataset 验证三类数据语义
-> 暂不优先进行大规模特征提取和复杂模型训练
```

视频仍是论文数据主体。图片和文字先作为跨模态扩展子集，不应立刻追求与视频相同
规模。

### 当前代码基线

截至 2026-08-04：

```text
现有清洗视频：121 条
统一 manifest 转换：121/121 成功
真实视频与完整歌曲文件校验：121/121 通过
图片配乐采集模板：已生成，尚待采集
文字配乐采集模板：已生成，尚待采集
统一 video/image/text RowDataset：已实现
```

## 二、建议的下一批规模

第一轮建议：

```text
新增视频：100 条
图片配乐：30 条试点
文字配乐：30 条试点
```

图片和文字试点通过双人核验、字段检查和 dataloader smoke test 后，再扩到每类
100 条以上。

### 视频 100 条建议分布

```text
卡点 / 转场 / 舞蹈 / 游戏剪辑：25
日常 Vlog / 学习 / 美食 / 生活记录：25
旅行 / 城市 / 风景 / 航拍：20
运动 / 健身 / 跑步：15
情绪 / 人物 / 剧情 / 电影感：15
```

尽量让 `sync_level=0/1` 接近均衡，并避免同一作者、同一模板、同一首歌占比过高。

### 图片 30 条建议分布

```text
自然风景 / 旅行：6
人物 / 情绪肖像：6
城市 / 夜景：5
产品 / 美食 / 静物：5
插画 / 游戏 / 二次元：4
活动 / 婚礼 / 节日：4
```

### 文字 30 条建议分布

```text
场景描述：8
情绪与氛围意图：8
使用场景指令：7
故事梗概 / 文案：7
```

## 三、每批新增视频的服务器验收

每批建议控制在 20–30 条。下载、预处理和增量导入后运行：

```bash
source config/server.env
conda activate mgsv_data

python -m intent_mgsv_pipeline.server.dataset_growth_report \
  --db "$MGSV_DB"
```

报告位置：

```text
outputs/server/diagnostics/dataset_growth_report.json
```

重点检查漏斗：

```text
disk.video_files
disk.unregistered_files
database.active_videos
database.missing_video_files
pipeline.videos_without_music_preparation
pipeline.music_preparation_statuses
pipeline.owner_annotation_statuses
pipeline.owner_song_verified
pipeline.peer_eligible
```

推荐验收标准：

```text
新下载文件均已登记，unregistered_files=0
数据库不存在真实缺失视频，missing_video_files=0
每条视频最终都有 music_preparations
歌曲必须经过 7862 人工核验
owner 完成后才开放 peer 复标
```

该命令必须读取正在使用的服务器数据库。本地数据库若没有同步服务器记录，会显示
`active_videos=0`，并把磁盘视频全部列为 `unregistered_files`；这只表示本地数据库
为空，不代表视频数据丢失。

## 四、统一跨模态样本语义

三类样本共享同一个任务：

> 给定内容，从完整音乐中定位适合该内容的片段。

共同监督目标：

```text
full_song_path
music_start
music_end
emotion
style
usage_scene
genre
vocal_presence
```

载体差异：

```text
video -> content_path + 视频时长 + sync/shot/seg_scores
image -> content_path/content_paths + overall_score
text  -> content_text + overall_score
```

图片和文字不是“零秒视频”：

- `sync_level` 固定为 0；
- 不填写 shot points；
- 不填写 `seg_scores_3/5`；
- 用 `overall_score` 表示内容与所选音乐片段的整体契合度；
- `music_start/music_end` 仍必须精确标注。

## 五、图片和文字采集规范

### 图片

- 优先使用自己拍摄、明确授权或许可允许研究使用的图片；
- `content_id` 必须稳定且唯一；
- 单图的 `content_path` 指向原图；
- 多图作品用 `content_paths` 保存按展示顺序排列的 JSON 路径数组；
- 多图作品的 `content_path` 指向第一张图，作为向后兼容的预览图；
- `source/source_url` 记录来源和许可线索；
- 同一图片的裁剪、滤镜版本使用相同 `group_id`，防止跨 split 泄漏。

### 抖音图文集

一个抖音图文集整体作为一个 `image` 样本，而不是将每张图拆成彼此无关的样本：

```text
sample_id = douyin_note_<作品ID>
content_path = 第一张图
content_paths = ["第1张", "第2张", ...]
group_id = douyin_note_<作品ID>
source_url = 原抖音 note 链接
```

DouK 下载的作品音频填写到 `source_audio_path`，仅用于识曲和核验。它通常是短原声，
不能代替 grounding 所需的完整歌曲。识曲并下载完整歌曲后，另行填写：

```text
music_id
full_song_path
music_start
music_end
```

从 DouK 下载目录和 `Download.xlsx` 自动生成待标注表：

```bash
python -m intent_mgsv_pipeline.data_collection.build_douyin_gallery_manifest \
  --limit 5 \
  --report outputs/intent_mgsv_dataset/pilot/douyin_gallery_pilot.report.json
```

生成：

```text
outputs/intent_mgsv_dataset/pilot/douyin_gallery_pilot.csv
```

2026-08-04 首次真实试运行结果：

```text
图文集：5
图片总数：26
各组图片数量：2 / 9 / 3 / 5 / 7
DouK 元数据匹配：5/5
带下载原声：4
缺少下载原声：1
```

缺原声不等于样本无效。可以根据 DouK 元数据中的歌曲标题搜索完整歌曲，或者人工选择
适合整组图文内容的完整歌曲。

### 文字

- 优先使用人工原创的场景描述、情绪意图或使用场景指令；
- `content_text` 保存原始文本，不把文本伪装成路径；
- 同一语义的改写版本使用相同 `group_id`；
- 不直接复制大段版权文本或个人敏感信息。

### 音乐片段

1. 选择完整歌曲。
2. 在完整歌曲中确定最适合内容的连续片段。
3. 填写准确的 `music_start/music_end`。
4. 填写音乐元信息与意图标签。
5. 试听确认后设置 `pair_verified=Yes`。
6. `overall_score` 使用 1–5 分。

首轮只收集可信正配对。负配对以后单独作为 compatibility 任务构建，不要给明显
错误配对伪造 grounding 区间。

## 六、生成采集模板

```bash
python -m intent_mgsv_pipeline.data_collection.build_cross_modal_templates
```

生成：

```text
outputs/intent_mgsv_dataset/templates/image_music_annotation_template.csv
outputs/intent_mgsv_dataset/templates/image_music_annotation_example.csv
outputs/intent_mgsv_dataset/templates/text_music_annotation_template.csv
outputs/intent_mgsv_dataset/templates/text_music_annotation_example.csv
```

## 七、统一 Dataloader

入口：

```text
intent_mgsv_pipeline/datasets/unified_intent_mgsv_dataset.py
```

示例：

```python
from intent_mgsv_pipeline.datasets.unified_intent_mgsv_dataset import (
    UnifiedIntentMGSVRowDataset,
)

dataset = UnifiedIntentMGSVRowDataset(
    [
        "video_manifest.csv",
        "image_manifest.csv",
        "text_manifest.csv",
    ],
    max_m_duration=400,
    strict=True,
    validate_files=True,
)

sample = dataset[0]
print(sample["modality"])
print(sample["content"])
print(sample["grounding"])
print(sample["intent"])
```

当前 dataloader 是稳定语义层，只读取路径、文字、标签和 grounding 区间，不加载
帧、像素、文本 embedding 或音频特征。

### 生成现有视频统一 manifest

```bash
python -m intent_mgsv_pipeline.data_collection.build_unified_manifest \
  --report outputs/intent_mgsv_dataset/unified/unified_video_manifest.report.json
```

默认输入：

```text
outputs/intent_mgsv_dataset/intent_mgsv_clean_ready.csv
```

默认输出：

```text
outputs/intent_mgsv_dataset/unified/unified_video_manifest.csv
```

图片和文字完成试采后，可重复传入多个表：

```bash
python -m intent_mgsv_pipeline.data_collection.build_unified_manifest \
  --input video_manifest.csv \
  --input image_manifest.csv \
  --input text_manifest.csv \
  --output unified_manifest.csv \
  --report unified_manifest.report.json
```

## 八、完整性检查

```bash
python -m intent_mgsv_pipeline.validation.validate_unified_dataset \
  --manifest image_manifest.csv \
  --manifest text_manifest.csv \
  --validate-files \
  --report outputs/server/diagnostics/unified_dataset_report.json
```

检查内容：

```text
content_type 是否合法
图片/视频路径是否存在
文字是否为空
完整歌曲是否存在
music_start < music_end
music_end 是否超过 max_m_duration
intent 与音乐标签是否齐全
各模态样本数量
```

## 九、后续 Dataloader 工作

当前已完成 RowDataset。下一步按顺序继续：

1. 每批服务器新增视频完成后，增量导出并合并统一 manifest。
2. 完成首批图片 30 条、文字 30 条采集并严格校验。
3. 按 `group_id/music_id` 设计防泄漏 train/val/test split。
4. 为图片、文字补充各自的特征加载适配器。
5. 定义统一 batch 中的 modality mask。
6. 数据规模和字段稳定后再接模型。
