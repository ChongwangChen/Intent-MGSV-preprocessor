# 抖音浏览器链接采集器

## 一、功能

采集器使用可见 Chrome 浏览器：

```text
人工登录抖音
-> 按配置表自动输入关键词
-> 滚动搜索结果
-> 提取 /video/ 和 /note/ 标准链接
-> 排除历史输出与 DouK 已下载作品
-> 每 20 条生成一个 TXT
```

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
5. 脚本自动搜索、滚动、收集和分批。

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
--skip-login-wait     不在首页等待人工按回车
```
