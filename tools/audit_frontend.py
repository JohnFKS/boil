#!/usr/bin/env python3
"""全站前端审计：路由 × 主题 × 视口 + 键盘可达 + 浏览器里的真 XSS 执行检查。

为什么要单独有这一个工具：
- Python 侧的测试只能证明"存下来的是原文"，证明不了"渲染出来不执行"。转义发生在前端，
  必须在真浏览器里塞 payload，看它到底有没有变成 DOM。
- 布局、焦点、点得着点不着，都是只有真渲染才暴露的问题：`querySelector` 能找到不等于看得见。

用法（需要先起一个被测实例）：
    BOIL_APP=http://127.0.0.1:7861 python3 tools/audit_frontend.py
退出码 0 = 没有 finding；非 0 = 有，明细打在最后一行并写进 screenshots/audit/summary.json。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import human_journey as hj          # noqa: E402
from human_journey import Bot, api  # noqa: E402

APP = os.environ.get("BOIL_APP", "http://127.0.0.1:7861")
hj.APP = APP
hj.PORT = int(os.environ.get("BOIL_CDP_PORT", "9347"))
hj.OUT = ROOT / "screenshots"
OUT = ROOT / "screenshots" / "audit"

VIEWPORTS = {"desktop": (1440, 1050, False), "tablet": (768, 1024, False),
             "phone": (390, 844, True)}
XSS = "__boilXssFired"

# 每个路由必须"存在且真鼠标点得着"的关键控件；缺一个就是产品问题，不是测试问题
KEY_CONTROLS = {
    "dashboard": ["#sidebar .brand", "[data-nav=dashboard]", "#agent-orb"],
    "map": ["#sidebar .brand", "[data-nav=map]", "#agent-orb"],
    "review": ["#sidebar .brand", "[data-nav=review]", "#agent-orb"],
    "library": ["#sidebar .brand", "[data-nav=library]", "#agent-orb"],
    "insights": ["#sidebar .brand", "[data-nav=insights]", "#agent-orb"],
    "notes": ["#sidebar .brand", "[data-nav=notes]", "#agent-orb"],
    "settings": ["#sidebar .brand", "[data-nav=settings]", "#agent-orb"],
    "unit": ["#sidebar .brand", "#agent-orb"],
    "onboarding": ["#agent-orb"],          # 开课向导没有侧栏，这是设计
}

PROBE = r"""
(() => {
  const css = (e) => getComputedStyle(e);
  const cls = (e) => (e.className && e.className.toString ? e.className.toString() : String(e.className || ""));
  const vis = (e) => {
    const s = css(e);
    if (s.display === "none" || s.visibility === "hidden" || parseFloat(s.opacity) === 0) return false;
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const textOf = (e) => ((e && e.textContent) || "").trim();
  // 可访问名按 WCAG 的算法近似算：aria-labelledby > aria-label > 包着的 label > label[for] > 自身文字 > alt/title
  // 不能用 value 或 placeholder 顶替：读屏不会把"填进去的内容"当成控件的名字。
  const accName = (e) => {
    const byId = e.getAttribute("aria-labelledby");
    if (byId) {
      const t = byId.split(/\s+/).map((id) => textOf(document.getElementById(id))).join(" ").trim();
      if (t) return t;
    }
    const al = (e.getAttribute("aria-label") || "").trim();
    if (al) return al;
    const wrap = e.closest("label");
    if (wrap && textOf(wrap)) return textOf(wrap);
    if (e.id) {
      const forLabel = document.querySelector(`label[for="${(window.CSS && CSS.escape) ? CSS.escape(e.id) : e.id}"]`);
      if (forLabel && textOf(forLabel)) return textOf(forLabel);
    }
    const img = e.querySelector && e.querySelector("img[alt]");
    if (img && img.alt) return img.alt;
    const own = ["BUTTON", "A"].includes(e.tagName) ? textOf(e) : "";
    // title 是规范里的最后一级兜底：只有图标的按钮靠它才叫得出名字
    return own || (e.getAttribute("title") || "").trim();
  };
  const SEL = 'a[href],button,input,select,textarea,[tabindex]:not([tabindex="-1"]),'
            + '[role="button"],[role="link"],[role="tab"],[contenteditable="true"]';
  const all = [...document.querySelectorAll(SEL)];
  const focusable = all.filter(vis);
  const desc = (e) => e.tagName.toLowerCase() + (e.type ? "[" + e.type + "]" : "")
    + (e.id ? "#" + e.id : "") + "." + cls(e).slice(0, 26);
  // 复选框常常只有 18px，但整条 label 都能点：按 WCAG 的"等效目标"算它合规
  const labelCovered = (e) => {
    const rectOf = (n) => (n ? n.getBoundingClientRect() : null);
    const wrap = rectOf(e.closest("label"));
    if (wrap && wrap.height >= 24 && wrap.width >= 20) return true;
    if (e.id) {
      const esc = (window.CSS && CSS.escape) ? CSS.escape(e.id) : e.id;
      const lab = rectOf(document.querySelector(`label[for="${esc}"]`));
      if (lab && lab.height >= 24 && lab.width >= 20) return true;
    }
    return false;
  };
  const small = innerWidth < 800
    ? focusable.filter((e) => { const r = e.getBoundingClientRect();
        return (r.height < 24 || r.width < 20) && !labelCovered(e); }).map(desc)
    : [];
  return {
    url: location.hash || "/",
    overflow: Math.max(0, document.documentElement.scrollWidth - innerWidth),
    wide: [...document.querySelectorAll("main *, header *, footer *")]
      .filter((e) => { const r = e.getBoundingClientRect();
        return vis(e) && css(e).position !== "fixed" && (r.right > innerWidth + 2 || r.left < -2); })
      .slice(0, 4).map(desc),
    focusable: focusable.length,
    unnamed: focusable.filter((e) => !accName(e)).map(desc),
    positiveTabindex: all.filter((e) => parseInt(e.getAttribute("tabindex") || "0", 10) > 0).length,
    imagesWithoutAlt: [...document.images].filter((i) => !i.hasAttribute("alt")).map((i) => i.src.slice(-40)),
    inlineHandlers: [...document.querySelectorAll("*")]
      .filter((e) => [...e.attributes].some((a) => a.name.startsWith("on"))).length,
    scripts: document.querySelectorAll("script").length,
    jsHref: document.querySelectorAll('a[href^="javascript:"]').length,
    injecteds: document.querySelectorAll('img[src="x"],iframe,object,embed').length,
    h1: document.querySelectorAll("h1").length,
    landmarks: ["header", "nav", "main"].filter((t) => document.querySelector(t)).length,
    liveRegion: !!document.querySelector("[aria-live],[role=status]"),
    lang: document.documentElement.getAttribute("lang"),
    title: document.title,
    smallTargets: small,
    textLength: (document.body.innerText || "").length,
  };
})();
"""


class Audit:
    def __init__(self):
        self.findings: list[dict] = []
        self.rows: list[dict] = []
        self.bot = Bot()

    # ---------- 基础设施 ----------
    def fail(self, where: str, message: str):
        self.findings.append({"where": where, "message": message})
        print(f"  ✗ {where}: {message}")

    def note(self, text: str):
        print(f"  · {text}")

    def evaluate(self, expr):
        return self.bot.evaluate(expr)

    def viewport(self, name: str):
        w, h, mobile = VIEWPORTS[name]
        self.bot.call("Emulation.setDeviceMetricsOverride",
                      {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": mobile})

    def theme(self, mode: str):
        self.evaluate(f"localStorage.setItem('boil_theme',{json.dumps(mode)});"
                      f"document.documentElement.dataset.theme={json.dumps(mode)};")

    def goto(self, hash_: str, wait_for: str | None = None):
        self.bot.call("Page.navigate", {"url": APP + "/" + hash_})
        if wait_for:
            self.bot.wait(wait_for, timeout=20)
        time.sleep(1.2)          # 让异步渲染落地，别把"还在 loading"当成布局缺陷

    def error_delta(self, mark: int):
        new = self.bot.errors[mark:]
        self.bot.errors[:] = self.bot.errors[:mark]
        return [e for e in new if "Failed to load resource" not in e or "favicon" not in e]

    # ---------- 1. 路由 × 主题 × 视口 ----------
    def matrix(self, routes: dict[str, str]):
        for route, wait_sel in routes.items():
            for vname in VIEWPORTS:
                for mode in ("dark", "light"):
                    where = f"{route}@{vname}@{mode}"
                    self.viewport(vname)
                    self.theme(mode)
                    mark = len(self.bot.errors)
                    self.goto("#/" + route, wait_sel)
                    probe = self.evaluate(PROBE)
                    if not probe:
                        self.fail(where, "探针没拿到数据（页面可能整个白掉）")
                        continue
                    self.rows.append({"case": where, **probe})
                    for err in self.error_delta(mark):
                        self.fail(where, f"控制台报错：{err[:160]}")
                    if probe["overflow"] > 2:
                        self.fail(where, f"横向溢出 {probe['overflow']}px，手机上要左右拖：{probe['wide']}")
                    if probe["unnamed"]:
                        self.fail(where, f"有可见但叫不出名字的控件（读屏只会念 button）：{probe['unnamed'][:4]}")
                    if probe["positiveTabindex"]:
                        self.fail(where, f"{probe['positiveTabindex']} 个正 tabindex 打乱了自然焦点顺序")
                    if probe["imagesWithoutAlt"]:
                        self.fail(where, f"图片没有 alt：{probe['imagesWithoutAlt'][:3]}")
                    if probe["jsHref"]:
                        self.fail(where, "页面里出现了 javascript: 链接")
                    if probe["injecteds"]:
                        self.fail(where, f"页面里出现了注入产物（img[src=x]/iframe/object）：{probe['injecteds']}")
                    if probe["inlineHandlers"]:
                        self.fail(where, f"页面里出现了内联事件属性（{probe['inlineHandlers']} 个）")
                    if vname == "phone" and probe["smallTargets"]:
                        self.fail(where, f"小屏上点击目标过小（<24px）：{probe['smallTargets'][:4]}")
                    if probe["textLength"] < 40:
                        self.fail(where, f"页面几乎没内容（{probe['textLength']} 字符），可能渲染失败")
                    if not probe["liveRegion"]:
                        self.fail(where, "没有 aria-live 区域，toast 对读屏用户是隐形的")

    # ---------- 2. 关键控件真点得着 ----------
    def key_controls(self, routes: dict[str, str]):
        self.viewport("desktop")
        for route, wait_sel in routes.items():
            self.goto("#/" + route, wait_sel)
            for sel in KEY_CONTROLS.get(route.split("/")[0], []):
                spot = self.bot.rect(sel, scroll=False)
                if not spot:
                    self.fail(f"{route}/{sel}", "关键控件不存在")
                elif not spot["hit"] or spot["w"] < 2 or spot["h"] < 2:
                    self.fail(f"{route}/{sel}", f"存在但点不着（被 {spot['blocker']} 盖住或是 0 尺寸）")

    # ---------- 3. 键盘可达 ----------
    def keyboard(self):
        self.viewport("desktop")
        self.theme("dark")
        self.goto("#/dashboard", "#sidebar")
        self.evaluate("document.body.focus();window.scrollTo(0,0);")
        stops = []
        for _ in range(40):
            self.bot.key("Tab")
            # 用元素自己身上的记号判断"绕回起点"：按 class 当 key 会把 7 个导航链接
            # 看成同一个控件（它们没有 id），于是走 4 站就误判成死循环。
            info = self.evaluate(
                "(()=>{const e=document.activeElement;if(!e||e===document.body)return null;"
                "const revisit=!!e.dataset.auditSeen; e.dataset.auditSeen='1';"
                "const r=e.getBoundingClientRect();const s=getComputedStyle(e);"
                "const ring=s.outlineWidth!=='0px'||/box-shadow/.test(s.boxShadow+'');"
                "return JSON.stringify({revisit:revisit,tag:e.tagName,id:e.id,"
                "cls:String(e.className||'').slice(0,24),"
                "label:(e.getAttribute('aria-label')||e.textContent||e.title||'').trim().slice(0,24),"
                "inView:r.top>=0&&r.bottom<=innerHeight+1&&r.width>0&&r.height>0,ring:ring})})()")
            if not info:
                continue
            stop = json.loads(info)
            key = f"{stop['tag']}#{stop['id']}.{stop['cls']}|{stop['label']}"
            if stop["revisit"]:
                if stop["label"]:
                    stops.append({"key": key, **stop})
                break          # 回到已经走过的控件 = 焦点环闭合
            stops.append({"key": key, **stop})
        if len(stops) < 6:
            self.fail("键盘", f"Tab 只能走到 {len(stops)} 个控件，大量交互点键盘到不了")
        invisible = [s["key"] for s in stops if not s["inView"]]
        if invisible:
            self.fail("键盘", f"焦点落在视口外/不可见的元素上：{invisible[:4]}")
        no_ring = [s["key"] for s in stops if not s["ring"]]
        if no_ring:
            self.fail("键盘", f"这些控件拿焦点后看不出焦点在哪（没有 outline/box-shadow）：{no_ring[:4]}")
        self.note(f"Tab 顺序 {len(stops)} 站：{[s['label'] or s['key'] for s in stops][:8]}")
        reachables = " ".join(f"{s['id']}" for s in stops)
        if "agent-orb" not in reachables and not any("orb" in s["key"] for s in stops):
            self.fail("键盘", "全局助手悬浮球不能用 Tab 到达")
        # 弹窗焦点陷阱：搜索框（Ctrl/Cmd+K 或搜索按钮）开合
        opened = self.evaluate(
            "(()=>{const b=document.querySelector('#search-btn');"
            "if(b){b.click();return true}return false})()")
        if opened:
            time.sleep(0.6)
            inside = []
            for _ in range(12):
                self.bot.key("Tab")
                here = self.evaluate(
                    "(()=>{const e=document.activeElement;"
                    "const m=document.querySelector('.modal-backdrop,#modal');"
                    "return JSON.stringify({inModal:!!(m&&e&&m.contains(e)),tag:e?e.tagName:'none'})})()")
                if not here:
                    break
                data = json.loads(here)
                if not data["inModal"] and data["tag"] != "BODY":
                    inside.append(data["tag"])
            if inside:
                self.fail("键盘", f"弹窗开着时 Tab 跑到了背景内容上（焦点陷阱失效）：{inside[:4]}")
            self.bot.key("Escape")
            time.sleep(0.4)
            if not self.evaluate("(()=>{const m=document.querySelector('.modal-backdrop,#modal');"
                                 "return !(m&&getComputedStyle(m).display!=='none')})()"):
                self.fail("键盘", "Escape 关不掉弹窗")
        else:
            self.note("没找到全局搜索入口，焦点陷阱未测")

    # ---------- 4. 减少动效 ----------
    def reduced_motion(self):
        self.viewport("desktop")
        self.goto("#/dashboard", "#sidebar")
        before = self.evaluate(
            "(()=>{const e=document.querySelector('.steam.s1')||document.querySelector('[class*=pulse]');"
            "if(!e)return null;const s=getComputedStyle(e);return s.animationDuration+'|'+s.transitionDuration})()")
        self.bot.call("Emulation.setEmulatedMedia", {"features": [
            {"name": "prefers-reduced-motion", "value": "reduce"}]})
        self.goto("#/dashboard", "#sidebar")
        after = self.evaluate(
            "(()=>{const e=document.querySelector('.steam.s1')||document.querySelector('[class*=pulse]');"
            "if(!e)return null;const s=getComputedStyle(e);return s.animationDuration+'|'+s.transitionDuration})()")
        if before is None:
            self.note("页面上找不到样板动画元素，减少动效未测")
        elif before == after:
            self.fail("减少动效", f"系统要求减少动效时动画时长没变（{before}）")
        else:
            self.note(f"减少动效生效：{before} → {after}")
        self.bot.call("Emulation.setEmulatedMedia", {"features": [
            {"name": "prefers-reduced-motion", "value": "no-preference"}]})

    # ---------- 5. 浏览器里的真 XSS ----------
    def xss(self):
        """种一批 payload，逐页渲染，看它们是不是老老实实当文字待着。"""
        payload_quotes = [
            '<img src=x onerror="window.__boilXssFired=(window.__boilXssFired||0)+1">',
            '<svg onload="window.__boilXssFired=(window.__boilXssFired||0)+1"></svg>',
            '<script>window.__boilXssFired=(window.__boilXssFired||0)+1</script>',
            '"><b onmouseover="window.__boilXssFired=(window.__boilXssFired||0)+1">hover</b>',
            "<a href=\"javascript:window.__boilXssFired=(window.__boilXssFired||0)+1\">点我</a>",
        ]
        marker = "AUDIT-Payload-别执行"
        for i, payload in enumerate(payload_quotes):
            api("/api/notes", {"quote": f"{marker} {i}", "content": payload})
        self.evaluate(f"window.{XSS}=0")
        for route in ("dashboard", "notes", "insights", "library", "settings", "review"):
            mark = len(self.bot.errors)
            self.goto("#/" + route, None)
            fired = self.evaluate(f"window.{XSS}")
            probe = self.evaluate(PROBE) or {}
            if fired:
                self.fail(f"xss/{route}", f"payload 真的执行了 {fired} 次")
            if probe.get("injecteds"):
                self.fail(f"xss/{route}", f"payload 变成了 DOM 节点（{probe['injecteds']} 个）")
            if probe.get("inlineHandlers"):
                self.fail(f"xss/{route}", f"payload 带出了内联事件属性（{probe['inlineHandlers']} 个）")
            if route == "notes" and marker not in (self.bot.body() or ""):
                self.fail("xss/notes", "payload 连文字都没渲染出来：既不安全也不可见，是被吞了")
            for err in self.bot.errors[mark:]:
                if "Refused to" in err or "unsupported MIME" in err:
                    self.note(f"CSP 拦下了一次加载：{err[:120]}")
        # 直接证明 CSP 在浏览器里生效，而不是只在响应头里好看。
        # 注意不能用 new Function/eval 来测：那是从 CDP 调试上下文里调的，浏览器不套页面 CSP，
        # 结论会是假的。真正受 CSP 管的是页面里插入的内联脚本和跨源脚本。
        csp = self.evaluate(
            "(async()=>{const s=document.createElement('script');"
            "s.textContent='window.__boilXssFired=(window.__boilXssFired||0)+1;';"
            "document.head.appendChild(s);"
            "const ext=await new Promise(res=>{const e=document.createElement('script');"
            "e.src='http://127.0.0.1:9/evil.js';e.onload=()=>res('loaded');e.onerror=()=>res('blocked');"
            "document.head.appendChild(e);setTimeout(()=>res('silent'),1500);});"
            "await new Promise(r=>setTimeout(r,250));"
            "return JSON.stringify({inlineRan:(window.__boilXssFired||0)>0,ext:ext})})()")
        csp = json.loads(csp) if isinstance(csp, str) else (csp or {})
        if csp.get("inlineRan"):
            self.fail("xss/CSP", "内联脚本执行了：script-src 'self' 没生效")
        if csp.get("ext") == "loaded":
            self.fail("xss/CSP", "跨源脚本被加载了：script-src 拦不住外部代码")
        self.note(f"CSP 实测：内联脚本执行={csp.get('inlineRan')} 跨源脚本={csp.get('ext')}")

    # ---------- 6. 主题开关真的能切 ----------
    def theme_toggle(self):
        self.viewport("desktop")
        self.evaluate("localStorage.removeItem('boil_theme')")
        self.goto("#/settings", "#sidebar")
        first = self.evaluate("document.documentElement.dataset.theme")
        clicked = self.evaluate(
            "(()=>{const b=[...document.querySelectorAll('button,[role=switch],input[type=checkbox]')]"
            ".find(x=>/浅色|深色|主题|light|dark/i.test((x.getAttribute('aria-label')||'')+x.textContent));"
            "if(!b)return false;b.click();return true})()")
        time.sleep(0.5)
        second = self.evaluate("document.documentElement.dataset.theme")
        if not clicked:
            self.fail("主题", "找不到主题开关")
        elif first == second:
            self.fail("主题", "点了开关但 data-theme 没变")
        current = self.evaluate("localStorage.getItem('boil_theme')")
        if current is None:
            self.fail("主题", "读完开关 localStorage 里根本没有 boil_theme")
            return
        want = "dark" if current == "light" else "light"
        self.evaluate(f"localStorage.setItem('boil_theme',{json.dumps(want)});"
                      "window.__auditDoc='before';")
        # 必须确认"真的换了一个文档"：Page.navigate 到同一个带 hash 的地址是同文档导航，
        # JS 不会重跑；不等这一层就会在旧文档上读出旧主题，得出"没记住"的假结论。
        self.bot.call("Page.navigate", {"url": APP + "/#/dashboard"})
        self.bot.call("Page.reload", {"ignoreCache": True})
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            time.sleep(0.4)
            if self.evaluate("window.__auditDoc") != "before":
                break
        self.bot.wait("#sidebar", timeout=20)
        got = self.evaluate("document.documentElement.dataset.theme")
        if self.evaluate("window.__auditDoc") == "before":
            self.fail("主题", "刷新没真的发生，这条检查作废")
        elif got != want:
            self.fail("主题", f"刷新后主题没记住：期望 {want}，实际 {got}")

    # ---------- 汇总 ----------
    def report(self):
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "summary.json").write_text(
            json.dumps({"app": APP, "cases": len(self.rows), "findings": self.findings,
                        "probe": self.rows}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n探了 {len(self.rows)} 个 路由×视口×主题 组合，页面字节数中位 "
              f"{sorted(r['textLength'] for r in self.rows)[len(self.rows)//2] if self.rows else 0}")
        if self.findings:
            print(f"\nfindings: {len(self.findings)}")
            for f in self.findings:
                print(f" - {f['where']}: {f['message']}")
        else:
            print("\nfindings: none")
        self.bot.chrome.terminate()
        return 1 if self.findings else 0


def main():
    audit = Audit()
    dashboard = api("/api/dashboard")
    if not dashboard.get("has_goal"):
        api("/api/onboarding", {"name": "前端审计", "pack_id": "boil.xueshen.learning",
                                "goal_title": "审计", "daily_minutes": 30, "deadline": "2026-12-31"})
        dashboard = api("/api/dashboard")
    unit_id = (dashboard.get("next_units") or [{}])[0].get("id") or ""
    routes = {
        "dashboard": "#sidebar", "map": "#sidebar", "review": "#sidebar",
        "library": "#sidebar", "insights": "#sidebar", "notes": "#sidebar",
        "settings": "#sidebar", "onboarding": "#onboarding-body",
    }
    if unit_id:
        routes["unit/" + unit_id] = "#sidebar"
    try:
        audit.matrix(routes)
        audit.key_controls(routes)
        audit.keyboard()
        audit.reduced_motion()
        audit.xss()
        audit.theme_toggle()
    finally:
        sys.exit(audit.report())


if __name__ == "__main__":
    main()
