# XiaoMiao 掌机：WiFi 记忆 + 按键 SSH 远程命令

按一下 **A 键**，掌机通过 SSH 登录你指定的内网主机，执行你配置的任意 shell 命令。默认示例是 `wol`，也可以换成重启服务、跑备份脚本等任意命令。

适合这种场景：

```
小喵掌机（任意网络） --SSH--> 家里/办公室常开主机（路由器/NAS/树莓派） --执行命令--> 目标
```

> 目标电脑和掌机**不在同一个局域网**时，才需要 SSH 中转。
> 如果它们在同一个 WiFi 下，直接发 UDP 魔术包更简单，不需要 SSH。

## 功能

- 记住多个 WiFi，开机按信号从强到弱自动连接
- 连不上时自动开热点；连上 WiFi 后，网页服务也会监听局域网 IP
- 多条 SSH 主机、多条 SSH 命令，每条都有可配置的别名
- 掌机上 **上/下** 切换“主机/命令”，**左/右** 在当前列表里选择，**A** 执行
- 网页保存后立即生效，不重启
- 长按 B 2 秒重新进入配置热点
- 按键映射与仓库示例一致：A = GPIO34，B = GPIO12，上 = GPIO2，下 = GPIO13，左 = GPIO27，右 = GPIO35
- SSH 使用密码认证，校验服务端 RSA 签名，首次连接记住该主机的指纹（TOFU）

## 文件

| 文件 | 作用 |
|---|---|
| `main.py` | 主程序：屏幕、按键、启动流程 |
| `wifi_manager.py` | WiFi/配置读写、扫描、自动连接、AP |
| `portal.py` | 手机配置网页（HTTP 表单） |
| `ssh_client.py` | 纯 MicroPython SSH 客户端（X25519 + AES-CTR + HMAC-SHA256） |
| `st7735_buf.py` | ST7735 屏幕驱动（来自本仓库 `microptyhon/`） |
| `easydisplay.py` | 中文字库渲染（来自本仓库 `microptyhon/`） |
| `font/text_lite_16px_2312.v3.bmf` | 原版 16px 中文字库（来自本仓库 `microptyhon/`） |
| `config.example.json` | hosts / commands 配置示例，可用 USB 直接放到板子 |

运行时会在板子上生成：

- `/wifi.json`：`{"networks":[{"ssid":"...","password":"..."}]}`
- `/config.json`：SSH 主机、端口、用户、密码、命令、主机指纹

## 1. 烧录固件

**4MB flash 的板子用 `bins/ESP32_SPIRAM_1.24_4MB.bin`**（推荐）。它是由仓库原 8MB 镜像改出来的：分区表改成 4MB、镜像头的 flash size 改成 4MB，并重新计算了分区表 MD5 和镜像 SHA256。

```bash
python3 -m pip install esptool
python3 -m esptool --chip esp32 --port /dev/tty.usbserial-XXXX erase_flash
python3 -m esptool --chip esp32 --port /dev/tty.usbserial-XXXX --baud 460800 \
  write_flash -z 0x1000 bins/ESP32_SPIRAM_1.24_4MB.bin
```

macOS 上串口通常是 `/dev/tty.usbserial-*` 或 `/dev/tty.wchusbserial*`，用 `ls /dev/tty.*` 确认。

> - 两个 bin 都是合并镜像（bootloader + 分区表 + app），从 `0x1000` 写入。
> - `ESP32_SPIRAM_1.24_SDCARDST7735.bin` 是原始 **8MB 布局**，只适用于 8MB flash 的板子。
> - **不要用 `lvgl9.3_mpy_ESP32_GENERIC_SPIRAM_4MB_st7735.bin`**：它虽然也是 4MB，但 LVGL 固件占用了 SPI(2)，我们的 `machine.SPI(2)` 会报 `can't create 'SPI' instances`。
> - 如果板子没有 PSRAM，换一个标准 ESP32 MicroPython 1.24 固件即可（确认包含 `cryptolib`）。

## 2. 上传代码

```bash
python3 -m pip install mpremote
cd wol-ssh
python3 -m mpremote mkdir :font
python3 -m mpremote cp main.py ssh_client.py wifi_manager.py portal.py st7735_buf.py :
python3 -m mpremote cp ../microptyhon/easydisplay.py :easydisplay.py
python3 -m mpremote cp ../microptyhon/text_lite_16px_2312.v3.bmf :font/
```

