# Intent-MGSV 非主标注员操作手册

## 一、适用范围

本手册供第二位、第三位及更多非主标注员使用。

所有标注员共同访问服务器上的复标网页，但必须使用各自唯一且长期固定的标注员
ID。服务器会按照“视频 ID + 标注员 ID”分别保存数据，因此不同标注员之间不会
互相覆盖。

非主标注员只重新标注以下内容：

```text
emotion
style
usage_scene
seg_scores_3
seg_scores_5
```

以下内容直接继承主标注员 owner 的结果，不需要重新填写：

```text
是否卡点 sync_level
方案 A / B 分镜点
vocal_presence
genre
歌曲及完整音乐 offset
歌曲人工核验结果
```

## 二、开始前的条件

一条视频只有同时满足以下条件，才会开放给非主标注员：

```text
owner 已完成主标注
歌曲已经人工确认
song_verified = Yes
视频没有被删除
```

因此，复标网页显示的总数可能小于服务器视频总数。这不是数据丢失，而是尚未通过
owner 标注和音乐核验的样本暂时不会开放。

开始多人标注前，管理员应确认服务正在运行：

```bash
cd /data/users/ccw/intent_mgsv/repo/MGSV_preprocessor
source config/server.env
conda activate mgsv_data

bash intent_mgsv_pipeline/server/manage_annotation_services.sh status
```

应看到：

```text
mgsv-owner: running
mgsv-peer: running
mgsv-music-review: running
```

非主标注员统一使用 `mgsv-peer`，即服务器的 `7861` 端口。

## 三、分配标注员 ID

管理员应提前给每位同学分配一个唯一 ID，例如：

```text
peer_zhang
peer_li
peer_wang
```

ID 规则：

1. 推荐只使用英文字母、数字和下划线。
2. 不使用空格、中文姓名或临时昵称。
3. 不得使用 `owner`。
4. 两名同学不得共用同一个 ID。
5. 同一位同学每次进入网页都必须使用完全相同的 ID。
6. ID 区分不同字符串，输入错误会被当成一位新的标注员。

建议管理员保存一张对应表：

```text
真实姓名    标注员 ID
张同学      peer_zhang
李同学      peer_li
```

## 四、从个人电脑连接服务器

每位标注员只需要浏览器和 SSH 客户端，不需要在自己的电脑安装 Python、Conda 或
项目依赖。

在 Windows PowerShell 中执行：

```powershell
ssh -p 32769 -N -o ExitOnForwardFailure=yes `
  -L 17861:127.0.0.1:7861 `
  ubuntu@121.48.162.165
```

输入服务器密码后，这个 PowerShell 窗口会保持空白，这是正常现象，表示隧道正在
工作。标注期间不要关闭该窗口。

然后在本机浏览器打开：

```text
http://127.0.0.1:17861
```

不同同学在各自电脑上都可以使用本地端口 `17861`，不会冲突。如果某位同学本机的
`17861` 已被占用，可以改成其他端口：

```powershell
ssh -p 32769 -N -o ExitOnForwardFailure=yes `
  -L 27861:127.0.0.1:7861 `
  ubuntu@121.48.162.165
```

对应访问：

```text
http://127.0.0.1:27861
```

## 五、领取和恢复任务

1. 在“标注员 ID”中输入管理员分配的固定 ID。
2. 点击“开始 / 继续”。
3. 系统会优先恢复该 ID 上次尚未完成的视频。
4. 如果没有未完成视频，系统会领取下一条可标样本。
5. 页面显示的进度只属于当前 ID，不是所有标注员的合计进度。

刷新网页或 SSH 临时断开不会清除已保存数据。重新连接后，输入相同 ID 并点击
“开始 / 继续”，即可恢复。

不要同时在多个浏览器标签页中使用同一个标注员 ID，以免两个页面对同一条记录
反复写入。

## 六、逐条标注流程

### 6.1 观看完整视频

先完整播放当前视频，理解视频的主体内容、情绪、剪辑方式和使用场景。不要只看
标题或标签作答。

### 6.2 对每个可见片段评分

页面中的方案 A 和方案 B 来自主标注员已经确认的分镜点：

```text
方案 A：Top-3 分镜方案
方案 B：Top-5 分镜方案
```

点击“播放 A段1”“播放 A段2”等按钮，会播放对应视频片段。每个显示出来的片段都
必须选择一个 1–5 分：

```text
1：音乐与画面明显不契合
2：契合度较低
3：一般或基本可接受
4：契合度较高
5：音乐与画面非常契合
```

评分原则：

1. 评价当前片段的画面内容、动作、情绪和剪辑节奏与音乐是否契合。
2. 不因为歌曲本身好听就给高分。
3. 不因为喜欢视频主题就给高分。
4. 同一视频的不同片段必须分别判断。
5. 页面显示几个评分项，就必须填写几个。
6. 方案 B 没有显示时，不需要填写方案 B。
7. 非卡点视频通常只显示整段评分。

### 6.3 选择 Emotion

Emotion 描述音乐与视频结合后传达的情绪。可以多选，但只选择能够从视频中明确
感受到的标签。

不要把含义相近的所有选项全部勾选，也不要因为不确定而机械选择“中性”标签。

### 6.4 选择 Style

