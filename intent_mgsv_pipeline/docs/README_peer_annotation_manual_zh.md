# Intent-MGSV 非主标注员操作手册

## 一、任务说明

本手册供非主标注员 `yxh` 和 `ls` 使用。

两位标注员需要分别完成同一批符合条件的视频，而不是各标一半。系统会按照
“视频 ID + 标注员 ID”独立保存结果，不会相互覆盖。最后对同一视频的分段评分
求平均值，对多选标签取并集。

非主标注员只重新标注：

```text
emotion
style
usage_scene
seg_scores_3
seg_scores_5
```

以下内容继承主标注员 `owner` 的结果，不需要修改：

```text
是否卡点 sync_level
方案 A / B 分镜点
vocal_presence
genre
完整歌曲和 offset
歌曲人工核验结果
```

## 二、固定标注员 ID

```text
真实姓名    标注员 ID
颜显骅      yxh
刘硕        ls
```

每次进入网页必须输入完全相同的固定 ID。不要使用中文名、临时昵称、对方 ID 或
`owner`，输入错误会被系统当作一位新的标注员。

## 三、可标数据条件

视频只有同时满足以下条件才会开放：

```text
owner 已完成主标注
歌曲和对齐已经人工确认
song_verified = Yes
视频文件仍然存在
视频没有被删除
```

因此，网页显示的总数可能小于服务器的视频总数。这通常表示部分视频仍在音乐
核验或主标注流程中，不是数据丢失。

系统不会提前为 `yxh` 和 `ls` 复制全部数据库记录。两人第一次领取某条视频时，
系统才会建立各自独立的标注记录。

## 四、连接信息

标注员自己的电脑只需要：

```text
浏览器
Windows PowerShell 或其他 SSH 客户端
服务器地址、端口、用户名和管理员单独发送的密码
```

不需要在个人电脑安装 Python、Conda 或项目依赖。

服务器连接信息：

```text
服务器：121.48.162.165
SSH 端口：32769
用户名：ubuntu
复标网页服务器端口：7861
```

服务器密码由管理员单独发送，不写入本手册。

## 五、主标注员不在场时启动网页

只要服务器已经开机且能够 SSH 登录，任意一位非主标注员都可以自行启动复标
网页。先在 Windows PowerShell 执行：

```powershell
ssh -p 32769 ubuntu@121.48.162.165
```

登录后，在服务器终端执行：

```bash
cd /data/users/ccw/intent_mgsv/repo/MGSV_preprocessor
source config/server.env
conda activate mgsv_data

bash intent_mgsv_pipeline/server/manage_annotation_services.sh status peer
bash intent_mgsv_pipeline/server/manage_annotation_services.sh start peer
bash intent_mgsv_pipeline/server/manage_annotation_services.sh status peer
```

正常结果：

```text
mgsv-peer: running
```

如果服务已经运行，系统会提示：

```text
mgsv-peer already running
```

这不是报错，也不会重复启动。网页运行在服务器的 `tmux` 后台会话中，退出这次
普通 SSH 登录不会关闭网页。

如果执行 `status peer` 却显示 owner、peer、music-review 三个服务，说明服务器
仍是旧版代码。不要继续操作服务，请联系管理员更新代码。

### 禁止执行

非主标注员只使用：

```text
status peer
start peer
```

标注期间不要执行：

```text
stop peer
restart peer
不带 peer 参数的 start / stop / restart
```

这些命令可能中断另一位同学正在进行的保存。

## 六、建立本地 SSH 隧道

确认 `mgsv-peer: running` 后，可以退出普通 SSH 登录。重新打开一个 PowerShell
窗口，执行：

```powershell
ssh -p 32769 -N -o ExitOnForwardFailure=yes `
  -L 17861:127.0.0.1:7861 `
  ubuntu@121.48.162.165
```

输入密码后窗口保持空白是正常现象。标注期间不要关闭这个窗口。

浏览器打开：

```text
http://127.0.0.1:17861
```

如果本机 `17861` 已被占用，可以改用：

```powershell
ssh -p 32769 -N -o ExitOnForwardFailure=yes `
  -L 27861:127.0.0.1:7861 `
  ubuntu@121.48.162.165
```

对应浏览器地址：

```text
http://127.0.0.1:27861
```

两位同学在各自电脑使用相同的本地端口不会冲突。

## 七、开始和恢复标注

1. 在“标注员 ID”中输入自己的固定 ID：`yxh` 或 `ls`。
2. 点击“开始 / 继续”。
3. 系统优先恢复该 ID 上次未完成的视频。
4. 如果没有未完成视频，系统领取下一条可标视频。
5. 页面进度只属于当前 ID，不是两人的合计进度。

刷新网页、关闭浏览器或 SSH 临时断开不会清空已保存结果。重新连接后输入相同 ID
即可继续。

不要同时在多个浏览器标签页或多台电脑上使用同一个 ID。

## 八、逐条标注流程

### 8.1 观看完整视频

先完整播放视频，理解主体内容、配乐情绪、剪辑方式和主要使用场景。不要只根据
标题、作者或标签作答。

### 8.2 播放并评分每个片段

页面中的分段方案来自主标注员确认的分镜点：

```text
方案 A：Top-3 分镜方案
方案 B：Top-5 分镜方案
```

点击“播放 A段1”“播放 B段2”等按钮，观看对应视频片段。页面显示几个评分项，就
必须填写几个。

评分标准：

