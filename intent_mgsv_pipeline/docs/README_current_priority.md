# 当前阶段优先级：数据集扩大、标注稳定与 Dataloader

本文档记录当前阶段的真实工作重心，避免后续开发跑偏。

## 一、当前判断

师兄当前意见可以理解为：

> 现阶段先把数据集扩大并标注好；代码侧最主要的是 dataloader。特征提取、复杂训练和模型结构暂时不用过早操心。

因此，当前工程目标应从“完整训练流水线”收紧为：

```text
数据集扩大
-> 标注质量控制
-> 字段语义稳定
-> dataloader 可读、可查、可复现
```

## 二、为什么 dataloader 是当前代码重点

在论文新范式里，dataloader 决定了后续模型看到什么数据。

现在最需要先固定的是：

- 每条视频样本如何被读取；
- 完整歌曲路径如何对应；
- `music_start` / `music_end` 作为 grounding 标签如何输出；
- `sync_level` 如何统一为 0/1；
- `emotion` / `style` / `usage_scene` 多标签如何解析；
- `seg_scores_3` / `seg_scores_5` 如何对应分镜段；
- `shot_points` 如何转成模型可用结构；
- `genre` / `vocal_presence` 如何作为元信息保留；
- 数据缺失时如何跳过或报错；
- train / val / test 是否稳定复现。

如果 dataloader 没稳定，后面的特征提取和训练都会建立在不可靠的数据解释上。

## 三、当前不应优先投入的事项

暂时不要把主要精力放在：

- 大规模视频特征提取；
- 更复杂的音频特征；
- 训练速度优化；
- 模型结构创新；
- 指标刷分；
- 端到端服务器训练自动化。

这些后面都需要，但不是现在最紧急的。

## 四、当前应优先推进的代码任务

### 1. Dataset 字段定义

明确最终 dataloader 应读取哪些字段：

```text
video_id
video_path / full_music_path
full_song_path
music_start
music_end
sync_level
shot_points
shot_points_3
shot_points_5
seg_scores_3
seg_scores_5
emotion
style
usage_scene
genre
vocal_presence
song_title
song_artist
```

### 2. Dataloader 输出结构

建议每条样本输出：

```python
{
    "ids": {...},
    "paths": {...},
    "grounding": {
        "start": float,
        "end": float,
        "center": float,
        "width": float,
    },
    "intent": {
        "emotion": list[str],
        "style": list[str],
        "usage_scene": list[str],
    },
    "rhythm": {
        "sync_level": int,
        "shot_points": list[float],
        "seg_scores_3": list[float],
        "seg_scores_5": list[float],
    },
    "music_meta": {
        "genre": str,
        "vocal_presence": str,
        "song_title": str,
        "song_artist": str,
    },
}
```

### 3. 数据合法性检查

dataloader 应至少检查：

- 视频文件存在；
- 完整歌曲文件存在；
- `music_start < music_end`；
- `music_end` 不明显超过完整歌曲时长；
- `sync_level` 能转成 0/1；
- 必填标签不为空；
- 分段分数数量和分镜段数量基本一致。

### 4. 抽样检查工具

需要一个轻量脚本，随机抽若干条样本，打印：

```text
视频名
歌曲名
music_start / music_end
sync_level
emotion / style / usage_scene
shot_points
seg_scores
文件是否存在
```

这比现在盲目训练更重要。

## 五、和服务器迁移的关系

服务器多人标注仍然重要，但近期服务器代码也应围绕数据稳定展开：

- Git 同步代码；
- 服务器保存正式数据；
- 数据库模式避免多人覆盖；
- dataloader 从导出的 clean/split 文件读取；
- 后续训练先不着急。

## 六、近期最小可执行路线

建议下一步按这个顺序做：

```text
1. 继续扩大数据集并标注
2. 修正/稳定主表字段
3. 完善 clean_ready 数据导出
4. 重构并验证 IntentMGSV dataset/dataloader
5. 写 sample inspection 脚本
6. 服务器同步代码
7. 等数据规模和字段稳定后，再回到特征和模型
```

当前一句话目标：

> 先让数据集变大、标注变稳、dataloader 读得准。

## 七、2026-08 跨模态扩展

当前新增执行路线：

```text
视频继续扩充 100 条，保持为主体
图片配乐 30 条试点
文字配乐 30 条试点
统一 content_type=video/image/text 的 RowDataset
通过字段和文件完整性检查后再扩大图片/文字规模
```

图片和文字共享 `music_start/music_end` grounding 目标，但不伪造视频时长、分镜点
或分段评分。详细规范见：

```text
intent_mgsv_pipeline/docs/README_dataset_growth_and_multimodal.md
```
