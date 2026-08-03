# 恢复旧数据的音乐核验队列

适用于 owner 标签已经完整，但服务器数据库中没有 `music_preparations`，只缺歌曲
人工确认的旧记录。

## 安全规则

恢复命令只处理：

```text
owner 记录存在
music_preparations 不存在
除 song confirmation 外的 owner 必填项全部完成
服务器能找到视频文件
服务器能找到完整歌曲文件
```

默认运行是 dry-run，不修改数据库。缺标签或缺歌曲文件的记录只进入报告。

## 第一步：备份

```bash
source config/server.env
conda activate mgsv_data

python -m intent_mgsv_pipeline.server.backup_database \
  --db "$MGSV_DB" \
  --out-dir outputs/server/backups
```

## 第二步：dry-run

```bash
python -m intent_mgsv_pipeline.server.restore_music_review_queue \
  --db "$MGSV_DB"
```

重点检查输出：

```text
ready
missing_video_file
missing_song_file
missing_labels
restored
```

dry-run 时 `restored` 必须为 0。详细清单位于：

```text
outputs/server/diagnostics/restore_music_review_queue.json
```

## 第三步：正式恢复

确认 `ready` 数量合理后执行：

```bash
python -m intent_mgsv_pipeline.server.restore_music_review_queue \
  --db "$MGSV_DB" \
  --apply
```

`ready` 记录会进入 `needs_review`，不会自动确认歌曲。

## 第四步：人工快速确认

打开音乐核验页面 `7862`：

1. 完整歌曲会定位到旧 `music_start`。
2. 试听歌曲和视频。
3. offset 正确时直接点击“歌曲和对齐均正确”。
4. offset 不正确时使用精细重对齐，或手动修改后刷新试听。
5. 确认后，原 owner 标签完整的记录会自动恢复为 `completed`。

## 第五步：测试非主标注员

在 `7861` 使用测试 ID：

```text
test_owner_peer
```

系统不需要提前创建账号。第一次领取时会自动建立该 ID 的独立标注记录。

测试 ID 不要加入正式共识导出命令。测试完成后可以保留作为回归记录；需要删除时
先备份数据库，再由管理员按该 ID 清理。
