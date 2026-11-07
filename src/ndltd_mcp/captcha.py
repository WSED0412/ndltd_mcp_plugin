"""Captcha hand-off to the human.

The program never reads, OCRs or forwards the captcha image: the bytes are kept in memory only to be served
to the user's own browser on 127.0.0.1, and the typed answer goes straight back to the site. Neither the image
nor the answer is ever put into a tool result or a log line.

NOTE: the live captcha form's exact field names were only partly observed (the answer is submitted with GET,
parameters randomimg / validpath / validinput / check). parse_captcha_page therefore reads the form generically
and must be re-checked against a real captcha page.
"""
from __future__ import annotations

import asyncio
import secrets
import threading
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urljoin

from selectolax.lexbor import LexborHTMLParser

from .errors import NdltdParseError


@dataclass
class CaptchaChallenge:
    action: str  # absolute URL the answer is submitted to
    method: str  # "GET" or "POST"
    answer_field: str  # name of the text input the human fills
    fields: dict[str, str] = field(default_factory=dict)  # hidden inputs + the submit button, sent unchanged
    image_url: str = ""


def parse_captcha_page(html: str, page_url: str) -> CaptchaChallenge:
    tree = LexborHTMLParser(html)
    for form in tree.css("form"):
        img = form.css_first("img[src]")
        text_inputs = [i for i in form.css("input") if (i.attributes.get("type") or "text").lower() == "text"
                       and i.attributes.get("name")]
        if not text_inputs:
            continue
        # prefer the input literally named validinput, else the only text input
        answer = next((i for i in text_inputs if i.attributes["name"] == "validinput"), text_inputs[0])
        if img is None:
            img = tree.css_first("img[src*='randomimg'], img[src*='doschk'], img[src*='valid']")
        if img is None:
            continue
        fields: dict[str, str] = {}
        for i in form.css("input"):
            name, typ = i.attributes.get("name"), (i.attributes.get("type") or "text").lower()
            if not name or i is answer:
                continue
            if typ in ("hidden", "submit"):
                fields[name] = i.attributes.get("value") or ""
        return CaptchaChallenge(
            action=urljoin(page_url, form.attributes.get("action") or page_url),
            method=(form.attributes.get("method") or "get").upper(),
            answer_field=answer.attributes["name"],
            fields=fields,
            image_url=urljoin(page_url, img.attributes["src"]),
        )
    raise NdltdParseError("驗證碼頁找不到「圖片＋文字輸入框」的表單；頁面結構可能改版。")


_PAGE = """<!doctype html><meta charset="utf-8"><title>NDLTD 驗證碼</title>
<style>body{{font:16px sans-serif;max-width:420px;margin:48px auto}}img{{border:1px solid #888;margin:12px 0}}
input[type=text]{{font-size:20px;width:10em}}</style>
<h2>臺灣博碩士論文系統：驗證碼</h2>
<p>請輸入圖片中的字元，送出後回到 Claude 繼續。</p>
<img src="/{token}/image.png" alt="驗證碼"><br>
<form method="post" action="/{token}/submit"><input type="text" name="answer" autofocus autocomplete="off">
<button type="submit">送出</button></form>{msg}"""


class _Handler(BaseHTTPRequestHandler):
    server_version = "ndltd-captcha"

    def log_message(self, *a):  # keep stderr clean; never log URLs or bodies
        pass

    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        s: HumanCaptchaServer = self.server.owner  # type: ignore[attr-defined]
        if self.path == f"/{s.token}/":
            self._send(200, _PAGE.format(token=s.token, msg="").encode())
        elif self.path == f"/{s.token}/image.png":
            self._send(200, s.image, s.image_type)
        else:
            self._send(404, b"not found")

    def do_POST(self):
        s: HumanCaptchaServer = self.server.owner  # type: ignore[attr-defined]
        if self.path != f"/{s.token}/submit":
            return self._send(404, b"not found")
        n = min(int(self.headers.get("Content-Length") or 0), 1024)
        vals = parse_qs(self.rfile.read(n).decode("utf-8", "replace"))
        answer = (vals.get("answer") or [""])[0].strip()
        if not answer:
            return self._send(200, _PAGE.format(token=s.token, msg="<p>請輸入驗證碼。</p>").encode())
        s.deliver(answer)
        self._send(200, "<meta charset='utf-8'><p>已收到，可以關閉這個視窗。</p>".encode())


class HumanCaptchaServer:
    """One-shot localhost page. `ask()` returns what the human typed, or None on timeout."""

    def __init__(self, image: bytes, image_type: str = "image/png", open_browser: bool = True):
        self.image, self.image_type = image, image_type
        self.token = secrets.token_urlsafe(16)
        self.open_browser = open_browser
        self._answer: str | None = None
        self._done = threading.Event()
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._httpd.owner = self  # type: ignore[attr-defined]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}/{self.token}/"

    def deliver(self, answer: str) -> None:
        self._answer = answer
        self._done.set()

    async def ask(self, timeout: float = 180.0) -> str | None:
        t = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        t.start()
        try:
            if self.open_browser:
                webbrowser.open(self.url)
            return await asyncio.to_thread(lambda: self._answer if self._done.wait(timeout) else None)
        finally:
            self._httpd.shutdown()
            self._httpd.server_close()

