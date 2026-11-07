# -*- coding: utf-8 -*-
"""
小喵掌机：WiFi 记忆 + 网页配置 + 多条 SSH 命令。

- 连上 WiFi 后，网页服务同时监听所有网卡（局域网 IP 和配置热点都能访问）
- 网页保存后立即更新内存配置，不重启
- 掌机上 上/下 选择命令，A 执行，长按 B 打开配置热点
"""

import time
import _thread
import machine
from machine import Pin, SPI

import st7735_buf
import wifi_manager
import portal
from ssh_client import SSHClient


WHITE = 0xFFFF
LINE_H = 16
READY_TOP = 2
RESULT_TOP = 2
RESULT_PER_PAGE = 3


def _ascii(text, limit=20):
    text = str(text)
    out = []
    for ch in text:
        code = ord(ch)
        out.append(ch if 32 <= code < 127 else "?")
        if len(out) >= limit:
            break
    return "".join(out)


def _wrap_line(text, max_px=156):
    text = str(text).replace("\t", "    ").rstrip("\r")
    if not text:
        return [""]
    out = []
    cur = ""
    width = 0
    for ch in text:
        w = 16 if ord(ch) > 127 else 8
        if cur and width + w > max_px:
            out.append(cur)
            cur = ch
            width = w
        else:
            cur += ch
            width += w
    if cur:
        out.append(cur)
    return out


class Display:
    def __init__(self):
        self.lcd = None
        self.ed = None
        self._ready_cache = None
        self._result_cache = None
        try:
            spi = SPI(
                2,
                baudrate=20000000,
                polarity=0,
                phase=0,
                sck=Pin(18),
                mosi=Pin(23),
            )
            self.lcd = st7735_buf.ST7735(
                width=160,
                height=128,
                spi=spi,
                cs=5,
                dc=4,
                res=19,
                rotate=1,
                bl=None,
                invert=False,
                rgb=True,
            )
        except Exception as e:
            print("LCD init failed:", e)

        if self.lcd is not None:
            try:
                import easydisplay
                self.ed = easydisplay.EasyDisplay(
                    display=self.lcd,
                    color_type="RGB565",
                    font="/font/text_lite_16px_2312.v3.bmf",
                    show=False,
                    clear=False,
                )
                print("Chinese font loaded")
            except Exception as e:
                print("font init failed:", e)
                self.ed = None

    def _draw(self, text, x, y):
        s = str(text)
        if self.ed is not None:
            for ch in s:
                if ord(ch) > 127:
                    self.ed.text(s, x, y, color=WHITE)
                    return
        self.lcd.text(_ascii(s, 40), x, y, WHITE)

    def show(self, lines):
        lines = lines or [""]
        self._ready_cache = None
        self._result_cache = None
        if self.lcd is None:
            for line in lines:
                print(line)
            return
        try:
            self.lcd.fill(0)
            y = 2
            for line in lines[:7]:
                self._draw(line, 2, y)
                y += LINE_H
            self.lcd.show()
            return
        except Exception as e:
            print("draw failed:", e)
            self.ed = None
        self.lcd.fill(0)
        y = 2
        for line in lines[:7]:
            self.lcd.text(_ascii(line), 2, y, WHITE)
            y += LINE_H
        self.lcd.show()

    def show_ready(self, lines):
        lines = lines or [""]
        if self.lcd is None:
            self.show(lines)
            return

        dynamic = (3, 4)  # 第 4/5 行是主机和命令，按键时只重画这两行
        old = self._ready_cache
        if old is not None and len(old) == len(lines):
            same_static = True
            for i in range(len(lines)):
                if i not in dynamic and old[i] != lines[i]:
                    same_static = False
                    break
            if same_static:
                try:
                    for i in dynamic:
                        y = READY_TOP + i * LINE_H
                        self.lcd.fill_rect(0, y, 160, LINE_H, 0)
                        self._draw(lines[i], 2, y)
                    self.lcd.show()
                    self._ready_cache = tuple(lines)
                    return
                except Exception as e:
                    print("font draw failed:", e)
                    self.ed = None
                    self.show(lines)
                    return

        try:
            self.lcd.fill(0)
            y = READY_TOP
            for line in lines[:7]:
                self._draw(line, 2, y)
                y += LINE_H
            self.lcd.show()
            self._ready_cache = tuple(lines)
        except Exception as e:
            print("font draw failed:", e)
            self.ed = None
            self.show(lines)

    def show_result(self, lines):
        lines = lines or [""]
        if self.lcd is None:
            self.show(lines)
            return

        dynamic = (3, 4, 5, 6)  # 输出区 + 底部提示行
        old = self._result_cache
        if old is not None and len(old) == len(lines):
            same_static = True
            for i in range(len(lines)):
                if i not in dynamic and old[i] != lines[i]:
                    same_static = False
                    break
            if same_static:
                try:
                    for i in dynamic:
                        y = RESULT_TOP + i * LINE_H
                        self.lcd.fill_rect(0, y, 160, LINE_H, 0)
                        self._draw(lines[i], 2, y)
                    self.lcd.show()
                    self._result_cache = tuple(lines)
                    return
                except Exception as e:
                    print("font draw failed:", e)
                    self.ed = None
                    self.show(lines)
                    return

        try:
            self.lcd.fill(0)
            y = RESULT_TOP
            for line in lines[:7]:
                self._draw(line, 2, y)
                y += LINE_H
            self.lcd.show()
            self._result_cache = tuple(lines)
        except Exception as e:
            print("font draw failed:", e)
            self.ed = None
            self.show(lines)


