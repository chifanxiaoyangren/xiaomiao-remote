# -*- coding: utf-8 -*-
"""
WiFi 记忆 + 自动连接 + 配置文件读写。

存储位置：
  /wifi.json    {"networks": [{"ssid": "...", "password": "..."}]}
  /config.json  SSH / WoL 参数
"""

import network
import json
import os
import time

WIFI_FILE = "/wifi.json"
CONFIG_FILE = "/config.json"

DEFAULT_CONFIG = {
    "hosts": [],
    "commands": [],
}

STAT_WRONG_PASSWORD = getattr(network, "STAT_WRONG_PASSWORD", 202)
STAT_NO_AP_FOUND = getattr(network, "STAT_NO_AP_FOUND", 201)
STAT_CONNECT_FAIL = getattr(network, "STAT_CONNECT_FAIL", 203)
STAT_GOT_IP = getattr(network, "STAT_GOT_IP", 1010)


def _load_json(path, default):
    try:
        with open(path, "r") as f:
            return json.loads(f.read())
    except Exception:
        return default


def _save_json(path, obj):
    text = json.dumps(obj)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    try:
        os.rename(tmp, path)
    except Exception:
        with open(path, "w") as f:
            f.write(text)
        try:
            os.remove(tmp)
        except Exception:
            pass


def _as_text(value):
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except Exception:
            return value.decode("utf-8", "replace")
    return value


def load_config():
    cfg = DEFAULT_CONFIG.copy()
    cfg.update(_load_json(CONFIG_FILE, {}))

    # 旧版单主机配置迁移成 hosts 列表
    hosts = cfg.get("hosts")
    if not isinstance(hosts, list) or (len(hosts) == 0 and cfg.get("ssh_host")):
        hosts = []
        old_host = cfg.get("ssh_host")
        if old_host:
            hosts = [{
                "alias": "主机1",
                "host": old_host,
                "port": cfg.get("ssh_port", 22),
                "user": cfg.get("ssh_user", ""),
                "password": cfg.get("ssh_password", ""),
                "fingerprint": cfg.get("host_fingerprint", ""),
            }]

    clean_hosts = []
    for item in hosts:
        if not isinstance(item, dict):
            continue
        host = str(item.get("host", "")).strip()
        if not host:
            continue
        alias = str(item.get("alias", "")).strip()
        if not alias:
            alias = "主机%d" % (len(clean_hosts) + 1)
        try:
            port = int(item.get("port", 22) or 22)
        except Exception:
            port = 22
        clean_hosts.append({
            "alias": alias,
            "host": host,
            "port": port,
            "user": str(item.get("user", "")).strip(),
            "password": str(item.get("password", "")),
            "fingerprint": str(item.get("fingerprint", "")).strip(),
        })
    cfg["hosts"] = clean_hosts
    for key in ("ssh_host", "ssh_port", "ssh_user", "ssh_password", "host_fingerprint"):
        cfg.pop(key, None)

    cmds = cfg.get("commands")
    if not isinstance(cmds, list) or (len(cmds) == 0 and cfg.get("ssh_command")):
        cmds = []
        old = cfg.get("ssh_command")
        if old:
            cmds = [{"alias": "命令1", "command": old}]

    clean = []
    for item in cmds:
        if not isinstance(item, dict):
            continue
        alias = str(item.get("alias", "")).strip()
        command = str(item.get("command", "")).strip()
        if not command:
            continue
        if not alias:
            alias = "命令%d" % (len(clean) + 1)
        clean.append({"alias": alias, "command": command})

    cfg["commands"] = clean
    cfg.pop("ssh_command", None)
    return cfg


def save_config(cfg):
    _save_json(CONFIG_FILE, cfg)


