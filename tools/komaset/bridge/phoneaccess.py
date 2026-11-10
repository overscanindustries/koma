"""Phone access for komabridge: Settings > Use on your phone in the Koma app.

The bridge's own phone-mode token model (--lan, bridge 0.3): a random token on every request as ?token= or the
X-Koma-Token header, compared in constant time, with the app's files served without it. What this module adds:

- It switches at runtime. The loopback listener (127.0.0.1, no token, Host and Origin guards) never changes; network
  access is a second listener on this computer's network address and the same port, opened and closed on request.
- The QR code carries a one-time pairing code, not the token. The phone trades it once (POST /phone/pair) for a token
  of its own, and the code is replaced at once, so a photo of the screen is useless after the phone has paired.
- Revoke signs every phone out (their tokens die and their open connections are closed) and makes a new code. Off
  does the same and stops listening. Nothing is kept across a bridge restart: every run starts with access off.
- Management is this computer's alone: the loopback routes refuse any Origin but 127.0.0.1 / localhost pages
  (localguard.py), so a web page in a sandboxed iframe (Origin "null") can neither turn access on nor read the code.
- The pairing link opened on this computer itself pairs nothing (the request comes from the listener's own address).
- The network address is checked again on every GET /phone: after a change (another Wi-Fi, a VPN) the listener moves
  to the new address with a new code, and phones signed in on the old one are signed out.

Routes (JSON):
  GET  /phone          loopback only: {on, address, url, qr, paired, error}
  POST /phone/on       loopback only: listen on the network address, new pairing code
  POST /phone/off      loopback only: stop listening, every phone signed out
  POST /phone/revoke   loopback only: every phone signed out, new pairing code (access stays on)
  POST /phone/pair     network listener only, no token, from another device: {"code": C} -> {"token": T}; C works once

Everything here is MOCK: tested with the simulated camera and a browser on this computer, never with a real phone.
"""
from __future__ import annotations

import hmac
import secrets
import socket
import socketserver
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import localguard
import qrmini

PAIR_PATH = "/phone/pair"


class _LanServer(ThreadingHTTPServer):
    """The network listener. Tracks its open connections so Off and Revoke can close them (an open live view or a
    keep-alive connection would otherwise outlive the token it was opened with)."""

    daemon_threads = True

    def __init__(self, address, handler, access: "PhoneAccess"):
        self.access = access
        super().__init__(address, handler)

    def server_bind(self):  # no DNS lookup of the address (HTTPServer.server_bind's getfqdn): see komabridge.Server
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = str(self.server_address[0]), self.server_address[1]

    def get_request(self):
        sock, addr = super().get_request()
        self.access._track(sock)
        return sock, addr

    def shutdown_request(self, request):
        self.access._untrack(request)
        super().shutdown_request(request)


class PhoneAccess:
    """One bridge's network access: the listener, the pairing code and the phones' tokens."""

    def __init__(self, link, port: int, kb, host=None, bind_port=None, quiet=True, pair_from_self=False):
        self.link, self.port, self.kb = link, port, kb
        self.host = host                  # None: this computer's network address (kb.lan_address()); tests set it
        self.bind_port = bind_port        # None: the bridge's own port; tests use 0 (any free port)
        self.quiet = quiet
        # A request from the listener's own address comes from this computer. Tests and the screenshots stand a phone
        # in on 127.0.0.1, so they let it pair; never set in the bridge itself.
        self.pair_from_self = pair_from_self
        self.lock = threading.Lock()
        self.server = None
        self.bound = None                 # the host the listener is bound to while on
        self.address = None               # "192.168.1.23:8765" while on
        self.code = None
        self.tokens: list[str] = []
        self.paired_total = 0
        self.error = None                 # {"code", "text"} of the last failed On
        self._conns: set = set()

    # -- state
    @property
    def on(self) -> bool:
        return self.server is not None

    def url(self) -> str | None:
        return f"http://{self.address}/?pair={self.code}" if self.on and self.code else None

    def snapshot(self) -> dict:
        with self.lock:
            url = self.url()
            return {"on": self.on, "address": self.address, "url": url,
                    "qr": ["".join("1" if v else "0" for v in row) for row in qrmini.matrix(url)] if url else None,
                    "paired": len(self.tokens), "error": self.error}

    # -- switching
    def turn_on(self) -> dict:
        with self.lock:
            if self.server is None:
                self._open_locked()
        return self.snapshot()

    def _lan_host(self) -> str | None:
        """This computer's network address now, or None when lan_address() finds no network."""
        host = self.kb.lan_address()
        return None if not host or host.startswith("127.") or host == "0.0.0.0" else host

    def _open_locked(self):
        self.error = None
        host = self.host or self._lan_host()  # self.host: set by tests and the screenshots only
        if not host:
            self.error = {"code": "no_network", "text": "This computer is not on a network."}
            return
        try:
            server = _LanServer((host, self.port if self.bind_port is None else self.bind_port),
                                lan_handler(self.kb, self.link, self.port, self), self)
        except OSError as error:
            self.error = {"code": "bind", "text": f"{type(error).__name__}: {error}"}
            return
        server.quiet = self.quiet
        self.server = server
        self.bound = host
        self.address = f"{host}:{server.server_address[1]}"
        self.code = secrets.token_urlsafe(16)
        self.tokens = []
        threading.Thread(target=server.serve_forever, daemon=True, name="phone-access").start()

    def follow_address(self):
        """The listener follows this computer's network address. After a change (another Wi-Fi, a VPN) it moves to the
        new address with a new code; phones signed in on the old address are signed out (they cannot reach it now).
        No network at the moment changes nothing: the address may come back."""
        if self.host or not self.on:
            return
        now = self._lan_host()
        if now and now != self.bound:
            self.turn_off()
            self.turn_on()

    def turn_off(self) -> dict:
        with self.lock:
            server, self.server = self.server, None
            self.tokens, self.code, self.address, self.bound = [], None, None, None
            conns = list(self._conns)
        if server is not None:
            self._close(conns)
            server.shutdown()
            server.server_close()
        return self.snapshot()

    def revoke(self) -> dict:
        with self.lock:
            self.tokens = []
            if self.server is not None:
                self.code = secrets.token_urlsafe(16)
            conns = list(self._conns)
        self._close(conns)
        return self.snapshot()

    # -- pairing and the token check (the network listener's handler calls these)
    def pair(self, code: str) -> str | None:
        with self.lock:
            if self.server is None or not self.code or not isinstance(code, str):
                return None
            if not hmac.compare_digest(code.encode(), self.code.encode()):
                return None
            token = secrets.token_urlsafe(16)
            self.tokens.append(token)
            self.paired_total += 1
            self.code = secrets.token_urlsafe(16)  # one time: the next phone needs the new code
            return token

    def token_ok(self, given: str) -> bool:
        with self.lock:
            tokens = list(self.tokens)
        ok = False
        for t in tokens:  # compare against every token, in constant time each
            ok |= hmac.compare_digest(given.encode(), t.encode())
        return ok

    # -- connections
    def _track(self, sock):
        with self.lock:
            self._conns.add(sock)

    def _untrack(self, sock):
        with self.lock:
            self._conns.discard(sock)

    @staticmethod
    def _close(conns):
        for sock in conns:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def lan_handler(kb, link, port: int, access: PhoneAccess):
    """The bridge's own handler in token mode, with the token check swapped for the phones' tokens and the one route a
    phone may use before it has a token (POST /phone/pair)."""
    base = kb.make_handler(link, port, secrets.token_urlsafe(32))  # a token nobody holds: token mode, never matched

    class LanHandler(base):
        koma_lan = True

        def _token_ok(self) -> bool:
            given = self.headers.get(kb.TOKEN_HEADER) or parse_qs(urlsplit(self.path).query).get("token", [""])[0]
            return bool(given) and access.token_ok(given)

        def _guard(self) -> bool:
            if self.command == "POST" and urlsplit(self.path).path.rstrip("/") == PAIR_PATH:
                return True
            return super()._guard()

    return LanHandler