def _acquire(lock):
    if lock:
        lock.acquire()


def _release(lock):
    if lock:
        lock.release()


def _connect_known(state, timeout=12):
    lock = state.get("wifi_lock")
    _acquire(lock)
    try:
        return state["wifi"].connect_known(timeout)
    finally:
        _release(lock)


def _start_ap(display, state):
    lock = state.get("wifi_lock")
    _acquire(lock)
    try:
        ap, name, password = wifi_manager.start_ap()
    except Exception as e:
        display.show(["AP FAIL", str(e)[:20]])
        return
    finally:
        _release(lock)

    state["ap_active"] = True
    lines = [
        "Setup hotspot",
        name,
        "PASS: " + password,
        "http://192.168.4.1",
    ]
    try:
        if state["wifi"].is_connected():
            info = state["wifi"].ifconfig()
            if info:
                lines.append("LAN: " + info[0])
    except Exception:
        pass
    display.show(lines)
    state["screen"] = "ready"
    print("AP:", name, "PASS:", password)


def _clamp_selected(state):
    cfg = state.get("config") or {}
    hosts = cfg.get("hosts") or []
    cmds = cfg.get("commands") or []

    if not hosts:
        state["host_index"] = 0
    elif state.get("host_index", 0) >= len(hosts) or state.get("host_index", 0) < 0:
        state["host_index"] = 0

    if not cmds:
        state["cmd_index"] = 0
    elif state.get("cmd_index", 0) >= len(cmds) or state.get("cmd_index", 0) < 0:
        state["cmd_index"] = 0

    mode = state.get("mode", "cmd")
    if mode not in ("host", "cmd"):
        mode = "cmd"
    if mode == "host" and not hosts and cmds:
        mode = "cmd"
    if mode == "cmd" and not cmds and hosts:
        mode = "host"
    state["mode"] = mode