Style 描述视频整体的审美风格、内容气质和剪辑表达。可以跨小类多选，例如：

```text
电影感
旅行
转场
节奏感强
```

只选择画面和剪辑中确实存在的特征。

### 6.5 选择 Usage Scene

Usage Scene 描述这段配乐适合或正在服务的内容场景，例如：

```text
跑步
学习
旅行Vlog
舞蹈
游戏剪辑
风景
婚礼
夜晚
```

它不是视频中出现过的所有物体清单，应选择主要使用场景。

### 6.6 保存与完成

页面选择发生变化时会自动保存为 `in_progress`。

按钮区别：

```text
保存当前修改
只保存当前内容，不把该条记为完成。

保存并进入下一条
检查所有必填项；通过后把该条记为 completed，并领取下一条。

上一条
先保存当前内容，再返回当前标注员已完成的上一条记录。
```

以下内容是完成必填项：

```text
Emotion 至少一个
Style 至少一个
Usage Scene 至少一个
方案 A 所有可见片段评分
方案 B 所有可见片段评分（仅在方案 B 可见时）
```

缺少任何一项时，系统会停留在当前视频并显示缺失项，不会误跳到下一条。

## 七、中断、返回和修改

### 临时结束

确认页面已经显示“当前修改已保存”后，可以直接关闭浏览器和 SSH 窗口。下次使用
相同标注员 ID 继续。

### 修改历史记录

点击“上一条”会先保存当前表单，再打开该标注员自己的上一条已完成记录。修改后：

```text
点击“保存当前修改”只保存修改
点击“保存并进入下一条”重新校验并完成该条
```

每位标注员只能修改自己的结果，不会修改 owner 或其他同学的数据。

## 八、多人并发规则

系统支持两名及以上非主标注员同时工作：

```text
peer_zhang -> 自己的 annotations 记录
peer_li    -> 自己的 annotations 记录
peer_wang  -> 自己的 annotations 记录
```

不同标注员可以同时标注同一个视频，这是为了计算标注一致性，不属于任务冲突。

SQLite 数据库已启用 WAL 模式，正常网页保存使用短事务。不要让任何标注员直接
打开、复制覆盖或编辑服务器数据库，也不要让多人共同编辑同一个 Excel。

## 九、常见问题

### 网页打不开

1. 检查 SSH 隧道窗口是否仍在运行。
2. 检查浏览器地址是否为本地端口，例如 `127.0.0.1:17861`。
3. 让管理员检查 `mgsv-peer` 服务状态。

### 网页打开但视频不显示

让管理员检查：

```bash
tail -n 100 outputs/server/logs/mgsv-peer.log
```

同时确认视频文件仍存在于服务器下载目录。

### 显示“当前没有待标样本”

可能原因：

1. 当前 ID 已经完成全部开放样本。
2. owner 尚未完成更多样本。
3. 歌曲尚未人工核验。
4. 输入了错误的新 ID。

### 保存后进度没有增加

通常表示必填项未完成。查看页面提示，补齐 Emotion、Style、Usage Scene 和全部
可见分段评分，再点击“保存并进入下一条”。

### 刷新后进入同一条视频

这是正常的断点恢复。当前视频尚未完成，所以系统优先恢复它。

## 十、管理员监控与备份

正式开始多人标注前备份数据库：

```bash
python -m intent_mgsv_pipeline.server.backup_database \
  --db "$MGSV_DB" \
  --out-dir outputs/server/backups
```

查看各标注员进度：

```bash
python - <<'PY'
import os
from pathlib import Path
from intent_mgsv_pipeline.server.db import connect

with connect(Path(os.environ["MGSV_DB"])) as conn:
    rows = conn.execute("""
        SELECT annotator_id, status, COUNT(*) AS count
        FROM annotations
        GROUP BY annotator_id, status
        ORDER BY annotator_id, status
    """).fetchall()

for row in rows:
    print(row["annotator_id"], row["status"], row["count"])
PY
```

管理员不得在正式多人标注后使用旧 Excel 覆盖服务器数据库，也不得使用带
`--replace` 的导入命令。

## 十一、合并两位以上非主标注员

当 owner、`peer_zhang` 和 `peer_li` 都完成一批共同视频后，执行：

```bash
python -m intent_mgsv_pipeline.server.export_consensus \
  --db "$MGSV_DB" \
  --owner-id owner \
  --peer-id peer_zhang \
  --peer-id peer_li \
  --out outputs/server/MGSV_Master_Dataset.consensus.xlsx
```

可以继续重复 `--peer-id` 加入更多标注员。

只有所有指定标注员都标记为 `completed` 的共同视频才会进入最终共识表。

合并规则：

```text
emotion / style / usage_scene
对所有指定标注员取并集

seg_scores_3 / seg_scores_5
每个对应片段对所有指定标注员求平均值

vocal_presence / genre
保留 owner 的结果

sync_level / shot_points
保留 owner 的结果
```

输出文件：

```text
MGSV_Master_Dataset.consensus.xlsx
MGSV_Master_Dataset.consensus.disagreements.xlsx
MGSV_Master_Dataset.consensus.summary.json
```

其中 `disagreements.xlsx` 会保留每位标注员的原始答案和共识答案，便于后续分析
标注一致性和处理高分歧样本。
