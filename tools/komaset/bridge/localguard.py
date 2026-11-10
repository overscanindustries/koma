"""Request checks shared by webcamhost.py and phoneaccess.py (the Koma app's Webcam and Use on your phone routes).

local_origin_ok: komabridge's loopback listener lets the Origin "null" and file:// through, for the old page opened
from disk. The routes that switch the webcam or network access, read the pairing code or carry the webcam's picture
accept only this computer's own pages: no Origin header (a same-origin GET, curl, komacam), or http(s)://127.0.0.1 or
localhost with any port. A sandboxed iframe on any website sends Origin "null", so "null" is refused here.

small_body: the JSON object in a request body of at most MAX_BODY bytes. A longer body, a bad Content-Length or a
chunked body is never read; the caller answers with its status and the connection closes, so the unread bytes never
become the next request on a kept-alive connection.
"""
from __future__ import annotations

import json
import re

LOCAL_ORIGIN = re.compile(r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$")
MAX_BODY = 4096


def local_origin_ok(handler) -> bool:
    origin = handler.headers.get("Origin")
    return origin is None or bool(LOCAL_ORIGIN.match(origin))


class BodyError(Exception):
    def __init__(self, status: int, text: str):
        super().__init__(text)
        self.status, self.text = status, text


def small_body(handler) -> dict:
    """The body as a JSON object ({} when it is empty or not an object). Raises BodyError for a body it will not read,
    with the connection already marked to close."""
    if handler.headers.get("Transfer-Encoding"):
        handler.close_connection = True
        raise BodyError(411, "Send a Content-Length.")
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        length = -1
    if length < 0 or length > MAX_BODY:
        handler.close_connection = True
        raise BodyError(413, "Body too large.") if length > MAX_BODY else BodyError(400, "Bad Content-Length.")
    raw = handler.rfile.read(length) if length else b""
    try:
        obj = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


def answer(handler, status: int, obj: dict):
    """A JSON answer that also closes the connection when the handler is marked to (after a body it did not read)."""
    extra = [("Connection", "close")] if getattr(handler, "close_connection", False) else []
    handler._send(status, json.dumps(obj).encode(), "application/json", extra)
