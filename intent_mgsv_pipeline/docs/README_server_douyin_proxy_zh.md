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

## 注意事项

- 本机必须保持联网，SSH 通道窗口必须保持运行。
- 当前 DouK 的 `proxy` 会同时代理详情请求和媒体下载，因此速度受本机网络、
  SSH 链路和服务器网络共同影响，但不会占用本机磁盘。
- 若 SSH 报告 `remote port forwarding failed`，说明服务器 SSH 配置未允许反向
  转发，需要管理员开启 `AllowTcpForwarding`。
- 不要在服务器上反复重试直接访问抖音；持续 403 时应先恢复本代理通道。
