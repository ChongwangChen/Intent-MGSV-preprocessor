# 本地 Codex 开发 + 服务器 Git 同步建议

本文档说明在“本地 Codex 更稳定、服务器 Codex 暂时不好用”的情况下，如何用 Git 推进 Intent-MGSV 项目。

## 一、推荐分工

```text
本地电脑：
  写代码、改文档、做小规模 smoke test

服务器：
  保存共享数据、多人标注、后台下载、特征提取、训练
```

核心原则：

> Git 只同步代码和文档，不同步大数据、cookie、数据库和实验输出。

## 二、应该进入 Git 的内容

推荐纳入 Git：

```text
README.md
AGENTS.md
annotate_tool.py
music_fetch.py
auto.py
shot_detect.py
transnetv2.py
pipeline.py
migrate_dataset_v2.py
yt_dy_auto.py
requirements_annotator.txt
intent_mgsv_pipeline/
.gitignore
```

这些是项目逻辑、工具、文档和服务器迁移代码。

## 三、不应该进入 Git 的内容

不要纳入 Git：

```text
outputs/
DouK-Source/Volume/
qqmusic_cookies.txt
qqmusic_cookies_*.txt
acrcloud_config.json
transnetv2-weights/
TransNetV2/
tmp_preview/
*.sqlite3
*.pt
*.npz
```

原因：

- `outputs/` 里有主表、歌曲、特征、实验结果，体积大且经常变化；
- `DouK-Source/Volume/` 是下载数据池；
- cookie 和 ACRCloud 配置可能包含登录态或密钥；
- 数据库是服务器多人标注的事实来源，不能被本地旧版本覆盖；
- checkpoint 和 feature cache 可以在服务器重新生成。

## 四、第一次上服务器的建议

第一次可以通过 Xftp 把完整目录传到服务器，用于初始化数据。

之后推荐改成：

```text
代码：Git 同步
数据：服务器保留，不再被本地覆盖
```

也就是说，服务器一旦开始多人标注，不要再整体覆盖：

```text
outputs/MGSV_Master_Dataset.xlsx
outputs/server/intent_mgsv.sqlite3
outputs/full_music/
outputs/full_songs/
DouK-Source/Volume/Download/
```

## 五、本地提交流程

本地修改代码后：

```bash
git status
git add README.md AGENTS.md annotate_tool.py music_fetch.py auto.py shot_detect.py transnetv2.py pipeline.py migrate_dataset_v2.py yt_dy_auto.py requirements_annotator.txt intent_mgsv_pipeline .gitignore
git commit -m "Update Intent-MGSV pipeline"
git push
```

提交前建议至少跑一次：

```bash
python intent_mgsv_pipeline/server/import_excel_to_db.py --input outputs/MGSV_Master_Dataset.xlsx --db outputs/server/intent_mgsv.local_check.sqlite3 --annotator-id owner --replace
python intent_mgsv_pipeline/server/export_db_to_excel.py --db outputs/server/intent_mgsv.local_check.sqlite3 --out outputs/server/MGSV_Master_Dataset.local_check.xlsx --annotator-id owner
```

本地检查产物在 `outputs/server/`，不会进入 Git。

## 六、服务器更新流程

服务器上更新代码：

```bash
git pull
```

然后根据修改内容运行检查：

```bash
python intent_mgsv_pipeline/server/import_excel_to_db.py --help
python intent_mgsv_pipeline/server/export_db_to_excel.py --help
python intent_mgsv_pipeline/server/assignment.py --help
```

如果已经有正式服务器数据库，更新代码前建议备份：

```bash
cp outputs/server/intent_mgsv.sqlite3 outputs/server/intent_mgsv.sqlite3.backup.$(date +%Y%m%d_%H%M%S)
```

## 七、服务器正式运行时的危险操作

正式多人标注开始后，避免：

- 从本地覆盖服务器 `outputs/`；
- 从本地覆盖服务器数据库；
- 把本地旧 Excel 导入服务器数据库时使用 `--replace`；
- 把 cookie、密钥、数据库文件提交到 Git；
- 在多人标注中继续让多个进程写同一个 Excel。

## 八、当前建议

短期推荐：

1. 本地初始化 Git；
2. 按 `.gitignore` 只提交代码和文档；
3. 第一次仍可用 Xftp 整体传数据；
4. 服务器开始标注后，只用 Git 更新代码；
5. 数据库和媒体文件留在服务器，不从本地覆盖。

