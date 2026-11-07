# 小喵遥控台（XiaoMiao Remote）

ESP32 小喵掌机上的 **WiFi 记忆 + 手机网页配置 + 多主机 SSH 命令** 项目。

按 **A** 在掌机上执行选中的 SSH 命令；命令不一定是 `wol`，也可以是重启服务、跑备份脚本等任意 shell 命令。

```
小喵掌机 --WiFi--> SSH 主机 --执行命令--> 目标
```

## 烧录镜像在哪

**4MB flash 的板子使用这个镜像：**

```text
firmware/ESP32_SPIRAM_1.24_4MB.bin
```

本地完整路径：

```text
/Users/dww/Desktop/xiaomiao-remote/firmware/ESP32_SPIRAM_1.24_4MB.bin
```

GitHub 仓库路径（如果仓库是私有的，需要先登录）：

```text
https://github.com/chifanxiaoyangren/xiaomiao-remote/blob/main/firmware/ESP32_SPIRAM_1.24_4MB.bin
```

这个镜像是 **合并镜像**（bootloader + 分区表 + app），从 **`0x1000`** 地址写入。

> 不要用原项目的 8MB 镜像 `ESP32_SPIRAM_1.24_SDCARDST7735.bin`：它是原 **8MB 布局**，4MB 板子会启动失败。
>
> 也不要用 `lvgl9.3_mpy_ESP32_GENERIC_SPIRAM_4MB_st7735.bin`：它虽然也是 4MB，但 LVGL 固件占用了 SPI(2)，本项目会报 `can't create 'SPI' instances`。

## 适用硬件

- ESP32-D0WD（4MB flash、4MB PSRAM 已验证）
- ST7735 160×128 TFT
- 6 个按键：上/下/左/右/A/B
- USB 转串口（板子上是 `GD32 USB CDC ACM`，macOS 显示为 `/dev/cu.usbmodem*`）

## 快速开始

1. 烧录 `firmware/ESP32_SPIRAM_1.24_4MB.bin` 到 `0x1000`
2. 用 `mpremote` 上传程序、`easydisplay.py` 和 16px 中文字库
3. 上电，手机连接 `XiaoMiao-XXXX` 热点，打开 `http://192.168.4.1`
4. 在网页里配置 WiFi、SSH 主机、SSH 命令
5. 掌机上用 上/下 切换主机/命令，左/右选择，按 A 执行

---

# 一、烧录固件

## 1. 安装 esptool

```bash
python3 -m pip install esptool
```

确认串口。macOS：

```bash
ls /dev/cu.*
```

常见结果：

```text
/dev/cu.usbmodem11101
/dev/cu.usbserial-0001
/dev/cu.wchusbserial1420
```

后面的命令用 `<PORT>` 代替，例如 `/dev/cu.usbmodem11101`。

## 2. 擦除 flash

```bash
python3 -m esptool --chip esp32 --port <PORT> erase_flash
```

## 3. 写入 4MB 镜像

```bash
python3 -m esptool --chip esp32 --port <PORT> --baud 460800 \
  write_flash -z 0x1000 firmware/ESP32_SPIRAM_1.24_4MB.bin
```

看到下面的输出就说明成功：

```text
Wrote ... bytes ...
Hash of data verified.
```

## 4. 复位运行

写入完成后执行：

```bash
python3 -m esptool --chip esp32 --port <PORT> --after hard-reset flash_id
```

或者直接拔插 USB / 按一下板子复位键。

串口应该出现：

```text
MicroPython v1.23.0 ...
>>> 
```

## 5. 烧录失败怎么办

如果 esptool 报 `Failed to connect` / `No serial data received`：

1. 按住板子的 **BOOT/IO0** 键
2. 点一下 **EN/RST** 复位键
3. 松开 BOOT
4. 再执行烧录命令

也可以让 esptool 自己复位：

```bash
python3 -m esptool --chip esp32 --port <PORT> --before default-reset \
  write_flash -z 0x1000 firmware/ESP32_SPIRAM_1.24_4MB.bin
```

> 注意：这块板子的 USB CDC 有时会在串口打开/关闭时让 ESP32 复位，这是正常的。烧录时如果中途断开，重新执行一次即可。