def _render(display, state):
    state["screen"] = "ready"
    _clamp_selected(state)
    cfg = state.get("config") or {}
    hosts = cfg.get("hosts") or []
    cmds = cfg.get("commands") or []
    wifi = state["wifi"]

    lines = ["XiaoMiao"]
    url = ""
    try:
        connected = wifi.is_connected()
    except Exception:
        connected = False

    if connected:
        ssid = wifi.current_ssid()
        info = wifi.ifconfig()
        ip = info[0] if info else "?"
        lines.append("WiFi " + ssid)
        lines.append("Web " + ip)
        url = "http://%s/" % ip
    else:
        lines.append("WiFi 未连接")
        if state.get("ap_active"):
            lines.append("AP 192.168.4.1")
        else:
            lines.append("长按B配网")

    mode = state.get("mode", "cmd")
    if hosts:
        hi = state.get("host_index", 0)
        item = hosts[hi]
        marker = ">" if mode == "host" else " "
        lines.append(marker + " 主机: " + str(item.get("alias", "")))
    else:
        lines.append("无主机")
    if cmds:
        ci = state.get("cmd_index", 0)
        item = cmds[ci]
        marker = ">" if mode == "cmd" else " "
        lines.append(marker + " 命令: " + str(item.get("alias", "")))
    else:
        lines.append("无命令")
    lines.append("A: 执行")
    lines.append("上下切换  左右选择")

    display.show_ready(lines)
    if url and url != state.get("last_web"):
        state["last_web"] = url
        print("web:", url)


def _render_result(display, state):
    data = state.get("result_data") or {}
    out = data.get("output_lines") or ["(无输出)"]
    per_page = RESULT_PER_PAGE
    pages = (len(out) + per_page - 1) // per_page
    if pages < 1:
        pages = 1
    page = state.get("result_page", 0)
    if page < 0:
        page = 0
    if page >= pages:
        page = pages - 1
    state["result_page"] = page

    chunk = out[page * per_page:(page + 1) * per_page]
    status = str(data.get("status", "?"))
    ms = data.get("ms", 0)
    lines = [
        "命令: " + str(data.get("alias", "")),
        "主机: " + str(data.get("host_alias", "")),
        status + " " + ("%d ms" % ms),
    ]
    lines.extend(chunk)
    while len(lines) < 3 + RESULT_PER_PAGE:
        lines.append("")
    lines.append("B返回 上/下翻页 %d/%d" % (page + 1, pages))

    state["screen"] = "result"
    display.show_result(lines)


def _page_result(state, delta):
    data = state.get("result_data") or {}
    out = data.get("output_lines") or []
    per_page = RESULT_PER_PAGE
    pages = (len(out) + per_page - 1) // per_page
    if pages < 1:
        pages = 1
    page = state.get("result_page", 0) + delta
    if page < 0:
        page = 0
    if page >= pages:
        page = pages - 1
    state["result_page"] = page


def _cycle_selected(state, delta):
    cfg = state.get("config") or {}
    if state.get("mode", "cmd") == "host":
        hosts = cfg.get("hosts") or []
        if not hosts:
            return False
        state["host_index"] = (state.get("host_index", 0) + delta) % len(hosts)
    else:
        cmds = cfg.get("commands") or []
        if not cmds:
            return False
        state["cmd_index"] = (state.get("cmd_index", 0) + delta) % len(cmds)
    state["result_until"] = 0
    return True


def _set_mode(state, mode):
    cfg = state.get("config") or {}
    if mode == "host":
        if not (cfg.get("hosts") or []):
            return False
    elif mode == "cmd":
        if not (cfg.get("commands") or []):
            return False
    else:
        return False
    state["mode"] = mode
    state["result_until"] = 0
    return True


def _result_active(state):
    until = state.get("result_until", 0)
    if not until:
        return False
    return time.ticks_diff(time.ticks_ms(), until) < 0


def _update_config(state, mutator):
    lock = state.get("config_lock")
    _acquire(lock)
    try:
        cfg = dict(state.get("config") or {})
        mutator(cfg)
        cfg.pop("ssh_command", None)
        state["config"] = cfg
        state["version"] = state.get("version", 0) + 1
        try:
            wifi_manager.save_config(cfg)
        except Exception as e:
            print("save config failed:", e)
        return cfg
    finally:
        _release(lock)


