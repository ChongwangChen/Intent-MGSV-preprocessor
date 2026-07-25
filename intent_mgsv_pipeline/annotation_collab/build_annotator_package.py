from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from export_second_annotator_template import DEFAULT_OUT as DEFAULT_TEMPLATE


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PACKAGE = PROJECT_ROOT / "outputs" / "inter_annotator" / "annotator_b_package"

MEDIA_DIRS = [
    Path("DouK-Source") / "Volume" / "Download",
    Path("outputs") / "full_music",
    Path("outputs") / "full_songs",
]


README = """# Intent-MGSV Annotator B 标注包

你需要完成第二轮主观标注。卡点/不卡点、分镜点、歌曲对齐区间、vocal_presence、genre 都已经固定，请不要修改。

## 你需要标注的字段

只需要重新标注：

- emotion
- style
- usage_scene
- seg_scores_3
- seg_scores_5

不要修改：

- sync_level
- shot_points
- shot_points_3
- shot_points_5
- music_start
- music_end
- full_song_path
- song_title
- song_artist
- vocal_presence
- genre

如果你觉得固定字段明显有错，请写在 `second_annotator_note`，不要直接修改固定字段。

## 目录内容

这个包至少应包含：

```text
annotate_tool.py
requirements_annotator.txt
outputs/MGSV_Master_Dataset.xlsx
outputs/full_music/
outputs/full_songs/
DouK-Source/Volume/Download/
```

如果你收到的压缩包里没有媒体目录，请联系数据负责人重新打包；没有这些目录，网页里无法播放视频和音频。

## 创建 Conda 环境

推荐使用 Python 3.10。

```powershell
conda create -n mgsv_annotator python=3.10 -y
conda activate mgsv_annotator
pip install -r requirements_annotator.txt
```

如果 `pip install` 很慢，可以换清华源：

```powershell
pip install -r requirements_annotator.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

## 启动标注工具

进入本文件所在目录：

```powershell
cd <你的标注包目录>
conda activate mgsv_annotator
python .\\annotate_tool.py
```

浏览器会打开：

```text
http://127.0.0.1:7860
```

## 标注规则

1. 每条数据先播放左侧原声/视频和右侧完整歌曲。
2. 不需要下载歌曲。
3. 不需要重新检测分镜。
4. 不需要修改卡点/不卡点。
5. 不需要修改 vocal_presence 和 genre。
6. 只标 emotion、style、usage_scene 和分段分数。
7. 分段分数按当前界面显示的 A/B 段逐段打分。
8. 如果分镜或歌曲明显不对，在 `second_annotator_note` 备注。

## 保存和返回

标注过程中工具会写入：

```text
outputs/MGSV_Master_Dataset.xlsx
```

标注完成后，把这个文件返回给数据负责人，并命名为：

```text
annotator_b_completed.xlsx
```

请不要只发截图，也不要只发浏览器缓存。
"""


def copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.tmp", "*.part"))


def dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    parser.add_argument("--out-dir", default=str(DEFAULT_PACKAGE))
    parser.add_argument("--copy-media", action="store_true", help="Copy video/audio directories into the package.")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    copy_file(PROJECT_ROOT / "annotate_tool.py", out_dir / "annotate_tool.py")
    copy_file(PROJECT_ROOT / "requirements_annotator.txt", out_dir / "requirements_annotator.txt")
    copy_file(Path(args.template), out_dir / "outputs" / "MGSV_Master_Dataset.xlsx")
    (out_dir / "outputs" / "annotation_backups").mkdir(parents=True, exist_ok=True)
    (out_dir / "outputs" / "inter_annotator").mkdir(parents=True, exist_ok=True)

    copied_media = []
    media_sizes = {}
    for rel in MEDIA_DIRS:
        src = PROJECT_ROOT / rel
        dst = out_dir / rel
        media_sizes[str(rel)] = dir_size(src)
        if args.copy_media:
            copy_tree(src, dst)
            copied_media.append(str(rel))

    readme_path = out_dir / "README.md"
    readme_path.write_text(README, encoding="utf-8")

    summary = {
        "package_dir": str(out_dir),
        "template": str(args.template),
        "copy_media": bool(args.copy_media),
        "copied_media": copied_media,
        "required_media_dirs": [str(p) for p in MEDIA_DIRS],
        "media_sizes_bytes": media_sizes,
    }
    with open(out_dir / "package_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
