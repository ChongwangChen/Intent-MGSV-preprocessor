# Intent-MGSV 服务器多人标注与训练流水线迁移蓝图

本文档用于说明 Intent-MGSV 项目迁移到共享服务器后的推荐架构。服务器版本的目标不是单纯跑训练，而是承担：

- 多人共享同一份数据；
- 多人同时标注；
- 大规模视频/音乐下载与保存；
- 自动对齐、分镜检测、特征提取；
- 训练、评估与结果导出。

最重要的一条原则是：

> 多人协作时，不能再让多个用户同时直接写同一个 Excel 文件。

Excel 后续只适合作为“导入/导出格式”。真正运行中的数据状态应该交给数据库管理。

## 一、总体目标

服务器版需要解决这些问题：

- 所有视频、原声、完整歌曲集中保存在服务器；
- 多个标注者可以同时打开标注界面；
- 不同标注者不会互相覆盖保存结果；
- 下载、识曲、自动对齐、分镜检测这类耗时任务不会卡住标注界面；
- 每次保存、删除、跳过、重新检测都有日志；
- 可以随时从数据库导出 Excel / CSV，用于论文分析和模型训练；
- 可以一键运行清洗、划分、特征提取、训练、评估流水线。

## 二、推荐服务器目录结构

建议服务器上使用类似下面的目录：

```text
/data/intent_mgsv/
  raw/
    douyin_links/          # 原始抖音分享链接、批量下载链接
    uploaded_links/        # 人工上传或补充的链接

  media/
    videos/                # 下载后的视频
    clip_audio/            # 视频中提取出的短音频/原声片段
    full_songs/            # QQ音乐等来源下载的完整歌曲

  db/
    intent_mgsv.sqlite3    # 初期推荐使用 SQLite 数据库
    backups/               # 数据库备份

  outputs/
    clean/                 # 清洗后的数据表
    splits/                # train / val / test
    features/              # 音频/视频特征缓存
    experiments/           # 训练实验结果
    reports/               # 评估报告、标注统计报告

  logs/
    app/                   # 标注界面日志
    jobs/                  # 下载/训练/检测任务日志

  repo/
    MGSV_preprocessor/     # 项目代码
```

初期只有几个人标注时，SQLite 开启 WAL 模式就可以满足需求；如果后面标注者更多、任务更多，再切换到 PostgreSQL。

## 三、数据源：数据库是唯一事实来源

当前项目的主数据表是：

```text
outputs/MGSV_Master_Dataset.xlsx
```

服务器版中，这个 Excel 应该变成导入/导出文件，而不是多人实时写入的文件。

推荐数据库表结构如下。

### 1. videos：视频表

保存每条短视频的基础信息。

```text
videos
  id
  video_path              # 服务器上的视频路径
  clip_audio_path         # 视频原声音频路径
  duration                # 视频时长
  douyin_title            # 抖音标题
  author                  # 作者
  tags                    # 标签
  created_at
  deleted_at              # 软删除时间；不建议直接物理删除
```

### 2. songs：完整歌曲表

保存下载后的完整歌曲信息，方便复用。

```text
songs
  id
  title
  artist
  album
  full_song_path
  qq_song_mid
  qq_song_id
  source                  # QQ音乐 / B站 / 手工上传 / 其他
  fingerprint             # 音频指纹，用于判断是否重复
  created_at
```

### 3. annotations：标注表

保存某个标注者对某个视频的标注结果。

```text
annotations
  id
  video_id
  annotator_id
  sync_level
  music_start
  music_end
  shot_points
  emotion
  style
  usage_scene
  seg_scores_3
  seg_scores_5
  vocal_presence
  genre
  song_verified
  status
  updated_at
```

注意：多人标注时，同一个 `video_id` 可以有多条 annotation，不同标注者各写各的。

### 4. annotation_assignments：标注领取/锁表

防止多人同时标同一条。

```text
annotation_assignments
  video_id
  annotator_id
  status
  lease_until             # 领取有效期，超时后可被重新领取
  updated_at
```

### 5. jobs：后台任务表

保存下载、识曲、对齐、分镜检测、训练等任务。

```text
jobs
  id
  job_type
  status
  payload_json
  result_json
  error
  created_by
  created_at
  started_at
  finished_at
```

### 6. events：操作日志表

记录关键操作，方便追踪问题。

```text
events
  id
  actor
  event_type
  target_type
  target_id
  payload_json
  created_at
```

## 四、多人标注规则

多人标注时，推荐遵守这些规则：

- 每个标注者登录或输入自己的 `annotator_id`；
- 点击“下一条”时，不是简单取 Excel 下一行，而是从数据库领取一个任务；
- 被领取的数据会有一个短期锁，锁定期间其他人不会拿到同一条；
- 保存时只保存当前标注者当前视频的 annotation，不重写整张表；
- 返回上一条时，只修改自己之前标过的那一条；
- 跳过只表示“当前标注者跳过”，不代表删除数据；
- 删除数据应该是管理员权限，并且优先软删除，不直接删除文件；
- 必填项未完成时，不允许把该条标为 completed；
- 下载歌曲后，必须二次确认音频和对齐结果，否则不能进入完成状态。

