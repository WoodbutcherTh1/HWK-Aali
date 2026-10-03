/* آلي ستوديو — client.
 *
 * The agent panel is the point of this file: it turns the server's tiny event
 * vocabulary (thinking / tool_call / tool_result / terminal / diff / text /
 * done) into blocks the owner can watch, exactly like a Cursor-style IDE.
 *
 * The IDE layer on top of it: tabs, a live file watcher, @file context chips,
 * accept/reject on every edit diff, markdown rendering, Ctrl+P quick open,
 * and conversations that survive a restart.
 */
"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  workspace: "",
  root: "",
  openPath: "",            // the ACTIVE tab
  model: "",
  editor: null,            // monaco instance or null (plain-text fallback)
  tabs: [],                // [{path, content, dirty, model}]  model = monaco
  allFiles: [],            // /api/files, loaded once, filtered locally
  context: [],             // [{path, selection}] the agent can see
  links: [],               // [{token, title, url}] read by /api/link
  lastLinks: [],
  lastAsk: "",
  lastContext: [],
  lastSid: "",
  requestId: "",
  busy: false,
  cmdRequestId: "",
  watchSource: null,
  turnBlocks: [],          // the current turn's blocks, for history saving
  paletteMode: "quick",    // quick | mention
  update: null,           // /api/update/status — the four update surfaces
};

/* ————— tiny helpers ————— */

function toast(text, ms = 2600) {
  const el = $("toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, ms);
}

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  let data = {};
  try { data = await response.json(); } catch (_) { /* empty body */ }
  if (!response.ok) {
    const error = new Error(data.error || ("HTTP " + response.status));
    error.payload = data;
    error.status = response.status;
    throw error;
  }
  return data;
}

/* POST + Server-Sent Events over fetch (EventSource cannot POST). */
async function stream(path, body, onEvent, signal) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok || !response.body) {
    let detail = "HTTP " + response.status;
    try { detail = (await response.json()).error || detail; } catch (_) {}
    onEvent("error", { error: detail });
    onEvent("done", { ok: false, reply: "" });
    return;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let split;
    while ((split = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      let name = "message", raw = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) name = line.slice(6).trim();
        else if (line.startsWith("data:")) raw += line.slice(5).trim();
      }
      if (!raw) continue;
      let data = {};
      try { data = JSON.parse(raw); } catch (_) { continue; }
      onEvent(name, data);
    }
  }
}

/* ————— markdown (safe subset, escaped first) ————— */