---

# 二、上传程序

## 1. 安装 mpremote

```bash
python3 -m pip install mpremote
```

## 2. 进入项目目录

```bash
cd /Users/dww/Desktop/xiaomiao-remote
```

## 3. 创建字体目录

```bash
python3 -m mpremote connect <PORT> mkdir :font
```

如果提示目录已存在，忽略即可。

## 4. 上传主程序和驱动

```bash
python3 -m mpremote connect <PORT> cp \
  main.py ssh_client.py wifi_manager.py portal.py st7735_buf.py :
```

## 5. 上传中文字库渲染库和字体

```bash
python3 -m mpremote connect <PORT> cp \
  easydisplay.py :easydisplay.py

python3 -m mpremote connect <PORT> cp \
  font/text_lite_16px_2312.v3.bmf :font/
```

## 6. 确认板子上的文件

```bash
python3 -m mpremote connect <PORT> exec \
  "import os; print(os.listdir('/')); print(os.listdir('/font'))"
```

应该包含：

```text
main.py
ssh_client.py
wifi_manager.py
portal.py
st7735_buf.py
easydisplay.py
font/text_lite_16px_2312.v3.bmf
```

## 7. 复位

```bash
python3 -m mpremote connect <PORT> reset
```

或者拔插 USB。

> 如果 mpremote 报 `could not enter raw repl`：
> - 关闭 Thonny、串口助手等占用串口的程序
> - 重新拔插 USB
> - 再执行一次命令
>
> mpremote 有时会把板子留在 raw REPL，重新复位即可。

### Thonny 方式

也可以使用 Thonny：

- 打开 `main.py`、`ssh_client.py`、`wifi_manager.py`、`portal.py`、`st7735_buf.py`
- “文件 → 保存到 MicroPython 设备”，保存到根目录
- 打开 `easydisplay.py`，保存到根目录
- 在板子上创建 `font` 目录，把 `font/text_lite_16px_2312.v3.bmf` 保存为 `/font/text_lite_16px_2312.v3.bmf`

---

# 三、第一次开机和配网

## 1. 没有保存过 WiFi 时

屏幕显示：

```text
Setup hotspot
XiaoMiao-XXXX
PASS: xiaomiao123
http://192.168.4.1
```

- 手机连接 `XiaoMiao-XXXX`
- 密码 `xiaomiao123`
- 浏览器打开 `http://192.168.4.1`

## 2. 已经保存过 WiFi 时

掌机会按信号从强到弱自动连接已保存网络，成功后屏幕显示：

```text
XiaoMiao
WiFi 你的WiFi名
Web 192.168.31.42
> 主机: Mac
  命令: 唤醒电脑
A: 执行
上下切换  左右选择
```

`Web` 后面的 IP 就是配置网页地址：

```text
http://192.168.31.42/
```

只要手机/电脑和掌机在同一个局域网，就可以直接访问，不需要连接热点。

## 3. 配置网页

网页分成几块：

- **WiFi**：添加/修改 WiFi，保存后自动重连
- **SSH 主机**：添加多台主机，每台有别名、IP、端口、用户名、密码、主机指纹
- **SSH 命令**：添加多条命令，每条有别名和命令内容
- **附近的 WiFi**：点“刷新”才重新扫描
- **已保存的 WiFi**：删除不需要的网络

所有保存都是**立即生效**，不重启。

---

# 四、配置 SSH 主机

在网页的“SSH 主机”区域填写：

| 字段 | 说明 |
|---|---|
| 别名 | 显示在掌机屏幕上，例如 `Mac`、`NAS` |
| 主机 / IP | 要 SSH 登录的服务器 IP，例如 `192.168.31.128` |
| 端口 | 默认 `22` |
| 用户名 | SSH 登录用户 |
| 密码 | SSH 登录密码 |
| 主机指纹 | 可选，首次连接成功后自动保存 |

## 重要：不要填掌机自己的 IP

网页上方显示的是：

```text
掌机 IP：192.168.31.42
```

这是**掌机自己的网页地址**，不是 SSH 目标。

