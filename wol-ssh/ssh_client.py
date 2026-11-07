# -*- coding: utf-8 -*-
"""
极简 SSH 客户端（MicroPython / CPython 通用）

只做一件事：密码认证 + 执行一条远程命令 + 收集输出。
为了在 ESP32 上跑得动，只实现 OpenSSH 默认仍然支持的算法：

  kex:      curve25519-sha256（优先）/ diffie-hellman-group14-sha256
  host key: rsa-sha2-256（服务端需要有 RSA host key）
  cipher:   aes128-ctr
  mac:      hmac-sha2-256
  auth:     password（失败时尝试 keyboard-interactive）
  compress: none

安全说明：
  - 会对服务端 host key 的 RSA 签名做校验，避免“只比对指纹不验签”的假安全。
  - 建议在配置里填 host_fingerprint（SHA256:...），首次连接也可以通过
    SSHClient.fingerprint 拿到实际值再写进配置（TOFU）。

在桌面 CPython 上测试时，cryptolib 不存在，会自动用 cryptography 库代替。
"""

import socket
import struct
import hashlib
import binascii
import os
import time

try:
    import cryptolib  # MicroPython ESP32
except ImportError:  # CPython，本地测试
    try:
        import ucryptolib as cryptolib
    except ImportError:
        cryptolib = None


# ---------------------------------------------------------------- 协议常量
MSG_DISCONNECT = 1
MSG_IGNORE = 2
MSG_UNIMPLEMENTED = 3
MSG_DEBUG = 4
MSG_SERVICE_REQUEST = 5
MSG_SERVICE_ACCEPT = 6
MSG_EXT_INFO = 7
MSG_KEXINIT = 20
MSG_NEWKEYS = 21
MSG_KEXDH_INIT = 30
MSG_KEXDH_REPLY = 31
MSG_USERAUTH_REQUEST = 50
MSG_USERAUTH_FAILURE = 51
MSG_USERAUTH_SUCCESS = 52
MSG_USERAUTH_BANNER = 53
MSG_USERAUTH_INFO_REQUEST = 60
MSG_USERAUTH_INFO_RESPONSE = 61
MSG_GLOBAL_REQUEST = 80
MSG_REQUEST_SUCCESS = 81
MSG_REQUEST_FAILURE = 82
MSG_CHANNEL_OPEN = 90
MSG_CHANNEL_OPEN_CONFIRMATION = 91
MSG_CHANNEL_OPEN_FAILURE = 92
MSG_CHANNEL_WINDOW_ADJUST = 93
MSG_CHANNEL_DATA = 94
MSG_CHANNEL_EXTENDED_DATA = 95
MSG_CHANNEL_EOF = 96
MSG_CHANNEL_CLOSE = 97
MSG_CHANNEL_REQUEST = 98
MSG_CHANNEL_SUCCESS = 99
MSG_CHANNEL_FAILURE = 100

# RFC 3526 group 14 (2048-bit MODP)
P14 = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD1"
    "29024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245"
    "E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3D"
    "C2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D"
    "670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9"
    "DE2BCBF6955817183995497CEA956AE515D2261898FA0510"
    "15728E5A8AACAA68FFFFFFFFFFFFFFFF",
    16,
)
G14 = 2


class SSHError(Exception):
    pass


# ---------------------------------------------------------------- 编码工具
def _sha256(data):
    return hashlib.sha256(data).digest()


def _sha512(data):
    return hashlib.sha512(data).digest()


def _hmac_sha256(key, msg):
    """MicroPython 没有 hmac 模块时也能用。"""
    if len(key) > 64:
        key = _sha256(key)
    key = key + b"\x00" * (64 - len(key))
    ipad = bytes([b ^ 0x36 for b in key])
    opad = bytes([b ^ 0x5C for b in key])
    return _sha256(opad + _sha256(ipad + msg))


def _u32(n):
    return struct.pack(">I", n)