function renderMarkdown(text) {
  const escaped = escapeHtml(text || "");
  const inline = (line) => line
    .replace(/`([^`\n]+)`/g, (_, code) => "<code>" + code + "</code>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]\n]+)\]\((https?:[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g,
      '$1<a href="$2" target="_blank" rel="noopener">$2</a>');

  const renderProse = (chunk) => {
    const out = [];
    let list = [];
    const flush = () => {
      if (list.length) { out.push("<ul>" + list.join("") + "</ul>"); list = []; }
    };
    for (const line of chunk.split("\n")) {
      if (!line.trim()) { flush(); continue; }
      if (/^#{1,4}\s/.test(line)) {
        flush();
        const level = line.match(/^#+/)[0].length;
        out.push("<h" + level + ">" + inline(line.replace(/^#+\s*/, "")) +
                "</h" + level + ">");
      } else if (/^\s*[-*]\s/.test(line)) {
        list.push("<li>" + inline(line.replace(/^\s*[-*]\s*/, "")) + "</li>");
      } else if (/^&gt;\s?/.test(line)) {
        flush();
        out.push("<blockquote>" + inline(line.replace(/^&gt;\s?/, "")) +
                "</blockquote>");
      } else if (/^\s*---+\s*$/.test(line)) {
        flush();
        out.push("<hr>");
      } else {
        flush();
        out.push("<p>" + inline(line) + "</p>");
      }
    }
    flush();
    return out.join("");
  };

  // Fenced code is kept ASIDE and never re-parsed as prose. No placeholder
  // sentinels: a stray NUL in the source makes the file binary to grep/diff.
  const fence = /```([A-Za-z0-9_+-]*)\n?([\s\S]*?)```/g;
  const html = [];
  let cursor = 0;
  let match;
  while ((match = fence.exec(escaped)) !== null) {
    if (match.index > cursor) html.push(renderProse(escaped.slice(cursor, match.index)));
    html.push('<pre class="md-code"><code class="lang-' +
      escapeHtml(match[1] || "text") + '">' +
      match[2].replace(/\n$/, "") + "</code></pre>");
    cursor = match.index + match[0].length;
  }
  if (cursor < escaped.length) html.push(renderProse(escaped.slice(cursor)));
  return html.join("");
}

/* ————— file tree ————— */

async function loadTree(path = ".") {
  const data = await api("/api/tree?path=" + encodeURIComponent(path));
  state.root = data.root || state.root;
  $("root-label").textContent = state.root;
  const list = $("tree");
  list.replaceChildren();
  if (path !== ".") {
    const parts = path.split("/");
    const up = el("li");
    const row = el("button", "row");
    row.append(el("span", "caret", "▾"), el("span", "", "📁 المجلد الأعلى"));
    row.onclick = () => loadTree(parts.slice(0, -1).join("/") || ".");
    up.append(row);
    list.append(up);
  }
  for (const entry of data.entries) {
    const item = el("li");
    const row = el("button", "row");
    if (entry.type === "dir") {
      const caret = el("span", "caret", "▸");
      row.append(caret, el("span", "folder", "📁 " + entry.name));
      row.onclick = () => {
        caret.textContent = caret.textContent === "▸" ? "▾" : "▸";
        loadTree(entry.path);
      };
      row.dataset.dir = "1";
    } else {
      row.append(el("span", "caret", ""), el("span", "", "📄 " + entry.name));
      row.onclick = () => openFile(entry.path);
      if (state.openPath === entry.path) row.classList.add("active");
    }
    // Keyboard: ↑↓ walk the tree, Enter opens, → expands a folder.
    row.onkeydown = (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const rows = [...list.querySelectorAll(".row")];
        const i = rows.indexOf(row);
        const next = rows[event.key === "ArrowDown" ? i + 1 : i - 1];
        if (next) next.focus();
      } else if (event.key === "Enter" ||
                 (event.key === "ArrowRight" && row.dataset.dir)) {
        event.preventDefault();
        row.click();
      }
    };
    item.append(row);
    list.append(item);
  }
}

async function loadAllFiles() {
  try {
    const data = await api("/api/files");
    state.allFiles = data.files || [];
  } catch (_) { state.allFiles = []; }
}

/* ————— tabs + editor ————— */

const LANG_BY_EXT = {
  py: "python", js: "javascript", ts: "typescript", tsx: "typescript",
  jsx: "javascript", json: "json", html: "html", css: "css", md: "markdown",
  yml: "yaml", yaml: "yaml", sh: "shell", bat: "shell", sql: "sql",
  c: "c", cpp: "cpp", cs: "csharp", go: "go", rs: "rust", java: "java",
  xml: "xml", toml: "ini", ini: "ini",
};

function activeTab() {
  return state.tabs.find((t) => t.path === state.openPath) || null;
}

function currentContent() {
  const tab = activeTab();
  if (state.editor) return state.editor.getValue();
  return tab ? tab.content : ($("plain-editor").value || "");
}

function setContent(text, path) {
  const language = LANG_BY_EXT[(path || "").split(".").pop().toLowerCase()] || "plaintext";
  if (state.editor) {
    // One monaco MODEL PER TAB: switching tabs must not throw away the undo
    // history or the cursor of the file you left.
    state.editor.setModel(monaco.editor.createModel(text, language));
  } else {
    $("plain-editor").value = text;
  }
}

/* A tab owns its model; this binds it to the visible editor. */
function bindTabModel(tab) {
  if (!state.editor) { $("plain-editor").value = tab.content; return; }
  if (!tab.model) {
    tab.model = monaco.editor.createModel(
      tab.content,
      LANG_BY_EXT[tab.path.split(".").pop().toLowerCase()] || "plaintext");
  }
  tab.model.setValue(tab.content);
  state.editor.setModel(tab.model);
}

function markDirty(dirty) {
  const tab = activeTab();
  if (tab) tab.dirty = dirty;
  state.dirty = dirty;
  $("tab-dirty").hidden = !dirty;
  renderTabs();
}

function renderTabs() {
  const bar = $("tabs");
  bar.replaceChildren();
  for (const tab of state.tabs) {
    const node = el("button", "tab-chip" + (tab.path === state.openPath ? " active" : ""));
    node.append(el("span", "t-name", tab.path.split("/").pop()),
                el("span", "t-dirty", tab.dirty ? "●" : ""));
    const close = el("span", "t-close", "✕");
    close.onclick = (event) => { event.stopPropagation(); closeTab(tab.path); };
    node.append(close);
    node.onclick = () => switchTab(tab.path);
    node.title = tab.path;
    bar.append(node);
  }
}

async function openFile(path) {
  const existing = state.tabs.find((t) => t.path === path);
  if (existing) return switchTab(path);
  const data = await api("/api/file?path=" + encodeURIComponent(path));
  state.tabs.push({ path, content: data.content || "", dirty: false });
  if (state.tabs.length > 12) {
    const dropped = state.tabs.shift();
    if (dropped.model) dropped.model.dispose();
    toast("أُغلق تبويب قديم: " + dropped.path);
  }
  bindTabModel(state.tabs[state.tabs.length - 1]);
  state.openPath = path;
  state.dirty = false;
  $("tab-name").textContent = path;
  $("tab-dirty").hidden = true;
  renderTabs();
  autoContext();
  subscribeWatcher();
  await loadTree(dirOf(path));
}

function switchTab(path) {
  const tab = state.tabs.find((t) => t.path === path);
  if (!tab) return;
  bindTabModel(tab);
  state.openPath = path;
  state.dirty = tab.dirty;
  $("tab-name").textContent = path;
  $("tab-dirty").hidden = !tab.dirty;
  renderTabs();
  autoContext();
  subscribeWatcher();
}

function activeModelFor(tab) {
  return tab.model;
}

async function closeTab(path) {
  const index = state.tabs.findIndex((t) => t.path === path);
  if (index < 0) return;
  const tab = state.tabs[index];
  if (tab.dirty && !confirm("لديك تعديلات غير محفوظة في " + path + " — أغلق anyways?")) {
    return;
  }
  state.tabs.splice(index, 1);
  if (tab.model) { tab.model.dispose(); tab.model = null; }
  if (state.openPath === path) {
    const next = state.tabs[index] || state.tabs[index - 1];
    if (next) await switchTab(next.path);
    else {
      state.openPath = "";
      setContent("", "");
      $("tab-name").textContent = "لم يُفتح ملف";
      $("tab-dirty").hidden = true;
      subscribeWatcher();
    }
  }
  renderTabs();
}

function showPlainEditor(note) {
  $("editor").hidden = true;
  $("editor-fallback").hidden = false;
  $("fallback-note").textContent = note;
}

function dirOf(path) {
  return path && path.includes("/") ? path.split("/").slice(0, -1).join("/") || "." : ".";
}

async function saveFile(path, content) {
  await api("/api/file", { method: "POST", body: { path, content } });
  const tab = state.tabs.find((t) => t.path === path);
  if (tab) { tab.dirty = false; tab.content = content; }
  if (path === state.openPath) {
    state.dirty = false;
    $("tab-dirty").hidden = true;
  }
  renderTabs();
  toast("تم الحفظ: " + path);
}

/* ————— the live file watcher (an agent write must not leave a stale tab) ————— */

const subscribeWatcher = debounce(() => {
  if (state.watchSource) { state.watchSource.close(); state.watchSource = null; }
  const paths = state.tabs.map((t) => t.path);
  if (!paths.length) return;
  const source = new EventSource("/api/watch?paths=" + encodeURIComponent(paths.join(",")));
  source.addEventListener("changed", async (event) => {
    let data = {};
    try { data = JSON.parse(event.data); } catch (_) { return; }
    const tab = state.tabs.find((t) => t.path === data.path);
    if (!tab || data.chars === undefined) return;
    if (tab.dirty) {
      toast("⚠️ " + data.path + " تغيّر على القرص ولديك تعديلات غير محفوظة");
      return;
    }
    if (tab.path === state.openPath) return;  // the diff block already shows it
    const fresh = await api("/api/file?path=" + encodeURIComponent(data.path));
    tab.content = fresh.content || "";
    if (tab.model) tab.model.setValue(tab.content);
    else if (tab.path === state.openPath) $("plain-editor").value = tab.content;
    toast("⟳ حُمّل " + data.path);
  });
  source.onerror = () => { /* EventSource retries on its own */ };
  state.watchSource = source;
}, 250);

/* ————— context chips: what the agent can see ————— */

function hasContext(path) {
  return state.context.some((c) => c.path === path);
}

function addContext(path, selection) {
  if (!path || hasContext(path)) return;
  state.detached = (state.detached || new Set());
  state.detached.delete(path);
  state.context.push({ path, selection: selection || "" });
  renderChips();
}

function removeContext(path) {
  state.context = state.context.filter((c) => c.path !== path);
  // Removing the OPEN file must stick: without this, autoContext would put it
  // straight back on the next keystroke.
  state.detached = state.detached || new Set();
  state.detached.add(path);
  renderChips();
}

function selectionOf() {
  if (!state.editor) return "";
  const selection = state.editor.getSelection();
  if (!selection || selection.isEmpty()) return "";
  const model = state.editor.getModel();
  return model ? model.getValueInRange(selection) : "";
}

function autoContext() {
  if (!state.openPath) return;
  if (state.detached && state.detached.has(state.openPath)) return;
  const selection = selectionOf();
  const existing = state.context.find((c) => c.path === state.openPath);
  if (existing) { existing.selection = selection; renderChips(); return; }
  addContext(state.openPath, selection);
}

function renderChips() {
  const box = $("context-chips");
  box.replaceChildren();
  for (const item of state.context) {
    const chip = el("span", "chip");
    chip.append(el("span", "chip-name", item.path.split("/").pop()));
    chip.title = item.path + (item.selection ? " — محدد" : "");
    if (item.selection) chip.append(el("span", "chip-tag", "محدد"));
    const remove = el("span", "chip-x", "✕");
    remove.onclick = () => removeContext(item.path);
    chip.append(remove);
    box.append(chip);
  }
  if (!state.context.length) {
    box.append(el("span", "chip-hint", "لا ملفات مرفقة — اكتب @ لإضافة ملف"));
  }
  $("btn-addcontext").classList.toggle("on", !!state.context.length);
}

/* ————— read a public link (Aali Reach) ————— */

// The client NEVER receives the page text it will later send to the model: the
// server keeps it and hands back a token. That is why a hostile page cannot
// smuggle instructions in through this UI, and why "attach" is a token in a
// request body rather than a blob in a textarea.
async function readLink() {
  const input = $("link-url");
  const url = (input.value || "").trim();
  if (!url) { toast("الصق رابطاً أولاً"); return; }
  const btn = $("btn-link");
  btn.disabled = true;
  btn.textContent = "⏳ جارٍ القراءة…";
  try {
    const data = await api("/api/link", { method: "POST", body: { url } });
    if (!data.ok) { toast("تعذّرت القراءة: " + (data.error || "?"), 5000); return; }
    if (state.links.length >= 3) {
      toast("٣ روابط كحدٍّ في الدور الواحد — احذف واحداً أولاً", 4200);
      return;
    }
    state.links.push({
      token: data.token, title: data.title || data.url,
      url: data.url, chars: data.chars, engine: data.engine,
    });
    input.value = "";
    renderLinkChips();
    const where = data.chars
      ? `${data.chars} حرف · ${data.engine} · ${data.elapsed_ms}ms`
      : "لا نص مستخرج (قد تكون الصفحة مبنية بجافاسكربت)";
    toast("🔗 " + (data.title || data.url) + " — " + where, 4200);
  } catch (err) {
    toast("تعذّرت القراءة: " + err.message, 5000);
  } finally {
    btn.disabled = false;
    btn.textContent = "🔗 قراءة";
  }
}

function removeLink(token) {
  state.links = state.links.filter((l) => l.token !== token);
  renderLinkChips();
}

function renderLinkChips() {
  const box = $("link-chips");
  box.replaceChildren();
  for (const link of state.links) {
    const chip = el("span", "chip chip-link");
    chip.append(el("span", "chip-name", link.title || link.url));
    chip.title = link.url + (link.chars ? ` — ${link.chars} حرف` : "");
    if (!link.chars) chip.append(el("span", "chip-tag", "بلا نص"));
    const remove = el("span", "chip-x", "✕");
    remove.onclick = () => removeLink(link.token);
    chip.append(remove);
    box.append(chip);
  }
  $("btn-link").classList.toggle("on", !!state.links.length);
}

/* ————— the agent panel ————— */

function newTurn(question) {
  const turn = el("div", "turn");
  const files = state.context.map((c) => c.path);
  turn.append(el("div", "ask-line", "❓ " + question));
  if (files.length) {
    const line = el("div", "ask-files", "📎 " + files.join(" · "));
    turn.append(line);
  }
  const thinking = el("div", "thinking collapsed");
  thinking.append(
    el("div", "th-label", "💭 تفكير آلي…"),
    el("div", "th-body", ""));
  thinking.querySelector(".th-label").onclick = () => thinking.classList.toggle("collapsed");
  const answer = el("div", "ans");
  answer.append(el("span", "cursor"));
  turn.append(thinking, answer);
  const actions = el("div", "turn-actions");
  const retry = el("button", "ghost warn", "▶️ إعادة المحاولة");
  retry.onclick = () => ask(state.lastAsk);
  retry.hidden = true;
  const copy = el("button", "ghost", "📋 نسخ");
  copy.onclick = () => {
    navigator.clipboard?.writeText(answer.textContent || "");
    toast("نُسخ الجواب");
  };
  const meta = el("span", "turn-meta", "");
  actions.append(retry, copy, meta);
  turn.append(actions);
  $("turns").append(turn);
  $("panel").scrollTop = $("panel").scrollHeight;
  return {
    turn, files,
    thinking: thinking.querySelector(".th-body"),
    label: thinking.querySelector(".th-label"),
    answer, meta, retry, toolEls: [], answerSoFar: "", hadError: false,
  };
}

function addTool(view, name, args) {
  const tool = el("div", "tool");
  const head = el("div", "tool-head");
  head.append(el("span", "", "🔧"), el("span", "t-name", name || "tool"));
  const status = el("span", "t-state", "…");
  head.append(status);
  tool.append(head);
  if (args && Object.keys(args).length) {
    tool.append(el("pre", null, JSON.stringify(args, null, 2)));
  }
  view.turn.insertBefore(tool, view.answer);
  return status;
}

function addResult(view, toolEl, ok, summary) {
  toolEl.textContent = ok ? "✓ تم" : "✗ فشل";
  toolEl.classList.add(ok ? "ok" : "bad");
  if (summary) toolEl.closest(".tool").append(el("pre", null, summary));
}

/* THE diff becomes actionable: apply the agent's edit, or revert it. */
function addDiff(view, path, before, after) {
  const box = el("div", "diff");
  const head = el("div", "diff-head");
  head.append(el("span", "", "✏️"), el("span", "", "تعديل"),
              el("span", "d-path", path));
  box.append(head);
  const cols = el("div", "diff-cols");
  const beforeCol = el("div", "diff-before");
  beforeCol.append(el("h4", null, "قبل"), el("pre", null, before || "(ملف جديد)"));
  const afterCol = el("div", "diff-after");
  afterCol.append(el("h4", null, "بعد"), el("pre", null, after));
  cols.append(beforeCol, afterCol);
  box.append(cols);

  const actions = el("div", "diff-actions");
  const accept = el("button", "ghost ok-btn", "✔︎ قبول");
  const revert = el("button", "ghost danger-ghost", "↩ تراجع عن التعديل");
  const open = el("button", "ghost", "↗ فتح");
  const decide = async (mode) => {
    try {
      await api("/api/apply", { method: "POST",
        body: { path, before, after, mode } });
      actions.replaceChildren(el("span", "diff-note",
        mode === "accept" ? "✔︎ التعديل موجود على القرص — حُمّل في المحرر"
                          : "↩ أُعيد الملف كما كان"));
      await refreshTab(path);
      loadTree(dirOf(path));
    } catch (error) {
      actions.replaceChildren(el("span", "diff-note bad", "⚠️ " + error.message));
      if (error.payload && error.payload.conflict) refreshTab(path);
    }
  };
  accept.onclick = () => decide("accept");
  revert.onclick = () => decide("revert");
  open.onclick = () => openFile(path);
  actions.append(accept, revert, open);
  box.append(actions);
  view.turn.insertBefore(box, view.answer);
}

async function refreshTab(path) {
  const tab = state.tabs.find((t) => t.path === path);
  if (!tab) return;
  try {
    const fresh = await api("/api/file?path=" + encodeURIComponent(path));
    tab.content = fresh.content || "";
    if (tab.model) tab.model.setValue(tab.content);
    if (path === state.openPath) {
      setContent(tab.content, path);
      tab.dirty = false;
      $("tab-dirty").hidden = true;
    }
    renderTabs();
  } catch (_) { /* the file may have been deleted */ }
}

function addPanelTerminal(view, line) {
  if (!view.term) {
    view.term = el("div", "pterm");
    view.turn.insertBefore(view.term, view.answer);
  }
  view.term.textContent += line + "\n";
  $("panel").scrollTop = $("panel").scrollHeight;
}

async function ask(question) {
  if (state.busy) { toast("آلي يعمل الآن — انتظر أو أوقفه"); return; }
  question = (question || "").trim();
  if (!question) return;
  state.lastAsk = question;
  state.lastContext = state.context.map((c) => ({ ...c }));
  state.lastLinks = state.links.map((l) => l.token);
  state.busy = true;
  $("btn-ask").disabled = true;
  $("btn-stop").hidden = false;
  $("btn-retry").hidden = true;

  const view = newTurn(question);
  state.turnBlocks = [];
  const controller = new AbortController();
  state.controller = controller;
  let answerSoFar = "";

  const onEvent = (name, data) => {
    switch (name) {
      case "request":
        state.requestId = data.request_id || "";
        break;
      case "context":
        view.meta.textContent = "سياق: " + (data.paths || []).join(" · ");
        break;
      case "thinking": {
        const reveal = document.documentElement.dataset.think !== "0";
        view.turn.querySelector(".thinking").classList.toggle("hidden", !reveal);
        view.thinking.textContent += data.token || "";
        /* The label carries the tail of the thought, not a number: the last
           words say more than any count ever did. */
        const tail = (view.thinking.textContent || "").trim().slice(-70);
        view.label.textContent = "💭 تفكير آلي… " + (tail ? "· " + tail : "");
        state.turnBlocks.push({ kind: "thinking", text: data.token || "" });
        break;
      }
      case "provider":
        view.meta.textContent = "المزوّد: " + (data.provider || "") + " " + (data.model || "");
        break;
      case "tool_call":
        view.toolEls.push({ name: data.name, el: addTool(view, data.name, data.args) });
        state.turnBlocks.push({ kind: "tool_call", text: data.name,
                                args: data.args || {} });
        break;
      case "tool_result": {
        const entry = (view.toolEls || []).find((t) => t.name === data.name) ||
                      (view.toolEls || [])[view.toolEls.length - 1];
        if (entry) addResult(view, entry.el, data.ok, data.summary);
        state.turnBlocks.push({ kind: "tool_result", text: data.name,
                                ok: data.ok, summary: String(data.summary || "").slice(0, 400) });
        break;
      }
      case "terminal":
        addPanelTerminal(view, data.line || "");
        break;
      case "diff":
        addDiff(view, data.path, data.before, data.after);
        state.turnBlocks.push({ kind: "diff", text: data.path,
                                before: data.before, after: data.after });
        toast("✏️ عدّل آلي: " + data.path);
        break;
      case "text":
        answerSoFar += data.token || "";
        view.answer.innerHTML = renderMarkdown(answerSoFar);
        break;
      case "error":
        view.answer.classList.add("failed");
        view.hadError = true;
        view.answer.innerHTML += "<p>⚠️ " + escapeHtml(data.error || "خطأ") + "</p>";
        break;
      case "done": {
        /* A failed turn still emits `done` with an empty reply. Rendering it
           wiped the error the previous event had just written, so a dead brain
           looked like a silent, empty answer — exactly what the MacBook
           showed. Never clobber a message we already showed the owner. */
        if (view.hadError && !(data.reply || answerSoFar)) {
          view.answer.querySelector(".cursor")?.remove();
          view.meta.textContent = "العقل لم يرد";
          view.retry.hidden = false;
          break;
        }
        view.answer.innerHTML = renderMarkdown(data.reply || answerSoFar);
        if (data.stopped) {
          view.meta.textContent = "أُوقف قبل النهاية";
        } else {
          view.meta.textContent = view.meta.textContent ||
            (new Date().toLocaleTimeString("ar"));
        }
        if (data.sid) state.lastSid = data.sid;
        view.retry.hidden = !!data.ok;
        view.answer.querySelector(".cursor")?.remove();
        const shown = (document.documentElement.dataset.think === "0")
          ? "💭 مُختصر" : null;
        view.meta.textContent = shown || view.meta.textContent ||
          (new Date().toLocaleTimeString("ar"));
        saveTurn(question, data.reply || answerSoFar, view.files);
        break;
      }
      default:
        break;
    }
    $("panel").scrollTop = $("panel").scrollHeight;
  };

  stream("/api/chat", {
    message: question, model: state.model, sid: state.lastSid,
    context: state.lastContext, links: state.lastLinks,
  }, onEvent, controller.signal)
    .catch((err) => onEvent("error", { error: err.message }))
    .finally(() => {
      state.busy = false;
      $("btn-ask").disabled = false;
      $("btn-stop").hidden = true;
      $("btn-retry").hidden = true;
      loadTree(dirOf(state.openPath));
    });
}

/* ————— conversation history (survives a restart) ————— */

async function saveTurn(question, reply, files) {
  try {
    await api("/api/turns", { method: "POST", body: {
      question, reply: reply || "", model: state.model,
      files: files || [], blocks: state.turnBlocks.slice(0, 40),
    }});
    await loadHistory();
  } catch (_) { /* history is a nicety — never break the turn */ }
}

async function loadHistory() {
  let data = {};
  try { data = await api("/api/turns"); } catch (_) { return; }
  const list = $("history");
  list.replaceChildren();
  const turns = data.turns || [];
  if (!turns.length) {
    list.append(el("li", "history-empty", "لا محادثات محفوظة بعد"));
    return;
  }
  for (const turn of turns) {
    const item = el("li");
    const button = el("button", "row");
    button.append(el("span", "h-q", turn.question || "(بلا نص)"),
                  el("span", "h-ts", (turn.ts || "").slice(5)));
    button.title = (turn.model || "") + " — " + (turn.ts || "");
    button.onclick = () => openTurn(turn.id);
    item.append(button);
    list.append(item);
  }
}

async function openTurn(turnId) {
  let data = {};
  try { data = await api("/api/turns/" + encodeURIComponent(turnId)); }
  catch (err) { toast(err.message); return; }
  const turn = data.turn || {};
  $("turns").replaceChildren();
  const view = newTurn(turn.question || "");
  if ((turn.files || []).length) {
    view.turn.insertBefore(
      el("div", "ask-files", "📎 " + turn.files.join(" · ")), view.answer);
  }
  let thinking = "";
  for (const block of turn.blocks || []) {
    if (block.kind === "thinking") {
      thinking += block.text || "";
    } else if (block.kind === "tool_call") {
      view.toolEls.push({ name: block.text, el: addTool(view, block.text, block.args) });
    } else if (block.kind === "tool_result") {
      const entry = view.toolEls[view.toolEls.length - 1];
      if (entry) addResult(view, entry.el, block.ok, block.summary);
    } else if (block.kind === "diff") {
      addDiff(view, block.text, block.before, block.after);
    }
  }
  view.thinking.textContent = thinking;
  const tail = thinking.trim().slice(-70);
  view.label.textContent = "💭 تفكير آلي " + (tail ? "· " + tail : "");
  if (document.documentElement.dataset.think === "0") {
    view.turn.querySelector(".thinking").classList.add("hidden");
  }
  view.answer.innerHTML = renderMarkdown(turn.reply || "");
  view.meta.textContent = (turn.model || "") + " · " + (turn.ts || "");
  view.retry.onclick = () => ask(turn.question);
  state.lastAsk = turn.question || "";
  $("panel").scrollTop = 0;
}

/* ————— terminal pane ————— */

function termLine(text, cls) {
  const line = el("div", cls || null, text);
  $("terminal").append(line);
  $("terminal").scrollTop = $("terminal").scrollHeight;
}

async function runCommand(command) {
  command = (command || "").trim();
  if (!command) return;
  termLine("❯ " + command, "meta");
  $("btn-cmd-stop").hidden = false;
  const controller = new AbortController();
  state.cmdController = controller;
  await stream("/api/run", { command }, (name, data) => {
    if (name === "request") return;
    if (name === "terminal") { termLine(data.line || ""); return; }
    if (name === "error") { termLine("⚠️ " + data.error, "err"); return; }
    if (name === "done") {
      termLine(data.ok ? "✓ انتهى" : "✗ فشل", data.ok ? "meta" : "err");
      $("btn-cmd-stop").hidden = true;
    }
  }, controller.signal).catch((err) => termLine("⚠️ " + err.message, "err"));
}

/* ————— models + keys ————— */

async function loadModels() {
  const data = await api("/api/models");
  state.model = data.current;
  const select = $("model");
  select.replaceChildren();
  for (const model of data.models) {
    const option = el("option", null, model.label + (model.ready ? "" : "  🔒"));
    option.value = model.id;
    if (model.id === data.current) option.selected = true;
    select.append(option);
  }
  showModelNote(data);
}

function showModelNote(data) {
  const model = (data.models || []).find((m) => m.id === state.model);
  const note = $("model-note");
  note.classList.remove("warn");
  if (!model) { note.textContent = ""; return; }
  if (model.kind === "local") { note.textContent = "محلي — بدون إنترنت"; return; }
  if (model.ready) { note.textContent = model.note; return; }
  note.classList.add("warn");
  note.textContent = "يحتاج مفتاح — اضغط 🔑 لإضافته";
}

/* ————— brain reachability —————
   The brain is a loopback service ON THE OWNER'S PC. On a MacBook
   127.0.0.1 is the MacBook, so the ask reaches nothing — and until the
   client stopped erasing its own error event, the panel just stayed blank.
   Now the failure is stated before the owner types anything. */
async function checkBrain() {
  let status;
  try {
    status = await api("/api/brain/status");
  } catch (err) {
    status = { ok: false, url: "?", reachable: false,
               hint: "تعذر قراءة حالة العقل: " + err.message };
  }
  const banner = $("brain-banner");
  const state = $("brain-state");
  const url = status.url || "";
  const urlInput = $("brain-url");
  if (urlInput && !urlInput.value && url) urlInput.value = url;
  if (state) {
    state.textContent = status.reachable
      ? "العقل يستجيب على " + url + (status.key_set ? " (المفتاح مضبوط)" : " (بلا مفتاح)")
      : "لا يستجيب على " + url + " — " + (status.detail || "");
  }
  if (status.ok) {
    banner.hidden = true;
    banner.classList.add("ok");
    return status;
  }
  banner.hidden = false;
  banner.classList.remove("ok");
  banner.replaceChildren();
  const text = el("span");
  text.append(el("b", null, "⚠️ العقل غير متصل — "),
              url + " · " + (status.hint || status.detail || ""));
  const fix = el("button", null, "🔑 اضبط العنوان والمفتاح");
  fix.onclick = () => openKeys().catch((e) => toast(e.message));
  banner.append(text, fix);
  return status;
}

async function openKeys() {
  const data = await api("/api/keys/status");
  $("cfg-dir").textContent = data.config_dir;
  const rows = $("key-rows");
  rows.replaceChildren();

  const keyRow = (label, field, ready, hint) => {
    const row = el("div", "key-row");
    const input = el("input");
    input.type = "password";
    input.placeholder = hint ? ("مضبوط: " + hint) : "ألصق المفتاح هنا";
    input.dataset.field = field;
    const status = el("div", "k-state " + (ready ? "on" : "off"),
                      ready ? "✓ مضبوط" : "— غير مضبوط");
    row.append(el("div", "k-label", label), input, status);
    rows.append(row);
  };

  if (data.brain_key) {
    keyRow("العقل المحلي (HWK-AZiZA)", data.brain_key.field,
           data.brain_key.ready, data.brain_key.hint);
  }
  for (const model of data.models) {
    if (model.kind === "local") continue;
    keyRow(model.label, model.key_field || model.id, model.ready, model.hint);
  }
  const settings = await api("/api/models");
  $("custom-url").value = (settings.custom && settings.custom.base_url) || "";
  $("custom-model").value = (settings.custom && settings.custom.model) || "";
  await checkBrain();
  // The comfort settings read their CURRENT values, never defaults.
  const prefs = await api("/api/prefs");
  $("pref-font").value = String(prefs.font_size || "14");
  $("pref-wrap").value = prefs.word_wrap === "off" ? "off" : "on";
  $("pref-term").value = prefs.terminal_visible === "0" ? "0" : "1";
  $("pref-think").value = prefs.show_thinking === "0" ? "0" : "1";
  $("keys-dialog").showModal();
}

async function saveKeys() {
  for (const input of $("key-rows").querySelectorAll("input[data-field]")) {
    if (input.value) {
      await api("/api/keys", { method: "POST",
        body: { field: input.dataset.field, key: input.value } });
      input.value = "";
    }
  }
  await api("/api/settings", { method: "POST", body: {
    custom_base_url: $("custom-url").value.trim(),
    custom_model: $("custom-model").value.trim(),
    brain_url: $("brain-url").value.trim(),
  }});
  const prefs = {
    font_size: $("pref-font").value,
    word_wrap: $("pref-wrap").value,
    terminal_visible: $("pref-term").value,
    show_thinking: $("pref-think").value,
  };
  await api("/api/prefs", { method: "POST", body: prefs });
  applyPrefs(prefs);
  syncMonacoPrefs();
  toggleTerminal(prefs.terminal_visible !== "0");
  $("keys-dialog").close();
  await loadModels();
  const brain = await checkBrain();
  toast(brain.ok ? "حُفظت الإعدادات" : "حُفظت — لكن العقل ما زال غير متصل");
}

/* ————— quick open (Ctrl+P) + @mention ————— */

const fuzzyScore = (query, text) => {
  const q = query.toLowerCase().replace(/^@/, "");
  const t = text.toLowerCase();
  if (!q) return 2;
  const direct = t.indexOf(q);
  if (direct === 0) return 0;
  if (direct > 0) return 1;
  let cursor = 0;
  for (const char of q) {
    const found = t.indexOf(char, cursor);
    if (found < 0) return -1;
    cursor = found + 1;
  }
  return 2;
};

function openPalette(mode) {
  state.paletteMode = mode;
  $("palette-input").value = "";
  $("palette").hidden = false;
  renderPalette("");
  $("palette-input").focus();
}

function closePalette() {
  $("palette").hidden = true;
}

function renderPalette(query) {
  const scored = [];
  for (const file of state.allFiles) {
    const score = fuzzyScore(query, file.path);
    if (score >= 0) scored.push({ ...file, score });
  }
  scored.sort((a, b) => (a.score - b.score) ||
    (a.path.length - b.path.length) || a.path.localeCompare(b.path));
  const list = $("palette-list");
  list.replaceChildren();
  for (const item of scored.slice(0, 40)) {
    const row = el("li", "palette-row");
    row.append(el("span", "p-name", item.name),
               el("span", "p-path", item.path));
    row.onclick = () => choosePalette(item.path);
    list.append(row);
  }
  if (!scored.length) list.append(el("li", "palette-empty", "لا نتائج"));
}

function choosePalette(path) {
  if (state.paletteMode === "mention") {
    const input = $("ask");
    const caret = input.selectionStart ?? input.value.length;
    const before = input.value.slice(0, caret).replace(/@\S*$/, "");
    input.value = before + "@" + path + " " + input.value.slice(caret);
    input.focus();
    addContext(path, "");
  } else {
    openFile(path).catch((err) => toast(err.message));
  }
  closePalette();
}

/* ————— auto-update —————
   The four surfaces are RENDERED BY THE SERVER (update_ui.py) so their
   Arabic labels and ids are assertable in pytest without a browser, and so a
   hub-supplied version string is HTML-escaped in exactly one place. This
   section only wires ids -> /api/update/*. The definitions themselves arrive
   as text from /api/update/ui/script and are evaluated on boot — one source
   of truth, no second copy to drift. */

async function mountUpdateUi() {
  const settingsSlot = $("update-settings-slot");
  if (settingsSlot) {
    settingsSlot.innerHTML = await api("/api/update/ui/settings");
  }
  const dialogSlot = $("update-dialog-slot");
  if (dialogSlot && !dialogSlot.firstElementChild) {
    dialogSlot.innerHTML = await api("/api/update/ui/dialog");
  }
  await updateMount();
  if (state.update && state.update.can_update && state.update.config.auto_check) {
    await updateCheck(false).catch(() => {});
  }
}

async function injectUpdateScript() {
  const code = await api("/api/update/ui/script");
  // eslint-disable-next-line no-new-func
  new Function(code)();
}

/* ————— boot ————— */

async function loadWorkspace() {
  const data = await api("/api/workspace");
  state.workspace = data.path;
  const name = (data.path || "").split(/[\\/]/).filter(Boolean).pop();
  $("ws-name").textContent = name || data.path || "—";
  $("ws-name").title = data.path || "";
  await loadTree(".");
  await loadAllFiles();
}

async function initMonaco() {
  try {
    if (!window.monaco) throw new Error("no cdn");
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("timeout")), 12000);
      window.require.config({ paths: { vs: "https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs" } });
      window.require(["vs/editor/editor.main"], () => {
        clearTimeout(timer);
        resolve();
      }, (err) => { clearTimeout(timer); reject(err); });
    });
    monaco.editor.defineTheme("aali-studio", {
      base: "vs-dark", inherit: true,
      rules: [],
      colors: {
        "editor.background": "#1b1b19",
        "editorGutter.background": "#1b1b19",
        "editorLineNumber.foreground": "#5d5a53",
        "editor.selectionBackground": "#4a4128",
        "editorCursor.foreground": "#d8b46a",
      },
    });
    const edFont = parseFloat(getComputedStyle(document.documentElement)
      .getPropertyValue("--ed-font")) || 13;
    state.editor = monaco.editor.create($("editor"), {
      value: "", language: "plaintext", theme: "aali-studio",
      fontSize: edFont, fontFamily: '"Cascadia Mono", Consolas, monospace',
      automaticLayout: true, minimap: { enabled: false },
      wordWrap: document.documentElement.dataset.wrap === "off" ? "off" : "on",
      scrollBeyondLastLine: false, tabSize: 4,
    });
    state.editor.onDidChangeContent(() => {
      markDirty(true);
      const tab = activeTab();
      if (tab) tab.content = state.editor.getValue();
      autoContext();
    });
    state.editor.onDidChangeCursorSelection(() => autoContext());
    state.editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, saveCurrent);
    state.editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyP,
      () => openPalette("quick"));
  } catch (_) {
    showPlainEditor("Monaco لم يُحمّل (لا إنترنت؟) — نستخدم محرراً نصياً بسيطاً. كل الوظائف الأخرى تعمل.");
  }
}

async function saveCurrent() {
  if (!state.openPath) { toast("لا يوجد ملف مفتوح"); return; }
  try {
    await saveFile(state.openPath, currentContent());
  } catch (err) {
    toast("تعذر الحفظ: " + err.message);
  }
}

function toggleTerminal(show) {
  $("terminal-pane").hidden = !show;
}

/* ————— UI preferences (2026-10-03) —————
   Saved server-side in settings.json and echoed through the boot URL, so the
   window, a browser tab and a future second window all agree on the owner's
   choices. :root[data-*] drives the CSS; Monaco reads the same variables. */

const FONT_SIZES = ["12", "13", "14", "15", "16", "18"];

function applyPrefs(prefs) {
  const root = document.documentElement;
  const font = String(prefs.font_size || "14");
  root.dataset.fs = FONT_SIZES.includes(font) ? font : "14";
  root.dataset.wrap = prefs.word_wrap === "off" ? "off" : "on";
  root.dataset.term = prefs.terminal_visible === "0" ? "0" : "1";
  root.dataset.think = prefs.show_thinking === "0" ? "0" : "1";
}

function applyUrlPrefs() {
  try {
    const query = new URLSearchParams(location.search);
    if (![...query.keys()].length) return;
    applyPrefs({
      font_size: query.get("fs") || "14",
      word_wrap: query.get("wrap") || "on",
      terminal_visible: query.get("term") || "1",
      show_thinking: "1",
    });
  } catch (_) { /* prefs are cosmetic — never block boot */ }
}

function syncMonacoPrefs() {
  if (!state.editor) return;
  const size = parseFloat(getComputedStyle(document.documentElement)
    .getPropertyValue("--ed-font")) || 13;
  const wrap = document.documentElement.dataset.wrap === "off" ? "off" : "on";
  state.editor.updateOptions({ fontSize: size, wordWrap: wrap });
  state.tabs.forEach((t) => t.model && t.model.updateOptions({ wordWrap: wrap }));
}

function wire() {
  $("btn-ask").onclick = () => ask($("ask").value);
  $("btn-link").onclick = () => readLink();
  $("link-url").onkeydown = (event) => {
    if (event.key === "Enter") { event.preventDefault(); readLink(); }
  };
  $("ask").onkeydown = (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      ask($("ask").value);
    }
  };
  $("ask").oninput = (event) => {
    const caret = event.target.selectionStart ?? 0;
    const before = event.target.value.slice(0, caret);
    if (/(^|\s)@\S*$/.test(before)) {
      openPalette("mention");
      $("palette-input").value = before.slice(before.lastIndexOf("@") + 1);
      renderPalette($("palette-input").value);
    } else if (!$("palette").hidden && state.paletteMode === "mention") {
      closePalette();
    }
  };
  $("btn-addcontext").onclick = () => {
    if (state.openPath) { addContext(state.openPath, selectionOf()); toast("أُرفق " + state.openPath); }
    else toast("افتح ملفاً أولاً");
  };
  $("btn-stop").onclick = async () => {
    if (state.requestId) {
      await api("/api/stop", { method: "POST", body: { request_id: state.requestId } });
    }
    state.controller?.abort();
    state.busy = false;
    $("btn-ask").disabled = false;
    $("btn-stop").hidden = true;
    toast("أُوقف الطلب");
  };
  $("btn-retry").onclick = () => ask(state.lastAsk);
  $("btn-clear").onclick = () => $("turns").replaceChildren();
  $("btn-save").onclick = saveCurrent;
  $("btn-refresh").onclick = () => loadTree(dirOf(state.openPath));
  $("btn-folder").onclick = async () => {
    const data = await api("/api/pick-folder").catch(() => null);
    if (data && data.path) {
      await loadWorkspace();
      toast("المجلد: " + data.path);
    }
  };
  $("ws-name").onclick = () => $("btn-folder").click();
  $("btn-newfile").onclick = async () => {
    const name = prompt("اسم الملف الجديد (داخل مجلد المشروع):");
    if (!name) return;
    try {
      await api("/api/file", { method: "POST", body: { path: name, content: "" } });
      await loadTree(dirOf(name));
      await openFile(name);
    } catch (err) { toast(err.message); }
  };
  $("btn-delete").onclick = async () => {
    if (!state.openPath) { toast("لا يوجد ملف مفتوح"); return; }
    if (!confirm("حذف " + state.openPath + " نهائياً؟")) return;
    try {
      await api("/api/delete", { method: "POST", body: { path: state.openPath } });
      await closeTab(state.openPath);
      await loadTree(".");
      toast("حُذف");
    } catch (err) { toast(err.message); }
  };
  $("btn-run").onclick = () => { toggleTerminal(true); runCommand($("cmd").value); };
  $("btn-cmd").onclick = () => runCommand($("cmd").value);
  $("cmd").onkeydown = (event) => { if (event.key === "Enter") runCommand($("cmd").value); };
  $("btn-cmd-stop").onclick = () => { state.cmdController?.abort(); $("btn-cmd-stop").hidden = true; };
  $("btn-panel-toggle").onclick = () => toggleTerminal($("terminal-pane").hidden);
  $("btn-term-toggle").onclick = () => toggleTerminal(true);
  $("model").onchange = async () => {
    state.model = $("model").value;
    await api("/api/models", { method: "POST", body: { model: state.model } });
    await loadModels();
  };
  $("btn-keys").onclick = () => openKeys().catch((e) => toast(e.message));
  $("btn-keys-save").onclick = () => saveKeys().catch((e) => toast(e.message));
  $("btn-keys-close").onclick = () => $("keys-dialog").close();
  $("btn-history-clear").onclick = async () => {
    if (!confirm("امسح كل المحادثات المحفوظة؟")) return;
    await api("/api/turns", { method: "DELETE" });
    await loadHistory();
  };
  $("btn-about").onclick = async () => {
    const about = await api("/api/about");
    $("about-ar").textContent = about.about_ar;
    $("about-en").textContent = about.about_en;
    $("about-limits").textContent =
      "ملاحظات صادقة: عقل HWK-AZiZA يعطي جوابه دفعة واحدة، "
      + "وستوديو يعرضه كلمة كلمة (المحتوى حرفي، الإيقاع تجميلي فقط). "
      + "النماذج الأخرى تبثّ رموزاً حقيقية مباشرة من المزوّد. "
      + "زر الإيقاف يقطع الاتصال — وقد يكمل العقل المحلي دورته جوفه. "
      + "Studio لا ينزّل أي نموذج على الحاسب.";
    $("about-dialog").showModal();
  };
  $("btn-about-close").onclick = () => $("about-dialog").close();

  $("palette-input").oninput = (event) => renderPalette(event.target.value);
  $("palette-input").onkeydown = (event) => {
    if (event.key === "Escape") { closePalette(); return; }
    if (event.key === "Enter") {
      const first = $("palette-list").querySelector(".palette-row");
      if (first) first.click();
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      const rows = [...$("palette-list").querySelectorAll(".palette-row")];
      if (!rows.length) return;
      event.preventDefault();
      const index = rows.findIndex((r) => r.classList.contains("active"));
      const next = event.key === "ArrowDown"
        ? Math.min(index + 1, rows.length - 1)
        : Math.max(index - 1, 0);
      rows.forEach((r) => r.classList.remove("active"));
      rows[next].classList.add("active");
      rows[next].scrollIntoView({ block: "nearest" });
    }
  };
  $("plain-editor").oninput = () => { markDirty(true); autoContext(); };

  document.addEventListener("keydown", (event) => {
    const ctrl = event.ctrlKey || event.metaKey;
    if (ctrl && event.key.toLowerCase() === "s") {
      event.preventDefault();
      saveCurrent();
    }
    if (ctrl && event.key.toLowerCase() === "p") {
      event.preventDefault();
      openPalette("quick");
    }
    if (ctrl && event.key.toLowerCase() === "w" && state.openPath) {
      event.preventDefault();
      closeTab(state.openPath);
    }
    if (event.key === "Escape" && !$("palette").hidden) closePalette();
    // Ctrl+Tab / Ctrl+Shift+Tab: cycle tabs like every real IDE.
    if (event.key === "Tab" && ctrl && state.tabs.length > 1) {
      event.preventDefault();
      const i = state.tabs.findIndex((t) => t.path === state.openPath);
      const step = event.shiftKey ? -1 : 1;
      const next = state.tabs[(i + step + state.tabs.length) % state.tabs.length];
      switchTab(next.path);
    }
  });
}

async function boot() {
  wire();
  applyUrlPrefs();
  toggleTerminal(document.documentElement.dataset.term !== "0");
  renderChips();
  renderTabs();
  await loadModels().catch((e) => toast(e.message));
  await loadWorkspace().catch((e) => toast(e.message));
  await checkBrain().catch(() => {});
  await loadHistory().catch(() => {});
  await initMonaco();
  setContent("", "");
  syncMonacoPrefs();
  await injectUpdateScript().catch(() => {});
  await mountUpdateUi().catch((e) => toast(e.message));
}

boot();