也可以使用 Thonny：把这些文件保存到设备根目录。

- `easydisplay.py` 用来加载中文点阵字体
- `/font/text_lite_16px_2312.v3.bmf` 是原版 16px 中文字库
- 如果字体文件缺失，程序会自动退回 8px ASCII 字体，中文别名会显示成 `?`

## 3. 首次配置

1. 上电。如果没有保存过 WiFi，屏幕会显示热点信息：
   - SSID：`XiaoMiao-XXXX`
   - 密码：`xiaomiao123`
   - 地址：`http://192.168.4.1`
2. 手机连接该热点，浏览器打开 `http://192.168.4.1`。
3. 在网页里保存 WiFi；然后在“SSH 主机”区域添加主机（别名、IP、端口、用户名、密码），在“SSH 命令”区域添加命令（别名 + 命令）。
4. 保存立即生效，不需要重启。掌机会自动连接 WiFi，屏幕显示当前主机和当前命令。
5. 用上/下键切换“主机/命令”，左/右选择，按 A 执行；首次连接成功会自动保存该主机的指纹。

如果网页打不开，也可以直接用 USB 放配置文件：

```bash
python3 -m mpremote cp config.example.json :config.json
```

## 4. SSH 主机要求

代码只实现了 OpenSSH 仍然支持的一组算法，目标是“能跑、够安全”：

| 项目 | 要求 |
|---|---|
| KEX | `curve25519-sha256`（优先）或 `diffie-hellman-group14-sha256` |
| 主机密钥 | **RSA**（`rsa-sha2-256`） |
| 加密 | `aes128-ctr` |
| MAC | `hmac-sha2-256` |
| 认证 | `password`（失败时自动尝试 `keyboard-interactive`） |
| 压缩 | 无 |

绝大多数 Linux/OpenSSH 默认配置都满足。需要确认时：

```bash
# 服务端是否有 RSA host key
ls /etc/ssh/ssh_host_rsa_key*

# 客户端算法是否包含
ssh -Q kex | grep -E 'curve25519|group14'
ssh -Q key-sig | grep rsa-sha2-256
```

如果服务器被加固过，可以在 `/etc/ssh/sshd_config` 里显式加上：

```
KexAlgorithms +curve25519-sha256,diffie-hellman-group14-sha256
HostKeyAlgorithms +rsa-sha2-256
Ciphers +aes128-ctr
MACs +hmac-sha2-256
PasswordAuthentication yes
```

然后 `sudo systemctl restart ssh`。

> **只有 Ed25519/ECDSA 主机密钥的服务器连不上。** 大多数发行版默认还会生成 RSA host key；如果被删掉了，可以补一个：
>
> ```bash
> sudo ssh-keygen -t rsa -b 3072 -f /etc/ssh/ssh_host_rsa_key -N ''
> sudo systemctl restart ssh
> ```

## 5. 远端命令示例

远程命令可以是任意 shell 命令，不一定是 `wol`。命令会原样交给 SSH 用户的 shell 执行，多行命令也可以。

先在自己的电脑上验证：

```bash
ssh -p 22 user@host 'wol AA:BB:CC:DD:EE:FF'
ssh -p 22 user@host 'systemctl restart nginx'
ssh -p 22 user@host '/usr/local/bin/backup.sh'
```

如果只是唤醒电脑，常见发行版：

| 系统 | 安装 | 命令 |
|---|---|---|
| Debian/Ubuntu | `sudo apt install wakeonlan` | `wakeonlan AA:BB:CC:DD:EE:FF` |
| OpenWrt | `opkg install etherwake` | `etherwake AA:BB:CC:DD:EE:FF`（需要 root） |
| Synology | 自带 | `synonet --wake AA:BB:CC:DD:EE:FF eth0` |
| 群晖/威联通等 | 看机型 | 通常有自己的 wol 工具 |

如果命令不在默认 PATH 里，写绝对路径，例如 `/usr/bin/wakeonlan AA:BB:CC:DD:EE:FF`。
`etherwake` 需要 root 权限时，给这个 SSH 账号配置一个受限 sudo，或换用不需要 root 的 `wol` 实现。

## 6. 工作流程