class WiFiManager:
    def __init__(self):
        self.wlan = network.WLAN(network.STA_IF)
        self.wlan.active(True)

    # ---------------------------------------------------------- 网络列表
    def networks(self):
        data = _load_json(WIFI_FILE, {})
        nets = data.get("networks", [])
        return nets if isinstance(nets, list) else []

    def add(self, ssid, password):
        nets = self.networks()
        for item in nets:
            if item.get("ssid") == ssid:
                item["password"] = password
                break
        else:
            nets.append({"ssid": ssid, "password": password})
        _save_json(WIFI_FILE, {"networks": nets})

    def remove(self, ssid):
        nets = [n for n in self.networks() if n.get("ssid") != ssid]
        _save_json(WIFI_FILE, {"networks": nets})

    # ---------------------------------------------------------- 扫描/连接
    def scan(self):
        try:
            return self.wlan.scan() or []
        except Exception as e:
            print("scan failed:", e)
            return []

    def current_ssid(self):
        try:
            return _as_text(self.wlan.config("ssid"))
        except Exception:
            return ""

    def try_connect(self, ssid, password, timeout=15):
        try:
            self.wlan.disconnect()
        except Exception:
            pass
        time.sleep_ms(150)
        try:
            if password:
                self.wlan.connect(ssid, password)
            else:
                self.wlan.connect(ssid)
        except Exception as e:
            print("connect error:", e)
            return False

        t0 = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), t0) < timeout * 1000:
            try:
                if self.wlan.isconnected():
                    return True
                status = self.wlan.status()
            except Exception:
                status = 0
            if status in (STAT_WRONG_PASSWORD, STAT_NO_AP_FOUND, STAT_CONNECT_FAIL):
                return False
            time.sleep_ms(250)
        try:
            return self.wlan.isconnected()
        except Exception:
            return False

    def connect_known(self, timeout=15):
        """按信号从强到弱，连接已保存的网络。返回 SSID 或 None。"""
        nets = self.networks()
        if not nets:
            return None

        # 已经连着且是已知网络，直接复用
        try:
            connected = self.wlan.isconnected()
        except Exception:
            connected = False
        if connected:
            ssid = self.current_ssid()
            for n in nets:
                if n.get("ssid") == ssid:
                    return ssid

        passwords = {}
        for n in nets:
            ssid = n.get("ssid")
            if ssid:
                passwords[ssid] = n.get("password", "")

        candidates = []
        seen = set()
        for ap in self.scan():
            try:
                ssid = _as_text(ap[0])
                rssi = ap[3]
            except Exception:
                continue
            if ssid in passwords and ssid not in seen:
                seen.add(ssid)
                candidates.append((rssi, ssid))
        candidates.sort(reverse=True)

        # 扫描不到的也最后试一次（隐藏 SSID / 扫描抖动）
        for ssid in passwords:
            if ssid not in seen:
                candidates.append((-999, ssid))

        for rssi, ssid in candidates:
            print("try wifi:", ssid, "rssi", rssi)
            if self.try_connect(ssid, passwords[ssid], timeout):
                print("wifi connected:", ssid)
                return ssid
        return None

    def ifconfig(self):
        try:
            return self.wlan.ifconfig()
        except Exception:
            return None

    def is_connected(self):
        try:
            return self.wlan.isconnected()
        except Exception:
            return False

    def disconnect(self):
        try:
            self.wlan.disconnect()
        except Exception:
            pass


# ---------------------------------------------------------------- AP 配置模式
def start_ap(name=None, password="xiaomiao123"):
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    if not name:
        mac = ap.config("mac")
        name = "XiaoMiao-%02X%02X" % (mac[4], mac[5])
    try:
        ap.config(
            essid=name,
            password=password,
            authmode=getattr(network, "AUTH_WPA_WPA2_PSK", 3),
        )
    except Exception:
        try:
            ap.config(essid=name, password=password)
        except Exception as e:
            print("AP config failed:", e)
    return ap, name, password


def stop_ap():
    try:
        ap = network.WLAN(network.AP_IF)
        ap.active(False)
    except Exception:
        pass
