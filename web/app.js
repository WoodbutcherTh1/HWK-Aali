/* آلي web client — talks to the local brain (Flask /api/ask) from anywhere. */
"use strict";

const $ = (sel) => document.querySelector(sel);
const chatEl = $("#chat");
const form = $("#askForm");
const input = $("#message");
const sendBtn = $("#sendBtn");
const connState = $("#connState");

let apiBase = localStorage.getItem("aali_api") || "http://127.0.0.1:5055";
let sid = localStorage.getItem("aali_sid") || "";

function saveApi() {
  localStorage.setItem("aali_api", apiBase.replace(/\/+$/, ""));
}

/* — rendering — */
function fmt(text) {
  const frag = document.createDocumentFragment();
  const re = /```([\s\S]*?)```|`([^`]+)`/g;
  let last = 0, m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) frag.append(span(text.slice(last, m.index)));
    if (m[1] !== undefined) {
      const pre = document.createElement("pre");
      const code = document.createElement("code");
      code.textContent = m[1].replace(/^\n+|\n+$/g, "");
      pre.append(code);
      frag.append(pre);
    } else {
      const code = document.createElement("code");
      code.textContent = m[2];
      frag.append(code);
    }
    last = re.lastIndex;
  }
  if (last < text.length) frag.append(span(text.slice(last)));
  return frag;
}
function span(t) { return document.createTextNode(t); }

function bubble(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  const who = document.createElement("span");
  who.className = "who";
  who.textContent = role === "user" ? "أنت" : "آلي";
  div.append(who, fmt(text));
  chatEl.append(div);
  chatEl.scrollTop = chatEl.scrollHeight;
  return div;
}

function thinking(on) {
  const old = $(".thinking");
  if (old) old.remove();
  if (!on) return;
  const div = document.createElement("div");
  div.className = "msg assistant thinking";
  div.innerHTML = '<span class="orbit"><span class="core"></span><span class="sat"></span></span><span>آلي يفكّر…</span>';
  chatEl.append(div);
  chatEl.scrollTop = chatEl.scrollHeight;
}

function setConn(ok) {
  connState.textContent = ok ? "متصل" : "غير متصل";
  connState.className = ok ? "ok" : "bad";
}

async function ping() {
  try {
    const r = await fetch(`${apiBase}/api/health`, { method: "GET" });
    setConn(r.ok);
  } catch {
    setConn(false);
  }
}

/* — ask — */
async function ask(text) {
  if (!text) return;
  bubble("user", text);
  input.value = "";
  input.style.height = "auto";
  sendBtn.disabled = true;
  thinking(true);
  try {
    const res = await fetch(`${apiBase}/api/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, sid: sid || undefined }),
    });
    const data = await res.json();
    if (data.sid) {
      sid = data.sid;
      localStorage.setItem("aali_sid", sid);
    }
    thinking(false);
    bubble("assistant", data.reply || data.error || "…");
    setConn(res.ok);
  } catch (err) {
    thinking(false);
    bubble("assistant", "تعذّر الاتصال بالخادم — تأكد من العنوان في ⚙︎ الإعدادات وأن الخادم يعمل على حاسوبك.");
    setConn(false);
  } finally {
    sendBtn.disabled = false;
    input.focus();
  }
}

/* — events — */
form.addEventListener("submit", (e) => {
  e.preventDefault();
  ask(input.value.trim());
});
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    ask(input.value.trim());
  }
});
input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, window.innerHeight * 0.4) + "px";
});
document.querySelectorAll(".chip").forEach((chip) =>
  chip.addEventListener("click", () => ask(chip.dataset.fill))
);
$("#newChatBtn").addEventListener("click", () => {
  sid = "";
  localStorage.removeItem("aali_sid");
  chatEl.querySelectorAll(".msg").forEach((el) => el.remove());
  $("#welcome").style.display = "";
  input.focus();
});

const settingsDlg = $("#settings");
$("#settingsBtn").addEventListener("click", () => {
  $("#apiBase").value = apiBase;
  settingsDlg.showModal();
});
$("#closeSettings").addEventListener("click", () => settingsDlg.close());
$("#saveSettings").addEventListener("click", () => {
  apiBase = $("#apiBase").value.trim() || "http://127.0.0.1:5055";
  saveApi();
  sid = "";
  settingsDlg.close();
  ping();
});

/* — boot — */
if ("serviceWorker" in navigator) navigator.serviceWorker.register("./sw.js");
ping();
setInterval(ping, 15000);
input.focus();