```
上电
 ├─ 读取 /wifi.json
 ├─ 启动网页服务（局域网 IP / 配置热点都能访问）
 ├─ 扫描附近 WiFi，按信号排序，依次尝试已保存网络
 ├─ 连接成功 → 屏幕显示 IP、当前主机、当前命令
 └─ 失败 → 开 AP，手机网页配置
上/下
 └─ 切换当前选择的是“主机”还是“命令”
左/右
 └─ 在当前列表里切换
按 A
 ├─ 读取 /config.json 里的 hosts[host_index] 和 commands[cmd_index]
 ├─ WiFi 掉了先自动重连
 ├─ SSH 连接（X25519 → RSA 验签 → 密码认证）
 ├─ 打开 session，执行选中的命令
 └─ 显示 exit code / 输出 / 耗时
网页保存
 └─ 立即写入内存和 /config.json，不重启
长按 B 2 秒
 └─ 打开配置热点（原网页服务继续可用）
```

## 7. 性能与限制

- 纯 Python 的 X25519 在 ESP32 上大约需要 **1~3 秒**，属正常现象。
- 只支持 2.4GHz WiFi（ESP32 硬件限制）。
- 一次按键建立一次 SSH 连接，执行一条命令；不做长连接/交互式 shell。
- 只支持 RSA 主机密钥；不支持 Ed25519/ECDSA 主机密钥。
- SSH 密码和 WiFi 密码以**明文**保存在 flash 的 JSON 文件里。请使用专用的低权限账号，不要用 root 密码。

## 8. 安全说明

- 客户端会校验服务端 host key 的 RSA 签名，不只是比对指纹，能防主动 MITM。
- 首次连接采用 TOFU：连接成功后把 `SHA256:...` 指纹写入 `/config.json`；以后指纹变化会拒绝连接。
- 服务器重装后指纹会变，需要清空该主机的指纹再连一次。
- 配置热点默认密码 `xiaomiao123`，可在 `wifi_manager.py` 的 `start_ap(..., password="xiaomiao123")` 里改。

## 9. 常见问题

**屏幕不亮 / 花屏**
- 确认使用的是 ST7735 160x128；引脚与仓库示例一致（SCK 18、MOSI 23、CS 5、DC 4、RES 19）。
- 可以先把 `rotate` 改成 0/2/3 试试方向。

**`WAKE FAIL: no common kex algorithm`**
- 服务端太老或加固过度，参考第 4 节加上 `curve25519-sha256` 或 `diffie-hellman-group14-sha256`。

**`WAKE FAIL: unsupported host key type`**
- 服务端只有 Ed25519/ECDSA 主机密钥，按第 4 节补一个 RSA host key。

**`WAKE FAIL: authentication failed`**
- 服务端没开密码登录，或用户名/密码错误；也可能只允许 publickey。

**`WAKE FAIL: connection closed by peer`**
- 服务器在算法协商阶段就断开，通常是算法不匹配；看第 4 节。

**网页打不开**
- 确认手机连的是 `XiaoMiao-XXXX`，不是流量；手动输入 `http://192.168.4.1`。
- 有些手机对无 Internet 的热点会切回流量，临时关闭“自动切换网络”。

**串口调试**

```bash
python3 -m mpremote repl
```

按 Ctrl-C 中断 `main.py`，可以手动执行：

```python
import wifi_manager
wifi_manager.load_config()
wifi_manager.WiFiManager().networks()
```

## 10. 桌面端自测

`ssh_client.py` 同时兼容 CPython（自动用 `cryptography` 代替 `cryptolib`），可以先用电脑验证 SSH 逻辑：

```bash
python3 -m pip install cryptography
python3 - <<'PY'
import sys
sys.path.insert(0, "wol-ssh")
from ssh_client import SSHClient
c = SSHClient("192.168.1.10", 22, "pi", "password", timeout=15, debug=True)
c.connect()
print(c.fingerprint)
print(c.exec("echo hello"))
c.close()
PY
```

## 11. 不想用 SSH？

如果目标电脑和掌机在同一个局域网，直接把 `ssh_command` 换成“发 UDP 魔术包”会更简单、更快；如果需要，我可以再加一个 `wol.py` 直连模式（ESP32 广播魔术包，不经过 SSH）。

如果目标在远端，也可以把 SSH 换成 HTTPS/MQTT 中继（掌机发一个请求，常开主机收到后执行 wol），实现更简单，但需要在主机上多跑一个小服务。
