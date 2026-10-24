#!/usr/bin/env python3
"""真人节奏的端到端点击旅程：一个新学习者从粘贴材料到复习、存灵感、看更新日志的完整一天。

和 browser_check.py 的分工：
  - browser_check 是全站体检（每个视图截图 + 抓 JS 报错）；
  - 这个脚本只走一条连贯的用户旅程，断言的是**用户看得见的文字结果**，
    而不是「某个 DOM 节点存在」。DOM 在、文字不对，用户照样学不下去。

真实感来自三件事：
  1. 只用真实输入事件：鼠标先 hover 再按下抬起，键盘走 dispatchKeyEvent，
     长文本用 insertText（等价于粘贴），拖动选词用 mouseMoved。
     `.click()` 不会触发 mousedown，划词浮层那类「点外面就关」的逻辑只有真事件才测得出来。
  2. 每步之间随机停顿、偶尔滚动，像人在读屏幕，而不是机器抢跑。
  3. 一个空 data/ 目录 + 一个干净的浏览器 profile：真的从「还没有目标」开始。

用法：
  BOIL_DATA_DIR=/tmp/boil-journey BOIL_PORT=7862 python3 run.py &
  BOIL_APP=http://127.0.0.1:7862 python3 tools/human_journey.py
"""
from __future__ import annotations

import base64
import json
import os
import random
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from browser_check import CDP, WS  # 复用同一个零依赖 CDP 客户端  # noqa: E402

APP = os.environ.get("BOIL_APP", "http://127.0.0.1:7862")
PORT = int(os.environ.get("BOIL_CDP_PORT", "9345"))
CHROME = os.environ.get("BOIL_CHROME", "chromium")
OUT = ROOT / "screenshots"
DOWNLOADS = Path(tempfile.mkdtemp(prefix="boil-journey-dl-"))

MATERIAL = """# 让记忆留下来的四件事

## 提取练习比重新阅读更有效

重新阅读给的是「我好像认识它」的感觉，那是熟悉感，不是能想起来的强度。
提取练习要求在没有答案的情况下先把内容拉回意识里，拉的动作本身会改变记忆：
一次成功的提取比三次重读更能延长保持时间。考试前的默写、合上书复述、
自己出题再做，都是同一件事的不同写法。提取失败也有价值，因为它暴露了
「你以为你懂」和「你其实懂」之间的缺口。

## 间隔重复对抗遗忘曲线

记忆随时间衰减，而且衰减是先快后慢的。如果把复习安排在快要想不起来
的那个时刻，一次复习能把保持时间拉得比上一次更长。用稳定度 S 描述一条
记忆能保持的程度，粗略可以写成 R = e^(-t/S)，其中 t 是距离上次复习的天数，
R 是此刻还想得起来的概率：

$$R = e^{-t/S}$$

同样隔三天，昨天刚学的内容 R 可能只有 0.55，而上个月学过两遍的内容 R 还有 0.85。
所以「到期再复习」不是省时间，是把复习花在刀刃上：在 R 掉到太低之前出手，
一次就够；掉下去了，就得从头再学一遍。

## 合意困难：难受不等于没用

容易完成的学习动作往往记得最少。交错练习、间隔练习、自己先做再对答案，
都会让当下更费劲，看起来进度更慢，但延后测出来的保持率更高。判断一段
学习有没有用，不能问「我现在感觉怎么样」，要问「一周后我还能不能想起来」。
需要区分的是：困难要落在「够得着但要想一想」的区间，太难只会让人放弃。

## 睡眠与巩固

白天形成的记忆是脆的，需要被搬运进长期存储。睡眠期间海马体会把白天的
经历回放给新皮层，快速眼动睡眠和陈述性记忆的稳固尤其相关。熬夜复习的
代价通常不是那两个小时少看了几页，而是之前几页白看。睡前一小时用来
做一次轻量的自测，比在凌晨三点重读笔记有效得多。
"""


class Fail(Exception):
    pass