def wake(display, state):
    # 如果 WiFi 掉了，先重连，避免 EHOSTUNREACH
    if not state["wifi"].is_connected():
        display.show(["WiFi lost", "Reconnecting..."])
        if not _connect_known(state, 12):
            state["result_data"] = {
                "alias": "WiFi",
                "host_alias": "-",
                "status": "FAIL",
                "ms": 0,
                "output_lines": ["WiFi 连接失败", "长按 B 打开配置热点"],
            }
            state["result_page"] = 0
            _render_result(display, state)
            return

    cfg = dict(state.get("config") or {})
    hosts = cfg.get("hosts") or []
    cmds = cfg.get("commands") or []
    if not hosts:
        display.show(["No hosts", "Open web to add"])
        return
    if not cmds:
        display.show(["No commands", "Open web to add"])
        return

    hi = state.get("host_index", 0)
    if hi >= len(hosts):
        hi = 0
        state["host_index"] = 0
    ci = state.get("cmd_index", 0)
    if ci >= len(cmds):
        ci = 0
        state["cmd_index"] = 0

    host_item = hosts[hi]
    cmd_item = cmds[ci]
    host_alias = str(host_item.get("alias", ""))
    cmd_alias = str(cmd_item.get("alias", ""))
    command = cmd_item.get("command", "")
    host = host_item.get("host", "")
    port = host_item.get("port", 22)
    user = host_item.get("user", "")
    password = host_item.get("password", "")
    fingerprint = host_item.get("fingerprint", "")

    if not host or not user:
        display.show([host_alias, "No host/user", "Open web to edit"])
        return
    if not command:
        display.show([cmd_alias, "Empty command", "Open web to edit"])
        return

    display.show([cmd_alias, "Host: " + host_alias, "Connecting..."])
    client = None
    started = time.ticks_ms()
    try:
        client = SSHClient(
            host,
            port,
            user,
            password,
            timeout=15,
            fingerprint=fingerprint or None,
        )
        client.connect()
        out, err, code = client.exec(command)
        elapsed = time.ticks_diff(time.ticks_ms(), started)

        # 首次连接记住这台主机的指纹；重新读取配置，避免覆盖网页修改
        if not fingerprint:
            new_fp = client.fingerprint

            def _remember(c):
                hs = c.get("hosts") or []
                for i in range(len(hs)):
                    h = hs[i]
                    if (str(h.get("host", "")) == str(host)
                            and str(h.get("port", 22)) == str(port)
                            and str(h.get("user", "")) == str(user)):
                        h = dict(h)
                        h["fingerprint"] = new_fp
                        hs[i] = h
                        c["hosts"] = hs
                        break

            _update_config(state, _remember)

        raw = (out or b"") + (err or b"")
        text = raw.decode("utf-8", "replace").strip()
        output_lines = []
        if text:
            for line in text.split("\n"):
                output_lines.extend(_wrap_line(line))
        else:
            output_lines = ["(无输出)"]
        if len(output_lines) > 200:
            output_lines = output_lines[:200]
            output_lines.append("... (输出已截断)")

        state["result_data"] = {
            "alias": cmd_alias,
            "host_alias": host_alias,
            "status": "OK" if code == 0 else "EXIT %s" % code,
            "ms": elapsed,
            "output_lines": output_lines,
        }
        state["result_page"] = 0
        _render_result(display, state)
        print("run:", host_alias, cmd_alias, "code", code, out, err)
    except Exception as e:
        msg = str(e)
        print("run failed:", host_alias, cmd_alias, msg)
        state["result_data"] = {
            "alias": cmd_alias,
            "host_alias": host_alias,
            "status": "FAIL",
            "ms": time.ticks_diff(time.ticks_ms(), started),
            "output_lines": _wrap_line(msg) if msg else ["(无输出)"],
        }
        state["result_page"] = 0
        _render_result(display, state)
    finally:
        if client is not None:
            client.close()