“SSH 主机 / IP”要填**另一台服务器**的 IP，例如：

```text
192.168.31.128   ← Mac / NAS / 树莓派
```

填成掌机自己的 IP 会出现 `ECONNRESET` / `error 104`。

## 主机指纹

- 第一次连接成功后，客户端会把 `SHA256:...` 指纹写入该主机的配置
- 以后指纹变化会拒绝连接，防止中间人攻击
- 服务器重装 / 换主机后，清空“主机指纹”再连接一次
- 如果换了一台全新的主机，也要清空旧指纹

## SSH 服务端要求

本项目实现的是 OpenSSH 仍然支持的一组算法：

| 项目 | 要求 |
|---|---|
| KEX | `curve25519-sha256`（优先）或 `diffie-hellman-group14-sha256` |
| 主机密钥 | **RSA**（`rsa-sha2-256`） |
| 加密 | `aes128-ctr` |
| MAC | `hmac-sha2-256` |
| 认证 | `password`（失败时自动尝试 `keyboard-interactive`） |
| 压缩 | 无 |

大多数 Linux / macOS 默认都满足。可以在服务器上检查：

```bash
ls /etc/ssh/ssh_host_rsa_key*
ssh -Q kex | grep -E 'curve25519|group14'
ssh -Q key-sig | grep rsa-sha2-256
```

如果服务器被加固过，在 `/etc/ssh/sshd_config` 里加上：

```text
KexAlgorithms +curve25519-sha256,diffie-hellman-group14-sha256
HostKeyAlgorithms +rsa-sha2-256
Ciphers +aes128-ctr
MACs +hmac-sha2-256
PasswordAuthentication yes
```

然后重启 SSH 服务。

> 只有 Ed25519/ECDSA 主机密钥的服务器连不上。可以补一个 RSA host key：
>
> ```bash
> sudo ssh-keygen -t rsa -b 3072 -f /etc/ssh/ssh_host_rsa_key -N ''
> sudo systemctl restart ssh
> ```

> 本项目只支持密码登录，不支持 publickey / 密钥登录。

---

# 五、配置 SSH 命令

在网页的“SSH 命令”区域添加：

| 字段 | 说明 |
|---|---|
| 别名 | 显示在掌机屏幕上，例如 `唤醒电脑`、`重启 nginx` |
| 命令 | 任意 shell 命令，可以多行 |

示例：

```text
wol AA:BB:CC:DD:EE:FF
systemctl restart nginx
/usr/local/bin/backup.sh
echo hello
```

命令会原样交给远端 SSH 用户的 shell 执行。可以先在电脑上验证：

```bash
ssh -p 22 user@host 'wol AA:BB:CC:DD:EE:FF'
ssh -p 22 user@host 'systemctl restart nginx'
```

## WoL 命令参考

| 系统 | 安装 | 命令 |
|---|---|---|
| Debian/Ubuntu | `sudo apt install wakeonlan` | `wakeonlan AA:BB:CC:DD:EE:FF` |
| OpenWrt | `opkg install etherwake` | `etherwake AA:BB:CC:DD:EE:FF`（通常需要 root） |
| Synology | 自带 | `synonet --wake AA:BB:CC:DD:EE:FF eth0` |
| 群晖/威联通等 | 看机型 | 使用厂商自带工具 |

如果命令不在 PATH 里，写绝对路径，例如：

```text
/usr/bin/wakeonlan AA:BB:CC:DD:EE:FF
```

## macOS 路径注意

macOS 的家目录是 `/Users/你的用户名`，不是 `/home/你的用户名`。

例如当前用户 `dww`：

```text
正确：touch /Users/dww/sddd
错误：touch /home/dww/sddd
```

命令返回 `EXIT 1` 时，先检查是不是路径、权限或命令不存在。

---

# 六、掌机操作说明

## 主页面

```text
XiaoMiao
WiFi 你的WiFi
Web 192.168.31.42
> 主机: Mac
  命令: 唤醒电脑
A: 执行
上下切换  左右选择
```

`>` 表示当前用左/右键选择的是“主机”还是“命令”。

## 按键