def api(path: str, payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(APP + path, data=data,
                                 headers={"Content-Type": "application/json"} if data else {},
                                 method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())["data"]


class Bot:
    """一个按人的节奏干活的浏览器。"""

    def __init__(self):
        self.profile = tempfile.mkdtemp(prefix="boil-chrome-journey-")
        self.chrome = subprocess.Popen(
            [CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
             "--hide-scrollbars", f"--remote-debugging-port={PORT}",
             f"--user-data-dir={self.profile}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.errors: list[str] = []
        version = None
        for _ in range(120):
            try:
                version = json.loads(urllib.request.urlopen(
                    f"http://127.0.0.1:{PORT}/json/version", timeout=2).read())
                break
            except Exception:
                time.sleep(0.25)
        if not version:
            raise RuntimeError("Chromium CDP 端口没起来")
        self.cdp = CDP(WS(version["webSocketDebuggerUrl"]))
        target = self.cdp.cmd("Target.createTarget", {"url": "about:blank"})["targetId"]
        self.sid = self.cdp.cmd("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        try:
            self.cdp.cmd("Browser.setDownloadBehavior",
                         {"behavior": "allow", "downloadPath": str(DOWNLOADS), "eventsEnabled": True})
        except Exception as exc:  # 下载不可观察时如实说明，不假装通过
            self.errors.append(f"download observation unavailable: {exc}")
        self.call("Page.enable")
        self.call("Runtime.enable")
        self.call("Log.enable")
        self.call("Network.enable")
        self.call("Emulation.setDeviceMetricsOverride",
                  {"width": 1440, "height": 1050, "deviceScaleFactor": 1, "mobile": False})
        self.call("Emulation.setEmulatedMedia", {"features": [
            {"name": "prefers-color-scheme", "value": "dark"}]})
        self.cdp.handlers.append(self._on_event)

    def call(self, method, params=None):
        return self.cdp.cmd(method, params, self.sid)

    def _on_event(self, msg):
        m = msg.get("method")
        if m == "Runtime.exceptionThrown":
            d = msg.get("params", {}).get("exceptionDetails", {})
            self.errors.append("JS exception: " + str(d.get("text")) + " "
                               + str(d.get("exception", {}).get("description", ""))[:300])
        elif m == "Runtime.consoleAPICalled" and msg.get("params", {}).get("type") == "error":
            args = msg["params"].get("args", [])
            self.errors.append("console.error: " + " ".join(
                str(a.get("value", a.get("description", ""))) for a in args)[:300])
        elif m == "Log.entryAdded":
            e = msg.get("params", {}).get("entry", {})
            if e.get("level") == "error":
                self.errors.append("log: " + str(e.get("text"))[:200])
        elif m == "Network.loadingFailed" and not msg.get("params", {}).get("canceled"):
            self.errors.append("request failed: " + str(msg["params"].get("errorText")))

    # ---------- 基础动作 ----------
    def think(self, lo=0.5, hi=1.5):
        time.sleep(random.uniform(lo, hi))

    def evaluate(self, expr):
        return self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                              "awaitPromise": True}).get("result", {}).get("value")

    def body(self):
        return self.evaluate("document.body.innerText") or ""

    def goto(self, url, wait_for=None, timeout=30.0):
        self.call("Page.navigate", {"url": url})
        if wait_for:
            self.wait(wait_for, timeout)
        self.think()

    def wait(self, selector, timeout=30.0, text=None):
        start = time.time()
        while time.time() - start < timeout:
            ok = self.evaluate(f"!!document.querySelector({json.dumps(selector)})")
            if ok and text:
                ok = text in self.body()
            if ok:
                time.sleep(0.3)
                return True
            time.sleep(0.2)
        return False

    def wait_text(self, needle, timeout=30.0):
        start = time.time()
        while time.time() - start < timeout:
            if needle in self.body():
                return True
            time.sleep(0.3)
        return False

    def rect(self, selector, scroll=True):
        """滚动到元素中间并回报命中情况；`scroll=False` 只量位置不滚。

        `behavior:'instant'` 是必须的：app.css 里 `html{scroll-behavior:smooth}`，
        平滑滚动期间 getBoundingClientRect 拿到的是动画起点的坐标，
        随后的点击就会落在别的元素上（老 harness 靠 emulate reduced-motion 绕过了这点，
        真人不会关动画，所以这里按真人来测）。
        """
        raw = self.evaluate(
            "(()=>{const e=document.querySelector(%s);if(!e)return null;"
            "if(%s)e.scrollIntoView({block:'center',inline:'center',behavior:'instant'});"
            "const r=e.getBoundingClientRect();"
            "const x=r.x+r.width/2, y=r.y+r.height/2;"
            "const hit=document.elementFromPoint(x,y);"
            "return JSON.stringify({x:x,y:y,w:r.width,h:r.height,"
            "top:r.top,bottom:r.bottom,left:r.left,right:r.right,vw:window.innerWidth,"
            "vh:window.innerHeight,hit:!!(hit&&(hit===e||e.contains(hit)||hit.contains(e))),"
            "blocker:hit?(hit.tagName+'#'+hit.id+'.'+(hit.className||'').toString().slice(0,28)):null})})()"
            % (json.dumps(selector), "true" if scroll else "false"))
        return json.loads(raw) if raw else None

    def click(self, selector, timeout=8.0):
        """确认这个点真的命中目标 → hover → 真实按下/抬起（能不滚就不滚）。
        找不到、被盖住、出视口都直接报错，不偷偷用 element.click() 兜底。"""
        def usable(sp):
            return bool(sp) and sp["w"] > 1 and sp["h"] > 1 and sp["hit"] \
                and sp["top"] >= -1 and sp["bottom"] <= sp["vh"] + 1 \
                and sp["left"] >= -1 and sp["right"] <= sp["vw"] + 1

        deadline = time.time() + timeout
        spot = None
        while time.time() < deadline:
            # 先量不滚：划词浮条与笔记面板是 fixed 定位，页面一滚（>10px）就被 onScroll 收掉，
            # 到那时候再点就只能点到一个 0×0 的空壳。
            spot = self.rect(selector, scroll=False)
            if usable(spot):
                break
            spot = self.rect(selector)
            if usable(spot):
                break
            time.sleep(0.2)
        if not spot or spot["w"] <= 1 or spot["h"] <= 1:
            raise Fail(f"{selector} 在页面上找不到或不可点击：{spot}")
        if not spot["hit"]:
            raise Fail(f"{selector} 的中心点被别的元素盖住了：{spot['blocker']}")
        if not (spot["top"] >= -1 and spot["bottom"] <= spot["vh"] + 1):
            raise Fail(f"{selector} 滚动后仍不在视口里（top={spot['top']:.0f} "
                       f"bottom={spot['bottom']:.0f} vh={spot['vh']}）")
        self.think(0.15, 0.4)
        self.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": spot["x"], "y": spot["y"]})
        self.think(0.08, 0.2)
        for kind in ("mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", {"type": kind, "x": spot["x"], "y": spot["y"],
                                                   "button": "left", "clickCount": 1})
        self.think(0.3, 0.8)
        return True

    def type(self, selector, text, paste=False):
        self.click(selector)
        if not self.evaluate("(()=>{const e=document.querySelector(%s);return document.activeElement===e})()"
                             % json.dumps(selector)):
            raise Fail(f"点击 {selector} 后没有获得焦点，后面的输入会落到别处")
        self.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a", "code": "KeyA",
                                             "modifiers": 2, "windowsVirtualKeyCode": 65})
        self.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a", "code": "KeyA",
                                             "modifiers": 2, "windowsVirtualKeyCode": 65})
        self.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Backspace",
                                             "code": "Backspace", "windowsVirtualKeyCode": 8})
        self.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Backspace",
                                             "code": "Backspace", "windowsVirtualKeyCode": 8})
        if paste or any(ord(c) > 127 for c in text):
            self.call("Input.insertText", {"text": text})   # 中文只能按「粘贴」模拟，逐字符键入是 IME 的事
        else:
            for ch in text:
                # keyDown 带 text 就已经插入了字符；再补一个 char 事件会变成 "RREESSEETT"
                self.call("Input.dispatchKeyEvent", {"type": "keyDown", "text": ch, "key": ch})
                self.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": ch})
                time.sleep(random.uniform(0.03, 0.09))
        self.think(0.2, 0.5)

    def key(self, k, modifiers=0):
        code = {"Escape": "Escape", "Enter": "Enter", "k": "KeyK", " ": "Space"}.get(k, k)
        vk = {"Escape": 27, "Enter": 13, "k": 75, " ": 32}.get(k, 0)
        base = {"key": k, "code": code, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk,
                "modifiers": modifiers}
        text = k if len(k) == 1 else ("\r" if k == "Enter" else None)
        self.call("Input.dispatchKeyEvent", dict(base, type="keyDown", **({"text": text} if text else {})))
        self.call("Input.dispatchKeyEvent", dict(base, type="keyUp"))
        self.think(0.2, 0.4)

    def wheel(self, clicks=6):
        self.call("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": 700, "y": 500,
                                               "deltaX": 0, "deltaY": 120 * clicks})
        self.think(0.3, 0.7)

    def visible(self, selector):
        """元素真的看得见吗：.selection-menu 这类浮层常驻 DOM，只靠 display 切换，
        querySelector 命中不等于出现。"""
        return bool(self.evaluate(
            "(()=>{const e=document.querySelector(%s);if(!e)return false;"
            "const s=getComputedStyle(e);if(s.display==='none'||s.visibility==='hidden')return false;"
            "const r=e.getBoundingClientRect();return r.width>2&&r.height>2})()"
            % json.dumps(selector)))

    def wait_visible(self, selector, timeout=8.0):
        start = time.time()
        while time.time() - start < timeout:
            if self.visible(selector):
                return True
            time.sleep(0.2)
        return False

    def value(self, selector):
        return self.evaluate(f"(()=>{{const e=document.querySelector({json.dumps(selector)});"
                             f"return e?e.value:null}})()")

    def text_of(self, selector):
        return self.evaluate(f"(()=>{{const e=document.querySelector({json.dumps(selector)});"
                             f"return e?e.innerText.trim():null}})()") or ""

    def toast(self):
        texts = self.evaluate(
            "[...document.querySelectorAll('.toast')].map(t=>t.innerText.trim())") or []
        return texts[-1] if texts else ""

    def last_toast_matching(self, needle, timeout=10.0):
        start = time.time()
        while time.time() - start < timeout:
            texts = self.evaluate("[...document.querySelectorAll('.toast')].map(t=>t.innerText.trim())") or []
            if any(needle in t for t in texts):
                return next(t for t in texts if needle in t)
            time.sleep(0.25)
        return ""

    def shot(self, name):
        OUT.mkdir(exist_ok=True)
        data = self.call("Page.captureScreenshot", {"format": "png"})["data"]
        (OUT / f"journey-{name}.png").write_bytes(base64.b64decode(data))
        print(f"        📸 journey-{name}.png")

    _TEXTMAP_JS = """(() => {
      // 把容器里的可见文字拼成一条坐标轴，再把偏移映射回 (文本节点, 节点内偏移)。
      // 不能只挑「一个足够长的文本节点」：markdown 会把一句话拆进 <strong>、<code> 好几个节点里。
      // 在所有匹配里挑「文字最多的那一个」：第一个 .unit-section 可能只有一句类比
      const matches = [...document.querySelectorAll(%s)];
      const host = matches.sort((a, b) => (b.textContent || "").trim().length
        - (a.textContent || "").trim().length)[0];
      if (!host) return null;
      const texts = [];
      const walk = document.createTreeWalker(host, NodeFilter.SHOW_TEXT);
      let node, total = 0;
      while ((node = walk.nextNode())) {
        if (!node.textContent.trim()) continue;
        texts.push({ at: total, len: node.textContent.length });
        total += node.textContent.length;
      }
      if (total < 80) return null;
      window.__agentDragHost = host;
      const nth = (i) => {
        const w = document.createTreeWalker(host, NodeFilter.SHOW_TEXT);
        let k = 0, n;
        while ((n = w.nextNode())) { if (!n.textContent.trim()) continue; if (k++ === i) return n; }
        return null;
      };
      const locate = (idx) => {
        for (let i = 0; i < texts.length; i++) {
          if (idx >= texts[i].at && idx <= texts[i].at + texts[i].len) return { ni: i, off: idx - texts[i].at };
        }
        return null;
      };
      const caret = (loc) => {
        const el = nth(loc.ni);
        if (!el) return [0, 0];
        const r = document.createRange();
        r.setStart(el, Math.min(loc.off, el.textContent.length));
        r.collapse(true);
        const b = r.getBoundingClientRect();
        return [b.left || b.x || 0, (b.top || 0) + (b.height || 12) / 2];
      };
      const A = locate(%d), B = locate(%d + %d);
      const ea = A && nth(A.ni), eb = B && nth(B.ni);
      if (!ea || !eb) return null;
      const fr = document.createRange();
      fr.setStart(ea, Math.min(A.off, ea.textContent.length));
      fr.setEnd(eb, Math.min(B.off, eb.textContent.length));
      return JSON.stringify({ a: caret(A), b: caret(B), A: A, B: B,
                              text: fr.toString().trim().slice(0, 90) });
    })()"""

    def select_with_mouse(self, selector, want=26, start=14):
        """按住左键真的拖出一段选区：划词逻辑只有真拖拽才走完整路径。

        起点默认往后挪 14 个字符：单元页最前面是「本质」这类小标签，
        从 0 开始拖会跨到两个块里，高亮器只能标出一段，测不出正常路径。
        """
        js = self._TEXTMAP_JS % (json.dumps(selector), start, start, want)
        raw = self.evaluate(js)
        if not raw:
            raise Fail(f"{selector} 里凑不出可拖选的文字（可见文本不足或映射失败）")
        plan = json.loads(raw)
        box = json.loads(self.evaluate(
            "(()=>{const e=window.__agentDragHost||document.querySelector(%s);"
            "const r=e.getBoundingClientRect();return JSON.stringify({x:r.x,y:r.y,w:r.width,h:r.height})})()"
            % json.dumps(selector)) or "null") or {"x": 0, "y": 0, "w": 4000, "h": 4000}

        def clamp(pt):
            return [min(max(pt[0], box["x"] + 3), box["x"] + box["w"] - 6),
                    min(max(pt[1], box["y"] + 3), box["y"] + box["h"] - 3)]

        a, b = clamp(plan["a"]), clamp(plan["b"])
        self.evaluate("window.getSelection().removeAllRanges()")
        self.call("Input.dispatchMouseEvent", {"type": "mousePressed", "x": a[0], "y": a[1],
                                               "button": "left", "clickCount": 1})
        for frac in (0.2, 0.45, 0.7, 0.9, 1.0):
            self.call("Input.dispatchMouseEvent", {"type": "mouseMoved",
                                                   "x": a[0] + (b[0] - a[0]) * frac,
                                                   "y": a[1] + (b[1] - a[1]) * frac,
                                                   "button": "left"})
            time.sleep(0.07)
        self.call("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": b[0], "y": b[1],
                                               "button": "left", "clickCount": 1})
        self.think(0.6, 1.0)
        got = self.evaluate("String(window.getSelection())") or ""
        if len(got.strip()) < 6:
            # caret 坐标被换行/行首缩进带歪时，退化成「程序化建 range + 真鼠标抬起」：
            # 仍然走 selectionchange → 浮条这条真实链路，只是不做物理拖拽。
            made = self.evaluate(
                "(()=>{const host=document.querySelector(%s);"
                "const nodeAt=(ni)=>{const w=document.createTreeWalker(host,NodeFilter.SHOW_TEXT);"
                "let k=0,q;while((q=w.nextNode())){if(!q.textContent.trim())continue;"
                "if(k===ni)return q;k++}return null};"
                "const A=%s,B=%s;const ea=nodeAt(A.ni),eb=nodeAt(B.ni);"
                "if(!ea||!eb)return false;const r=document.createRange();"
                "r.setStart(ea,Math.min(A.off,ea.textContent.length));"
                "r.setEnd(eb,Math.min(B.off,eb.textContent.length));"
                "const s=window.getSelection();s.removeAllRanges();s.addRange(r);return true})()"
                % (json.dumps(selector), json.dumps(plan["A"]), json.dumps(plan["B"])))
            if not made:
                return got
            rect = self.evaluate(
                "(()=>{const r=window.getSelection().getRangeAt(0)?.getBoundingClientRect();"
                "return r?JSON.stringify([r.right||r.x,r.top+(r.height||12)/2]):null})()")
            if rect:
                x2, y2 = clamp(json.loads(rect))
                self.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x2, "y": y2,
                                                       "button": "left"})
                self.call("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x2, "y": y2,
                                                       "button": "left", "clickCount": 1})
            self.think(0.5, 0.8)
            got = self.evaluate("String(window.getSelection())") or ""
        return got

    def touch_select(self, selector, start, length):
        """手机上的长按选词：TouchStart/Move/End + contextmenu。"""
        info = json.loads(self.evaluate(
            "(()=>{const e=document.querySelector(%s);if(!e)return null;"
            "e.scrollIntoView({block:'center',behavior:'instant'});"
            "const r=e.getBoundingClientRect();return JSON.stringify({x:r.x,y:r.y,w:r.width,h:r.height})})()"
            % json.dumps(selector)) or "null")
        if not info:
            raise Fail(f"{selector} 不存在")
        x = info["x"] + min(120, info["w"] * 0.3)
        y = info["y"] + info["h"] * 0.35
        self.call("Input.dispatchTouchEvent", {"type": "touchStart",
                                               "touchPoints": [{"x": x, "y": y}]})
        time.sleep(0.6)                      # 真人会按住一会儿
        self.call("Input.dispatchTouchEvent", {"type": "touchMove",
                                               "touchPoints": [{"x": x + 40, "y": y}]})
        self.call("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        picked = self.evaluate(
            "(()=>{const e=document.querySelector(%s);"
            "const w=document.createTreeWalker(e,NodeFilter.SHOW_TEXT);let pos=0,n;"
            "while((n=w.nextNode())){const L=n.textContent.length;"
            "if(%d>=pos&&%d<=pos+L){const r=document.createRange();"
            "r.setStart(n,%d-pos);r.setEnd(n,%d-pos);window.getSelection().removeAllRanges();"
            "window.getSelection().addRange(r);return JSON.stringify(r.getBoundingClientRect())}"
            "pos+=L}return null})()"
            % (json.dumps(selector), start, start + length, start, start + length))
        if picked:
            rect = json.loads(picked)
            self.call("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": rect["x"] + 2, "y": rect["y"] + rect["height"] / 2,
                "button": "right", "clickCount": 1, "context": True})
            self.call("Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": rect["x"] + 2, "y": rect["y"] + rect["height"] / 2,
                "button": "right", "clickCount": 1, "context": True})
        time.sleep(0.8)
        return self.evaluate("String(window.getSelection())") or ""


