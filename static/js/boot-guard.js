/* Classic script: shows an actionable fallback if the module graph never boots. */
(function () {
  var done = false;
  var app = document.getElementById("app");

  function fail(title, detail) {
    if (done || !app) return;
    done = true;
    app.textContent = "";
    var box = document.createElement("div");
    box.className = "boot";
    box.style.padding = "24px";

    var heading = document.createElement("h1");
    heading.style.fontFamily = "var(--font-display)";
    heading.style.fontSize = "30px";
    heading.textContent = title;

    var text = document.createElement("p");
    text.style.maxWidth = "460px";
    text.textContent = detail;

    var row = document.createElement("div");
    row.className = "row";
    row.style.justifyContent = "center";
    row.style.marginTop = "8px";

    var retry = document.createElement("button");
    retry.className = "btn btn-primary";
    retry.textContent = "重新加载";
    retry.addEventListener("click", function () { location.reload(); });

    var hint = document.createElement("p");
    hint.className = "faint tiny";
    hint.textContent = "若仍停在加载画面，请按 Ctrl+Shift+R（Mac：Cmd+Shift+R）强制刷新。";

    row.appendChild(retry);
    box.appendChild(heading);
    box.appendChild(text);
    box.appendChild(row);
    box.appendChild(hint);
    app.appendChild(box);
  }

  window.__boilBootReady = function () { done = true; };

  window.addEventListener("error", function (event) {
    var target = event.target;
    if (target && target.tagName === "SCRIPT") {
      fail("前端脚本加载失败", "请确认 python3 run.py 仍在运行，然后重新加载。");
    }
  }, true);

  setTimeout(function () {
    if (!done && app && app.querySelector(".boot")) {
      fail("加载时间有点久", "服务可能刚启动，或前端资源没有加载完成。请确认 python3 run.py 仍在运行。");
    }
  }, 12000);
})();
