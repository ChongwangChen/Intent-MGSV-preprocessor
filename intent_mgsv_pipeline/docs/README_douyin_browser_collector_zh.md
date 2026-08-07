# 抖音浏览器链接采集器

## 一、功能

采集器使用可见 Chrome 浏览器：

```text
人工登录抖音
-> 按配置表自动输入关键词
-> 切换“视频”或“图文”搜索标签
-> 逐个点击搜索结果封面进入作品详情
-> 点击右侧“分享”，再点击“复制链接”
-> 从剪贴板分享文案提取 /video/ 或 /note/ 标准链接
-> 排除历史输出与 DouK 已下载作品
-> 每 20 条生成一个 TXT
```

如果分享弹窗或剪贴板偶发不可用，采集器会使用详情页 URL 中的
`modal_id` 生成相同作品的标准链接，不会因此把整批结果变成 0 条。

它不会绕过验证码、滑块或平台限制。浏览器出现验证时，人工完成后回到
终端按回车继续。

## 二、安装

```powershell
cd E:\MGSV_preprocessor
conda activate mgsv_data
python -m pip install -r requirements_collection.txt
```

脚本使用电脑已安装的 Google Chrome，无需运行 `playwright install` 下载额外浏览器。

## 三、配置关键词

编辑：

```text
config/douyin_collection_keywords.csv
```

字段：

```text
keyword      搜索关键词
content_type video / image / all
quota        该关键词希望新收集的数量
```

`image` 只保留 `/note/` 图文链接，`video` 只保留 `/video/` 链接。

## 四、预检

只检查关键词表与分批数量：

```powershell
python -m intent_mgsv_pipeline.data_collection.collect_douyin_browser_links `
  --dry-run
```

只检查 Playwright 能否启动本机 Chrome：

```powershell
python -m intent_mgsv_pipeline.data_collection.collect_douyin_browser_links `
  --browser-smoke-test
```

Chrome 短暂打开并自动关闭，终端显示 `Browser smoke test passed` 即为正常。

## 五、首次采集

```powershell
python -m intent_mgsv_pipeline.data_collection.collect_douyin_browser_links
```

1. 脚本打开专用 Chrome 窗口。
2. 在该窗口登录抖音。
3. 手动完成可能出现的验证。
4. 回到 PowerShell 按回车。
5. 脚本自动搜索、点击封面、分享复制、返回列表、滚动和分批。

运行时请保持这个专用 Chrome 窗口可见，不要在窗口内手动切换页面或点击
其他作品。若弹出验证码，先人工完成验证，再按终端提示继续。

同一关键词下会连续处理多条作品。每条详情默认至少停留3秒，复制后优先
使用 `Esc` 原地关闭详情并继续当前列表，不再为每条作品重新加载搜索页。
这用于稳定页面和保留人工观察时间，但不能保证平台不再要求验证。

首屏卡片处理完后，程序会把最后一张已加载卡片滚入视野，并向结果区域发送
滚轮事件以触发下一批瀑布流内容。终端中的 `collected` 可以因此继续超过
首屏常见的“视频3条/图文2条”，不会再仅凭 `body.scrollTop` 误判到底。

默认采前筛选规则：

```text
卡片显示时长超过 5 分钟：跳过
标题/标签明确写有清唱、翻唱或现场演唱：跳过
标题/标签明确写有钢琴、吉他等乐器演奏：跳过
语义不明确、普通纯音乐、舞蹈、卡点、Vlog：保留
```

筛选只依据搜索卡片可见的标题、标签和时长，采用保守规则。它不能完全代替
人工核验，也不会根据画面猜测并删除不确定内容。

每成功复制一条链接，程序都会立即写入当前会话的
`_checkpoint_links.txt`。网络跳转失败、关闭浏览器或按 `Ctrl+C` 时，已经
复制的链接仍会保留；正常结束后会自动整理为 `dydownload_XX.txt`。

登录状态保存在：

```text
outputs/browser_profiles/douyin_collector
```

这是专用配置目录，不会读取日常 Chrome 个人配置。

## 六、后续采集

已确认专用 Chrome 仍保持登录时：

```powershell
python -m intent_mgsv_pipeline.data_collection.collect_douyin_browser_links `
  --skip-login-wait
```

## 七、输出

每次运行建立时间戳目录：

```text
outputs/douyin_link_collection/20260806_120000/
  dydownload_01.txt
  dydownload_02.txt
  dydownload_03.txt
  collected_links.csv
  summary.json
```

TXT 默认每批 20 条，可直接作为 DouK 的“从文本文档读取待采集链接”输入。

## 八、去重

采集前自动排除：

```text
outputs/douyin_link_collection 中历史 TXT
DouK-Source/Volume/Data/Download.xlsx 中已采集作品
当前运行中不同关键词搜到的重复作品
```

因此同一作品不会在后续批次中重复输出。

## 九、常用参数

```text
--batch-size 20       每个 TXT 的链接数
--max-scrolls 60      每个关键词最多滚动次数
--scroll-pause-ms 1600 每次滚动等待时间
--detail-dwell-ms 3000 每条详情至少停留的毫秒数（不得低于 3000）
--max-duration-seconds 300 超过该时长的卡片跳过
--disable-content-filter 关闭时长与明确非目标内容筛选
--skip-login-wait     不在首页等待人工按回车
```