def _string(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return _u32(len(data)) + data


def _int_byte_len(n):
    # MicroPython 的 int 没有 bit_length()，用 hex() 的长度算
    if n == 0:
        return 0
    return (len(hex(n)) - 2 + 1) // 2


def _mpint(n):
    if n == 0:
        return _u32(0)
    raw = n.to_bytes(_int_byte_len(n), "big")
    if raw[0] & 0x80:
        raw = b"\x00" + raw
    return _string(raw)


def _read_u32(buf, pos):
    return struct.unpack(">I", buf[pos:pos + 4])[0], pos + 4


def _read_string(buf, pos):
    n, pos = _read_u32(buf, pos)
    return buf[pos:pos + n], pos + n


def _name_list(data):
    return data.decode("ascii", "replace").split(",") if data else []


class _AESECB:
    """单块 AES-128 ECB，只用来手工实现 CTR。"""

    def __init__(self, key):
        if cryptolib is not None:
            # MicroPython 1.23 用 cryptolib.aes，较新版本用 cryptolib.AES
            aes_cls = getattr(cryptolib, "AES", None) or getattr(cryptolib, "aes", None)
            if aes_cls is None:
                raise SSHError("cryptolib has no AES class")
            # ESP32: mode 1 = ECB
            self._aes = aes_cls(key, 1)
            self._enc = None
        else:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            self._aes = None
            self._enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()

    def encrypt(self, block):
        if self._aes is not None:
            return self._aes.encrypt(block)
        return self._enc.update(block)


def _ctr_xor(key, iv, offset, data):
    """
    SSH 的 aes128-ctr。offset 是这个方向已经消费的明文字节数。
    自己维护 128-bit 大端计数器，避免依赖 cryptolib 的流式状态。
    """
    if not data:
        return b""
    aes = _AESECB(key)
    out = bytearray(len(data))
    block_index = offset // 16
    skip = offset % 16
    counter = int.from_bytes(iv, "big")
    mask = (1 << 128) - 1
    pos = 0
    i = 0
    while pos < len(data):
        ctr = ((counter + block_index + i) & mask).to_bytes(16, "big")
        ks = aes.encrypt(ctr)
        start = skip if i == 0 else 0
        take = min(16 - start, len(data) - pos)
        for j in range(take):
            out[pos + j] = data[pos + j] ^ ks[start + j]
        pos += take
        i += 1
    return bytes(out)


def _fingerprint(host_key_blob):
    b64 = binascii.b2a_base64(_sha256(host_key_blob)).strip().decode("ascii")
    return "SHA256:" + b64


# ---------------------------------------------------------------- X25519
_X25519_P = 2 ** 255 - 19
_X25519_A24 = 121665


def _x25519(scalar, u):
    """RFC 7748 X25519，纯 Python，ESP32 上大概 1~3 秒。"""
    if len(scalar) != 32 or len(u) != 32:
        raise SSHError("bad X25519 key length")
    k = bytearray(scalar)
    k[0] &= 248
    k[31] &= 127
    k[31] |= 64
    k = int.from_bytes(k, "little")
    x1 = int.from_bytes(u, "little") & ((1 << 255) - 1)

    p = _X25519_P
    a24 = _X25519_A24
    x2, z2 = 1, 0
    x3, z3 = x1, 1
    swap = 0
    for t in range(254, -1, -1):
        kt = (k >> t) & 1
        swap ^= kt
        if swap:
            x2, x3 = x3, x2
            z2, z3 = z3, z2
        swap = kt

        a = (x2 + z2) % p
        aa = (a * a) % p
        b = (x2 - z2) % p
        bb = (b * b) % p
        e = (aa - bb) % p
        c = (x3 + z3) % p
        d = (x3 - z3) % p
        da = (d * a) % p
        cb = (c * b) % p
        x3 = ((da + cb) * (da + cb)) % p
        z3 = (x1 * ((da - cb) * (da - cb))) % p
        x2 = (aa * bb) % p
        z2 = (e * (aa + a24 * e)) % p

    if swap:
        x2, x3 = x3, x2
        z2, z3 = z3, z2
    return (x2 * pow(z2, p - 2, p) % p).to_bytes(32, "little")


def _verify_rsa_signature(host_key_blob, sig_blob, exchange_hash):
    key_type, pos = _read_string(host_key_blob, 0)
    if key_type != b"ssh-rsa":
        raise SSHError("unsupported host key type: %r" % (key_type,))
    e_raw, pos = _read_string(host_key_blob, pos)
    n_raw, pos = _read_string(host_key_blob, pos)
    e = int.from_bytes(e_raw, "big")
    n = int.from_bytes(n_raw, "big")

    sig_type, pos = _read_string(sig_blob, 0)
    sig_raw, pos = _read_string(sig_blob, pos)

    if sig_type == b"rsa-sha2-256":
        digest = _sha256(exchange_hash)
        prefix = binascii.unhexlify("3031300d060960864801650304020105000420")
    elif sig_type == b"rsa-sha2-512":
        digest = _sha512(exchange_hash)
        prefix = binascii.unhexlify("3051300d060960864801650304020305000440")
    else:
        raise SSHError("unsupported signature type: %r" % (sig_type,))

    k = _int_byte_len(n)
    em = pow(int.from_bytes(sig_raw, "big"), e, n).to_bytes(k, "big")
    expected = (
        b"\x00\x01"
        + b"\xff" * (k - 3 - len(prefix) - len(digest))
        + b"\x00"
        + prefix
        + digest
    )
    if em != expected:
        raise SSHError("host key signature verification failed")


class SSHClient:
    def __init__(self, host, port=22, user="", password="", timeout=15,
                 fingerprint=None, debug=False):
        self.host = host
        self.port = int(port)
        self.user = user
        self.password = password
        self.timeout = timeout
        self.expected_fingerprint = fingerprint or None
        self.debug = debug

        self.sock = None
        self.v_c = b"SSH-2.0-XiaoMiao_1.0"
        self.v_s = b""
        self.i_c = b""
        self.i_s = b""
        self.session_id = b""
        self.host_key = b""
        self.fingerprint = ""

        self.seq_out = 0
        self.seq_in = 0
        self.enc_out = False
        self.enc_in = False
        self.key_c2s = b""
        self.key_s2c = b""
        self.iv_c2s = b""
        self.iv_s2c = b""
        self.mac_c2s = b""
        self.mac_s2c = b""
        self.off_out = 0
        self.off_in = 0

        self.local_chan = 0
        self.remote_chan = 0
        self.local_window = 1 << 20
        self.remote_window = 0
        self.remote_maxp = 32768

        self.stdout = b""
        self.stderr = b""
        self.exit_status = None

    # ------------------------------------------------------------ 低层 I/O
    def _log(self, *args):
        if self.debug:
            print("[ssh]", *args)

    def _read_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise SSHError("connection closed by peer")
            buf += chunk
        return buf

    def _readline(self, limit=255):
        line = b""
        while len(line) < limit:
            ch = self.sock.recv(1)
            if not ch:
                raise SSHError("connection closed while reading banner")
            if ch == b"\n":
                break
            line += ch
        return line.rstrip(b"\r")

    def _send(self, payload):
        block = 16 if self.enc_out else 8
        pad = block - ((4 + 1 + len(payload)) % block)
        if pad < 4:
            pad += block
        packet = _u32(1 + len(payload) + pad) + bytes([pad]) + payload
        if pad:
            packet += os.urandom(pad)

        if self.enc_out:
            enc = _ctr_xor(self.key_c2s, self.iv_c2s, self.off_out, packet)
            self.off_out += len(packet)
            mac = _hmac_sha256(self.mac_c2s, _u32(self.seq_out) + packet)
            self.sock.sendall(enc + mac)
        else:
            self.sock.sendall(packet)
        self.seq_out = (self.seq_out + 1) & 0xFFFFFFFF

    def _recv(self):
        if not self.enc_in:
            hdr = self._read_exact(4)
            plen = struct.unpack(">I", hdr)[0]
            if plen < 5 or plen > (1 << 20):
                raise SSHError("bad packet length: %d" % plen)
            packet = hdr + self._read_exact(plen)
        else:
            first = self._read_exact(16)
            first_plain = _ctr_xor(self.key_s2c, self.iv_s2c, self.off_in, first)
            self.off_in += 16
            plen = struct.unpack(">I", first_plain[:4])[0]
            if plen < 5 or plen > (1 << 20):
                raise SSHError("bad packet length: %d" % plen)
            total = 4 + plen
            if total > 16:
                rest = self._read_exact(total - 16)
                rest_plain = _ctr_xor(self.key_s2c, self.iv_s2c, self.off_in, rest)
                self.off_in += total - 16
                packet = first_plain + rest_plain
            else:
                packet = first_plain[:total]
            mac_recv = self._read_exact(32)
            mac_calc = _hmac_sha256(self.mac_s2c, _u32(self.seq_in) + packet)
            if mac_recv != mac_calc:
                raise SSHError("MAC verification failed")

        self.seq_in = (self.seq_in + 1) & 0xFFFFFFFF
        pad_len = packet[4]
        if pad_len < 4 or 5 + pad_len > len(packet):
            raise SSHError("bad padding")
        return packet[5:len(packet) - pad_len]

    def _ignore(self, payload):
        t = payload[0]
        if t == MSG_DISCONNECT:
            reason = "disconnected"
            if len(payload) >= 8:
                reason, _ = _read_string(payload, 5)
                reason = reason.decode("utf-8", "replace")
            raise SSHError(reason)
        if t in (MSG_IGNORE, MSG_DEBUG, MSG_UNIMPLEMENTED, MSG_EXT_INFO):
            return True
        return False

    def _handle_global(self, payload):
        # SSH_MSG_GLOBAL_REQUEST: string name, boolean want_reply
        name, pos = _read_string(payload, 1)
        want = payload[pos] if pos < len(payload) else 0
        self._log("global request:", name)
        if want:
            self._send(bytes([MSG_REQUEST_FAILURE]))

    # ------------------------------------------------------------ 握手
    def _build_kexinit(self):
        # 只列出我们真正实现的算法；服务端必须从中选择。
        lists = [
            b"curve25519-sha256,curve25519-sha256@libssh.org,diffie-hellman-group14-sha256",
            b"rsa-sha2-256",                    # host key
            b"aes128-ctr",                      # enc c2s
            b"aes128-ctr",                      # enc s2c
            b"hmac-sha2-256",                   # mac c2s
            b"hmac-sha2-256",                   # mac s2c
            b"none",                            # comp c2s
            b"none",                            # comp s2c
            b"",                                # lang c2s
            b"",                                # lang s2c
        ]
        payload = bytes([MSG_KEXINIT]) + os.urandom(16)
        for item in lists:
            payload += _string(item)
        payload += b"\x00" + _u32(0)
        return payload

    def _kex(self):
        self.i_c = self._build_kexinit()
        self._send(self.i_c)

        while True:
            payload = self._recv()
            if payload[0] == MSG_KEXINIT:
                self.i_s = payload
                break
            self._ignore(payload)

        # 服务端 KEXINIT 里第 1 个 name-list 是它支持的 kex
        pos = 17  # type(1) + cookie(16)
        server_kex_raw, pos = _read_string(self.i_s, pos)
        server_kex = _name_list(server_kex_raw)

        selected = None
        for alg in (
            b"curve25519-sha256",
            b"curve25519-sha256@libssh.org",
            b"diffie-hellman-group14-sha256",
        ):
            if alg.decode("ascii") in server_kex:
                selected = alg
                break
        if selected is None:
            raise SSHError("no common kex algorithm")
        self._log("kex:", selected)

        if selected.startswith(b"curve25519"):
            host_key, exchange_hash, K = self._kex_curve25519()
        else:
            host_key, exchange_hash, K = self._kex_group14()

        self._finish_kex(host_key, exchange_hash, K)

    def _kex_curve25519(self):
        private = os.urandom(32)
        q_c = _x25519(private, b"\x09" + b"\x00" * 31)
        self._send(bytes([MSG_KEXDH_INIT]) + _string(q_c))

        while True:
            payload = self._recv()
            if payload[0] == MSG_KEXDH_REPLY:
                break
            self._ignore(payload)

        pos = 1
        host_key, pos = _read_string(payload, pos)
        q_s, pos = _read_string(payload, pos)
        sig_blob, pos = _read_string(payload, pos)
        if len(q_s) != 32:
            raise SSHError("bad server X25519 key")

        shared = _x25519(private, q_s)
        if shared == b"\x00" * 32:
            raise SSHError("X25519 produced an all-zero shared secret")
        K = int.from_bytes(shared, "big")
        exchange_hash = _sha256(
            _string(self.v_c)
            + _string(self.v_s)
            + _string(self.i_c)
            + _string(self.i_s)
            + _string(host_key)
            + _string(q_c)
            + _string(q_s)
            + _mpint(K)
        )
        _verify_rsa_signature(host_key, sig_blob, exchange_hash)
        return host_key, exchange_hash, K

    def _kex_group14(self):
        x = int.from_bytes(os.urandom(32), "big")
        # 保证 1 < x < p-1
        x = 2 + (x % (P14 - 3))
        e = pow(G14, x, P14)
        self._send(bytes([MSG_KEXDH_INIT]) + _mpint(e))

        while True:
            payload = self._recv()
            if payload[0] == MSG_KEXDH_REPLY:
                break
            self._ignore(payload)

        pos = 1
        host_key, pos = _read_string(payload, pos)
        f_raw, pos = _read_string(payload, pos)
        sig_blob, pos = _read_string(payload, pos)
        f = int.from_bytes(f_raw, "big")
        K = pow(f, x, P14)
        exchange_hash = _sha256(
            _string(self.v_c)
            + _string(self.v_s)
            + _string(self.i_c)
            + _string(self.i_s)
            + _string(host_key)
            + _mpint(e)
            + _string(f_raw)
            + _mpint(K)
        )
        _verify_rsa_signature(host_key, sig_blob, exchange_hash)
        return host_key, exchange_hash, K

    def _finish_kex(self, host_key, exchange_hash, K):
        self.host_key = host_key
        self.fingerprint = _fingerprint(host_key)

        if self.expected_fingerprint and self.fingerprint != self.expected_fingerprint:
            raise SSHError(
                "host fingerprint mismatch: got %s" % self.fingerprint
            )

        self.session_id = exchange_hash
        mp_k = _mpint(K)
        self.iv_c2s = _sha256(mp_k + exchange_hash + b"A" + exchange_hash)[:16]
        self.iv_s2c = _sha256(mp_k + exchange_hash + b"B" + exchange_hash)[:16]
        self.key_c2s = _sha256(mp_k + exchange_hash + b"C" + exchange_hash)[:16]
        self.key_s2c = _sha256(mp_k + exchange_hash + b"D" + exchange_hash)[:16]
        self.mac_c2s = _sha256(mp_k + exchange_hash + b"E" + exchange_hash)[:32]
        self.mac_s2c = _sha256(mp_k + exchange_hash + b"F" + exchange_hash)[:32]

        self._send(bytes([MSG_NEWKEYS]))
        self.enc_out = True

        while True:
            payload = self._recv()
            if payload[0] == MSG_NEWKEYS:
                break
            self._ignore(payload)
        self.enc_in = True

    # ------------------------------------------------------------ 认证
    def _auth(self):
        self._send(bytes([MSG_SERVICE_REQUEST]) + _string(b"ssh-userauth"))
        while True:
            payload = self._recv()
            if payload[0] == MSG_SERVICE_ACCEPT:
                break
            self._ignore(payload)

        self._send(
            bytes([MSG_USERAUTH_REQUEST])
            + _string(self.user)
            + _string(b"ssh-connection")
            + _string(b"password")
            + b"\x00"
            + _string(self.password)
        )

        while True:
            payload = self._recv()
            t = payload[0]
            if t == MSG_USERAUTH_SUCCESS:
                return
            if t == MSG_USERAUTH_BANNER:
                continue
            if t == MSG_USERAUTH_FAILURE:
                methods_raw, pos = _read_string(payload, 1)
                methods = _name_list(methods_raw)
                self._log("password auth failed, methods:", methods)
                if "keyboard-interactive" in methods:
                    if self._auth_keyboard_interactive():
                        return
                raise SSHError("authentication failed (%s)" % ",".join(methods))
            self._ignore(payload)

    def _auth_keyboard_interactive(self):
        self._send(
            bytes([MSG_USERAUTH_REQUEST])
            + _string(self.user)
            + _string(b"ssh-connection")
            + _string(b"keyboard-interactive")
            + _string(b"")
            + _string(b"")
        )
        while True:
            payload = self._recv()
            t = payload[0]
            if t == MSG_USERAUTH_SUCCESS:
                return True
            if t == MSG_USERAUTH_FAILURE:
                return False
            if t == MSG_USERAUTH_INFO_REQUEST:
                # name, instruction, lang, num-prompts, then (prompt, echo)*
                pos = 1
                _, pos = _read_string(payload, pos)
                _, pos = _read_string(payload, pos)
                _, pos = _read_string(payload, pos)
                num, pos = _read_u32(payload, pos)
                resp = bytes([MSG_USERAUTH_INFO_RESPONSE]) + _u32(num)
                for _ in range(num):
                    _, pos = _read_string(payload, pos)
                    pos += 1  # echo
                    resp += _string(self.password)
                self._send(resp)
                continue
            if t == MSG_USERAUTH_BANNER:
                continue
            self._ignore(payload)

    # ------------------------------------------------------------ 通道
    def _open_session(self):
        self._send(
            bytes([MSG_CHANNEL_OPEN])
            + _string(b"session")
            + _u32(self.local_chan)
            + _u32(self.local_window)
            + _u32(32768)
        )
        while True:
            payload = self._recv()
            t = payload[0]
            if t == MSG_CHANNEL_OPEN_CONFIRMATION:
                pos = 1
                _, pos = _read_u32(payload, pos)       # recipient (our channel)
                self.remote_chan, pos = _read_u32(payload, pos)
                self.remote_window, pos = _read_u32(payload, pos)
                self.remote_maxp, pos = _read_u32(payload, pos)
                return
            if t == MSG_CHANNEL_OPEN_FAILURE:
                pos = 1
                _, pos = _read_u32(payload, pos)
                reason, pos = _read_u32(payload, pos)
                desc, pos = _read_string(payload, pos)
                raise SSHError("channel open failed (%d): %s" % (reason, desc))
            if t == MSG_GLOBAL_REQUEST:
                self._handle_global(payload)
                continue
            self._ignore(payload)

    def _append_data(self, payload, extended=False):
        pos = 1
        _, pos = _read_u32(payload, pos)  # recipient
        if extended:
            dtype, pos = _read_u32(payload, pos)
        else:
            dtype = 0
        data, pos = _read_string(payload, pos)
        if dtype == 1:
            self.stderr += data
        else:
            self.stdout += data

        self.local_window -= len(data)
        if self.local_window < 65536:
            add = (1 << 20) - self.local_window
            self._send(
                bytes([MSG_CHANNEL_WINDOW_ADJUST])
                + _u32(self.remote_chan)
                + _u32(add)
            )
            self.local_window += add

    def _handle_channel_request(self, payload):
        pos = 1
        _, pos = _read_u32(payload, pos)      # recipient
        req, pos = _read_string(payload, pos)
        pos += 1  # want_reply
        if req == b"exit-status":
            self.exit_status, pos = _read_u32(payload, pos)
        elif req == b"exit-signal":
            pass
        return req

    def exec(self, command):
        self._send(
            bytes([MSG_CHANNEL_REQUEST])
            + _u32(self.remote_chan)
            + _string(b"exec")
            + b"\x01"
            + _string(command)
        )

        closed = False
        while not closed:
            payload = self._recv()
            t = payload[0]
            if t == MSG_CHANNEL_DATA:
                self._append_data(payload, False)
            elif t == MSG_CHANNEL_EXTENDED_DATA:
                self._append_data(payload, True)
            elif t == MSG_CHANNEL_EOF:
                pass
            elif t == MSG_CHANNEL_CLOSE:
                closed = True
            elif t == MSG_CHANNEL_REQUEST:
                self._handle_channel_request(payload)
            elif t == MSG_CHANNEL_WINDOW_ADJUST:
                pos = 1
                _, pos = _read_u32(payload, pos)
                add, pos = _read_u32(payload, pos)
                self.remote_window += add
            elif t in (MSG_CHANNEL_SUCCESS, MSG_CHANNEL_FAILURE):
                if t == MSG_CHANNEL_FAILURE:
                    raise SSHError("exec request rejected")
            elif t == MSG_GLOBAL_REQUEST:
                self._handle_global(payload)
            else:
                if not self._ignore(payload):
                    self._log("unhandled message", t)

        try:
            self._send(bytes([MSG_CHANNEL_CLOSE]) + _u32(self.remote_chan))
            self._send(
                bytes([MSG_DISCONNECT])
                + _u32(11)
                + _string(b"bye")
                + _string(b"")
            )
        except Exception:
            pass
        return self.stdout, self.stderr, self.exit_status

    # ------------------------------------------------------------ 公共接口
    def connect(self):
        infos = socket.getaddrinfo(self.host, self.port)
        addr = None
        for info in infos:
            if info[0] == socket.AF_INET:
                addr = info[-1]
                break
        if addr is None and infos:
            addr = infos[0][-1]
        if addr is None:
            raise SSHError("cannot resolve %s" % self.host)
        self.sock = socket.socket()
        self.sock.settimeout(self.timeout)
        self.sock.connect(addr)
        self.sock.sendall(self.v_c + b"\r\n")
        while True:
            line = self._readline()
            if line.startswith(b"SSH-"):
                self.v_s = line
                break
        self._log("server:", self.v_s)
        self._kex()
        self._auth()
        self._open_session()
        return self

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *args):
        self.close()

    def run(self, command):
        return self.exec(command)
