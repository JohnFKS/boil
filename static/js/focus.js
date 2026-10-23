/* 专注（番茄钟）：开始不落库，结束才记；只有「完成」的这一次计入学习时长。 */
import { icon, toast, openModal } from "./utils.js";

const KEY = "boil_focus";
let state = null;
let timer = null;
let ctxRef = null;

function load() {
  try {
    return JSON.parse(localStorage.getItem(KEY) || "null");
  } catch {
    return null;
  }
}

function save(value) {
  try {
    if (value) localStorage.setItem(KEY, JSON.stringify(value));
    else localStorage.removeItem(KEY);
  } catch { /* 无痕模式下写不了，只影响刷新后的恢复 */ }
}

function unitInView() {
  const m = (location.hash || "").match(/^#\/unit\/([^/?]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

function remainingMs() {
  return state ? state.startedAt + state.minutes * 60000 - Date.now() : 0;
}

function label() {
  const el = document.getElementById("focus-label");
  if (!el) return;
  el.parentElement?.classList.toggle("running", !!state);
  if (!state) { el.textContent = "专注"; return; }
  const left = Math.max(0, Math.ceil(remainingMs() / 1000));
  el.textContent = `${String(Math.floor(left / 60)).padStart(2, "0")}:${String(left % 60).padStart(2, "0")}`;
}

function tick() {
  label();
  if (state && remainingMs() <= 0) finish("completed");
}

function beginTicking() {
  clearInterval(timer);
  timer = setInterval(tick, 1000);
  label();
}

export async function startFocus(minutes, ctx) {
  try {
    const res = await ctx.api.post("/api/focus/start", { minutes, unit_id: unitInView() });
    state = { session: res.session, minutes: res.minutes,
              startedAt: Date.now(), unitId: res.unit_id };
    save(state);
    beginTicking();
    toast(`已开始 ${res.minutes} 分钟专注；完成时会记进今天的学习时长`, "success", 3600);
  } catch (e) {
    toast(e.message || "专注没能开始", "error", 4200);
  }
}

export async function finish(outcome, ctx = ctxRef) {
  if (!state) return;
  const f = state;
  state = null;
  save(null);
  clearInterval(timer);
  timer = null;
  label();   // 先复原按钮：记录失败也不该把界面卡在 00:00
  try {
    const res = await ctx.api.post("/api/focus/finish", {
      session: f.session, minutes: f.minutes, unit_id: f.unitId, outcome,
    });
    if (res.duplicate) toast(`这次专注已经记过了：今天 ${res.minutes} 分钟（${res.sessions} 次）`, "info", 4200);
    else if (outcome === "completed") toast(`完成一次专注；今天 ${res.minutes} 分钟（${res.sessions} 次）`, "success", 4200);
    else toast("提前结束了，这次不计入学习时长", "info", 3000);
  } catch (e) {
    toast(e.message || "专注记录没保存上", "error", 4400);
  }
}

function openStart(ctx) {
  const modal = openModal({
    title: "开始一次专注",
    subtitle: "只做一件事；让倒计时走完才算完成",
    body: `<p class="faint tiny">完成了才计入热力图与学习时长。提前结束不会记，也不会假装记。</p>`,
    actions: [15, 25, 45].map(n => ({
      label: `${n} 分钟`,
      kind: n === 25 ? "btn-primary" : "btn-ghost",
      onClick: () => { modal.close(); startFocus(n, ctx); },
    })),
  });
}

function openRunning() {
  const left = Math.max(1, Math.ceil(remainingMs() / 60000));
  const modal = openModal({
    title: "专注中",
    subtitle: `还剩约 ${left} 分钟`,
    body: `<p class="faint tiny">提前结束不计入学习时长；让倒计时走完才算一次完成。</p>`,
    actions: [
      { label: "结束这次专注", kind: "btn-ghost",
        onClick: () => { modal.close(); finish("abandoned"); } },
      { label: "继续专注", kind: "btn-primary" },
    ],
  });
}

export function initFocus(ctx) {
  ctxRef = ctx;
  state = load();
  if (state && remainingMs() <= 0) finish("completed", ctx);
  else if (state) beginTicking();
  else label();

  document.addEventListener("click", (e) => {
    if (!e.target.closest("#focus-btn")) return;
    if (state) openRunning();
    else openStart(ctxRef);
  });
  // 壳层每次导航都会重建按钮，补一次文案（倒计时本身由 interval 接着更新）
  window.addEventListener("hashchange", () => setTimeout(label, 0));
}