def for_link(link, port: int, kb) -> PhoneAccess:
    """The PhoneAccess of this bridge (one per Link), made on first use. configure() sets its options first."""
    pa = getattr(link, "phone_access", None)
    if pa is None:
        opts = getattr(link, "phone_access_config", {}) or {}
        pa = PhoneAccess(link, port, kb, **opts)
        link.phone_access = pa
    return pa


def configure(link, **opts):
    """Options for the PhoneAccess made by for_link (host, bind_port, quiet). Tests and the screenshots use it."""
    link.phone_access_config = dict(opts)


def shutdown(link):
    pa = getattr(link, "phone_access", None)
    if pa is not None and pa.on:
        pa.turn_off()


def dispatch(handler, link, port: int, method: str, route: str, kb):
    """Routes under /phone. The network listener answers only /phone/pair; the loopback listener everything else."""
    try:
        return _dispatch(handler, link, port, method, route, kb)
    except localguard.BodyError as error:
        return localguard.answer(handler, error.status, {"error": error.text, "code": "body"})


def _dispatch(handler, link, port: int, method: str, route: str, kb):
    pa = for_link(link, port, kb)
    lan = getattr(handler, "koma_lan", False)
    if not lan and not localguard.local_origin_ok(handler):  # management and the code: this computer's pages only
        return handler._json(403, {"error": "Phone access is managed by Koma on this computer.", "code": "origin"})
    if route == PAIR_PATH:
        if method != "POST":
            return handler._json(405, {"error": "POST {\"code\": ...}"})
        body = localguard.small_body(handler)
        local = f"http://127.0.0.1:{port}/"
        if not lan or (handler.client_address[0] == pa.bound and not pa.pair_from_self):  # opened on this computer
            return handler._json(404, {"error": "This link pairs a phone. Open it on the phone.", "code": "not_lan",
                                       "local": local})
        token = pa.pair(body.get("code"))
        if token is None:
            return handler._json(403, {"error": "This code has been used or is not current.", "code": "pair_code"})
        return handler._json(200, {"ok": True, "token": token})
    if lan:  # a paired phone cannot turn access on or off, revoke, or read the pairing code
        return handler._json(403, {"error": "Phone access is managed on the computer.", "code": "computer_only"})
    if method == "GET" and route == "/phone":
        pa.follow_address()
        return handler._json(200, pa.snapshot())
    if method == "POST" and route in ("/phone/on", "/phone/off", "/phone/revoke"):
        localguard.small_body(handler)
        snap = {"/phone/on": pa.turn_on, "/phone/off": pa.turn_off, "/phone/revoke": pa.revoke}[route]()
        return handler._json(200, snap)
    return handler._json(404, {"error": f"No such endpoint: {method} {route}"})


def terminal_note(url: str) -> str:
    """What `komabridge --lan` prints: the one-time address and its QR code."""
    try:
        qr = qrmini.terminal(url)
    except Exception as error:  # noqa: BLE001  (the address is enough)
        qr = f"(no QR code: {error})"
    return f"  {url}\n\n{qr}\n"
