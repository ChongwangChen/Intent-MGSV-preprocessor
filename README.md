# Intent-MGSV 项目总览：给服务器协作者与 Codex 的入口说明

本文档面向第一次接触本目录的 Codex 或协作者。请先读完本文，再修改代码、移动数据或运行批量任务。

## 一、项目目标

本项目正在构建一个新的 Intent-MGSV 数据集与代码范式，用于研究：

> 给定短视频及其配乐意图，模型需要从完整音乐中定位适合该视频使用的音乐片段，并理解视频-音乐之间的情绪、风格、使用场景、节奏同步关系。

当前项目不是单纯复现原 MGSV，而是在原始“视频-音乐片段 grounding”的基础上扩展：

- 视频是否需要卡点/同步；
- 视频情绪；
- 视频风格；
- 使用场景；
- 分镜点与分段打分；
- 人声存在情况；
- 音乐 genre；
- 从完整歌曲中定位短视频实际使用片段。

后续论文代码需要支持：

```text
数据清洗
-> 多人标注
-> 歌曲下载与复用
-> 自动对齐
-> 分镜检测
-> 特征提取
-> 训练
-> 评估
-> 导出论文实验结果
```

## 二、当前最重要的结论

### 当前阶段优先级

根据当前项目推进意见，近期优先级应收紧为：

```text
第一优先级：继续扩大数据集并完成高质量标注
第二优先级：保证标注工具和数据保存稳定
第三优先级：先构建可靠 dataloader，对齐后续论文代码输入输出
暂缓事项：复杂特征提取、模型训练流水线、模型结构大改
```

也就是说，当前代码侧最重要的不是继续堆特征或模型，而是让数据能被稳定、清晰、可复现地读出来。

近期 Codex 不应优先推进：

```text
大规模特征提取优化
复杂 multimodal baseline
训练指标刷分
新模型结构实验
```

近期 Codex 应优先推进：

```text
数据字段稳定
标注流程稳定
多人/服务器标注不丢数据
dataloader 输出格式稳定
样本可视化/抽查工具
```

多人服务器版不能继续让多个人同时写同一个 Excel。

当前本地历史主表是：

```text
outputs/MGSV_Master_Dataset.xlsx
```

但服务器多人协作时，Excel 只能作为导入/导出格式。真正运行中的状态应该放进数据库：

```text
intent_mgsv_pipeline/server/
```

数据库底座已经初步建立，目标是让多人标注时每个人只写自己的 annotation row，避免互相覆盖、进度倒退、标注丢失。

## 三、关键目录说明

```text
.
├─ annotate_tool.py
├─ music_fetch.py
├─ auto.py
├─ shot_detect.py
├─ outputs/
├─ DouK-Source/
├─ intent_mgsv_pipeline/
└─ README.md
```

### 1. 标注与预处理旧入口

```text
annotate_tool.py
```

当前主要 Gradio 标注工具。历史上承担了：

- 视频浏览；
- QQ 音乐识曲结果核对；
- 手动下载完整歌曲；
- 自动对齐；
- 分镜点检查；
- 情绪/风格/使用场景/分段分数/vocal/genre 标注；
- 写入 `outputs/MGSV_Master_Dataset.xlsx`。

注意：这是当前本地标注主入口，但还没有完全改成服务器数据库模式。服务器多人版改造时，不要让多人同时直接运行同一个 Excel 写入流程。

```text
music_fetch.py
```

音乐下载、QQ 音乐链接解析、完整歌曲复用、音频对齐等逻辑主要在这里。

```text
auto.py
```

早期自动预处理入口，负责批量处理下载后的视频/音乐并更新主表。

```text
shot_detect.py
```

分镜检测相关逻辑。

### 2. 数据输出目录

```text
outputs/
```

当前最关键的数据都在这里。不要随意删除。

重要子目录：

```text
outputs/MGSV_Master_Dataset.xlsx
outputs/full_music/
outputs/full_songs/
outputs/intent_mgsv_dataset/
outputs/inter_annotator/
outputs/server/
```

说明：

- `MGSV_Master_Dataset.xlsx`：历史主表。
- `full_music/`：视频原声或短音频片段。
- `full_songs/`：QQ 音乐等来源下载的完整歌曲。
- `intent_mgsv_dataset/`：清洗数据、划分、特征、训练实验结果。
- `inter_annotator/`：第二标注者协作包。
- `server/`：服务器数据库 smoke test 结果和后续服务器数据库文件。