| 按键 | 作用 |
|---|---|
| **上** | 切到“主机”模式 |
| **下** | 切到“命令”模式 |
| **左 / 右** | 在当前列表里切换主机或命令 |
| **A** | 在选中的主机上执行选中的命令 |
| **B 短按** | 在结果页时返回主页面 |
| **B 长按 2 秒** | 打开配置热点 |

## 结果页

按 A 执行后，屏幕停在结果页：

```text
命令: 唤醒电脑
主机: Mac
OK 1234 ms
输出第 1 行
输出第 2 行
输出第 3 行
B返回 上/下翻页 1/2
```

- **上 / 下**：翻页，每页 3 行输出
- **B 短按**：返回主页面
- **B 长按 2 秒**：打开配置热点
- **A**：重新执行当前命令

输出会自动按屏幕宽度折行；stdout 和 stderr 都会显示；最多保留 200 行。

## 中文字体

- 屏幕使用 `/font/text_lite_16px_2312.v3.bmf` 原版 16px 中文字库
- 纯英文/数字行用内置 8px 点阵字体，速度更快
- 中文行才走 16px 中文字库
- 如果字体文件缺失，程序会退回 8px ASCII，中文别名会显示成 `?`
- 别名太长会被屏幕边缘裁掉，建议控制在 6 个中文以内

---

# 七、工作流程

```text
上电
 ├─ 读取 /wifi.json
 ├─ 启动网页服务（局域网 IP / 配置热点都能访问）
 ├─ 扫描附近 WiFi，按信号排序，依次尝试已保存网络
 ├─ 连接成功 → 屏幕显示 IP、当前主机、当前命令
 └─ 失败 → 开 AP，手机网页配置

上 / 下
 └─ 切换当前选择的是“主机”还是“命令”

左 / 右
 └─ 在当前列表里切换

按 A
 ├─ 读取 /config.json 里的 hosts 和 commands
 ├─ WiFi 掉了先自动重连
 ├─ SSH 连接（X25519 → RSA 验签 → 密码认证）
 ├─ 打开 session，执行选中的命令
 └─ 停在结果页，显示 exit code / 输出 / 耗时

网页保存
 └─ 立即写入内存和 /config.json，不重启
```

---

# 八、常见问题

## 屏幕不亮 / 花屏

- 确认是 ST7735 160×128
- 引脚：SCK 18、MOSI 23、CS 5、DC 4、RES 19
- 可以尝试修改 `main.py` 里的 `rotate=1` 为 `0`、`2`、`3`

## `can't create 'SPI' instances`

用了 LVGL 4MB 固件。换回：

```text
firmware/ESP32_SPIRAM_1.24_4MB.bin
```

## `spi_flash: Detected size(4096k) smaller than ... (8192k)`

用了 8MB 布局的镜像。确认烧录的是：

```text
firmware/ESP32_SPIRAM_1.24_4MB.bin
```

并且从 `0x1000` 写入。

## 中文显示成 `?`

- 检查 `/font/text_lite_16px_2312.v3.bmf` 是否存在
- 检查 `easydisplay.py` 是否在根目录
- 串口启动日志应该有 `Chinese font loaded`
- 字体文件缺失时程序会退回 8px ASCII

## SSH 连接失败

| 屏幕/串口信息 | 原因 |
|---|---|
| `EHOSTUNREACH` | 掌机没连上 WiFi，或目标主机不在同一网络 |
| `ECONNRESET` / `error 104` | SSH 主机填成了掌机自己的 IP，或目标端口不是 SSH |
| `no common kex algorithm` | 服务端 KEX 算法不匹配，按“SSH 服务端要求”修改 |
| `unsupported host key type` | 服务端没有 RSA host key |
| `authentication failed` | 用户名/密码错误，或服务端只允许 publickey |
| `connection closed by peer` | 算法协商失败或服务端拒绝连接 |

## `EXIT 1`

SSH 已经连上并执行了命令，但远端命令返回非 0。常见原因：

- 命令路径错误（macOS 用 `/Users/...`，不是 `/home/...`）
- 命令需要 root 权限
- 命令不存在或参数错误

结果页会显示 stderr 的第一行，按上/下翻页查看。

## 网页打不开