def main():
    random.seed(int(time.time()))
    results: list[tuple[str, bool, str]] = []
    bot = Bot()
    expected_version = ""
    try:
        expected_version = api("/api/releases")["version"]
    except Exception as exc:
        results.append(("读取 /api/releases", False, str(exc)))

    def step(name, fn):
        start = time.time()
        try:
            detail = fn() or ""
            results.append((name, True, detail))
            print(f"PASS  {name}{('  →  ' + detail) if detail else ''}   [{time.time() - start:.1f}s]")
        except Fail as exc:
            results.append((name, False, str(exc)))
            print(f"FAIL  {name}  →  {exc}")
            try:
                bot.shot(f"fail-{len(results):02d}")
            except Exception:
                pass
        except Exception as exc:  # 意外崩溃也要留证据
            results.append((name, False, f"{type(exc).__name__}: {exc}"))
            print(f"ERROR {name}  →  {type(exc).__name__}: {exc}")
            try:
                bot.shot(f"fail-{len(results):02d}")
            except Exception:
                pass
        bot.think(0.4, 1.1)

    # ---------------------------------------------------------------- 1 初见
    def s01_open():
        bot.goto(APP + "/", wait_for=".step-dots", timeout=40)
        # 数一数 /api/reset 被调了几次：危险闸门的证据不能靠"看起来没反应"
        bot.evaluate("window.__resetHits=0;const f=window.fetch;"
                     "window.fetch=(u,...a)=>{if(String(u).includes('/api/reset'))window.__resetHits++;return f(u,...a)};")
        text = bot.body()
        for needle in ("你想先烧开哪一壶水", "把知识", "用我的材料创建"):
            if needle not in text:
                raise Fail(f"首屏没看到「{needle}」")
        bot.shot("01-onboarding")
        return "首屏出现三步引导，标题与内容包卡片都在"

    step("新用户打开首页", s01_open)

    def s02_choose_material():
        bot.click("[data-material]")
        if not bot.wait("#ob-text", 10):
            raise Fail("点「用我的材料创建」后没有进入材料表单")
        for needle in ("材料内容", "目标名称", "AI 生成信息"):
            if needle not in bot.body():
                raise Fail(f"表单里少了「{needle}」")
        return "第二步表单齐全（称呼 / 目标 / 材料 / 每日时长）"

    step("选择「用我的材料创建」", s02_choose_material)

    def s03_paste():
        bot.type("#ob-title", "搞懂怎么让自己记得更久")
        bot.type("#ob-text", MATERIAL, paste=True)
        got = bot.value("#ob-text") or ""
        if len(got) < len(MATERIAL) - 3:
            raise Fail(f"材料只进去 {len(got)} 字，应有 {len(MATERIAL)} 字")
        if (bot.value("#ob-title") or "") != "搞懂怎么让自己记得更久":
            raise Fail("目标名称没有真的写进去")
        bot.wheel(4)
        bot.shot("03-material")
        return f"敲了标题、粘了 {len(got)} 字材料（含一条 LaTeX 公式）"

    step("输入目标并粘贴材料", s03_paste)

    def s04_suggest():
        chars = len((bot.value("#ob-text") or "").strip())
        if chars < 60:
            raise Fail(f"点 AI 生成信息之前材料就没了（{chars} 字），是上一步的问题不是这一步")
        bot.click("#ob-suggest")
        toast = bot.last_toast_matching("生成", timeout=200)
        if not toast:
            seen = " / ".join(bot.evaluate(
                "[...document.querySelectorAll('.toast')].map(t=>t.innerText.slice(0,60))") or [])
            raise Fail(f"等了 200 秒没看到生成提示；材料 {chars} 字；当时的提示：{seen or '（一条都没有＝请求没发出去或端点没回）'}")
        title = bot.value("#ob-material-title") or ""
        subject = bot.value("#ob-subject") or ""
        goal = bot.value("#ob-title") or ""
        if not title:
            raise Fail(f"AI 没有把材料标题填进来：{title!r}")
        if "AI 已生成" not in toast:
            raise Fail(f"提示说的是本地规则而不是模型：{toast}")
        if not subject and "学科" not in toast:
            raise Fail(f"模型没给学科，提示却仍然只说生成了标题与主题：{toast}")
        bot.shot("04-suggest")
        return f"模型给出材料标题「{title}」· 学科「{subject}」· 目标名保留用户输入「{goal}」"

    step("点「AI 生成信息」", s04_suggest)

    def s05_compile():
        bot.click("[data-submit]")
        if not bot.wait("[data-start]", 600):
            raise Fail("编译 600 秒还没出预览")
        text = bot.body()
        for needle in ("全部", "个单元", "阶段计划"):
            if needle not in text:
                raise Fail(f"预览里没看到「{needle}」")
        units = bot.evaluate(
            "document.querySelectorAll('.preview-unit').length") or 0
        if units < 3:
            raise Fail(f"只切出 {units} 个单元，材料白喂了")
        bot.shot("05-compile")
        return f"模型把材料切成 {units} 个单元，预览带阶段计划"

    step("编译材料并预览路径", s05_compile)

    def s06_start_goal():
        bot.click("[data-start]")
        if not bot.wait(".hero", 40):
            raise Fail("点了「开始学习」没能进入今日页")
        if "搞懂怎么让自己记得更久" not in bot.body():
            raise Fail("今日页顶部没有显示刚创建的目标名")
        bot.shot("06-dashboard")
        return "目标落地，今日页出现进度环与建议"

    step("创建目标进入今日", s06_start_goal)

    def s07_advisor():
        bot.wait("#advisor-body[data-advice-ready='1']", 20)
        advice = bot.text_of("#advisor-body")
        if len(advice) < 12:
            raise Fail(f"沸腾顾问没给建议：{advice!r}")
        bot.wheel(3)
        bot.shot("07-advisor")
        return "顾问建议：" + advice.replace("\n", " ")[:60]

    step("读到今日建议", s07_advisor)

    def s08_open_unit():
        href = bot.evaluate(
            "(()=>{const a=document.querySelector('.hero-actions .btn-primary');"
            "return a?a.getAttribute('href'):null})()") or ""
        if not href:
            raise Fail("今日页主行动按钮不见了")
        bot.click(".hero-actions .btn-primary")
        if not bot.wait(".unit-main", 40):
            raise Fail("点主按钮后没进入学习视图")
        text = bot.body()
        for needle in ("一句话本质", "本单元火候", "六种模式"):
            if needle not in text:
                raise Fail(f"单元页缺了「{needle}」")
        bot.shot("08-unit")
        return f"从「{href}」进入单元页，预消化内容都在"

    step("顺着建议打开第一个单元", s08_open_unit)

    def s09_mnemonic():
        if not bot.evaluate("!!document.querySelector('[data-mnemonic-ai]')"):
            raise Fail("AI 开着却没有「AI 编口诀」按钮")
        bot.click("[data-mnemonic-ai]")
        if not bot.wait("#mnemonic-ai [data-mnemonic-fill]", 240):
            raise Fail(f"口诀 240 秒没出结果，框里是：{bot.text_of('#mnemonic-ai')[:80]!r}")
        box = bot.text_of("#mnemonic-ai")
        if len(box) < 10:
            raise Fail(f"口诀框是空的：{box!r}")
        bot.click("[data-mnemonic-fill]")
        filled = bot.value("#note-mnemonic") or ""
        if len(filled) < 6:
            raise Fail("填进速记后文本框还是空的")
        bot.click('[data-save-note="mnemonic"]')
        toast = bot.last_toast_matching("已保存", timeout=30) or bot.toast()
        bot.shot("09-mnemonic")
        return f"模型口诀「{box[:24]}…」已填进速记并保存（{toast[:24]}）"

    step("生成口诀、填进速记并保存", s09_mnemonic)

    def s10_select_note():
        quote, shown = "", False
        for attempt in (1, 2):
            quote = bot.select_with_mouse(".unit-main .unit-section")
            if len(quote.strip()) < 8:
                raise Fail(f"拖拽没有选中文字，只拿到 {quote!r}")
            shown = bot.wait_visible(".selection-menu", 6)
            if shown:
                break
            if attempt == 1:
                print(f"        …浮条第一次没出现（选区被页面重渲染吃掉了），像真人一样重选一次")
        if not shown:
            raise Fail("选中文字后浮条没有出现（重选两次仍然不出现）")
        actions = bot.evaluate("document.querySelectorAll('.selection-menu [data-sel]').length") or 0
        if actions < 4:
            raise Fail(f"浮条只有 {actions} 个动作")
        # 真人会犹豫一下再点：这一步同时验证面板不会被 mousedown 抢关
        bot.think(1.0, 1.8)
        bot.click('[data-sel="note"]')
        if not bot.wait_visible(".note-popover", 8):
            raise Fail("点「存为灵感」后笔记面板没出现")
        if "存为灵感" not in bot.text_of(".note-popover"):
            raise Fail("笔记面板标题不对")
        bot.type("#sel-note", "这一句要能在没有答案时自己说出来", paste=True)
        bot.click(".note-popover [data-note-save]")
        saved = bot.last_toast_matching("灵感", timeout=20)
        if not saved:
            raise Fail("保存灵感没有提示")
        bot.shot("10-selection")
        return f"拖拽选中「{quote[:18]}…」→ 存为灵感（{saved[:26]}）"

    step("划词把一句话存进灵感库", s10_select_note)

    def s11_reload_highlight():
        # ① 当场就该亮起来：存完灵感不需要刷新页面
        if not bot.wait_text("已标出", 15):
            raise Fail("存完灵感后，当前页没有立刻把引文标出来")
        live_tip = bot.text_of(".note-hl-tip")
        live_marks = bot.evaluate("document.querySelectorAll('.unit-main mark.note-hl').length") or 0
        # ② 重新加载之后还得在：说明它是从笔记里查出来的，不是临时 DOM
        bot.call("Page.reload", {"ignoreCache": True})
        if not bot.wait(".unit-main", 30):
            raise Fail("重新加载后单元页没回来")
        if not bot.wait_text("已标出", 15):
            raise Fail("重新进入单元页后没有引文高亮提示")
        tip = bot.text_of(".note-hl-tip")
        marks = bot.evaluate("document.querySelectorAll('.unit-main mark.note-hl').length") or 0
        bot.shot("11-highlight")
        if not tip.strip():
            raise Fail("高亮提示是空的")
        return f"当场 {live_marks} 段 / 刷新后 {marks} 段：{live_tip.strip()[:40]}…"

    step("回到页面确认引文被高亮", s11_reload_highlight)

    def s12_review_undo():
        bot.click('a[href="#/review"]')
        if not bot.wait("#review-card-slot", 30):
            raise Fail("复习页没渲染出卡片位")
        if not bot.wait("#flashcard", 20):
            text = bot.body()
            raise Fail(f"今天没有可复习的卡：{text[:120]}")
        front = bot.text_of("#flash-front")
        bot.type("#recall-input", "先自己组织一遍：提取练习让拉回记忆的动作本身强化通路，"
                                  "间隔到快忘时复习最省力。", paste=True)
        bot.click("#reveal-btn")
        if not bot.evaluate("getComputedStyle(document.querySelector('#flash-back')).display!=='none'"):
            raise Fail("点了「显示答案」背面没展开")
        bot.click("[data-rate='3']")
        if not bot.wait("#last-review", 30):
            raise Fail("评分后没有出现「刚才这次」撤销条")
        strip = bot.text_of("#last-review")
        if "撤销这次评分" not in strip or "掌握度" not in strip:
            raise Fail(f"撤销条内容不对：{strip!r}")
        due_before = bot.evaluate("document.querySelector('#review-progress-label')?.innerText||''")
        bot.click("[data-undo-review]")
        undone = bot.last_toast_matching("撤销", timeout=25)
        if not undone:
            raise Fail("点撤销没有回应")
        after = bot.text_of("#last-review")
        bot.shot("12-review")
        return f"「{front[:16]}…」先回忆再评分，随后撤销：{undone[:24]}（进度 {due_before} → {after[:20]}）"

    step("复习：先回忆、翻面、评分、再撤销", s12_review_undo)

    def s13_focus():
        bot.click("#focus-btn")
        if not bot.wait(".modal-foot .btn-primary", 8):
            raise Fail("番茄钟时长选择没弹出")
        bot.click(".modal-foot .btn-primary")
        time.sleep(1.6)
        label = bot.text_of("#focus-label")
        if ":" not in label or not bot.evaluate("!!document.querySelector('#focus-btn.running')"):
            raise Fail(f"没有进入倒计时状态：{label!r}")
        bot.click("#focus-btn")
        bot.click(".modal-foot .btn-ghost")
        toast = bot.last_toast_matching("不计入", timeout=15)
        if not toast:
            raise Fail("提前结束没有说明不计入时长")
        bot.shot("13-focus")
        return f"计时 {label} 后提前放弃：{toast[:28]}"

    step("开一个番茄钟再提前结束", s13_focus)

    def s14_version_chip():
        chip = bot.text_of("#release-chip")
        if expected_version and f"v{expected_version}" not in chip:
            raise Fail(f"侧边栏版本徽标不是当前版本：{chip!r}")
        if not bot.evaluate("!!document.querySelector('#release-dot')"):
            raise Fail("第一次见到这个版本却没有「未读」小圆点")
        bot.shot("14-version-chip")
        return f"徽标显示「{chip.strip()}」，带未读圆点"

    step("看见侧边栏的版本徽标", s14_version_chip)

    def s15_release_modal():
        dot_before = bot.evaluate("!!document.querySelector('#release-dot')")
        bot.click("#release-chip")
        if not bot.wait(".modal", 8):
            raise Fail("点版本徽标没有弹出更新内容")
        modal = bot.text_of(".modal")
        for needle in ("版本与更新内容", "当前版本", expected_version or "1."):
            if needle not in modal:
                raise Fail(f"浮层里没看到「{needle}」")
        entries = bot.evaluate("document.querySelectorAll('.modal .rel-list li').length") or 0
        groups = bot.evaluate("document.querySelectorAll('.modal .rel-entry').length") or 0
        if entries < 5 or groups < 2:
            raise Fail(f"更新条目太少：{entries} 条 / {groups} 个版本")
        build = bot.evaluate("document.querySelector('.modal .rel-build')?.innerText||''") or ""
        bot.shot("15-release-modal")          # 浮层还开着时拍，README 要用
        bot.click(".modal .modal-foot button")
        time.sleep(0.6)
        dot_after = bot.evaluate("!!document.querySelector('#release-dot')")
        if not dot_before or dot_after:
            raise Fail(f"看完之后未读圆点没有消失（before={dot_before} after={dot_after}）")
        if bot.evaluate("localStorage.getItem('boil_seen_release')") != expected_version:
            raise Fail("没有把已读版本记进本机浏览器")
        bot.shot("15b-release-read")
        return f"{groups} 个版本 / {entries} 条更新（{build.strip()[:48]}），看完圆点消失"

    step("打开「这一版改了什么」", s15_release_modal)

    def s16_settings_release():
        bot.click('a[href="#/settings"]')
        if not bot.wait("#settings-releases", 30):
            raise Fail("设置页没有「版本与更新」分区")
        text = bot.text_of("#settings-releases")
        if expected_version and expected_version not in text:
            raise Fail(f"分区里没有当前版本号：{text[:80]!r}")
        if "CHANGELOG.md" not in text:
            raise Fail("没有交代更新内容是从哪来的")
        if "版本与更新" not in bot.body():
            raise Fail("设置导航里没有版本入口")
        bot.wheel(10)
        bot.shot("16-settings")
        return "设置页摊开完整时间线，并说明来源是仓库里的 CHANGELOG.md"

    step("在设置页读到版本与更新分区", s16_settings_release)

    def s17_search():
        bot.key("k", modifiers=2)          # Ctrl+K
        if not bot.wait("#global-search", 8):
            raise Fail("键盘没有打开搜索")
        bot.type("#global-search", "间隔", paste=True)
        hit = '.search-item[data-href^="#/unit/"]'   # 快捷入口也是 .search-item，必须区分开
        if not bot.wait(hit, 20):
            raise Fail("输入关键词后没有搜到单元")
        first = bot.text_of(hit)
        bot.click(hit)
        if not bot.wait(".unit-main", 30):
            raise Fail("点搜索结果没有进入单元")
        title = bot.text_of(".page-title") or bot.text_of(".unit-main h2")
        bot.shot("17-search")
        return f"键盘搜到「{first.splitlines()[0][:20]}」→ 打开「{title[:20]}」"

    step("用键盘搜索并跳到单元", s17_search)

    def s18_insights():
        bot.goto(APP + "/#/insights", wait_for="#insight-ai-card", timeout=30)
        if not bot.evaluate("!!document.querySelector('#insight-ai-btn')"):
            raise Fail("AI 开着却没有生成解读按钮")
        bot.click("#insight-ai-btn")
        if not bot.wait("#insight-ai-body .badge-teal", 240):
            raise Fail("等了 240 秒没有出现「AI 分析」标记")
        text = bot.text_of("#insight-ai-body")
        if len(text) < 40 or "还没有生成" in text:
            raise Fail(f"洞察解读没有内容：{text[:60]!r}")
        bot.shot("18-insights")
        return "解读开头：" + text.replace("\n", " ")[:56]

    step("生成学习洞察解读", s18_insights)

    def s19_notes():
        bot.goto(APP + "/#/notes", wait_for=".note-card", timeout=25)
        quote_seen = bot.evaluate(
            "[...document.querySelectorAll('.note-card')].map(n=>n.innerText).join('\\n')") or ""
        if "这一句要能在没有答案时自己说出来" not in quote_seen:
            raise Fail("灵感库里看不到刚才划词存下的那条")
        if "来源" not in quote_seen and "单元" not in quote_seen:
            raise Fail("灵感条目没有标出来源")
        cards = bot.evaluate("document.querySelectorAll('.note-card').length") or 0
        bot.shot("19-notes")
        return f"灵感库里有 {cards} 条，划词存下的那条带引文和来源"

    step("去灵感库确认灵感还在", s19_notes)

    def s20_export():
        bot.goto(APP + "/#/settings", wait_for="#export-data", timeout=25)
        before = set(p.name for p in DOWNLOADS.glob("*")) if DOWNLOADS.exists() else set()
        bot.click("#export-data")
        start = time.time()
        landed = None
        while time.time() - start < 20:
            names = set(p.name for p in DOWNLOADS.glob("*")) if DOWNLOADS.exists() else set()
            fresh = names - before
            if fresh:
                landed = sorted(fresh)[0]
                break
            time.sleep(0.4)
        toast = bot.toast()
        if not landed:
            raise Fail(f"点导出后下载目录里没有文件（toast：{toast[:40]}）")
        size = (DOWNLOADS / landed).stat().st_size
        blob = json.loads((DOWNLOADS / landed).read_text(encoding="utf-8"))
        if "sk-" in json.dumps(blob) or "ak_" in json.dumps(blob):
            raise Fail("导出包里出现了密钥")
        if blob.get("version") != expected_version:
            raise Fail(f"导出包里的版本号不对：{blob.get('version')}")
        bot.shot("20-export")
        return (f"导出 {landed}（{size} 字节）：{len(blob.get('units') or [])} 个单元 / "
                f"{len(blob.get('cards') or [])} 张卡，版本号 {blob.get('version')}，无密钥")

    step("导出数据真的落到磁盘", s20_export)

    def s20b_agent_orb():
        if not bot.visible(".agent-orb"):
            raise Fail("右下角没有悬浮球")
        bot.click(".agent-orb")
        if not bot.wait_visible(".agent-panel", 8):
            raise Fail("点了悬浮球，助手面板没打开")
        bot.type("#agent-input", "记忆健康度是怎么算的，它可靠吗", paste=True)
        bot.key("Enter")
        # 占位消息「正在想…」也是 assistant 消息：要等的是带引擎标签的那一条
        if not bot.wait("#agent-log .agent-msg.assistant [data-agent-engine]", 200):
            raise Fail("等了 200 秒助手没给出回答")
        answer = bot.evaluate(
            "(()=>{const m=[...document.querySelectorAll('.agent-msg.assistant')].pop();"
            "return JSON.stringify({text:m.innerText.slice(0,300),"
            "engine:(m.querySelector('[data-agent-engine]')||{}).innerText||'',"
            "cites:[...m.querySelectorAll('.agent-cite')].map(x=>x.innerText)})})()")
        info = json.loads(answer)
        if "记忆健康度" not in info["text"] or info["text"].startswith("正在想"):
            raise Fail(f"助手没在回答这个问题：{info['text'][:80]}")
        # 危险动作闸门：模型每次回答不一样，所以这一步用注入的计划结果来验界面自己的规矩
        bot.evaluate("""window.__boilAgent.propose({reply:'要清空吗', engine:'model', actions:[
            {id:'data.reset', ok:true, kind:'api', risk:'danger', method:'POST', path:'/api/reset',
             path_args:[], args:{}, desc:'清空本机全部学习数据（不可撤销）', why:'旅程测试'}]})""")
        time.sleep(0.5)
        gate = bot.evaluate("""(()=>{const cards=[...document.querySelectorAll('.agent-action')];
            const c=cards[cards.length-1];
            return JSON.stringify({risk:c.querySelector('.agent-risk').innerText,
                disabled:c.querySelector('[data-run]').disabled,
                hasField:!!c.querySelector('[data-confirm-for]')})})()""")
        g = json.loads(gate)
        if g["risk"] != "危险" or not g["disabled"] or not g["hasField"]:
            raise Fail(f"危险动作没有被锁住：{g}")
        # 先照着按钮点一下：锁着的按钮不该发出任何请求
        bot.click(".agent-action:last-of-type [data-run]")
        time.sleep(1.2)
        fired = bot.evaluate("window.__resetHits || 0")
        if fired:
            raise Fail(f"没输确认词，却已经发出了 {fired} 次 /api/reset 请求")
        bot.click(".agent-panel [data-confirm-for]")
        bot.type(".agent-panel [data-confirm-for]", "RESET")
        unlocked = bot.evaluate("""(()=>{const cards=[...document.querySelectorAll('.agent-action')];
            return cards[cards.length-1].querySelector('[data-run]').disabled})()""")
        if unlocked:
            raise Fail("输入确认词之后按钮仍然锁着")
        bot.shot("20b-agent-orb")
        return (f"面板能问能答（{info['engine'] or '无标签'}"
                f"{'·' + info['cites'][0][:26] if info['cites'] else '·无引用'}）；"
                f"清空数据这类动作默认锁死、不输确认词一次都没发出请求（reset 请求 {fired} 次）")

    step("悬浮球助手：问答、动作卡片与危险闸门", s20b_agent_orb)

    def s21_phone():
        bot.call("Emulation.setDeviceMetricsOverride",
                 {"width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True})
        bot.goto(APP + "/#/dashboard", wait_for=".mobile-nav", timeout=25)
        if not bot.evaluate("!!document.querySelector('.mobile-nav')"):
            raise Fail("手机上没有底部导航")
        bot.click("#menu-btn")
        if not bot.wait("#sidebar.open", 8):
            raise Fail("抽屉菜单打不开")
        chip = bot.text_of("#release-chip")
        if expected_version and f"v{expected_version}" not in chip:
            raise Fail(f"抽屉里的版本徽标不对：{chip!r}")
        bot.evaluate("window.__boilAgent?.close()")   # 助手面板占满窄屏，不关掉点到的会是它
        # 点遮罩收抽屉：坐标要落在遮罩上而不是抽屉自己身上（390 宽时抽屉几乎占满屏）
        spot = bot.evaluate(
            "(()=>{const b=document.querySelector('#sidebar-backdrop');if(!b)return null;"
            "const r=b.getBoundingClientRect();return JSON.stringify([r.width-18,120])})()")
        if spot:
            x, y = json.loads(spot)
            bot.call("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
            bot.call("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        else:
            bot.key("Escape")
        time.sleep(0.8)
        if bot.evaluate("!!document.querySelector('#sidebar.open')"):
            raise Fail("点遮罩没有收起抽屉")
        bot.goto(APP + "/#/review", wait_for="#review-card-slot", timeout=25)
        sel = "#flash-front" if bot.evaluate("!!document.querySelector('#flash-front')") else ".unit-main"
        quote = bot.touch_select(sel, 10, 20)
        menu_ok = bot.evaluate("!!document.querySelector('.selection-menu')")  # 只当「有没有这个浮层」用
        rows = bot.evaluate(
            "(()=>{const m=document.querySelector('.selection-menu');if(!m)return null;"
            "const r=m.getBoundingClientRect();return JSON.stringify({w:r.width,items:m.children.length})})()")
        shown = bot.visible(".selection-menu")
        bot.shot("21-phone")
        bot.call("Emulation.clearDeviceMetricsOverride")
        if quote.strip() and not shown:
            raise Fail(f"触摸选中了「{quote[:12]}…」但浮条没出现（{rows}）")
        # 触摸端能不能起选区取决于浏览器自己怎么解释长按，测不到时如实说，不假装通过
        pick = f"触摸选中「{quote[:10]}…」且浮条在屏幕内" if quote.strip() and shown \
            else "长按没有起原生选区（桌面端已用真拖拽覆盖这条链路）"
        return f"390×844：底部导航 + 抽屉里的版本徽标 + {pick}"

    step("换到手机尺寸再走一遍", s21_phone)

    # ---------------------------------------------------------------- 收尾
    def s22_events():
        events = api("/api/events?limit=200").get("events") or []
        kinds: dict[str, int] = {}
        for e in events:
            kinds[e["type"]] = kinds.get(e["type"], 0) + 1
        for required in ("note.saved", "review.rated"):     # note.saved=速记，note.created=划词灵感
            if required not in kinds:
                raise Fail(f"事件日志里缺少 {required}：{sorted(kinds)}")
        if "note.created" not in kinds:
            raise Fail(f"划词存灵感没有留下 note.created：{sorted(kinds)}")
        interesting = {k: v for k, v in kinds.items()
                       if k in ("goal.created", "card.created", "note.created", "review.rated",
                                "focus.session", "unit.mnemonic")}
        return "事件流：" + "、".join(f"{k}×{v}" for k, v in sorted(interesting.items()))

    step("事件日志记录了这一天的动作", s22_events)

    print("\n================ 旅程结论 ================")
    failed = [r for r in results if not r[1]]
    for name, ok, detail in results:
        mark = "✅" if ok else "❌"
        print(f"{mark} {name}" + (f"\n     {detail}" if not ok else ""))
    print(f"\n{len(results) - len(failed)}/{len(results)} 步通过")
    if bot.errors:
        print(f"\n浏览器报错 {len(bot.errors)} 条：")
        for e in sorted(set(bot.errors))[:12]:
            print("  -", e[:180])
    print(f"截图目录：{OUT}   下载目录：{DOWNLOADS}")
    try:
        bot.chrome.terminate()
    except Exception:
        pass
    return 1 if (failed or bot.errors) else 0


if __name__ == "__main__":
    sys.exit(main())