def main():
    try:
        machine.freq(240000000)
    except Exception:
        pass
    display = Display()
    display.show(["XiaoMiao", "Booting..."])

    wifi = wifi_manager.WiFiManager()
    state = {
        "wifi": wifi,
        "config": wifi_manager.load_config(),
        "version": 0,
        "wifi_dirty": False,
        "ap_active": False,
        "host_index": 0,
        "cmd_index": 0,
        "mode": "cmd",
        "result_until": 0,
        "last_web": "",
        "scan_cache": [],
        "wifi_lock": _thread.allocate_lock(),
        "config_lock": _thread.allocate_lock(),
    }

    web = portal.Portal(state)
    web.start()

    ssid = _connect_known(state, 12)
    if ssid:
        _render(display, state)
    else:
        _start_ap(display, state)

    # A=GPIO34, B=GPIO12, 上=GPIO2, 下=GPIO13, 左=GPIO27, 右=GPIO35
    key_up = Pin(2, Pin.IN, Pin.PULL_UP)
    key_down = Pin(13, Pin.IN, Pin.PULL_UP)
    key_left = Pin(27, Pin.IN, Pin.PULL_UP)
    key_right = Pin(35, Pin.IN)
    key_a = Pin(34, Pin.IN)
    key_b = Pin(12, Pin.IN, Pin.PULL_UP)

    up_prev = 1
    down_prev = 1
    left_prev = 1
    right_prev = 1
    a_prev = 1
    b_prev = 1
    b_pressed_at = 0
    last_version = state["version"]

    while True:
        if state.get("wifi_dirty"):
            state["wifi_dirty"] = False
            state["screen"] = "ready"
            display.show(["WiFi reconnecting", "..."])
            if _connect_known(state, 15):
                _render(display, state)
            else:
                _start_ap(display, state)

        if state.get("version") != last_version:
            last_version = state["version"]
            print("config updated:", last_version)
            if state.get("screen") == "ready":
                _render(display, state)

        left_now = key_left.value()
        if left_prev == 1 and left_now == 0:
            if state.get("screen") != "result":
                if _cycle_selected(state, -1):
                    _render(display, state)
            time.sleep_ms(120)
            left_prev = key_left.value()
        else:
            left_prev = left_now

        right_now = key_right.value()
        if right_prev == 1 and right_now == 0:
            if state.get("screen") != "result":
                if _cycle_selected(state, 1):
                    _render(display, state)
            time.sleep_ms(120)
            right_prev = key_right.value()
        else:
            right_prev = right_now

        up_now = key_up.value()
        if up_prev == 1 and up_now == 0:
            if state.get("screen") == "result":
                _page_result(state, -1)
                _render_result(display, state)
            else:
                if _set_mode(state, "host"):
                    _render(display, state)
            time.sleep_ms(120)
            up_prev = key_up.value()
        else:
            up_prev = up_now

        down_now = key_down.value()
        if down_prev == 1 and down_now == 0:
            if state.get("screen") == "result":
                _page_result(state, 1)
                _render_result(display, state)
            else:
                if _set_mode(state, "cmd"):
                    _render(display, state)
            time.sleep_ms(120)
            down_prev = key_down.value()
        else:
            down_prev = down_now

        a_now = key_a.value()
        if a_prev == 1 and a_now == 0:
            wake(display, state)
            time.sleep_ms(250)
            a_prev = key_a.value()
        else:
            a_prev = a_now

        b_now = key_b.value()
        if b_prev == 1 and b_now == 0:
            b_pressed_at = time.ticks_ms()
        elif b_prev == 0 and b_now == 1:
            if time.ticks_diff(time.ticks_ms(), b_pressed_at) >= 2000:
                _start_ap(display, state)
            elif state.get("screen") == "result":
                state["screen"] = "ready"
                state["result_data"] = None
                _render(display, state)
        b_prev = b_now

        time.sleep_ms(20)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("FATAL:", e)
        try:
            d = Display()
            d.show(["FATAL", str(e)[:20], str(e)[20:40]])
        except Exception:
            pass
