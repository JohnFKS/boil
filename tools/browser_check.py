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
from datetime import datetime, timezone
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlsplit

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

        def real_click(selector: str) -> bool:
            """派发真实鼠标事件。element.click() 不触发 mousedown，浮层的
            「点外部就关闭」这类逻辑只有真鼠标才测得到。
            只点已经在视口里的元素：划词浮层跟着滚动会被收掉。"""
            spot = evaluate(
                "(()=>{const e=document.querySelector(%s);if(!e)return null;"
                "const r=e.getBoundingClientRect();"
                "if(r.width<1||r.height<1)return null;"
                "if(r.top<0||r.bottom>window.innerHeight||r.left<0||r.right>window.innerWidth)return null;"
                "return JSON.stringify([r.x+r.width/2,r.y+r.height/2])})()"
                % json.dumps(selector))
            if not spot:
                return False
            x, y = json.loads(spot)
            for kind in ("mousePressed", "mouseReleased"):
                call("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y,
                                                  "button": "left", "clickCount": 1})
            return True

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
        # F15/F16：地图不崩、重复卡片按数据有无正确显隐
        map_state = evaluate(
            "(()=>({nodes:document.querySelectorAll('.map-node-wrap').length,"
            "dup:!!document.querySelector('.dup-card')}))()")
        if not map_state or map_state.get("nodes", 0) < 1:
            errors.append(f"地图节点没有渲染: {map_state}")
        shot("map.png")

        # E1/E3：真的种一个重复单元，用真鼠标走完「候选 → 依据 → 合并」这条链
        seed_id = "unit-browser-dup"
        dup_db = Path(os.environ.get("BOIL_DATA_DIR", str(ROOT / "data"))) / "boil.db"
        try:
            live = api("/api/dashboard")["data"]
            live_goal = (live.get("goal") or {}).get("id")
            if not live_goal:
                raise RuntimeError("没有活动目标，种不了重复单元")
            node = api(f"/api/units/{quote(unit_id)}")["data"]["unit"]
            import sqlite3 as _sql
            with _sql.connect(str(dup_db), timeout=15) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO units (id, goal_id, pack_id, pack_unit_id,"
                    " title, essence, order_index, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (seed_id, live_goal, "browser.dup", seed_id,
                     node["title"], node["essence"], 900,
                     datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     datetime.now(timezone.utc).isoformat(timespec="seconds")))
                conn.commit()
            # goto() 只改 hash，同一个页面不会重渲染；必须硬刷新才会重新拉候选
            call("Page.reload", {"ignoreCache": True})
            wait_for(".dup-card", "疑似重复", 12)
            card = evaluate(
                "(()=>{const c=document.querySelector('.dup-card');if(!c)return {present:false};"
                "const row=[...c.querySelectorAll('.dup-row')].find(r=>r.querySelector('[data-merge]'));"
                "if(!row) return {present:true, head:c.querySelector('.side-label').textContent.trim(), missing:true};"
                "const t=(sel)=>{const e=row.querySelector(sel);return e?e.textContent.trim():''};"
                "return {present:true, head:c.querySelector('.side-label').textContent.trim(),"
                " signal:t('.dup-titles .faint'), why:t('.dup-why'),"
                " btn:t('.link-btn'), disabled:!!row.querySelector('.link-btn[disabled]'),"
                " merge:row.querySelector('[data-merge]').getAttribute('data-merge'),"
                " keep:row.querySelector('[data-keep]').getAttribute('data-keep')}})()")
            if not card or not card.get("present"):
                errors.append(f"种了重复单元但候选卡片没出现: {card}")
            elif not card.get("btn") or card.get("disabled"):
                errors.append(f"候选行的合并按钮不可用: {card}")
            elif "字面" not in (card.get("why") or "") + (card.get("head") or ""):
                errors.append(f"候选行没写明判据来源: {card}")
            elif card.get("merge") != seed_id:
                errors.append(f"候选把真单元当成了被吸收方，跳过点击以免误删: {card}")
            else:
                print(f"  dup card: {card['head']} | {card['why'][:40]}")
                if not real_click(".dup-card .dup-row .link-btn"):
                    errors.append("合并按钮真鼠标点不到（浮层/定位问题）")
                else:
                    time.sleep(2.2)
                    done = evaluate("(document.querySelector('.toast')||{}).textContent||''")
                    if "并入" not in done or "字面" not in done:
                        errors.append(f"合并后 toast 没说清依据: {done}")
                    else:
                        print(f"  merged: {done[:60]}")
                        # G1：撤销合并要走真鼠标 + 确认框，撤销后单元必须回到地图
                        evaluate(f"location.hash = '#/unit/' + encodeURIComponent({seed_id!r})")
                        time.sleep(1.6)
                        if not wait_for("[data-unmerge]", timeout=8):
                            errors.append("被合并单元的页面上没有撤销入口")
                        elif not real_click("[data-unmerge]"):
                            errors.append("撤销按钮真鼠标点不到")
                        else:
                            time.sleep(0.6)
                            if not real_click(".modal-foot .btn-primary"):
                                errors.append("撤销确认框点不到")
                            else:
                                time.sleep(2.0)
                                note = evaluate("(document.querySelector('.toast')||{}).textContent||''")
                                alive = evaluate(
                                    "(async()=>{const r=await fetch('/api/units/'+encodeURIComponent(%r));"
                                    "const j=await r.json();return {merged:!!j.data.merged_into,"
                                    " can:j.data.can_unmerge}})()" % seed_id)
                                if "撤销" not in note:
                                    errors.append(f"撤销后 toast 不对: {note}")
                                elif not alive or alive.get("merged"):
                                    errors.append(f"撤销后单元没有回到存活状态: {alive}")
                                else:
                                    print(f"  unmerged: {note[:60]}")
        except Exception as exc:
            errors.append(f"重复候选回归没跑成: {type(exc).__name__}: {exc}")
        finally:
            try:
                import sqlite3 as _sql
                with _sql.connect(str(dup_db), timeout=15) as conn:
                    conn.execute("DELETE FROM units WHERE id=?", (seed_id,))
                    conn.execute("DELETE FROM events WHERE payload_json LIKE ?", (f"%{seed_id}%",))
                    conn.commit()
            except Exception as exc:
                print("seed cleanup warning:", exc, file=sys.stderr)

        goto(f"#/unit/{unit_id}")
        wait_for(".essence-card", "一句话本质")
        wait_for(".unit-heat-card", "本单元火候")
        # E3/E5：AI 口诀入口只在模型可用时出现，未配置时应当完全不存在（而不是置灰）
        ai_entry = evaluate(
            "(async()=>{const h=await fetch('/api/health');const j=await h.json();"
            "return {mode:j.data.llm.mode, btn:!!document.querySelector('[data-mnemonic-ai]')}})()"
        )
        if ai_entry:
            expect_btn = ai_entry.get("mode") == "live"
            if bool(ai_entry.get("btn")) != expect_btn:
                errors.append(f"AI 速记入口的显隐与模型状态不符: {ai_entry}")
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
        # E1：翻面前要先写回忆
        if not evaluate("!!document.querySelector('#recall-input')"):
            errors.append("复习卡缺少回忆输入框")
        evaluate("document.querySelector('#recall-input').value='浏览器回归写的回忆草稿';")
        # F1：错因标签由后端下发，且没选标签前评分会被拦一次（不真的提交评分，避免扰动排程）
        chips = evaluate(
            "(()=>{document.querySelector('#reveal-btn').click();"
            "const row=document.querySelector('#mistake-row');"
            "return {shown:row?getComputedStyle(row).display:'none',"
            "chips:[...document.querySelectorAll('.mistake-chip')].map(c=>c.textContent.trim())}})()")
        if not chips or chips.get("shown") == "none" or len(chips.get("chips") or []) < 4:
            errors.append(f"错因标签没有出现在翻面后: {chips}")
        gate = evaluate(
            "(()=>{const before=document.querySelector('#review-progress-label').textContent;"
            "document.querySelector('[data-rate=\"1\"]').click();"
            "const nudge=document.querySelector('#mistake-row').classList.contains('nudge');"
            "return {nudge, same:document.querySelector('#review-progress-label').textContent===before}})()")
        if not gate or not gate.get("nudge") or not gate.get("same"):
            errors.append(f"未选错因时评分没有被拦一次: {gate}")
        evaluate("document.querySelector('[data-tag-skip]').click()")
        queue = evaluate(
            "(()=>{const items=document.querySelectorAll('.queue-item');"
            "return {items:items.length, add:!!document.querySelector('[data-queue-add]'),"
            "select:!!document.querySelector('[data-queue-select]'),"
            "current:!!document.querySelector('.queue-item.current')}})()"
        )
        if not queue or queue.get("items", 0) < 1 or not queue.get("add") \
                or not queue.get("select") or not queue.get("current"):
            errors.append(f"复习队列侧栏不完整: {queue}")
        time.sleep(3.6)
        shot("review.png")
        evaluate("document.querySelector('#reveal-btn')?.click()")
        time.sleep(0.5)
        echo = evaluate(
            "(()=>{const e=document.querySelector('#recall-echo');"
            "return {shown:e?getComputedStyle(e).display:'none',"
            "text:(document.querySelector('.recall-echo-text')||{}).textContent||''}})()"
        )
        if not echo or echo.get("shown") == "none" or "回归" not in echo.get("text", ""):
            errors.append(f"翻面后没有回显回忆内容: {echo}")
        shot("review-revealed.png")

        # G2：单元页要能看到自己写过的回忆（种一条临时记录，跑完删掉，不动真实排程）
        db_path = Path(os.environ.get("BOIL_DATA_DIR", str(ROOT / "data"))) / "boil.db"
        import sqlite3 as _sq
        with _sq.connect(str(db_path), timeout=15) as conn:
            rec_unit = conn.execute(
                "SELECT id FROM units WHERE merged_into IS NULL ORDER BY order_index LIMIT 1").fetchone()[0]
            conn.execute(
                "INSERT OR REPLACE INTO attempts (id, user_id, unit_id, probe_id, kind, response,"
                " score, confidence, hints_used, feedback_json, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("at-browser-recall", "local", rec_unit, None, "review_recall",
                 "浏览器回归写入的回忆记录：提取练习=合书复述后对照原文找差距。", 0.8, 0.0, 0,
                 "{}", datetime.now(timezone.utc).isoformat(timespec="seconds")))
            conn.commit()
        try:
            goto(f"#/unit/{rec_unit}")
            wait_for(".recall-log", "我的回忆记录", 10)
            rec = evaluate(
                "(()=>{const t=document.querySelector('.recall-log-text');"
                "return {text:t?t.textContent.trim():'', logs:document.querySelectorAll('.recall-log').length}})()")
            if not rec or "回归写入" not in rec.get("text", ""):
                errors.append(f"单元页没有显示回忆记录: {rec}")
            else:
                print(f"  recalls: {rec['logs']} 条，首条={rec['text'][:20]}")
        finally:
            with _sq.connect(str(db_path), timeout=15) as conn:
                conn.execute("DELETE FROM attempts WHERE id='at-browser-recall'")
                conn.commit()

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
        # E6：记忆健康度卡片
        health = evaluate(
            "(()=>{const st=[...document.querySelectorAll('.stat')];"
            "const h=st.find(x=>x.textContent.includes('记忆健康度'));"
            "return {present:!!h, value:h?(h.querySelector('.stat-value')||{}).textContent:''}})()"
        )
        if not health or not health.get("present"):
            errors.append(f"洞察页缺少记忆健康度: {health}")
        # F1：错因标签不能只存在库里，洞察页要真的给出分布或诚实的空态
        mist = evaluate(
            "(()=>{const t=[...document.querySelectorAll('.section-title')]"
            ".find(x=>x.textContent.includes('错因分布'));"
            "if(!t) return {present:false};"
            "const card=t.closest('.card');"
            "return {present:true, rows:card.querySelectorAll('.mistake-dist-row').length,"
            "empty:!!card.querySelector('.empty'), note:(card.querySelector('.faint')||{}).textContent||''}})()")
        if not mist or not mist.get("present"):
            errors.append(f"洞察页缺少错因分布: {mist}")
        elif not mist.get("rows") and not mist.get("empty"):
            errors.append(f"错因分布既没有行也没有空态: {mist}")
        else:
            print(f"  mistake tags: rows={mist.get('rows')} empty={mist.get('empty')}")
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
        if not menu or menu.get("display") != "flex" or menu.get("btns", 0) < 2:
            errors.append(f"划词菜单没有出现: {menu}")
        shot("selection-menu.png")
        # F11/F14：配置了模型时菜单要多出「做卡片」和「问 AI」
        ai_items = evaluate(
            "(async()=>{const h=await fetch('/api/health');const j=await h.json();"
            "return {mode:j.data.llm.mode,"
            "cards:!!document.querySelector('[data-sel=\"cards\"]'),"
            "ask:!!document.querySelector('[data-sel=\"ask\"]')}})()")
        if ai_items and ai_items.get("mode") == "live" and not (ai_items.get("cards") and ai_items.get("ask")):
            errors.append(f"live 模式下划词菜单缺少 AI 动作: {ai_items}")
        live = bool(ai_items and ai_items.get("mode") == "live")
        if live and not real_click('[data-sel="cards"]'):
            errors.append("划词菜单里的「做成复习卡」真鼠标点不到")
        elif not live:
            evaluate("document.querySelector('[data-sel=\"cards\"]')?.click()")
        time.sleep(4.5)
        preview = evaluate(
            "(()=>{const p=document.querySelector('#sel-panel-cards');"
            "if(!p) return {shown:false};"
            "return {shown:getComputedStyle(p).display!=='none',"
            "cards:p.querySelectorAll('.card-preview').length,"
            "engine:!!p.querySelector('.badge'),"
            "confirm:!!p.querySelector('[data-cards-confirm]')}})()")
        if not preview or not preview.get("shown") or not preview.get("confirm"):
            errors.append(f"卡片预览面板没有打开: {preview}")
        elif preview.get("cards", 0) < 1:
            errors.append(f"卡片预览没有内容: {preview}")
        else:
            print(f"  cards preview: {preview.get('cards')} 张, engine badge={preview.get('engine')}")
        # 回归：面板内部的真实按下不能把它拆掉（document 级 hideAll 曾抢在 click 前清空 DOM，
        # 于是「确认存入」永远点不动，而合成 click() 完全测不到这一层）
        if not real_click("#sel-panel-cards .panel-body"):
            errors.append("卡片面板内部无法用真鼠标点中")
        else:
            time.sleep(0.5)
            survived = evaluate(
                "(()=>{const p=document.querySelector('#sel-panel-cards');"
                "if(!p) return {shown:false};"
                "return {shown:getComputedStyle(p).display!=='none',"
                "cards:p.querySelectorAll('.card-preview').length}})()")
            if not survived or not survived.get("shown") or survived.get("cards", 0) < 1:
                errors.append(f"点面板内部把它关掉了: {survived}")
        if not real_click("#sel-panel-cards [data-panel-cancel]"):
            evaluate("document.querySelector('#sel-panel-cards [data-panel-cancel]')?.click()")
        time.sleep(0.4)
        # F14：追问面板要能用真鼠标打开并输入文字（不提问，省一次模型调用）
        if live:
            evaluate("(()=>{const el=document.querySelector('.essence-text');"
                     "const r=document.createRange();r.selectNodeContents(el);"
                     "const s=getSelection();s.removeAllRanges();s.addRange(r);})()")
            time.sleep(0.8)
            if not real_click('[data-sel="ask"]'):
                errors.append("「对这段追问」真鼠标点不到")
            else:
                time.sleep(0.5)
                call("Input.insertText", {"text": "这跟前面的区别是什么？"})
                time.sleep(0.3)
                ask = evaluate(
                    "(()=>{const p=document.querySelector('#sel-panel-ask');"
                    "if(!p||getComputedStyle(p).display==='none') return {shown:false};"
                    "return {shown:true,val:(p.querySelector('#ask-input')||{}).value||'',"
                    "anchor:!!p.querySelector('.ask-anchor')}})()")
                if not ask or not ask.get("shown"):
                    errors.append(f"追问面板被真实点击关掉了: {ask}")
                elif len(ask.get("val", "")) < 4:
                    errors.append(f"追问输入框收不到文字: {ask}")
                else:
                    print(f"  ask panel: 输入 {len(ask.get('val'))} 字, 锚点={ask.get('anchor')}")
                real_click("#sel-panel-ask [data-panel-cancel]")
                time.sleep(0.3)
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
        # E2：保存过的引文要在单元页被重新点亮。
        # 必须先去别的页面再回来：同一个 hash 不会触发重渲染，高亮那次就不会跑。
        goto("#/dashboard")
        wait_for(".hero", "学习闭环")
        goto(f"#/unit/{unit_id}")
        wait_for(".essence-text", "一句话本质")
        time.sleep(1.6)
        hl = evaluate(
            "(()=>{const marks=[...document.querySelectorAll('.note-hl')];"
            "return {marks:marks.length, tip:!!document.querySelector('.note-hl-tip'),"
            "text:marks.map(m=>m.textContent).join('').slice(0,40)}})()"
        )
        if not hl or hl.get("marks", 0) < 1 or not hl.get("tip"):
            errors.append(f"灵感引文没有高亮: {hl}")
        shot("unit-note-highlight.png")
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
        # E7：缓存开关与统计
        cache = evaluate(
            "(()=>{const t=document.querySelector('#llm-cache-enabled');"
            "const row=t?t.closest('.cache-row'):null;"
            "return {toggle:!!t, checked:t?t.checked:false,"
            "hint:(row&&row.querySelector('.hint'))?row.querySelector('.hint').textContent:'',"
            "clear:!!document.querySelector('#clear-cache')}})()"
        )
        if not cache or not cache.get("toggle") or not cache.get("clear") \
                or "命中" not in cache.get("hint", ""):
            errors.append(f"设置页缺少缓存控制: {cache}")
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
        # F16：风格测试入口 → 5 题 → 人设落到目标 meta
        goto("#/dashboard")
        wait_for(".persona-card", "带法")
        evaluate("document.querySelector('[data-style-test]').click()")
        wait_for(".style-q", "希望教练怎么带你", 6)
        style_state = evaluate(
            "(()=>({qs:document.querySelectorAll('.style-q').length,"
            "opts:[...document.querySelectorAll('.style-q')].map(q=>q.querySelectorAll('.style-opt').length)}))()")
        if not style_state or style_state.get("qs") != 5:
            errors.append(f"风格测试题数不对: {style_state}")
        evaluate(
            "(()=>{document.querySelectorAll('.style-q').forEach(q=>{"
            "q.querySelector('.style-opt').click();});})()")
        shot("style-test.png")
        evaluate("document.querySelector('.modal-foot .btn-primary').click()")
        time.sleep(1.6)
        persona = evaluate(
            "(async()=>{const r=await fetch('/api/dashboard');const j=await r.json();"
            "const p=j.data.goal.meta.persona;"
            "return {key:p?p.key:'',label:p?p.label:'',strategy:p?p.strategy:'',"
            "card:!!document.querySelector('.persona-card .badge-violet')}})()")
        if not persona or not persona.get("key") or not persona.get("strategy"):
            errors.append(f"风格测试没有写入人设: {persona}")
        else:
            print(f"  style persona: {persona.get('label')} (card={persona.get('card')})")
        goto("#/settings")
        wait_for(".settings-grid", "模型连接")
        # F6：诊断面板要么给出成功态，要么给出可复制的分类诊断
        diag = evaluate(
            "(()=>({ok:!!document.querySelector('.diag-ok'),"
            "box:!!document.querySelector('.diag-box'),"
            "hint:(document.querySelector('.diag-body .callout')||{}).textContent||'',"
            "copy:!!document.querySelector('[data-diag-copy]')}))()")
        if not diag or not (diag.get("ok") or diag.get("box")):
            errors.append(f"模型诊断区没有渲染: {diag}")
        shot("settings-desktop.png")
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
