# 服务器通过本机网络下载抖音数据

## 为什么需要这个方案

同一份 Cookie 和同一批作品链接在本机请求成功，但服务器访问抖音作品详情接口
持续返回 HTTP 403。这说明问题位于服务器公网出口，而不是链接、Cookie 或 DouK
本身。

该方案让 DouK 继续运行在服务器，文件也直接保存在服务器，只把网络请求通过 SSH
通道从本机发出。无需先下载到本地再用 Xftp 搬运。

## 一、本地建立通道

在 Windows PowerShell 中运行：

```powershell
cd E:\MGSV_preprocessor
.\scripts\start_douyin_server_proxy.ps1
```

输入服务器密码后，这个窗口会保持空白，这是正常状态。DouK 下载期间不要关闭它。
远端代理只绑定服务器的 `127.0.0.1:10808`，不会公开到外网。

## 二、服务器准备 SOCKS 支持

第一次使用时，在服务器执行：

```bash
cd /data/users/ccw/intent_mgsv/repo/MGSV_preprocessor
conda activate douyin
python -m pip install socksio
```

验证通道：

```bash
curl --proxy socks5h://127.0.0.1:10808 https://api.ipify.org
```

命令应输出本机当前的公网出口 IP，而不是服务器 IP。若提示无法连接，
先检查本地 PowerShell 通道是否仍在运行。

## 三、启用 DouK 代理

在服务器项目根目录执行：

```bash
conda activate douyin
python scripts/configure_douk_proxy.py enable
python scripts/configure_douk_proxy.py status
python scripts/patch_douk_proxy_routing.py
```

然后正常进入 DouK：

```bash
cd DouK-Source
python main.py
```

按照 `3 -> 2 -> 2` 读取服务器上的链接文件。下载结果仍直接写入服务器的
`DouK-Source/Volume/Download/`。

## 四、完成后关闭

先退出 DouK，再回到项目根目录执行：

```bash
python scripts/configure_douk_proxy.py disable
```

最后在本地 PowerShell 通道窗口按 `Ctrl+C`。

## 五、自动分批下载

新采集链接包上传并解压后，可以跳过 DouK 的重复菜单操作。新包默认只处理尚未
进入服务器下载记录的作品，并使用小批次降低代理连接压力：

```bash
cd /data/users/ccw/intent_mgsv/repo/MGSV_preprocessor
conda activate douyin

python scripts/download_douyin_batches.py \
  /data/users/ccw/intent_mgsv/transfer/douyin_expand_120_20260812 \
  --batch-size 5 \
  --batches-per-process 4 \
  --max-rounds 2 \
  --pause-seconds 60 \
  --abort-after-network-errors 8
```

这里仍按每小批 5 条控制请求压力，但连续 4 个小批共用一个 DouK
进程。120 条链接通常只需启动约 6 次 DouK，而不是 24 次。

脚本会在每个 DouK 进程组结束后重新读取 `Download.xlsx`，成功作品不会进入下一
轮。若某个进程组没有新增任何作品，同时出现大量 TLS、403 或详情获取失败，网络
熔断会立即停止后续任务并生成 `unresolved_links.txt`，避免失效代理连续重试全部
链接。

只有明确需要检查历史作品并补下载 Music 时才增加 `--run-original-batches`。该
模式会严格执行所有原始 TXT，不适合已经完成服务器去重的新采集包。

`patch_douk_proxy_routing.py` 除了让大体积媒体文件保持服务器直连，还会让作品
详情请求复用 DouK 的异步代理连接，避免每个作品都重新经历 SOCKS 和 TLS 握手。

运行日志和最终未完成链接保存在：

```text
outputs/server/douyin_batch_download/<时间>/
```

只检查重复率而不开始下载：

```bash
python scripts/download_douyin_batches.py \
  /data/users/ccw/intent_mgsv/transfer/douyin_expand_120_20260812 \
  --dry-run
```

## 注意事项

- 本机必须保持联网，SSH 通道窗口必须保持运行。
- `patch_douk_proxy_routing.py` 会让批量作品详情显式使用配置中的代理，同时为
  视频、图片和音乐建立服务器直连下载客户端。这样 SSH 只承载小体积元数据请求，
  大文件不会挤占隧道。脚本可以重复执行。
- 若 SSH 报告 `remote port forwarding failed`，说明服务器 SSH 配置未允许反向
  转发，需要管理员开启 `AllowTcpForwarding`。
- 不要在服务器上反复重试直接访问抖音；持续 403 时应先恢复本代理通道。
