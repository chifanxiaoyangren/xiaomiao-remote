# -*- coding: utf-8 -*-
"""
手机配置网页（AP 热点 / 已连接 WiFi 的局域网 IP 都能访问）。

页面支持：
  - 修改 WiFi + SSH 连接参数
  - 添加 / 编辑 / 删除多条 SSH 命令，每条有别名
  - 选择当前命令
保存后直接更新内存里的配置，不重启，主循环下一轮就生效。
"""

import socket
import time
import machine
import _thread

from wifi_manager import save_config, start_ap


PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>XiaoMiao 配置</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;padding:16px;background:#f5f6f8;color:#222}
h1{font-size:20px;margin:0 0 12px}h2{font-size:15px;margin:20px 0 8px;color:#555}h3{font-size:14px;margin:12px 0 6px;color:#555}
.card{background:#fff;border-radius:12px;padding:14px;margin-bottom:12px;box-shadow:0 1px 3px #0001}
label{display:block;font-size:13px;color:#666;margin:10px 0 4px}
input,textarea{width:100%;box-sizing:border-box;padding:11px;border:1px solid #ccd;border-radius:8px;font-size:16px}
textarea{min-height:96px;resize:vertical;font-family:ui-monospace,Menlo,Consolas,monospace;line-height:1.4;white-space:pre-wrap}
input:focus,textarea:focus{outline:2px solid #2f6fed33;border-color:#2f6fed}
button{width:100%;padding:12px;margin-top:12px;border:0;border-radius:8px;background:#2f6fed;color:#fff;font-size:16px}
a{color:#2f6fed;text-decoration:none}.muted{color:#888;font-size:12px;line-height:1.5}
.banner{padding:10px 12px;border-radius:10px;margin-bottom:12px;background:#e8f6ec;color:#1c7a35;font-size:13px}
.banner.err{background:#fdecec;color:#b42318}
ul{padding-left:0;list-style:none;margin:0}li{padding:8px 0;border-bottom:1px solid #eee}
li:last-child{border:0}.rssi{color:#999;font-size:12px;float:right}
.row{display:flex;gap:8px;align-items:center;margin-top:10px}
.row button{margin-top:0;flex:1}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;background:#e8f0fe;color:#2f6fed;font-size:11px;margin-left:6px}
.danger{color:#d33}
</style></head><body>
<h1>XiaoMiao 配置</h1>
{{BANNER}}
<div class="card">
<div class="muted">当前 WiFi：{{CURRENT}}</div>
<div class="muted">掌机 IP：{{IP}}</div>
<div class="muted">配置热点：{{AP}}</div>
<div class="muted">已保存 WiFi：{{SAVED}}</div>
<div class="muted">当前主机：{{CURRENT_HOST}}　当前命令：{{CURRENT_CMD}}　选择：{{MODE}}</div>
</div>
<form method="post" action="/save">
<div class="card">
<h2>WiFi</h2>
<label>WiFi SSID</label><input name="ssid" value="{{SSID}}" placeholder="选择下方 WiFi 或手动输入">
<label>WiFi 密码（留空表示不修改）</label><input name="password" type="password" placeholder="留空则不修改已保存密码">
<button type="submit">保存 WiFi（立即生效）</button>
<div class="muted">修改 WiFi 后掌机会自动重连；如果换了网络，请用屏幕上的新 IP 访问。</div>
</div>
</form>
<h2>SSH 主机（掌机上 左/右键切换主机/命令，上/下选择）</h2>
{{HOSTS}}
<div class="card">
<h3>添加主机</h3>
<form method="post" action="/host/add">
<label>别名（显示在掌机屏幕上）</label><input name="alias" placeholder="例如：Mac mini">
<label>主机 / IP</label><input name="host" placeholder="192.168.31.128">
<label>端口</label><input name="port" value="22">
<label>用户名</label><input name="user" placeholder="dww">
<label>密码</label><input name="password" type="password">
<label>主机指纹（可选，SHA256:...）</label><input name="fingerprint" placeholder="首次连接后可填写">
<button type="submit">添加主机</button>
</form>
</div>
<h2>SSH 命令（掌机上 左/右键切换主机/命令，上/下选择，A 执行）</h2>
{{COMMANDS}}
<div class="card">
<h3>添加命令</h3>
<form method="post" action="/cmd/add">
<label>别名（显示在掌机屏幕上）</label><input name="alias" placeholder="例如：唤醒电脑">
<label>命令（任意 shell 命令，可多行）</label><textarea name="command" rows="4" autocapitalize="off" autocorrect="off" spellcheck="false" placeholder="例如：&#10;wol AA:BB:CC:DD:EE:FF&#10;systemctl restart nginx&#10;/usr/local/bin/backup.sh"></textarea>
<button type="submit">添加命令</button>
</form>
</div>
<div class="card">
<h2>附近的 WiFi <a style="font-size:12px;font-weight:normal" href="/?scan=1">刷新</a></h2>
<ul>{{NETWORKS}}</ul>
</div>
<div class="card">
<h2>已保存的 WiFi</h2>
<ul>{{SAVEDLIST}}</ul>
</div>
<p class="muted">配置保存在掌机 flash 中。SSH 密码为明文存储，请使用专用的低权限账号。</p>
<p class="muted"><a href="/reboot" onclick="return confirm('确定重启掌机？')">重启掌机</a></p>
</body></html>
"""


CARD = """<div class="card">
<form method="post" action="/cmd/update">
<input type="hidden" name="index" value="{{INDEX}}">
<label>别名{{BADGE}}</label><input name="alias" value="{{ALIAS}}" placeholder="命令 {{INDEX_PLUS}}">
<label>命令</label><textarea name="command" rows="4">{{COMMAND}}</textarea>
<div class="row">
<button type="submit">保存这条</button>
<a class="danger" href="/cmd/delete?index={{INDEX}}" onclick="return confirm('删除这条命令？')">删除</a>
<a href="/cmd/select?index={{INDEX}}">设为当前</a>
</div>
</form>
</div>"""


HOST_CARD = """<div class="card">
<form method="post" action="/host/update">
<input type="hidden" name="index" value="{{INDEX}}">
<label>别名{{BADGE}}</label><input name="alias" value="{{ALIAS}}" placeholder="主机 {{INDEX_PLUS}}">
<label>主机 / IP</label><input name="host" value="{{HOST}}" placeholder="192.168.31.128">
<label>端口</label><input name="port" value="{{PORT}}">
<label>用户名</label><input name="user" value="{{USER}}">
<label>密码（留空保持不变）</label><input name="password" type="password">
<label>主机指纹（可选）</label><input name="fingerprint" value="{{FINGERPRINT}}">
<div class="row">
<button type="submit">保存这条</button>
<a class="danger" href="/host/delete?index={{INDEX}}" onclick="return confirm('删除这台主机？')">删除</a>
<a href="/host/select?index={{INDEX}}">设为当前</a>
</div>
</form>
</div>"""


def _html_escape(text):
    text = str(text)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _url_decode(text):
    text = text.replace("+", " ")
    out = bytearray()
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "%" and i + 2 < len(text):
            try:
                out.append(int(text[i + 1:i + 3], 16))
                i += 3
                continue
            except Exception:
                pass
        out.extend(ch.encode("utf-8"))
        i += 1
    try:
        return out.decode("utf-8")
    except Exception:
        return out.decode("utf-8", "replace")


def _url_encode(text):
    raw = text.encode("utf-8")
    out = []
    for b in raw:
        if (48 <= b <= 57) or (65 <= b <= 90) or (97 <= b <= 122) or b in (45, 46, 95, 126):
            out.append(chr(b))
        else:
            out.append("%%%02X" % b)
    return "".join(out)


def _parse_qs(text):
    result = {}
    for part in text.split("&"):
        if not part:
            continue
        key, sep, value = part.partition("=")
        if sep:
            result[_url_decode(key)] = _url_decode(value)
        else:
            result[_url_decode(key)] = ""
    return result


def _normalize_command(text):
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _send(client, status, body, content_type="text/html; charset=utf-8", extra=""):
    if isinstance(body, str):
        body = body.encode("utf-8")
    header = (
        "HTTP/1.0 %s\r\n"
        "Content-Type: %s\r\n"
        "Content-Length: %d\r\n"
        "Connection: close\r\n"
        "%s"
        "\r\n" % (status, content_type, len(body), extra)
    )
    client.sendall(header.encode("utf-8") + body)


def _redirect(client, location):
    _send(client, "302 Found", "redirect", extra="Location: %s\r\n" % location)


class Portal:
    def __init__(self, state):
        self.state = state

    # ------------------------------------------------------------ 线程
    def start(self):
        try:
            _thread.stack_size(16 * 1024)
        except Exception:
            pass
        try:
            _thread.start_new_thread(self.serve_forever, ())
            print("portal thread started")
        except Exception as e:
            print("portal thread failed:", e)

    def serve_forever(self):
        while True:
            try:
                server = socket.socket()
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.bind(("0.0.0.0", 80))
                server.listen(2)
                print("portal: listening on :80")
                while True:
                    client, addr = server.accept()
                    try:
                        client.settimeout(5)
                        self._handle(client)
                    except Exception as e:
                        print("portal request error:", e)
                    finally:
                        try:
                            client.close()
                        except Exception:
                            pass
            except Exception as e:
                print("portal server error:", e)
                time.sleep(2)

    # ------------------------------------------------------------ 请求解析
    def _read_request(self, client):
        data = b""
        while b"\r\n\r\n" not in data and len(data) < 16384:
            chunk = client.recv(1024)
            if not chunk:
                break
            data += chunk
        head, _, body = data.partition(b"\r\n\r\n")
        lines = head.split(b"\r\n")
        if not lines or not lines[0]:
            return None
        parts = lines[0].split(b" ")
        if len(parts) < 2:
            return None
        method = parts[0].decode("ascii", "replace")
        target = parts[1].decode("utf-8", "replace")
        headers = {}
        for line in lines[1:]:
            key, sep, value = line.partition(b":")
            if sep:
                headers[key.strip().lower()] = value.strip()
        try:
            raw_length = headers.get(b"content-length", b"0")
            if isinstance(raw_length, bytes):
                raw_length = raw_length.decode("ascii", "replace")
            length = int(raw_length)
        except Exception:
            length = 0
        while len(body) < length:
            chunk = client.recv(min(1024, length - len(body)))
            if not chunk:
                break
            body += chunk
        return method, target, headers, body[:length]

    def _handle(self, client):
        req = self._read_request(client)
        if not req:
            _send(client, "400 Bad Request", "bad request")
            return
        method, target, headers, body = req
        path, _, query = target.partition("?")
        args = _parse_qs(query)

        if method == "POST":
            args.update(_parse_qs(body.decode("utf-8", "replace")))

        if path == "/save" and method == "POST":
            self._save_wifi(client, args)
            return
        if path == "/host/add" and method == "POST":
            self._host_add(client, args)
            return
        if path == "/host/update" and method == "POST":
            self._host_update(client, args)
            return
        if path == "/host/delete":
            self._host_delete(client, args)
            return
        if path == "/host/select":
            self._host_select(client, args)
            return
        if path == "/cmd/add" and method == "POST":
            self._cmd_add(client, args)
            return
        if path == "/cmd/update" and method == "POST":
            self._cmd_update(client, args)
            return
        if path == "/cmd/delete":
            self._cmd_delete(client, args)
            return
        if path == "/cmd/select":
            self._cmd_select(client, args)
            return
        if path == "/del":
            ssid = args.get("ssid", "")
            if ssid:
                self.state["wifi"].remove(ssid)
            _redirect(client, "/?ok=wifi")
            return
        if path == "/reboot":
            _send(client, "200 OK", "<h3>Rebooting...</h3>")
            time.sleep_ms(500)
            machine.reset()
            return
        if path in ("/generate_204", "/hotspot-detect.html", "/connecttest.txt", "/ncsi.txt"):
            _redirect(client, "/")
            return
        if path != "/":
            _send(client, "404 Not Found", "not found")
            return
        self._index(client, args)

    # ------------------------------------------------------------ 配置
    def _commands(self):
        cfg = self.state.get("config") or {}
        cmds = cfg.get("commands")
        return cmds if isinstance(cmds, list) else []

    def _set_config(self, cfg):
        lock = self.state.get("config_lock")
        if lock:
            lock.acquire()
        try:
            cfg.pop("ssh_command", None)
            self.state["config"] = cfg
            self.state["version"] = self.state.get("version", 0) + 1
            try:
                save_config(cfg)
            except Exception as e:
                print("save config failed:", e)
        finally:
            if lock:
                lock.release()

    def _set_commands(self, cmds):
        cfg = dict(self.state.get("config") or {})
        cfg["commands"] = cmds
        self._set_config(cfg)

    def _scan(self):
        lock = self.state.get("wifi_lock")
        if lock:
            lock.acquire()
        try:
            return self.state["wifi"].scan()
        except Exception as e:
            print("scan failed:", e)
            return []
        finally:
            if lock:
                lock.release()

    # ------------------------------------------------------------ 主机列表
    def _hosts(self):
        cfg = self.state.get("config") or {}
        hosts = cfg.get("hosts")
        return hosts if isinstance(hosts, list) else []

    def _set_hosts(self, hosts):
        cfg = dict(self.state.get("config") or {})
        cfg["hosts"] = hosts
        self._set_config(cfg)

    def _save_wifi(self, client, args):
        ssid = args.get("ssid", "").strip()
        password = args.get("password", "")
        dirty = False
        if ssid:
            old = {}
            for n in self.state["wifi"].networks():
                old[n.get("ssid")] = n.get("password", "")
            old_pw = old.get(ssid, "")
            if password == "":
                password = old_pw
            self.state["wifi"].add(ssid, password)
            current = self.state["wifi"].current_ssid()
            if ssid != current or (password and password != old_pw):
                dirty = True

        _redirect(client, "/?ok=wifi")
        if dirty:
            self.state["wifi_dirty"] = True

    def _host_add(self, client, args):
        host = args.get("host", "").strip()
        if not host:
            _redirect(client, "/?err=emptyhost")
            return
        alias = args.get("alias", "").strip()
        hosts = list(self._hosts())
        if not alias:
            alias = "主机%d" % (len(hosts) + 1)
        try:
            port = int(args.get("port", "22") or 22)
        except Exception:
            port = 22
        hosts.append({
            "alias": alias,
            "host": host,
            "port": port,
            "user": args.get("user", "").strip(),
            "password": args.get("password", ""),
            "fingerprint": args.get("fingerprint", "").strip(),
        })
        self._set_hosts(hosts)
        _redirect(client, "/?ok=host")

    def _host_update(self, client, args):
        try:
            index = int(args.get("index", "-1"))
        except Exception:
            index = -1
        hosts = list(self._hosts())
        if index < 0 or index >= len(hosts):
            _redirect(client, "/?err=index")
            return
        host = args.get("host", "").strip()
        if not host:
            _redirect(client, "/?err=emptyhost")
            return
        old = hosts[index]
        password = args.get("password", "")
        if password == "":
            password = old.get("password", "")
        alias = args.get("alias", "").strip()
        if not alias:
            alias = "主机%d" % (index + 1)
        try:
            port = int(args.get("port", "22") or 22)
        except Exception:
            port = old.get("port", 22)
        hosts[index] = {
            "alias": alias,
            "host": host,
            "port": port,
            "user": args.get("user", "").strip(),
            "password": password,
            "fingerprint": args.get("fingerprint", "").strip(),
        }
        self._set_hosts(hosts)
        _redirect(client, "/?ok=host")

    def _host_delete(self, client, args):
        try:
            index = int(args.get("index", "-1"))
        except Exception:
            index = -1
        hosts = list(self._hosts())
        if index < 0 or index >= len(hosts):
            _redirect(client, "/?err=index")
            return
        del hosts[index]
        self._set_hosts(hosts)
        _redirect(client, "/?ok=host")

    def _host_select(self, client, args):
        try:
            index = int(args.get("index", "-1"))
        except Exception:
            index = -1
        hosts = self._hosts()
        if index < 0 or index >= len(hosts):
            _redirect(client, "/?err=index")
            return
        self.state["host_index"] = index
        self.state["mode"] = "host"
        self.state["version"] = self.state.get("version", 0) + 1
        _redirect(client, "/?ok=select")

    # ------------------------------------------------------------ 命令编辑
    def _cmd_add(self, client, args):
        alias = args.get("alias", "").strip()
        command = _normalize_command(args.get("command", ""))
        if not command:
            _redirect(client, "/?err=empty")
            return
        cmds = list(self._commands())
        if not alias:
            alias = "命令%d" % (len(cmds) + 1)
        cmds.append({"alias": alias, "command": command})
        self._set_commands(cmds)
        _redirect(client, "/?ok=cmd")

    def _cmd_update(self, client, args):
        try:
            index = int(args.get("index", "-1"))
        except Exception:
            index = -1
        cmds = list(self._commands())
        if index < 0 or index >= len(cmds):
            _redirect(client, "/?err=index")
            return
        command = _normalize_command(args.get("command", ""))
        if not command:
            _redirect(client, "/?err=empty")
            return
        alias = args.get("alias", "").strip()
        if not alias:
            alias = "命令%d" % (index + 1)
        cmds[index] = {"alias": alias, "command": command}
        self._set_commands(cmds)
        _redirect(client, "/?ok=cmd")

    def _cmd_delete(self, client, args):
        try:
            index = int(args.get("index", "-1"))
        except Exception:
            index = -1
        cmds = list(self._commands())
        if index < 0 or index >= len(cmds):
            _redirect(client, "/?err=index")
            return
        del cmds[index]
        self._set_commands(cmds)
        _redirect(client, "/?ok=cmd")

    def _cmd_select(self, client, args):
        try:
            index = int(args.get("index", "-1"))
        except Exception:
            index = -1
        cmds = self._commands()
        if index < 0 or index >= len(cmds):
            _redirect(client, "/?err=index")
            return
        self.state["cmd_index"] = index
        self.state["mode"] = "cmd"
        self.state["version"] = self.state.get("version", 0) + 1
        _redirect(client, "/?ok=select")

    # ------------------------------------------------------------ 页面
    def _index(self, client, args):
        cfg = self.state.get("config") or {}
        cmds = self._commands()
        hosts = self._hosts()
        wifi = self.state["wifi"]

        try:
            connected = wifi.is_connected()
        except Exception:
            connected = False
        current = wifi.current_ssid() if connected else "未连接"
        info = wifi.ifconfig() if connected else None
        ip = info[0] if info else "-"
        ap = "已开启 (192.168.4.1)" if self.state.get("ap_active") else "未开启"

        nets = wifi.networks()
        saved_names = ", ".join([n.get("ssid", "") for n in nets]) or "无"

        # 扫描并去重（默认用缓存，点“刷新”才重新扫描，避免拖慢 SSH）
        scan_results = self.state.get("scan_cache") or []
        if args.get("scan") or not scan_results:
            scan_results = self._scan()
            self.state["scan_cache"] = scan_results
        found = {}
        for ap_info in scan_results:
            try:
                ssid = ap_info[0]
                if isinstance(ssid, bytes):
                    ssid = ssid.decode("utf-8", "replace")
                if not ssid:
                    continue
                rssi = ap_info[3]
                if ssid not in found or rssi > found[ssid]:
                    found[ssid] = rssi
            except Exception:
                pass
        items = sorted(found.items(), key=lambda kv: kv[1], reverse=True)
        net_rows = []
        for ssid, rssi in items[:20]:
            net_rows.append(
                '<li><a href="/?ssid=%s">%s</a><span class="rssi">%d dBm</span></li>'
                % (_url_encode(ssid), _html_escape(ssid), rssi)
            )
        if not net_rows:
            net_rows.append('<li class="muted">没有扫描到 WiFi</li>')

        saved_rows = []
        for n in nets:
            ssid = n.get("ssid", "")
            saved_rows.append(
                '<li>%s <a style="float:right" href="/del?ssid=%s">删除</a></li>'
                % (_html_escape(ssid), _url_encode(ssid))
            )
        if not saved_rows:
            saved_rows.append('<li class="muted">还没有保存 WiFi</li>')

        # 主机卡片
        host_cards = []
        host_index = self.state.get("host_index", 0)
        for i in range(len(hosts)):
            h = hosts[i]
            badge = '<span class="badge">当前</span>' if i == host_index else ""
            card = (
                HOST_CARD.replace("{{INDEX}}", str(i))
                .replace("{{INDEX_PLUS}}", str(i + 1))
                .replace("{{BADGE}}", badge)
                .replace("{{ALIAS}}", _html_escape(h.get("alias", "")))
                .replace("{{HOST}}", _html_escape(h.get("host", "")))
                .replace("{{PORT}}", _html_escape(h.get("port", 22)))
                .replace("{{USER}}", _html_escape(h.get("user", "")))
                .replace("{{FINGERPRINT}}", _html_escape(h.get("fingerprint", "")))
            )
            host_cards.append(card)
        if not host_cards:
            host_cards.append('<div class="card muted">还没有主机，请在下面添加。</div>')

        # 命令卡片
        cards = []
        cmd_index = self.state.get("cmd_index", 0)
        for i in range(len(cmds)):
            c = cmds[i]
            badge = '<span class="badge">当前</span>' if i == cmd_index else ""
            card = (
                CARD.replace("{{INDEX}}", str(i))
                .replace("{{INDEX_PLUS}}", str(i + 1))
                .replace("{{BADGE}}", badge)
                .replace("{{ALIAS}}", _html_escape(c.get("alias", "")))
                .replace("{{COMMAND}}", _html_escape(c.get("command", "")))
            )
            cards.append(card)
        if not cards:
            cards.append('<div class="card muted">还没有命令，请在下面添加。</div>')

        host_alias = hosts[host_index]["alias"] if 0 <= host_index < len(hosts) else "无"
        cmd_alias = cmds[cmd_index]["alias"] if 0 <= cmd_index < len(cmds) else "无"
        mode_text = "主机" if self.state.get("mode") == "host" else "命令"

        banner = ""
        ok = args.get("ok")
        if ok:
            text = {
                "wifi": "WiFi 设置已保存，立即生效",
                "host": "主机已保存，立即生效",
                "cmd": "命令已保存，立即生效",
                "select": "已切换当前选择",
            }.get(ok, "已保存")
            banner = '<div class="banner">%s</div>' % text
        err = args.get("err")
        if err:
            text = {
                "empty": "命令不能为空",
                "emptyhost": "主机 / IP 不能为空",
                "index": "条目不存在，请刷新页面",
            }.get(err, "操作失败")
            banner = '<div class="banner err">%s</div>' % text

        selected_ssid = args.get("ssid", "")
        if not selected_ssid and current != "未连接":
            selected_ssid = current
        html = (
            PAGE.replace("{{BANNER}}", banner)
            .replace("{{CURRENT}}", _html_escape(current))
            .replace("{{IP}}", _html_escape(ip))
            .replace("{{AP}}", _html_escape(ap))
            .replace("{{SAVED}}", _html_escape(saved_names))
            .replace("{{SSID}}", _html_escape(selected_ssid))
            .replace("{{CURRENT_HOST}}", _html_escape(host_alias))
            .replace("{{CURRENT_CMD}}", _html_escape(cmd_alias))
            .replace("{{MODE}}", _html_escape(mode_text))
            .replace("{{HOSTS}}", "".join(host_cards))
            .replace("{{COMMANDS}}", "".join(cards))
            .replace("{{NETWORKS}}", "".join(net_rows))
            .replace("{{SAVEDLIST}}", "".join(saved_rows))
        )
        _send(client, "200 OK", html)