### 3. DouK-Source

```text
DouK-Source/
```

用于抖音数据下载的外部项目目录。下载后的视频通常在：

```text
DouK-Source/Volume/Download/
```

这个目录可能包含历史下载数据、未纳入数据集的数据、已处理数据。后续服务器版需要明确区分：

- 原始下载池；
- 已入库数据；
- 被删除/排除数据。

### 4. 新范式代码目录

```text
intent_mgsv_pipeline/
```

这是我们正在逐步整理的新论文范式代码。相比根目录旧脚本，这里更适合长期维护和服务器部署。

主要模块：

```text
intent_mgsv_pipeline/
  annotation_collab/
  baseline/
  datasets/
  docs/
  features/
  metrics/
  models/
  schema/
  server/
  splits/
  training/
  validation/
```

各模块职责：

- `schema/`：字段清洗、标签标准化、去除冗余字段。
- `splits/`：生成 train / val / test。
- `datasets/`：训练数据读取。
- `features/`：音频/视频特征提取。
- `models/`：baseline 模型。
- `training/`：训练入口。
- `metrics/`：评估指标。
- `validation/`：dry-run 和数据合法性检查。
- `annotation_collab/`：第二标注者协作、模板生成、合并标注。
- `server/`：多人服务器标注数据库底座。
- `docs/`：项目规划和说明文档。

## 四、当前数据与实验状态

最近一次整理时的状态：

```text
主表记录：123 条
清洗后可训练记录：121 条
train：85
val：18
test：18
sync_level=0：74
sync_level=1：47
```

已经跑通过：

- 表格 baseline；
- 快速多模态 baseline；
- 音频特征提取；
- 快速视频统计特征提取；
- train/val/test 生成；
- 基础指标评估。

当前 baseline 指标不高是预期内的，因为视频侧仍然是轻量统计特征，不是强语义视觉编码器。现阶段更重要的是先让数据和流水线稳定。

## 五、服务器多人标注规划

服务器版的目标是让多人共享数据、共同标注、共同下载和训练。

详细蓝图见：

```text
intent_mgsv_pipeline/docs/README_server_pipeline.md
```

核心原则：

- Excel 只做导入/导出；
- 数据库是多人协作时的唯一事实来源；
- 每个标注者有自己的 `annotator_id`；
- 每个标注者保存自己的 annotation；
- 下载、自动对齐、分镜检测、训练都应该变成后台 job；
- 删除数据要软删除，不要直接物理删除；
- 复用同一首歌时，也必须对当前视频重新计算 `music_start` 和 `music_end`。

服务器数据库底座已初步建立：

```text
intent_mgsv_pipeline/server/schema.sql
intent_mgsv_pipeline/server/db.py
intent_mgsv_pipeline/server/import_excel_to_db.py
intent_mgsv_pipeline/server/export_db_to_excel.py
intent_mgsv_pipeline/server/assignment.py
```

已经通过 smoke test：

- 当前 Excel 导入数据库；
- 第二标注者领取一条；
- 保存单条 annotation patch；
- 释放任务；
- 导出 owner / annotator_b 标注结果。

## 六、重要文档索引

```text
intent_mgsv_pipeline/docs/README_server_pipeline.md
```

服务器多人标注与训练流水线迁移蓝图。

```text
intent_mgsv_pipeline/docs/README_training.md
```

当前训练脚本、baseline 和实验输出说明。

```text
intent_mgsv_pipeline/docs/README_inter_annotator.md
```

第二标注者协作说明。

```text
outputs/intent_mgsv_dataset/README_pipeline.md
```

当前数据清洗、划分、实验输出的阶段性记录。

## 七、常用命令

以下命令以本地 Windows 环境为例。服务器上需要换成服务器自己的 Python/Conda 路径。

### 1. 清洗字段

```powershell
E:\Users\30993\miniconda3\envs\mgsv_data\python.exe intent_mgsv_pipeline\schema\clean_dataset_schema.py
```

### 2. 生成 train/val/test

```powershell
E:\Users\30993\miniconda3\envs\mgsv_data\python.exe intent_mgsv_pipeline\splits\prepare_intent_mgsv_dataset.py
```

### 3. 提取快速音频特征

```powershell
E:\Users\30993\miniconda3\envs\mgsv_data\python.exe intent_mgsv_pipeline\features\extract_audio_features.py --out-dir outputs\intent_mgsv_dataset\features_fast\audio
```

### 4. 提取快速视频统计特征

