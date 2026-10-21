#!/usr/bin/env python3
"""Zero-dependency CDP browser check for Boil.

- Spawns headless Chromium
- Collects JS exceptions / console errors / failed requests
- Waits for each SPA view to actually render
- Takes screenshots into screenshots/
- Optionally exercises reveal / modal interactions
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "screenshots"
APP = os.environ.get("BOIL_APP", "http://127.0.0.1:7861")
PORT = int(os.environ.get("BOIL_CDP_PORT", "9342"))
CHROME = os.environ.get("BOIL_CHROME", "chromium")


class WS:
    def __init__(self, url: str):
        u = urlsplit(url)
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        path = u.path + (("?" + u.query) if u.query else "")
        req = (f"GET {path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n"
               f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += self.sock.recv(4096)
        if b"101" not in resp.split(b"\r\n")[0]:
            raise RuntimeError(f"WS handshake failed: {resp[:200]!r}")
        self.buf = b""

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("WS closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, obj: dict) -> None:
        payload = json.dumps(obj).encode()
        mask = os.urandom(4)
        ln = len(payload)
        header = bytes([0x81])
        if ln < 126:
            header += bytes([0x80 | ln])
        elif ln < (1 << 16):
            header += bytes([0x80 | 126]) + struct.pack(">H", ln)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", ln)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def _frame(self):
        b0 = self._read(1)[0]
        b1 = self._read(1)[0]
        fin, op = b0 & 0x80, b0 & 0x0F
        ln = b1 & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", self._read(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", self._read(8))[0]
        return fin, op, (self._read(ln) if ln else b"")

    def recv(self, timeout: float = 30.0) -> dict:
        self.sock.settimeout(timeout)
        parts, opcode = b"", None
        deadline = time.time() + timeout
        while True:
            try:
                fin, op, payload = self._frame()
            except (TimeoutError, OSError):
                if opcode is None and time.time() < deadline:
                    continue
                raise
            if op == 0x8:
                raise RuntimeError("WS peer closed")
            if op == 0x9:
                self.sock.sendall(bytes([0x8A, len(payload)]) + payload)
                continue
            if op == 0xA:
                continue
            if op in (0x1, 0x2):
                parts, opcode = payload, op
            elif op == 0x0:
                parts += payload
            if fin:
                return json.loads(parts.decode("utf-8"))
            if time.time() > deadline:
                raise TimeoutError("WS message timeout")

    def close(self):
        try:
            self.sock.sendall(bytes([0x88, 0x80]) + os.urandom(4))
        except OSError:
            pass
        self.sock.close()


class CDP:
    def __init__(self, ws: WS):
        self.ws = ws
        self.mid = 0
        self.pending = {}
        self.handlers = []

    def cmd(self, method: str, params: dict | None = None, session: str | None = None):
        self.mid += 1
        payload = {"id": self.mid, "method": method}
        if params:
            payload["params"] = params
        if session:
            payload["sessionId"] = session
        self.ws.send(payload)
        while True:
            msg = self.ws.recv()
            if msg.get("id") == self.mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            for h in self.handlers:
                try:
                    h(msg)
                except Exception:
                    pass


def api(path: str, payload: dict | None = None):
    data = json.dumps(payload or {}).encode() if payload is not None else None
    req = urllib.request.Request(APP + path, data=data,
                                 headers={"Content-Type": "application/json"} if data else {},
                                 method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def main():
    OUT.mkdir(exist_ok=True)
    # Seed a socratic chat + a completed report via API
    pack = api("/api/packs")["data"]
    goal = api("/api/dashboard")["data"]
    unit_id = goal["next_units"][0]["id"]
    session = api("/api/socratic/start", {"unit_id": unit_id})["data"]["session"]
    chat_sid = session["id"]
    done_sid = None
    try:
        # one exchange through the streaming endpoint, then finish
        req = urllib.request.Request(
            APP + "/api/socratic/stream",
            data=json.dumps({"session_id": chat_sid,
                             "message": "学习是模仿加练习，最终形成可以提取的记忆；输出和复习能让回路更稳。"}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
        done = api("/api/socratic/finish", {"session_id": chat_sid})["data"]
        done_sid = done["session"]["id"]
        chat_sid = api("/api/socratic/start", {"unit_id": unit_id})["data"]["session"]["id"]
    except Exception as exc:
        print("seed warning:", exc, file=sys.stderr)

    profile = tempfile.mkdtemp(prefix="boil-chrome-")
    chrome = subprocess.Popen(
        [CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
         "--hide-scrollbars", "--remote-debugging-port=" + str(PORT),
         "--user-data-dir=" + profile, "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    errors = []
    try:
        version = None
        for _ in range(80):
            try:
                version = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=2).read())
                break
            except Exception:
                time.sleep(0.25)
        if not version:
            raise RuntimeError("Chromium CDP port not ready")
        browser_ws = WS(version["webSocketDebuggerUrl"])
        cdp = CDP(browser_ws)
        target = cdp.cmd("Target.createTarget", {"url": "about:blank"})["targetId"]
        sid = cdp.cmd("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        call = lambda m, p=None: cdp.cmd(m, p, sid)
        call("Page.enable")
        call("Runtime.enable")
        call("Log.enable")
        call("Network.enable")
        call("Emulation.setDeviceMetricsOverride",
             {"width": 1440, "height": 1100, "deviceScaleFactor": 1, "mobile": False})
        call("Emulation.setEmulatedMedia",
             {"features": [{"name": "prefers-reduced-motion", "value": "reduce"},
                           {"name": "prefers-color-scheme", "value": "dark"}]})
        call("Page.addScriptToEvaluateOnNewDocument", {
            "source": "try{localStorage.setItem('boil_theme','dark')}catch(e){}"
        })

        def on_event(msg):
            method = msg.get("method")
            if method == "Runtime.exceptionThrown":
                d = msg.get("params", {}).get("exceptionDetails", {})
                errors.append("JS exception: " + str(d.get("text")) + " " + str(d.get("exception", {}).get("description", ""))[:400])
            elif method == "Runtime.consoleAPICalled":
                if msg.get("params", {}).get("type") == "error":
                    args = msg["params"].get("args", [])
                    errors.append("console.error: " + " ".join(str(a.get("value", a.get("description", ""))) for a in args)[:400])
            elif method == "Log.entryAdded":
                e = msg.get("params", {}).get("entry", {})
                if e.get("level") == "error":
                    errors.append("log: " + str(e.get("text"))[:300])
            elif method == "Network.loadingFailed":
                p = msg.get("params", {})
                if not p.get("canceled"):
                    errors.append("request failed: " + str(p.get("errorText")) + " " + str(p.get("type")))
        cdp.handlers.append(on_event)

        def evaluate(expr):
            return call("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                             "awaitPromise": True}).get("result", {}).get("value")

        def wait_for(selector: str, text: str | None = None, timeout: float = 12.0):
            start = time.time()
            while time.time() - start < timeout:
                ok = evaluate(f"!!document.querySelector({json.dumps(selector)})")
                if ok and text:
                    body = evaluate("document.body.innerText") or ""
                    ok = text in body
                if ok:
                    time.sleep(0.45)
                    return True
                time.sleep(0.2)
            return False

        def goto(hash_path: str):
            evaluate(f"location.hash={json.dumps(hash_path)}")
            time.sleep(0.5)

        def shot(name: str, full: bool = False):
            params = {"format": "png", "captureBeyondViewport": full}
            data = call("Page.captureScreenshot", params)["data"]
            (OUT / name).write_bytes(base64.b64decode(data))
            print("  shot", name)

        call("Page.navigate", {"url": APP + "/#/dashboard"})
        if not wait_for(".hero", "学习闭环", 20):
            body = evaluate("document.body.innerText") or ""
            errors.append("dashboard did not render: " + body[:300])
        wait_for("#advisor-body[data-advice-ready='1']", None, 12)
        shot("dashboard.png")
        # full page dashboard
        shot("dashboard-full.png", full=True)

        goto("#/map")
        wait_for(".map-svg", "知识地图")
        shot("map.png")

        goto(f"#/unit/{unit_id}")
        wait_for(".essence-card", "一句话本质")
        wait_for(".unit-heat-card", "本单元火候")
        shot("unit.png")
        shot("unit-full.png", full=True)
        evaluate("document.querySelector('[data-audio]')?.click()")
        wait_for(".audio-script", "第 0 档", 6)
        shot("audio.png")
        evaluate("document.querySelector('.modal-head [data-close]')?.click()")
        time.sleep(0.3)

        goto(f"#/socratic/{chat_sid}")
        wait_for(".chat-panel", "苏格拉底")
        shot("socratic.png")

        if done_sid:
            goto(f"#/socratic/{done_sid}")
            wait_for(".report", "对话报告")
            shot("report.png", full=True)

        goto(f"#/feynman/{unit_id}")
        wait_for(".feynman-layout", "费曼挑战")
        shot("feynman.png")
        evaluate("(function(){const t=document.querySelector('#feynman-text'); t.value='学习是模仿加练习，最后形成可以提取的记忆。要形成回路，需要提高刺激强度，比如合上书自己讲出来、做出来；也需要提高刺激频度，也就是及时复习、间隔回测。但如果没有理解，只是重复，效果会差。睡眠不足时，记忆也不稳定。'; t.dispatchEvent(new Event('input',{bubbles:true}));})()")
        evaluate("document.querySelector('#submit-btn')?.click()")
        wait_for("#feynman-result", "讲解分", 10)
        time.sleep(4.2)
        shot("feynman-result.png")
        goto(f"#/feynman/{unit_id}?mode=examiner")
        wait_for(".prompt-card", "极致对标", 10)
        shot("benchmark.png")

        goto("#/review")
        wait_for(".flashcard", "先回忆")
        time.sleep(4.2)
        shot("review.png")
        evaluate("document.querySelector('#reveal-btn')?.click()")
        time.sleep(0.5)
        shot("review-revealed.png")

        goto("#/library")
        wait_for(".library-grid", "内容库")
        shot("library.png")
        evaluate("document.querySelector('[data-detail]')?.click()")
        wait_for(".modal", "Learning Pack", 6)
        shot("library-detail.png")
        evaluate("document.querySelector('.modal-head [data-close]')?.click()")

        goto("#/insights")
        wait_for(".insight-grid", "学习证据")
        wait_for(".heat-dist", "火候分布", 8)
        shot("insights.png")
        if not evaluate("!!document.querySelector('#insight-ai-btn')"):
            errors.append("insights AI 解读入口不存在")
        shot("insights-full.png", full=True)

        # 划词菜单 + 灵感库
        goto(f"#/unit/{unit_id}")
        wait_for(".essence-text", "一句话本质")
        evaluate(
            "(()=>{const el=document.querySelector('.essence-text');"
            "const r=document.createRange();r.selectNodeContents(el);"
            "const s=getSelection();s.removeAllRanges();s.addRange(r);})()"
        )
        time.sleep(0.6)
        menu = evaluate(
            "(()=>{const m=document.querySelector('.selection-menu');"
            "const c=getComputedStyle(m);"
            "return {display:c.display, btns:[...m.querySelectorAll('.sel-btn')].length}})()"
        )
        if not menu or menu.get("display") != "flex" or menu.get("btns") != 2:
            errors.append(f"划词菜单没有出现: {menu}")
        shot("selection-menu.png")
        evaluate("document.querySelector('[data-sel=\"note\"]')?.click()")
        time.sleep(0.5)
        pop = evaluate(
            "(()=>{const p=document.querySelector('.note-popover');"
            "return {display:getComputedStyle(p).display,"
            "quote:(p.querySelector('.note-quote')?.textContent||'').length>0,"
            "area:!!p.querySelector('#sel-note')}})()"
        )
        if not pop or pop.get("display") != "grid" or not pop.get("quote") or not pop.get("area"):
            errors.append(f"灵感保存气泡不完整: {pop}")
        evaluate(
            "(()=>{const a=document.querySelector('#sel-note');"
            "a.value='浏览器回归自动生成的灵感，用完即删。';"
            "document.querySelector('[data-note-save]').click();})()"
        )
        time.sleep(1.4)
        shot("note-popover.png")
        created = evaluate(
            "(async()=>{const r=await fetch('/api/notes');const j=await r.json();"
            "const n=j.data.notes.find(x=>x.content&&x.content.includes('浏览器回归'));"
            "return {total:j.data.total, id:n?n.id:'', src:n?n.source:'', unit:n?!!n.unit_id:false}})()"
        )
        if not created or not created.get("id") or created.get("src") != "unit" or not created.get("unit"):
            errors.append(f"划词保存的笔记不完整: {created}")
        goto("#/notes")
        wait_for(".note-card", "灵感库")
        notes_page = evaluate(
            "(()=>({cards:document.querySelectorAll('.note-card').length,"
            "quote:!!document.querySelector('.note-card .note-quote'),"
            "nav:!!document.querySelector('[data-nav=\"notes\"]')}))()"
        )
        if not notes_page or notes_page.get("cards", 0) < 1 or not notes_page.get("quote") \
                or not notes_page.get("nav"):
            errors.append(f"灵感库渲染异常: {notes_page}")
        shot("notes.png")
        if created.get("id"):
            id_literal = json.dumps(created["id"])
            removed = evaluate(
                "(async()=>{const r=await fetch('/api/notes/delete',"
                "{method:'POST',headers:{'Content-Type':'application/json'},"
                "body:JSON.stringify({id:" + id_literal + "})});"
                "const j=await r.json();return {ok:r.ok,deleted:j.data?j.data.deleted:''}})()"
            )
            if not removed or not removed.get("ok"):
                errors.append(f"回归笔记清理失败: {removed}")

        goto("#/settings")
        wait_for(".settings-grid", "模型连接")
        shot("settings.png")
        anki = evaluate(
            "(async()=>{const r=await fetch('/api/export/anki');const t=await r.text();"
            "return {ok:r.ok,type:r.headers.get('content-type')||'',head:t.slice(0,80)}})()"
        )
        if not anki or not anki.get("ok") or "#separator:tab" not in anki.get("head", ""):
            errors.append(f"anki export failed in browser: {anki}")
        evaluate("document.querySelector('[data-jump=\"settings-data\"]')?.click()")
        time.sleep(0.8)
        jump = evaluate(
            "(()=>{const r=document.querySelector('#settings-data').getBoundingClientRect();"
            "const a=document.querySelector('.jump-chip.active');"
            "return {visible:r.top<innerHeight&&r.bottom>120, active:a?.dataset.jump||''}})()"
        )
        if not jump or not jump.get("visible") or jump.get("active") != "settings-data":
            errors.append(f"settings jump nav failed: {jump}")
        shot("settings-data.png")
        evaluate("window.scrollTo(0,0)")
        time.sleep(0.3)
        shot("settings-full.png", full=True)

        goto("#/onboarding")
        wait_for(".onboarding", "你想先烧开哪一壶水")
        shot("onboarding.png")

        # mobile
        call("Emulation.setDeviceMetricsOverride",
             {"width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True})
        goto("#/dashboard")
        wait_for(".hero", "学习闭环")
        shot("dashboard-mobile.png")
        goto("#/settings")
        wait_for(".settings-jump", "模型连接")
        shot("settings-mobile.png")

        print("\nerrors:")
        if errors:
            for e in errors:
                print(" -", e)
            sys.exit(2)
        print(" none")
    finally:
        try:
            chrome.terminate()
            chrome.wait(timeout=5)
        except Exception:
            chrome.kill()


if __name__ == "__main__":
    main()