```text
1：音乐与画面明显不契合
2：契合度较低
3：一般或基本可接受
4：契合度较高
5：音乐与画面非常契合
```

注意：

1. 评价画面内容、动作、情绪和剪辑节奏与音乐的契合度。
2. 不要因为歌曲本身好听就给高分。
3. 不要因为喜欢视频主题就给高分。
4. 不同片段需要分别判断。
5. 方案 B 没有显示时，不需要填写方案 B。
6. 非卡点视频通常只显示整段评分。

### 8.3 Emotion

Emotion 描述配乐在当前视频中传达的主要情绪。可以多选，以配乐听感为主并结合
画面语境，只选择能够明确感受到的标签。

不要把含义相近的标签全部勾选，也不要因为不确定而机械选择中性标签。

### 8.4 Style

Style 描述视频整体审美、内容气质和剪辑表达，可以跨类别多选，例如：

```text
电影感
旅行
转场
节奏感强
```

只选择画面和剪辑中确实存在的风格。

### 8.5 Usage Scene

Usage Scene 描述这段配乐正在服务或适合服务的主要内容场景，例如：

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

它不是视频中出现过的全部物体清单，应选择主要场景。

### 8.6 保存

```text
保存当前修改
只保存当前内容，不把该条记为完成。

保存并进入下一条
检查必填项，通过后将该条记为 completed，并领取下一条。

上一条
先保存当前内容，再返回当前标注员自己的上一条记录。
```

完成一条视频必须满足：

```text
Emotion 至少一个
Style 至少一个
Usage Scene 至少一个
方案 A 所有可见评分项
方案 B 所有可见评分项（仅在方案 B 可见时）
```

缺少任何一项时，系统会停留并显示缺失项。

## 九、返回和修改

需要修改历史结果时，点击“上一条”。系统会先保存当前表单，再打开当前标注员
自己的上一条记录。

修改后：

```text
保存当前修改：只保存，不跳转
保存并进入下一条：重新校验并完成，然后继续
```

每位标注员只能修改自己的结果，不会修改 `owner` 或另一位同学的数据。

## 十、发现歌曲、对齐或分镜错误

非主标注员不要自行修改继承的歌曲、Genre、offset 或分镜点，也不要勉强完成
明显存在问题的视频。

请记录：

```text
视频 ID
问题类型：歌曲错误 / 对齐错误 / 分镜错误
简短说明
```

然后发给管理员处理。

管理员退回歌曲核验后，该视频会暂时停止向非主标注员开放；修正并重新确认后才会
再次开放。已经保存的个人标签不会覆盖 `owner` 数据。

## 十一、中断和结束

临时结束前确认页面显示当前修改已保存，然后可以关闭浏览器和 SSH 隧道窗口。

不要停止服务器的 `mgsv-peer` 服务，因为另一位同学可能仍在使用。服务长期运行
不会要求主标注员电脑保持开机。

## 十二、常见问题

### 网页打不开

1. 检查 SSH 隧道窗口是否仍在运行。
2. 检查地址是否为 `127.0.0.1:17861` 或自己设置的本地端口。
3. 重新登录服务器，执行 `status peer`。
4. 若服务停止，执行 `start peer`。

### 视频不显示

在服务器项目目录执行：

```bash
tail -n 100 outputs/server/logs/mgsv-peer.log
```

把输出和当前视频 ID 发给管理员。

### 当前没有待标样本

可能原因：

1. 当前 ID 已完成全部开放样本。
2. owner 尚未完成更多样本。
3. 部分歌曲尚未通过人工核验。
4. 输入了错误 ID。

### 保存后进度不增加

查看页面提示，补齐 Emotion、Style、Usage Scene 和全部可见分段评分，再点击
“保存并进入下一条”。

### 刷新后仍是同一条

这是正常的断点恢复，说明当前视频尚未完成。

## 十三、管理员操作

### 开始前备份

```bash
source config/server.env
conda activate mgsv_data

python -m intent_mgsv_pipeline.server.backup_database \
  --db "$MGSV_DB" \
  --out-dir outputs/server/backups
```

### 查看两位标注员进度

```bash
python - <<'PY'
import os
from pathlib import Path
from intent_mgsv_pipeline.server.assignment import annotation_progress

db = Path(os.environ["MGSV_DB"])
for annotator_id in ("yxh", "ls"):
    print(annotator_id, annotation_progress(db, annotator_id, "owner"))
PY
```

两人的 `total` 应相同。正式多人标注后，不要用旧 Excel 覆盖服务器数据库，也
不要执行带 `--replace` 的导入命令。

## 十四、导出多人共识

当 `owner`、`yxh` 和 `ls` 都完成共同样本后执行：

```bash
python -m intent_mgsv_pipeline.server.export_consensus \
  --db "$MGSV_DB" \
  --owner-id owner \
  --peer-id yxh \
  --peer-id ls \
  --out outputs/server/MGSV_Master_Dataset.consensus.xlsx
```

合并规则：

```text
emotion / style / usage_scene
对 owner、yxh、ls 取并集

seg_scores_3 / seg_scores_5
对每个对应片段求平均值

vocal_presence / genre
保留 owner 结果

sync_level / shot_points / 歌曲 offset
保留 owner 结果
```

只有所有指定标注员均为 `completed` 的共同视频才进入最终共识表。

输出：

```text
MGSV_Master_Dataset.consensus.xlsx
MGSV_Master_Dataset.consensus.disagreements.xlsx
MGSV_Master_Dataset.consensus.summary.json
```
