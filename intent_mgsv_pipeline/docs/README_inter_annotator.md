# Intent-MGSV 第二标注员协作说明

Date: 2026-07-24

## 当前目标

第二位同学只需要重新标注主观场景语义和分段契合度，不重新判断卡点、不重新检测分镜、不重新下载歌曲。

Annotator B 只标：

- `emotion`
- `style`
- `usage_scene`
- `seg_scores_3`
- `seg_scores_5`

Annotator B 不标、不改：

- `sync_level`
- `shot_points`
- `shot_points_3`
- `shot_points_5`
- `music_start`
- `music_end`
- `full_song_path`
- `song_title`
- `song_artist`
- `vocal_presence`
- `genre`

如果 B 认为固定字段明显错误，只在 `second_annotator_note` 写备注，不直接改固定字段。

## 给同学打包哪些文件

跨电脑标注时，推荐给同学一个独立文件夹，不要让他直接改你的项目主表。

最小必需文件和目录：

```text
annotate_tool.py
requirements_annotator.txt
README.md
outputs/MGSV_Master_Dataset.xlsx
outputs/full_music/
outputs/full_songs/
DouK-Source/Volume/Download/
```

说明：

- `outputs/MGSV_Master_Dataset.xlsx` 是给 B 的模板表，不是你的原始主表。
- `outputs/full_music/` 是抖音原声片段。
- `outputs/full_songs/` 是完整歌曲。
- `DouK-Source/Volume/Download/` 是视频文件。
- `qqmusic_cookies.txt` 不需要给 B，因为 B 不下载歌曲。
- `shot_detect.py` 和模型权重不需要给 B，因为 B 不重新检测分镜。

当前媒体大约 2.7GB：

- `DouK-Source/Volume/Download/`: 约 1.78GB
- `outputs/full_music/`: 约 107MB
- `outputs/full_songs/`: 约 816MB

## 生成 B 的模板

在你的项目根目录运行：

```powershell
E:\Users\30993\miniconda3\envs\mgsv_data\python.exe E:\MGSV_preprocessor\intent_mgsv_pipeline\annotation_collab\export_second_annotator_template.py
```

输出：

```text
outputs/inter_annotator/annotator_b_template.xlsx
outputs/inter_annotator/annotator_b_template.summary.json
```

这个模板会保留 `vocal_presence` 和 `genre`，只清空 B 需要重标的 5 个字段。

## 生成可发送给 B 的标注包

先生成模板，然后运行：

```powershell
E:\Users\30993\miniconda3\envs\mgsv_data\python.exe E:\MGSV_preprocessor\intent_mgsv_pipeline\annotation_collab\build_annotator_package.py --copy-media
```

默认输出目录：

```text
outputs/inter_annotator/annotator_b_package/
```

这个目录可以压缩后发给同学。里面会包含：

```text
README.md
annotate_tool.py
requirements_annotator.txt
outputs/MGSV_Master_Dataset.xlsx
outputs/full_music/
outputs/full_songs/
DouK-Source/Volume/Download/
```

如果暂时不想复制 2.7GB 媒体，只想先生成说明和模板，可以不加 `--copy-media`：

```powershell
E:\Users\30993\miniconda3\envs\mgsv_data\python.exe E:\MGSV_preprocessor\intent_mgsv_pipeline\annotation_collab\build_annotator_package.py
```

但真正给同学标注时必须补齐三个媒体目录。

## B 的环境配置

推荐 Python 3.10。

```powershell
conda create -n mgsv_annotator python=3.10 -y
conda activate mgsv_annotator
pip install -r requirements_annotator.txt
```

如果下载慢：

```powershell
pip install -r requirements_annotator.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

## B 启动标注工具

在 B 的标注包目录运行：

```powershell
conda activate mgsv_annotator
python .\annotate_tool.py
```

浏览器打开：

```text
http://127.0.0.1:7860
```

## B 的操作规则

1. 只标 `emotion/style/usage_scene/seg_scores_3/seg_scores_5`。
2. 不点击“下载/搜索原曲”。
3. 不点击“重新检测当前分镜”。
4. 不修改 `sync_level`。
5. 不修改 `vocal_presence` 和 `genre`。
6. 如果发现歌曲、分镜、卡点状态明显错误，写在 `second_annotator_note`。

## B 返回什么

B 标完后，只需要返回：

```text
outputs/MGSV_Master_Dataset.xlsx
```

你收到后重命名并放到：

```text
outputs/inter_annotator/annotator_b_completed.xlsx
```

## 合并 A/B 标注

运行：

```powershell
E:\Users\30993\miniconda3\envs\mgsv_data\python.exe E:\MGSV_preprocessor\intent_mgsv_pipeline\annotation_collab\merge_annotators.py
```

输出：

```text
outputs/inter_annotator/MGSV_Master_Dataset.consensus.xlsx
outputs/inter_annotator/MGSV_Master_Dataset.consensus.disagreements.xlsx
outputs/inter_annotator/MGSV_Master_Dataset.consensus.summary.json
```

合并规则：

- `emotion/style/usage_scene`: 取并集。
- `seg_scores_3/seg_scores_5`: 逐段取平均。
- `vocal_presence/genre`: 保留 Annotator A 的原值。
- `sync_level/shot_points/music_start/music_end`: 保留 Annotator A 的原值。

示例：

```text
emotion A: 治愈/温暖
emotion B: 温暖/浪漫
合并: 治愈/温暖/浪漫

seg_scores_3 A: 3/4/5
seg_scores_3 B: 4/4/3
合并: 3.5/4/4
```

合并后先检查 `*.disagreements.xlsx`。如果某些行分段分数差异很大，建议人工复核后再进入最终训练集。