推荐状态：

```text
unassigned              # 未领取
in_progress             # 标注中
needs_song              # 缺完整歌曲
needs_alignment_check   # 需要检查自动对齐
needs_review            # 需要复核
completed               # 已完成
skipped                 # 当前标注者跳过
deleted                 # 数据被软删除
```

## 五、耗时任务必须后台化

这些操作不应该直接卡在标注页面里：

- 批量下载抖音视频；
- QQ 音乐链接解析；
- 完整歌曲下载；
- 音频自动对齐；
- 分镜检测；
- 音频特征提取；
- 视频特征提取；
- 数据清洗与划分；
- 模型训练；
- 评估报告生成。

它们应该作为后台任务运行：

```text
queued -> running -> succeeded
queued -> running -> failed
```

标注界面只负责提交任务、查看任务状态、展示任务结果。

推荐 job 类型：

```text
download_douyin_links
resolve_qq_song
download_full_song
align_song_to_clip
detect_shots
extract_audio_features
extract_video_features
build_splits
train_baseline
evaluate_predictions
export_excel
backup_database
```

这样一个人在下载歌曲，另一个人仍然可以继续标注，不会互相阻塞。

## 六、歌曲下载与复用规则

服务器版一定要把“歌曲复用”设计清楚，否则完整歌曲会重复下载、重复命名，甚至错误复用。

推荐下载前按以下顺序判断是否已有歌曲：

1. QQ 音乐 `song_mid` 或 `song_id` 完全一致；
2. 标准化后的歌名 + 歌手完全一致；
3. 音频指纹高度一致；
4. 人工确认复用。

如果复用了已有完整歌曲，仍然必须对当前视频重新做自动对齐。

也就是说：

> 复用歌曲文件，不等于复用另一条视频的 `music_start` 和 `music_end`。

每条视频自己的音乐片段位置都应该单独计算和确认。

## 七、训练流水线

服务器训练流水线应该从数据库导出的干净快照开始：

```text
数据库
-> 导出当前快照
-> 清洗字段
-> 生成 train / val / test
-> 提取或更新音频特征
-> 提取或更新视频特征
-> 训练模型
-> 评估预测
-> 输出报告
```

理想情况下，服务器上只需要一个命令：

```bash
python -m intent_mgsv_pipeline.server.run_pipeline --config configs/server.yaml
```

执行后自动完成：

```text
1. 导出数据库当前快照
2. 清洗数据表
3. 生成 train / val / test
4. 提取音频特征
5. 提取视频特征
6. 训练指定模型
7. 评估预测结果
8. 写入实验报告
```

## 八、迁移阶段规划

### Phase 1：服务器路径与配置整理

目标：先让项目能在服务器固定目录下稳定运行。

- 增加服务器配置文件；
- 所有路径改成可配置；
- 不再写死 `E:\MGSV_preprocessor`；
- 保留当前 Excel 工作流，但明确导入/导出边界。

### Phase 2：数据库作为标注源

目标：解决多人同时写 Excel 会互相覆盖的问题。

- 新建数据库 schema；
- 写 Excel 导入数据库脚本；
- 写数据库导出 Excel/CSV 脚本；
- 给每个标注者增加 annotator_id；
- 保存时改成单条 annotation 写入数据库；
- 增加领取任务和锁机制。

### Phase 3：后台任务队列

目标：把下载、对齐、分镜检测这些耗时操作从页面里拆出去。

- 新增 jobs 表；
- 增加后台 worker；
- 标注页面提交 job；
- 页面显示 job 状态和结果；
- 每个失败任务记录错误日志。

### Phase 4：多人标注合并

目标：支持第二标注者、第三标注者，并能生成共识标签。

- 每个标注者独立保存 annotation；
- 分段分数取平均；
- emotion / style / usage_scene 取并集；
- vocal_presence / genre 可按项目设定只保留主标注者版本；
- 输出分歧报告。

### Phase 5：训练自动化

目标：从当前数据库一键生成训练结果。

- 一键导出数据；
- 一键清洗与划分；
- 一键特征提取；
- 一键训练；
- 一键评估；
- 每次实验保存到独立目录。

## 九、当前最应该先做的事情

当前最优先的不是继续堆模型，而是先把多人标注底座稳住。

建议下一步实现：

```text
intent_mgsv_pipeline/server/schema.sql
intent_mgsv_pipeline/server/import_excel_to_db.py
intent_mgsv_pipeline/server/export_db_to_excel.py
intent_mgsv_pipeline/server/assignment.py
```

然后再把 `annotate_tool.py` 增加一个可选的数据库模式。

这样之后无论是你自己标注、同学一起标注，还是服务器批量下载和训练，都不会再依赖多人共同写同一个 Excel 文件。