```powershell
E:\Users\30993\miniconda3\envs\mgsv_data\python.exe intent_mgsv_pipeline\features\extract_video_stats_features.py
```

### 5. 训练快速多模态 baseline

```powershell
E:\Users\30993\miniconda3\envs\mgsv_data\python.exe intent_mgsv_pipeline\training\train_multimodal_baseline.py --feature-root outputs\intent_mgsv_dataset\features_fast --epochs 40 --batch-size 8 --out-dir outputs\intent_mgsv_dataset\experiments\multimodal_baseline_fast --device cpu
```

服务器有 GPU 时可以把 `--device cpu` 改成 `--device cuda`。

### 6. 导入 Excel 到服务器数据库

```powershell
E:\Users\30993\miniconda3\envs\mgsv_data\python.exe intent_mgsv_pipeline\server\import_excel_to_db.py --input outputs\MGSV_Master_Dataset.xlsx --db outputs\server\intent_mgsv.sqlite3 --annotator-id owner --replace
```

服务器下载后处理、多人复标和本地 Git 更新的实际操作见：

```text
intent_mgsv_pipeline/docs/README_server_operations.md
```

### 7. 从数据库导出 Excel

```powershell
E:\Users\30993\miniconda3\envs\mgsv_data\python.exe intent_mgsv_pipeline\server\export_db_to_excel.py --db outputs\server\intent_mgsv.sqlite3 --out outputs\server\MGSV_Master_Dataset.export.xlsx --annotator-id owner
```

### 8. 领取一条标注任务

```powershell
E:\Users\30993\miniconda3\envs\mgsv_data\python.exe intent_mgsv_pipeline\server\assignment.py --db outputs\server\intent_mgsv.sqlite3 --annotator-id annotator_b claim-next
```

## 八、协作与安全规则

请遵守以下规则，尤其是服务器多人协作时：

1. 不要直接删除 `outputs/full_songs/`、`outputs/full_music/`、`DouK-Source/Volume/Download/` 里的文件。
2. 不要让多个进程同时写 `outputs/MGSV_Master_Dataset.xlsx`。
3. 不要把 `qqmusic_cookies.txt` 提交到公开仓库。
4. 不要把 smoke test 数据库误当作正式数据库。
5. 不要在没有备份的情况下批量改 Excel。
6. 如果要重跑下载、对齐、分镜检测，优先写入 job/log，而不是静默覆盖原字段。
7. 修改 `annotate_tool.py` 前要特别小心，它承载了大量历史修复。
8. 服务器版应优先推进数据库模式，而不是继续给单机 Excel 模式打补丁。

## 九、下一阶段开发计划

### 阶段 1：服务器数据库模式接入标注工具

目标：

- `annotate_tool.py` 增加数据库模式；
- 支持 `annotator_id`；
- 支持领取任务；
- 支持单条 annotation 保存；
- 支持导出 Excel；
- 避免多人写同一个 Excel。

### 阶段 2：后台任务队列

目标：

- 下载歌曲变成 job；
- 自动对齐变成 job；
- 分镜检测变成 job；
- 页面只显示任务状态和结果；
- 失败任务可重试。

### 阶段 3：多人标注合并

目标：

- 第二标注者独立保存；
- 分段分数取平均；
- emotion / style / usage_scene 取并集；
- 输出分歧报告。

### 阶段 4：改进模型侧

目标：

- 接入更强的视频语义特征；
- 加入音频边界预测；
- 加入 beat/shot 辅助目标；
- 形成论文新范式 baseline。

### 阶段 5：扩展文字/图片配乐数据

目标：

- 在视频配乐 grounding 稳定后，再补文字/图片场景；
- 不要过早打散主任务；
- 先保证视频-音乐核心数据集和训练链路可靠。

## 十、给新 Codex 的工作建议

如果你是服务器上的新 Codex，请按这个顺序接手：

1. 先读本文档。
2. 再读 `intent_mgsv_pipeline/docs/README_server_pipeline.md`。
3. 查看 `intent_mgsv_pipeline/server/` 的数据库底座。
4. 不要直接重构 `annotate_tool.py`，先找最小接入点。
5. 优先实现“数据库模式下领取/保存/导出”的闭环。
6. 所有批量改数据动作先生成备份。
7. 每次新增服务器能力，都同步更新本文档或 server pipeline 文档。

当前最重要的工程目标：

> 把项目从单机 Excel 标注，稳定迁移到服务器数据库多人标注。