- 没有保存 WiFi 时，手机要连接 `XiaoMiao-XXXX`
- 已连 WiFi 时，用屏幕上的 `Web <IP>` 地址
- 有些手机对无 Internet 的热点会自动切回流量，临时关闭“自动切换网络”
- 路由器如果开了 AP 隔离/客户端隔离，手机可能访问不到掌机 IP

## mpremote 报 `could not enter raw repl`

- 关闭 Thonny、串口助手等占用串口的程序
- 拔插 USB
- 重新执行命令
- 仍然失败时先按复位键，等 2 秒再试

## 串口调试

```bash
python3 -m mpremote connect <PORT> repl
```

按 `Ctrl-C` 中断 `main.py`，然后可以手动执行：

```python
import wifi_manager
wifi_manager.load_config()
wifi_manager.WiFiManager().networks()
```

---

# 九、配置格式

板子运行时会在 flash 生成：

```text
/wifi.json
/config.json
```

`/config.json` 结构：

```json
{
  "hosts": [
    {
      "alias": "Mac",
      "host": "192.168.31.128",
      "port": 22,
      "user": "dww",
      "password": "change-me",
      "fingerprint": ""
    }
  ],
  "commands": [
    {
      "alias": "唤醒电脑",
      "command": "wol AA:BB:CC:DD:EE:FF"
    }
  ]
}
```

如果网页打不开，也可以把 `config.example.json` 放到板子上：

```bash
python3 -m mpremote connect <PORT> cp config.example.json :config.json
```

---

# 十、安全与限制

- SSH 密码和 WiFi 密码以**明文**保存在板子 flash 的 JSON 里
- 请使用专用的低权限 SSH 账号，不要用 root 密码
- 配置网页没有登录密码，同一局域网的人都能访问；只建议在可信网络使用
- AP 热点默认密码 `xiaomiao123`，可以在 `wifi_manager.py` 的 `start_ap(..., password="xiaomiao123")` 里修改
- 只支持 2.4GHz WiFi（ESP32 硬件限制）
- 一次按 A 建立一次 SSH 连接，执行一条命令；不做长连接/交互式 shell
- 只支持 RSA 主机密钥，不支持 Ed25519/ECDSA 主机密钥
- 只支持密码认证，不支持 publickey

---

# 十一、性能说明

- 16px 中文字库在 ESP32 上渲染比英文慢，属于正常现象
- 纯英文/数字行使用 8px 内置字体，速度很快
- 主页面切换主机/命令时只重画两行
- 结果页翻页时只重画输出区和提示行
- 第一次打开页面/第一次结果页会整屏重画，稍慢
- X25519 握手在 ESP32 上约 1 秒左右，SSH 首次连接通常几秒
- CPU 已设置为 240MHz

---

# 十二、文件说明

| 文件 | 作用 |
|---|---|
| `main.py` | 主程序：屏幕、按键、启动流程 |
| `wifi_manager.py` | WiFi/配置读写、扫描、自动连接、AP |
| `portal.py` | 手机配置网页（HTTP 表单） |
| `ssh_client.py` | 纯 MicroPython SSH 客户端（X25519 + AES-CTR + HMAC-SHA256） |
| `st7735_buf.py` | ST7735 屏幕驱动 |
| `easydisplay.py` | 中文字库渲染 |
| `font/text_lite_16px_2312.v3.bmf` | 原版 16px 中文字库 |
| `firmware/ESP32_SPIRAM_1.24_4MB.bin` | 4MB flash 合并固件 |
| `config.example.json` | hosts / commands 配置示例 |

---

# 十三、桌面端自测

`ssh_client.py` 同时兼容 CPython（自动用 `cryptography` 代替 `cryptolib`），可以先在电脑上验证 SSH 逻辑：

```bash
python3 -m pip install cryptography
cd /Users/dww/Desktop/xiaomiao-remote
python3 - <<'PY'
import sys
sys.path.insert(0, ".")
from ssh_client import SSHClient
c = SSHClient("192.168.1.10", 22, "pi", "password", timeout=15, debug=True)
c.connect()
print(c.fingerprint)
print(c.exec("echo hello"))
c.close()
PY
```
