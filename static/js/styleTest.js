/* 学习风格测试：5 道情境题 → 教学人设，写进当前目标，之后教练按这个带法走。 */
import { escapeHTML, icon, toast } from "./utils.js";

export function personaBadge(persona) {
  if (!persona || !persona.label) return "";
  return `<span class="badge badge-violet">${icon("compass", 12)} ${escapeHTML(persona.label)}</span>`;
}

export function openStyleTest(ctx, onDone) {
  let questions = null;
  let answers = [];
  const body = document.createElement("div");

  const paint = () => {
    if (!questions) {
      body.innerHTML = '<p class="muted small">正在取题…</p>';
      return;
    }
    body.innerHTML = `
      <div class="style-progress"><i style="width:${(answers.length / questions.questions.length) * 100}%"></i></div>
      ${questions.questions.map((q, qi) => `
        <div class="style-q">
          <div class="style-q-text"><span class="style-q-n">${qi + 1}</span>${escapeHTML(q.text)}</div>
          <div class="style-opts">
            ${q.options.map((o, oi) => `
              <button class="style-opt ${answers[qi] === oi ? "on" : ""}" type="button" data-q="${qi}" data-o="${oi}">
                ${escapeHTML(o.text)}
              </button>`).join("")}
          </div>
        </div>`).join("")}
      <p class="faint tiny">没有对错，只选「你真实会怎么做」。测完随时可以重测。</p>`;
    body.querySelectorAll("[data-q]").forEach(btn => btn.addEventListener("click", () => {
      const qi = Number(btn.dataset.q);
      const prev = body.querySelector(`.style-q[data-focus="${qi}"]`);
      answers[qi] = Number(btn.dataset.o);
      paint();
      if (!prev) return;
      const next = body.querySelectorAll(".style-q");
      next[Math.min(qi + 1, next.length - 1)]?.scrollIntoView({ block: "center", behavior: "smooth" });
    }));
  };

  const modal = ctx.openModal({
    title: "3 分钟：你希望教练怎么带你",
    subtitle: "5 道情境题，决定追问与讲解的带法",
    body,
    wide: true,
    actions: [
      { label: "以后再说", kind: "btn-ghost" },
      {
        label: "生成带法",
        kind: "btn-primary",
        close: false,
        onClick: async ({ close }) => {
          const btn = modal.modal.querySelector(".modal-foot .btn-primary");
          if (!questions || answers.some(a => a === undefined || a === null)) {
            toast("还有题没选完", "info");
            return;
          }
          if (btn) { btn.disabled = true; btn.textContent = "生成中…"; }
          try {
            const res = await ctx.api.post("/api/styles/apply", { answers });
            toast(`带法已设定：${res.persona.label}。苏格拉底追问与费曼反馈都会照这个来。`, "success", 5200);
            close();
            onDone?.(res.persona);
          } catch (err) {
            toast(err.message || "生成失败", "error");
            if (btn) { btn.disabled = false; btn.textContent = "生成带法"; }
          }
        },
      },
    ],
  });

  paint();
  ctx.api.get("/api/styles/questions").then((data) => {
    questions = data;
    answers = new Array(data.questions.length);
    paint();
  }).catch((e) => {
    body.innerHTML = `<p class="muted small">取题失败：${escapeHTML(e.message || "未知错误")}</p>`;
  });
}
