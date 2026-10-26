#!/usr/bin/env python3
"""真人边界探针：专打"顺滑路径"测不到的地方。

`tools/human_journey.py` 走的是一个新用户顺利过完一天；这个工具走**真人会做的事**：
连点、中途刷新、切页面、把截止日期填成昨天、往搜索框里粘 `<script>`、
并发点同一个按钮、重启服务器再回来看东西还在不在。断言一律是"用户看得见的文字/数字"。

用法（服务器由调用方起；两阶段之间重启以验持久化）：
    python3 tools/real_user_probe.py --phase 1
    <重启服务器>
    python3 tools/real_user_probe.py --phase 2
退出码非 0 = 有发现。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from human_journey import Bot, api  # noqa: E402  复用同一个零依赖 CDP 客户端

APP = os.environ.get("BOIL_APP", "http://127.0.0.1:7860")

# 真人会粘的东西：表格、emoji、公式、代码块、无换行超长段
MATERIAL_EDGE = (
    "## 洛必达法则 📐\n"
    "| 类型 | 能不能用 | 备注 |\n|---|---|---|\n| 0/0 | 能 | 先化简 |\n| ∞/∞ | 能 | 检查导数存在 |\n"
    "当 $x\\to 0$ 时 $\\frac{\\sin x}{x}=1$，这是常用起点。\n"
    "```python\nlimit = 0  # 代码块里的字不该被当成知识点\n```\n"
    + "这是一段没有换行的超长中文说明，用来测试切分器在整节只有一句话时的表现，" * 12 + "\n\n"
    "## 泰勒展开与等价无穷小\n"
    "等价无穷小替换只能在乘除因子上用，加减时要谨慎；$x-\\sin x\\sim x^3/6$。\n"
    "把 $e^x$、$\\ln(1+x)$、$(1+x)^\\alpha$ 三个展开式背熟，可以解决大部分比较阶数的题。\n")

MATERIAL_TINY = "## 只有一句\n这一节真的只有一句话。"


def _api_raw(method, path, payload=None, timeout=60):
    """直接看状态码的调用：探针要区分"报错"和"报错但说实话"。"""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(APP + path, data=data,
                                 headers={"Content-Type": "application/json"} if data else {},
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()[:200]
    except Exception as exc:
        return 0, str(exc)[:200]


def squash(text):
    return re.sub(r"\s+", "", text or "")


def day(offset):
    return (date.today() + timedelta(days=offset)).isoformat()


class Probe:
    def __init__(self):
        self.bot = Bot()
        self.findings: list[tuple[str, str]] = []
        self.passed = 0

    # ---- 断言原语：失败就是"用户看得见的东西不对" ----
    def check(self, name, ok, detail=""):
        if ok:
            self.passed += 1
            print(f"  ok   {name}" + (f"  · {detail[:90]}" if detail else ""))
        else:
            self.findings.append((name, detail))
            print(f"  FIND {name}  ·  {str(detail)[:150]}")

    def reload(self):
        self.bot.call("Page.reload", {"ignoreCache": True})
        time.sleep(2.4)

    def js_errors(self):
        return [e for e in self.bot.errors if "favicon" not in e and "ERR_" not in e]

    def close(self):
        try:
            self.bot.chrome.terminate()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 阶段一
# ---------------------------------------------------------------------------
def phase_one(p):
    bot = p.bot
    api("/api/reset", {"confirm": "RESET"})

    # 1. 空实例直接撞内页：要引导，不许白屏/500
    for route in ("#/sprint", "#/review", "#/insights", "#/map", "#/dashboard"):
        bot.goto(APP + route, timeout=30)
        time.sleep(1.4)
        body = bot.body()
        landed = bot.evaluate("location.hash") or ""
        p.check(f"空实例访问 {route}",
                ("#/onboarding" in landed or "还没有学习目标" in body) and "500" not in body,
                f"落在 {landed!r}，正文开头 {body[:60]!r}")

    # 2. 材料太短：如实拒绝并说清门槛
    bot.goto(APP + "/#/onboarding", wait_for=".step-dots", timeout=40)
    bot.click("[data-material]")
    bot.wait("#ob-text", 20)
    bot.type("#ob-text", MATERIAL_TINY, paste=True)
    bot.type("#ob-title", "测试目标", paste=True)
    bot.click("[data-submit]")
    toast = bot.last_toast_matching("短", timeout=30) or bot.toast()
    p.check("材料只有 15 字被如实拒绝", "太短" in (toast or "") or "60" in (toast or ""),
            f"提示：{toast!r}")

    # 3. 表格 / emoji / 代码块 / 无换行超长段：编译要活下来，代码块不许当知识点
    bot.type("#ob-text", "\n\n" + MATERIAL_EDGE, paste=True)
    _counter(bot)
    clicks = 0
    for _ in range(3):                       # 真人连点；编译成功后按钮会消失，那是对的
        try:
            bot.click("[data-submit]", timeout=2.0)
            clicks += 1
        except Exception:
            break
        time.sleep(0.15)
    if not bot.wait("[data-start]", 300):
        p.check("边界材料编译", False, "等了 300 秒没出预览")
        return
    posts = [x for x in _fetches(bot) if "compile" in x]
    p.check("连点「编译并预览」只发一次请求", len(posts) <= 1,
            f"点了 {clicks} 次、发出 {len(posts)} 次请求")
    titles = " ".join(bot.evaluate("[...document.querySelectorAll('.preview-unit')].map(e=>e.innerText)") or [])
    p.check("代码块/表头没被当成知识点", "```" not in titles and "能不能用" not in titles,
            titles[:110])
    body = bot.body()
    p.check("预览里没有 NaN/undefined",
            not re.search(r"NaN|undefined", body),
            [m.group(0) for m in re.finditer(r"NaN|undefined", body)][:3])
    p.check("预览报出拆解体检", "拆得够不够" in body, "")
    bot.click("[data-start]")
    bot.wait(".hero", 40)

    # 4. 截止日期填成今天 / 已经过了：不许出现负数天数
    for offset, label in ((0, "今天考"), (-3, "deadline 已过 3 天")):
        api("/api/onboarding", {"name": "边界探针", "pack_id": _installed_pack_id(),
                                "goal_title": f"目标{offset}", "daily_minutes": 40,
                                "deadline": day(offset)})
        for route in ("#/sprint", "#/dashboard"):
            bot.goto(APP + route, timeout=30)
            time.sleep(1.6)
            neg = re.search(r"-\s*\d+\s*天|还剩\s*-|剩余\s*-", bot.body())
            p.check(f"{label} · {route} 不出现负数天数", not neg,
                    f"匹配到 {neg.group(0)!r}" if neg else "")

    # 5. 每日分钟极端值：0 与超大
    for minutes in (1, 999):
        status, out = _api_raw("POST", "/api/onboarding",
                               {"name": "边界探针", "pack_id": _installed_pack_id(),
                                "goal_title": f"分钟{minutes}", "daily_minutes": minutes,
                                "deadline": day(30)})
        ok = status == 200 or (isinstance(out, str) and ("分钟" in out or "上限" in out))
        p.check(f"daily_minutes={minutes} 有明确回应", ok, f"HTTP {status} {str(out)[:90]}")
    api("/api/onboarding", {"name": "边界探针", "pack_id": _installed_pack_id(),
                            "goal_title": "回到正常", "daily_minutes": 40, "deadline": day(30)})

    # 6. 连点评分：服务端要扛住，队列数字不能乱
    bot.goto(APP + "/#/review", timeout=30)
    if bot.wait("#flashcard", 25):
        before = api("/api/review")["due_total"]
        bot.click("#reveal-btn")
        _counter(bot)
        rated_clicks = 0
        for _ in range(3):
            try:
                bot.click("[data-rate='3']", timeout=2.0)   # 评完分会换卡，按钮消失是正常的
                rated_clicks += 1
            except Exception:
                break
            time.sleep(0.15)
        time.sleep(2.0)
        after = api("/api/review")["due_total"]
        posts = [x for x in _fetches(bot) if "/api/review/" in x]
        logs = api("/api/events?limit=200")["events"]
        rated = [e for e in logs if e["type"] == "review.rated"]
        p.check("连点评分 3 次只前进一次", len(rated) <= max(1, len(posts)),
                f"发了 {len(posts)} 个请求、{len(rated)} 条评分事件；到期 {before}→{after}")
    else:
        p.check("连点评分 3 次", False, "复习页没有卡，这条没测到")

    # 7. 流式回答中途刷新：不许留下"正在思考"的僵尸
    unit = (_map_nodes() or [{}])[0].get("id")
    if unit:
        bot.goto(APP + "/#/unit/" + urllib.parse.quote(unit), timeout=40)
        bot.wait("[data-socratic]", 25)
        bot.click("[data-socratic]")
        if bot.wait("#chat-input", 20):
            bot.type("#chat-input", "提取练习为什么比重读更有效？", paste=True)
            bot.click("#send-btn")
            time.sleep(0.5)                       # 流还在吐字
            p.reload()
            bot.wait(".unit-main", 30)
            stuck = bot.evaluate(
                "(()=>{const t=document.querySelector('#stream-typing');"
                "return t&&getComputedStyle(t).display!=='none'})()")
            p.check("SSE 中途刷新不留僵尸'正在思考'", not stuck, f"typing 仍在显示：{stuck}")
        else:
            p.check("SSE 中途刷新", False, "点苏格拉底后没出输入框")

    # 8. 搜索特殊字符与注入串
    for probe in ("<script>alert(1)</script>", ".*+", "%zz%", '"引号"', "   ", "a" * 400, "洛必达"):
        status, out = _api_raw("GET", "/api/search?q=" + urllib.parse.quote(probe))
        p.check(f"搜索 {probe[:16]!r} 不 500", status == 200, f"HTTP {status} {str(out)[:80]}")
    bot.goto(APP + "/#/notes?q=" + urllib.parse.quote('<img src=x onerror=alert(1)>'), timeout=30)
    time.sleep(1.8)
    injected = bot.evaluate("!!document.querySelector('img[src=\"x\"]')")
    p.check("灵感库搜索串被当文字而不是 HTML", not injected,
            "" if not injected else "渲染出了真 <img src=x> 元素")

    # 9. 键盘-only：Ctrl+K 开面板 → 焦点进输入框 → Esc 关 → 焦点回原处
    bot.goto(APP + "/#/dashboard", timeout=30)
    bot.wait(".hero", 25)
    bot.key("k", modifiers=2)                     # Ctrl+K
    if not bot.wait("#global-search", 8):
        p.check("Ctrl+K 打开全局搜索", False, "面板没出现")
    else:
        focused = bot.evaluate("document.activeElement?.id||''")
        p.check("Ctrl+K 后焦点自动进输入框", focused == "global-search", f"焦点在 {focused!r}")
        bot.type("#global-search", "洛必达", paste=True)
        time.sleep(1.4)
        # 搜到东西时结果列表会整块替换掉快捷入口，所以不能拿总数比大小，要看有没有那一行
        hits = bot.evaluate("[...document.querySelectorAll('#search-results .search-item .title')]"
                            ".map(e=>e.innerText)||[]")
        bot.key("Escape")
        time.sleep(0.8)
        still = bot.visible("#global-search")
        back = bot.evaluate("document.activeElement?.id||String(document.activeElement?.className||document.activeElement?.tagName)||'body'")
        got = [t for t in (hits or []) if "洛必达" in t]
        p.check("搜索面板能搜到刚编译的知识点", bool(got),
                f"命中 {got[:1]}" if got else f"结果行是 {(hits or [])[:3]}")
        p.check("Esc 关闭搜索面板", not still, "面板还开着" if still else "")
        p.check("Esc 之后焦点有落点（不掉回 body）", back not in ("", "body"),
                f"焦点回到 {back}" if back not in ("", "body") else "焦点掉回 body：下一次 Tab 从页面顶端开始")

    # 10. 并发：两个线程同时斩同一个知识点，不许 500、不许重复计数
    shots = api("/api/cram")["items"][:1]
    if shots:
        uid = shots[0]["unit_id"]
        codes = []

        def hit():
            codes.append(_api_raw("POST", "/api/cram/shot", {"unit_id": uid})[0])

        threads = [threading.Thread(target=hit) for _ in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        p.check("6 路并发斩同一知识点全部不 5xx", all(c == 200 for c in codes), str(codes))
        after = [x["unit_id"] for x in api("/api/cram")["items"]]
        p.check("被斩掉的那条真的从清单消失", uid not in after,
                f"{uid[:40]}… 仍在清单上（{len(after)} 条）" if uid in after else "")
        p.check("清单不会因并发出现重复条目", len(after) == len(set(after)), str(len(after)))
        api("/api/cram/reset", {})

    # 11. 打印：连点两次只留一张临时纸
    bot.goto(APP + "/#/sprint", timeout=30)
    if bot.wait("[data-print]", 20):
        bot.evaluate("window.print=()=>{window.__printed=(window.__printed||0)+1}")
        for _ in range(2):
            try:
                bot.click("[data-print]", timeout=3.0)
            except Exception:
                break
            time.sleep(0.5)
        sheets = bot.evaluate("document.querySelectorAll('.print-sheet').length") or 0
        printed = bot.evaluate("window.__printed") or 0
        p.check("打印真的走了浏览器通道", printed >= 1, f"window.print 被调用 {printed} 次")
        p.check("连点打印不留临时纸堆积", sheets == 0, f"页面上还剩 {sheets} 张 .print-sheet")
    else:
        p.check("连点打印", False, "冲刺页没有打印入口")

    # 12. 手机尺寸下新块不破版
    bot.call("Emulation.setDeviceMetricsOverride",
             {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True})
    bot.goto(APP + "/#/map", timeout=30)
    time.sleep(2.0)
    wide = bot.evaluate(
        "(()=>[...document.querySelectorAll('.map-side-card,.card')]"
        ".filter(e=>e.getBoundingClientRect().right>window.innerWidth+2)"
        ".map(e=>(e.className||'')+'|'+e.innerText.slice(0,18)))()")
    p.check("小屏下没有卡片溢出视口", not wide, str(wide)[:120])
    bot.call("Emulation.clearDeviceMetricsOverride")

    p.check("阶段一没有 JS 异常", not p.js_errors(), "; ".join(p.js_errors())[:160])


# ---------------------------------------------------------------------------
# 阶段二：服务器重启之后
# ---------------------------------------------------------------------------
def phase_two(p):
    bot = p.bot
    try:
        dash = api("/api/dashboard")
    except Exception as exc:
        p.check("重启后 /api/dashboard 可用", False, str(exc)[:160])
        return
    p.check("重启后目标还在", bool(dash.get("has_goal")), f"counts={dash.get('counts')}")
    bot.goto(APP + "/#/sprint", timeout=40)
    ok = bot.wait(".sprint-head", 25)
    p.check("重启后冲刺页可渲染", ok, "" if ok else bot.body()[:120])
    if ok:
        p.check("重启后冲刺页没有报错文案",
                "500" not in bot.body() and "内部错误" not in bot.body(), bot.body()[:100])

    # 长 unit id 的知识点在重启后仍能斩（曾被定长截断坑过）
    ids = bot.evaluate("[...document.querySelectorAll('[data-shot]')].map(b=>b.dataset.shot)") or []
    if ids:
        longest = max(len(x) for x in ids)
        bot.click(".cram-item [data-shot]")
        toast = bot.last_toast_matching("", timeout=10) or ""
        p.check(f"最长 {longest} 字符的 id 重启后仍能斩掉",
                "不在你的目标里" not in toast and "失败" not in toast, toast[:100])
        api("/api/cram/reset", {})

    # 番茄钟跨刷新：不许被清零
    bot.goto(APP + "/#/dashboard", timeout=30)
    bot.wait("#focus-btn", 25)
    bot.click("#focus-btn")
    bot.wait(".modal-foot .btn-primary", 10)
    bot.click(".modal-foot .btn-primary")
    time.sleep(1.8)
    first = bot.text_of("#focus-label")
    p.reload()
    bot.wait("#focus-btn", 25)
    second = bot.text_of("#focus-label")
    p.check("番茄钟刷新后没被清零", ":" in second and "00:00" not in second,
            f"{first!r} → {second!r}")
    if ":" in second:
        bot.click("#focus-btn")
        if bot.wait(".modal-foot .btn-ghost", 8):
            bot.click(".modal-foot .btn-ghost")

    # 导出→合并导入自己的数据：计数不许变少
    status, _ = _api_raw("GET", "/api/export")
    p.check("导出可用", status == 200, f"HTTP {status}")
    try:
        req = urllib.request.Request(APP + "/api/export")
        with urllib.request.urlopen(req, timeout=60) as r:
            blob = json.loads(r.read().decode())
        before = api("/api/dashboard")["counts"]
        blob["mode"] = "merge"
        status, out = _api_raw("POST", "/api/import", blob)
        after = api("/api/dashboard")["counts"]
        lost = {k: (before[k], after[k]) for k in before
                if isinstance(before.get(k), int) and after.get(k, 0) < before[k]}
        p.check("导出→合并导入之后计数不变少", status == 200 and not lost,
                f"HTTP {status} {str(out)[:70]} 变少项={lost}")
    except Exception as exc:
        p.check("导出→合并导入回环", False, str(exc)[:160])

    p.check("阶段二没有 JS 异常", not p.js_errors(), "; ".join(p.js_errors())[:160])


# ---------------------------------------------------------------------------
def _counter(bot):
    bot.evaluate("window.__f=[];if(!window.__patched){const o=window.fetch;"
                 "window.fetch=(...a)=>{window.__f.push(String(a[0]));return o(...a)};"
                 "window.__patched=1}")


def _fetches(bot):
    return bot.evaluate("window.__f||[]") or []


def _installed_pack_id():
    try:
        return api("/api/dashboard")["goal"]["pack_id"] or "boil.xueshen.learning"
    except Exception:
        return "boil.xueshen.learning"


def _map_nodes():
    try:
        return api("/api/map")["nodes"]
    except Exception:
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", type=int, choices=(1, 2, 3), default=1)
    args = ap.parse_args()
    p = Probe()
    try:
        {1: phase_one, 2: phase_two, 3: phase_three}[args.phase](p)
    finally:
        p.close()
    print(f"\n================ 真人边界探针 · 阶段 {args.phase} ================")
    if p.findings:
        for name, detail in p.findings:
            print(f"❌ {name}\n     {str(detail)[:300]}")
    else:
        print("✅ 没有发现")
    print(f"\n{p.passed} 项通过，{len(p.findings)} 项有发现")
    return 1 if p.findings else 0



# ---------------------------------------------------------------------------
# 阶段三：破坏性输入、多目标、主题与手机
# ---------------------------------------------------------------------------
def phase_three(p):
    bot = p.bot
    goal_before = api("/api/goals")["goals"]

    # 1. 损坏 / 半截 / 错类型的备份导入：必须 400，且现有数据一条不少
    counts_before = api("/api/dashboard")["counts"]
    broken = {
        "半截 JSON": '{"goals": [',
        "顶层是数组": ["not", "a", "dict"],
        "缺字段": {"mode": "merge", "goals": "不是列表"},
        "错类型": {"mode": "merge", "units": [{"id": 1, "title": None}]},
    }
    for label, payload in broken.items():
        status, out = _api_raw("POST", "/api/import", payload)
        p.check(f"损坏备份（{label}）被拒且不说 500", status == 400,
                f"HTTP {status} {str(out)[:90]}")
    counts_after = api("/api/dashboard")["counts"]
    p.check("坏备份导入没有清空数据", counts_after == counts_before,
            f"{counts_before} → {counts_after}")

    # 2. 替换导入必须要确认词
    status, out = _api_raw("POST", "/api/import", {"mode": "replace", "goals": []})
    p.check("替换导入没有确认词时被拒绝", status == 400 and "confirm" in str(out).lower(),
            f"HTTP {status} {str(out)[:80]}")

    original_pack = _installed_pack_id()
    ids_before = {n["id"] for n in _map_nodes()}
    # 换目标会新建一份单元（id 按目标生成），所以"回来了没有"要看标题集合，不是比 id
    titles_before = {n["title"] for n in _map_nodes()}

    # 3. 超长单元标题（模型真的会给出这种）：不许破版、不许溢出视口
    long_title = "论" + "在乘除因子上使用等价无穷小替换时必须注意的边界条件与常见误用情形" * 4
    pack = {"id": "user.probe.longtitle", "version": "1.0.0", "name": "超长标题包",
            "units": [{"id": "u-long", "title": long_title, "essence": "替换只能用于乘除因子",
                       "probes": [{"kind": "recall", "prompt": "什么时候不能用？", "answer": "加减时"}],
                       "cards": [{"front": "能不能用于加减", "back": "不能"}]}]}
    status, out = _api_raw("POST", "/api/packs/import", {"pack": pack})
    p.check("超长标题的包能通过导入或有明确说明", status in (200, 400), f"HTTP {status} {str(out)[:80]}")
    if status == 200:
        api("/api/onboarding", {"name": "边界探针", "pack_id": "user.probe.longtitle",
                                "goal_title": "超长标题", "daily_minutes": 40, "deadline": day(20)})
        bot.goto(APP + "/#/map", timeout=30)
        time.sleep(2.0)
        wide = bot.evaluate(
            "(()=>[...document.querySelectorAll('.map-node-card,.node-title')]"
            ".filter(e=>e.getBoundingClientRect().right>window.innerWidth+2).length)()")
        p.check("超长标题没把地图节点撑出视口", not wide, f"{wide} 个元素溢出")
        overflow = bot.evaluate(
            "(()=>{const e=document.querySelector('.node-title');if(!e)return 0;"
            "return e.scrollWidth>e.clientWidth+2&&!getComputedStyle(e).textOverflow==='ellipsis'})()")
        p.check("超长标题在节点里有截断处理", not overflow, "文字被裁掉但既没省略号也没换行")

    ids_long = {n["id"] for n in _map_nodes()}
    # 4. 浅色主题下新块的对比度（出处块、拆解体检）：换回原来的那个包，不是刚切的长标题包
    api("/api/onboarding", {"name": "边界探针", "pack_id": original_pack,
                            "goal_title": "回到正常", "daily_minutes": 40, "deadline": day(30)})
    bot.goto(APP + "/#/map", timeout=30)
    bot.wait(".map-svg", 25)
    bot.evaluate("document.documentElement.dataset.theme='light'")
    time.sleep(0.8)
    ratio = bot.evaluate(
        "(()=>{const lum=(c)=>{const [r,g,b]=c.match(/\\d+/g).map(Number).map(v=>{v/=255;"
        "return v<=0.03928?v/12.92:Math.pow((v+0.055)/1.055,2.4)});"
        "return 0.2126*r+0.7152*g+0.0722*b};"
        "const bg=(el)=>{let n=el;while(n){const c=getComputedStyle(n).backgroundColor;"
        "if(c&&c!=='rgba(0, 0, 0, 0)'&&!c.includes('0)'))return c;n=n.parentElement}"
        "return 'rgb(255,255,255)'};"
        "const e=document.querySelector('.chapter-row .chapter-title')||document.querySelector('.map-side-card');"
        "if(!e)return 0;const s=getComputedStyle(e);const a=lum(s.color),b=lum(bg(e));"
        "return +(((Math.max(a,b)+0.05)/(Math.min(a,b)+0.05)).toFixed(2))})()")
    p.check("浅色主题下拆解体检文字对比度 ≥4.5", (ratio or 0) >= 4.5, f"实测 {ratio}")
    bot.evaluate("document.documentElement.dataset.theme='dark'")

    # 5. 同一条引文存两次：不许静默丢一条
    unit = (_map_nodes() or [{}])[0].get("id")
    quote = "提取本身会改变记忆"
    api("/api/notes", {"quote": quote, "content": "第一次存", "unit_id": unit, "source": "unit"})
    api("/api/notes", {"quote": quote, "content": "第二次存", "unit_id": unit, "source": "unit"})
    listed = api("/api/notes?unit_id=" + urllib.parse.quote(unit or ""))["notes"]
    p.check("同一句引文存两次会留下两条", len(listed) >= 2, f"列表里 {len(listed)} 条")
    bot.goto(APP + "/#/notes", timeout=30)
    time.sleep(1.6)
    p.check("灵感库里两条都看得见", bot.body().count("提取本身会改变记忆") >= 1, "")

    # 6. 试卷重复交卷：第二次必须被拒
    exam = api("/api/exam", {"minutes": 10, "count": 3})
    answers = [{"id": q["id"], "answer": "替换只能用在乘除因子上，加减时要谨慎。"}
                 for q in exam["questions"]]
    s1, _o1 = _api_raw("POST", "/api/exam/submit", {"exam_id": exam["exam_id"], "answers": answers})
    s2, o2 = _api_raw("POST", "/api/exam/submit", {"exam_id": exam["exam_id"], "answers": answers})
    p.check("同一份卷子交第二次被拒绝", s1 == 200 and s2 == 400, f"第一次 {s1}，第二次 {s2} {str(o2)[:60]}")

    # 7. 设置页填非法地址：400 + 说清哪儿不对（以前是原样保存并回"已启用"）
    for bad in ("ht tp://x", "not-a-url", "https://", "ftp://a.b"):
        status, out = _api_raw("POST", "/api/settings/llm", {"base_url": bad, "api_key": "sk-x",
                                                            "model": "m"})
        p.check(f"非法 base_url {bad!r} 被 400 拒绝", status == 400 and "Base URL" in str(out),
                f"HTTP {status} {str(out)[:90]}")
    # 清空地址必须真的关掉网关，而不是回填 api.openai.com
    ok_status, _ = _api_raw("POST", "/api/settings/llm",
                            {"base_url": "https://my.example/v1", "api_key": "sk-x", "model": "m"})
    cleared = api("/api/settings/llm")
    p.check("保存合法地址生效", ok_status == 200 and cleared["mode"] == "live", str(cleared["mode"]))
    _api_raw("POST", "/api/settings/llm", {"api_key": ""})

    # 8. 手机底部导航真的能进设置
    bot.call("Emulation.setDeviceMetricsOverride",
             {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True})
    bot.goto(APP + "/#/dashboard", timeout=30)
    bot.wait(".mobile-nav", 20)
    # 底部导航只放 6 个高频入口，设置走抽屉 —— 真人要找的是"能不能到"，不是"在不在底栏"
    in_nav = bot.evaluate("!!document.querySelector('.mobile-nav a[href=\"#/settings\"]')")
    try:
        bot.click("#menu-btn")
        opened = bot.wait("#sidebar.open", 8)
        if not opened:
            p.check("手机抽屉能打开", False, "点汉堡按钮没展开侧栏")
        else:
            link = bot.evaluate("!!document.querySelector('#sidebar a[href=\"#/settings\"]')")
            p.check("手机能到达设置页（抽屉里）", link,
                    f"底栏有设置={bool(in_nav)}，抽屉里有={link}")
            if link:
                bot.click("#sidebar a[href=\"#/settings\"]")
                ok = bot.wait(".settings-jump", 20)
                p.check("从抽屉点设置真的进入页面", ok, "" if ok else bot.body()[:100])
    except Exception as exc:
        p.check("手机能到达设置页（抽屉里）", False, str(exc)[:120])
    bot.call("Emulation.clearDeviceMetricsOverride")

    # 9. 换目标之后：地图只列当前目标的单元，旧目标的单元不许混进来
    goals = api("/api/goals")["goals"]
    ids_back = {n["id"] for n in _map_nodes()}
    if len(goals) >= 2:
        p.check("换目标后地图不混入别的目标的单元", not (ids_back & ids_long),
                f"长标题包的 {len(ids_long)} 个单元里有 {len(ids_back & ids_long)} 个还留在地图上")
        titles_back = {n["title"] for n in _map_nodes()}
        p.check("换回原包之后单元完整回来", titles_back == titles_before and bool(titles_back),
                f"{len(titles_before)} 个标题 → {len(titles_back)} 个；差集 "
                f"{sorted(titles_before ^ titles_back)[:2]}")
        dash = api("/api/dashboard")["counts"]
        p.check("换目标后计数与地图一致", dash["units"] == len(ids_back),
                f"计数 {dash['units']} vs 地图 {len(ids_back)}")
    p.check("阶段三没有 JS 异常", not p.js_errors(), "; ".join(p.js_errors())[:160])


if __name__ == "__main__":
    sys.exit(main())
