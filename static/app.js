"use strict";

const $ = (sel) => document.querySelector(sel);
const messagesEl = $("#messages");
const emptyEl = $("#empty");
const input = $("#input");
const sendBtn = $("#send");

const DOMAIN_NAMES = { files: "Files", database: "Database", web: "Web API", system: "System", unknown: "Rejected" };

// Line icons (shapes from Lucide, ISC licence: https://lucide.dev). Stroke uses currentColor.
const ICON_PATHS = {
  book: '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>',
  weather: '<path d="M12 2v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="M20 12h2"/><path d="m19.07 4.93-1.41 1.41"/><path d="M15.95 12.65a4 4 0 0 0-5.93-4.13"/><path d="M13 22H7a5 5 0 1 1 4.9-6H13a3 3 0 0 1 0 6Z"/>',
  calculator: '<rect width="16" height="20" x="4" y="2" rx="2"/><path d="M8 6h8"/><path d="M16 14v4"/><path d="M16 10h.01"/><path d="M12 10h.01"/><path d="M8 10h.01"/><path d="M12 14h.01"/><path d="M8 14h.01"/><path d="M12 18h.01"/><path d="M8 18h.01"/>',
  laptop: '<path d="M20 16V7a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v9m16 0H4m16 0 1.28 2.55a1 1 0 0 1-.9 1.45H3.62a1 1 0 0 1-.9-1.45L4 16"/>',
  trash: '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><path d="M10 11v6"/><path d="M14 11v6"/>',
  file: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M16 13H8"/><path d="M16 17H8"/><path d="M10 9H8"/>',
  database: '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/>',
  globe: '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/>',
  terminal: '<path d="m4 17 6-6-6-6"/><path d="M12 19h8"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  ban: '<circle cx="12" cy="12" r="10"/><path d="m4.9 4.9 14.2 14.2"/>',
  pause: '<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>',
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
};
function icon(name) {
  return `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">${ICON_PATHS[name] || ""}</svg>`;
}
const DOMAIN_ICONS = { files: "file", database: "database", web: "globe", system: "terminal", unknown: "ban" };
const STATUS_ICONS = { success: "check", error: "x", blocked: "ban", pending: "pause", approved: "check", rejected: "undo" };
const STATUS_TEXT = {
  success: "Done", error: "Failed", blocked: "Blocked", pending: "Needs approval",
  approved: "Approved", rejected: "Rejected",
};
const badgeHtml = (status) => `${icon(STATUS_ICONS[status])}${esc(STATUS_TEXT[status] || status)}`;

let busy = false;
let currentBubble = null; // the agent bubble that receives the next batch of events
let knownFiles = new Set();
let knownTasks = new Set();

// ------------------------------------------------------------------ utils

function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function el(tag, className, html) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (html !== undefined) node.innerHTML = html;
  return node;
}

// Small, safe markdown subset: everything is escaped first, then formatted.
function markdown(src) {
  const blocks = [];
  let text = esc(src).replace(/```(?:\w+)?\n?([\s\S]*?)```/g, (_, code) => {
    blocks.push(`<pre><code>${code.replace(/\n$/, "")}</code></pre>`);
    return `\u0000${blocks.length - 1}\u0000`;
  });
  const inline = (s) =>
    s.replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,!?]|$)/g, "$1<em>$2</em>");
  const out = [];
  let list = null;
  const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    let m;
    if ((m = line.match(/^\u0000(\d+)\u0000$/))) { closeList(); out.push(blocks[+m[1]]); continue; }
    if ((m = line.match(/^\s*[-*•]\s+(.*)/))) {
      if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; }
      out.push(`<li>${inline(m[1])}</li>`); continue;
    }
    if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) {
      if (list !== "ol") { closeList(); out.push("<ol>"); list = "ol"; }
      out.push(`<li>${inline(m[1])}</li>`); continue;
    }
    closeList();
    if ((m = line.match(/^#{1,4}\s+(.*)/))) { out.push(`<h4>${inline(m[1])}</h4>`); continue; }
    if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  closeList();
  return out.join("").replace(/\u0000(\d+)\u0000/g, (_, i) => blocks[+i]);
}

function toast(message) {
  const t = $("#toast");
  t.textContent = message;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, 4000);
}

async function api(path, options = {}) {
  const opts = { headers: { "Content-Type": "application/json" }, credentials: "same-origin", ...options };
  let res;
  try {
    res = await fetch(path, opts);
  } catch {
    throw new Error("Can't reach the server. Check your connection and try again.");
  }
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON error page */ }
  if (!res.ok) {
    const detail = data?.error || (Array.isArray(data?.detail) ? data.detail[0]?.msg : data?.detail);
    throw new Error(detail || `Request failed (${res.status}).`);
  }
  return data;
}

function scrollDown() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function formatArgs(args) {
  return Object.entries(args || {})
    .map(([k, v]) => {
      let s = typeof v === "string" ? JSON.stringify(v) : JSON.stringify(v);
      if (s && s.length > 60) s = s.slice(0, 57) + "…\"";
      return `${k}=${s}`;
    })
    .join("  ");
}

function formatResult(result) {
  if (result == null) return "";
  if (typeof result === "object" && typeof result.content === "string") return result.content;
  if (typeof result === "object" && typeof result.stdout === "string") return result.stdout || "(no output)";
  return JSON.stringify(result, null, 2);
}

// --------------------------------------------------------------- messages

function addUser(text) {
  emptyEl.hidden = true;
  const msg = el("article", "msg user");
  msg.append(el("div", "bubble", esc(text)));
  messagesEl.append(msg);
  currentBubble = null;
  scrollDown();
}

function agentBubble() {
  if (currentBubble) return currentBubble;
  emptyEl.hidden = true;
  const msg = el("article", "msg agent");
  msg.append(el("div", "avatar", "SP"));
  const bubble = el("div", "bubble");
  bubble.append(el("ol", "steps"));
  msg.append(bubble);
  messagesEl.append(msg);
  currentBubble = bubble;
  return bubble;
}

function renderAction(ev) {
  const li = el("li", `action d-${ev.domain || "unknown"} s-${ev.status}`);
  if (ev.pending_id) li.dataset.pendingId = ev.pending_id;
  li.innerHTML = `
    <div class="action-head">
      <span class="domain">${icon(DOMAIN_ICONS[ev.domain] || "ban")}${esc(DOMAIN_NAMES[ev.domain] || ev.domain || "")}</span>
      <code class="tool">${esc(ev.tool)}</code>
      <span class="args">${esc(formatArgs(ev.args))}</span>
      <span class="badge">${badgeHtml(ev.status)}</span>
    </div>
    <p class="summary"${ev.status === "pending" ? " hidden" : ""}>${esc(ev.summary || "")}</p>`;
  const result = formatResult(ev.result);
  if (result && ev.status === "success") {
    const det = el("details");
    det.innerHTML = `<summary>Show result${ev.ms != null ? ` · ${ev.ms} ms` : ""}</summary><pre>${esc(result)}</pre>`;
    li.append(det);
  }
  return li;
}

function renderConfirm(li, pending) {
  const box = el("div", "confirm");
  box.innerHTML = `<p><strong>Approve this action?</strong> ${esc(pending.reason)}</p>`;
  const yes = el("button", "btn-approve", "Approve");
  const no = el("button", "btn-reject", "Reject");
  yes.type = no.type = "button";
  yes.addEventListener("click", () => decide(pending.id, true, li));
  no.addEventListener("click", () => decide(pending.id, false, li));
  box.append(yes, no);
  li.append(box);
}

function renderEntry(entry) {
  const bubble = agentBubble();
  const steps = bubble.querySelector(".steps");
  for (const ev of entry.events || []) {
    if (ev.type === "thought") steps.append(el("li", "thought", esc(ev.text)));
    else if (ev.type === "notice") steps.append(el("li", "notice", esc(ev.text)));
    else if (ev.type === "error") steps.append(el("li", "error-box", esc(ev.text)));
    else if (ev.type === "action") {
      const li = renderAction(ev);
      if (ev.status === "pending" && entry.pending && entry.pending.id === ev.pending_id) renderConfirm(li, entry.pending);
      steps.append(li);
    }
  }
  if (entry.reply) bubble.append(el("div", "reply", markdown(entry.reply)));
  if (entry.reply || entry.error) currentBubble = null;
  scrollDown();
}

function showThinking() {
  const bubble = agentBubble();
  const node = el("div", "thinking", `<span class="dots"><i></i><i></i><i></i></span><span class="label">Thinking…</span>`);
  bubble.append(node);
  const started = Date.now();
  const timer = setInterval(() => {
    const s = Math.round((Date.now() - started) / 1000);
    node.querySelector(".label").textContent = s < 4 ? "Thinking…" : `Working… ${s}s (the free model can be slow)`;
  }, 1000);
  scrollDown();
  return () => { clearInterval(timer); node.remove(); };
}

function setBusy(value) {
  busy = value;
  sendBtn.disabled = value;
  input.disabled = value;
  document.querySelectorAll(".confirm button").forEach((b) => { b.disabled = value; });
  if (!value) input.focus();
}

// ---------------------------------------------------------------- actions

async function send(text) {
  text = text.trim();
  if (!text || busy) return;
  addUser(text);
  input.value = "";
  autosize();
  setBusy(true);
  const stop = showThinking();
  try {
    const res = await api("/api/chat", { method: "POST", body: JSON.stringify({ message: text }) });
    stop();
    renderEntry(res);
    renderWorkspace(res.workspace);
  } catch (err) {
    stop();
    renderEntry({ events: [{ type: "error", text: err.message }], error: err.message });
  } finally {
    setBusy(false);
    refreshLog();
  }
}

async function decide(pendingId, approve, li) {
  if (busy) return;
  const box = li.querySelector(".confirm");
  setBusy(true);
  box?.remove();
  li.querySelector(".summary").hidden = false;
  li.className = li.className.replace(/s-\w+/, approve ? "s-approved" : "s-rejected");
  li.querySelector(".badge").innerHTML = badgeHtml(approve ? "approved" : "rejected");
  if (!approve) li.querySelector(".summary").textContent += " You rejected it, so it was not run.";
  currentBubble = li.closest(".bubble");
  const stop = showThinking();
  try {
    const res = await api("/api/confirm", { method: "POST", body: JSON.stringify({ pending_id: pendingId, approve }) });
    stop();
    renderEntry(res);
    renderWorkspace(res.workspace);
  } catch (err) {
    stop();
    renderEntry({ events: [{ type: "error", text: err.message }], error: err.message });
  } finally {
    setBusy(false);
    refreshLog();
  }
}

// -------------------------------------------------------------- workspace

function fmtSize(bytes) {
  return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB`;
}

function fmtDue(due) {
  if (!due) return "";
  const d = new Date(due + "T00:00:00");
  return "due " + d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

function renderWorkspace(ws) {
  if (!ws) return;
  const files = $("#files");
  files.innerHTML = "";
  $("#file-count").textContent = ws.files.length;
  if (!ws.files.length) files.append(el("li", "empty-list", "No files yet."));
  const nextFiles = new Set();
  for (const f of ws.files) {
    const key = `${f.path}:${f.size}:${f.modified}`;
    nextFiles.add(key);
    const li = el("li");
    if (knownFiles.size && !knownFiles.has(key)) li.classList.add("flash");
    const btn = el("button", "file-btn", `${icon("file")}<span class="name">${esc(f.path)}</span><span class="size">${fmtSize(f.size)}</span>`);
    btn.type = "button";
    btn.addEventListener("click", () => openFile(f.path));
    li.append(btn);
    files.append(li);
  }
  knownFiles = nextFiles;

  const tasks = $("#tasks");
  tasks.innerHTML = "";
  const open = ws.tasks.filter((t) => !t.done).length;
  $("#task-count").textContent = `${open} open`;
  if (!ws.tasks.length) tasks.append(el("li", "empty-list", "No tasks yet."));
  const nextTasks = new Set();
  for (const t of ws.tasks) {
    const key = `${t.id}:${t.done}`;
    nextTasks.add(key);
    const li = el("li", `task${t.done ? " done" : ""}`);
    if (knownTasks.size && !knownTasks.has(key)) li.classList.add("flash");
    const meta = [`#${t.id}`, t.priority, fmtDue(t.due)].filter(Boolean).join(" · ");
    li.innerHTML = `<span class="dot ${esc(t.priority)}" title="${esc(t.priority)} priority"></span><span><span class="title">${esc(t.title)}</span><span class="meta">${esc(meta)}</span></span>`;
    tasks.append(li);
  }
  knownTasks = nextTasks;
}

async function openFile(path) {
  try {
    const res = await fetch(`/api/file?path=${encodeURIComponent(path)}`, { credentials: "same-origin" });
    if (!res.ok) throw new Error("Couldn't open that file.");
    $("#file-title").textContent = path;
    $("#file-body").textContent = await res.text();
    $("#file-dialog").showModal();
  } catch (err) {
    toast(err.message);
  }
}

// -------------------------------------------------------------------- log

const LOG_CLASS = {
  action: "action", confirmation: "warn", confirmation_requested: "warn", llm_call: "llm", reply: "llm",
  action_blocked: "bad", parse_error: "bad", llm_error: "bad", agent_error: "bad", rate_limited: "bad",
};

function describe(e) {
  switch (e.kind) {
    case "user_request": return `“${e.text}”`;
    case "llm_call": return `${e.model} · step ${e.step} · ${e.prompt_tokens || 0}+${e.completion_tokens || 0} tokens · ${e.latency_ms || 0} ms${e.attempts > 1 ? ` · ${e.attempts} attempts` : ""}`;
    case "action": return `${e.tool} → ${e.status}: ${e.summary}`;
    case "action_blocked": return `${e.tool}: ${e.error}`;
    case "confirmation_requested": return `${e.tool}: ${e.reason}`;
    case "confirmation": return `${e.tool} ${e.approved ? "approved" : "rejected"} by user`;
    case "parse_error": return e.error;
    case "reply": return `${e.steps} step(s), ${e.actions} action(s), ${e.ms} ms`;
    case "llm_error": case "agent_error": return e.error;
    case "rate_limited": return e.reason;
    case "workspace_reset": return "Workspace restored to the starter files";
    default: return "";
  }
}

async function refreshLog() {
  if ($("#tab-log").hidden) return;
  try {
    const { entries } = await api("/api/log");
    const list = $("#log");
    list.innerHTML = "";
    if (!entries.length) list.append(el("li", "empty-list", "Nothing logged yet."));
    for (const e of entries.slice().reverse()) {
      const time = new Date(e.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
      list.append(el("li", "", `<span class="k ${LOG_CLASS[e.kind] || ""}">${esc(e.kind)}</span><time>${esc(time)}</time><p>${esc(describe(e))}</p>`));
    }
  } catch (err) {
    toast(err.message);
  }
}

// ------------------------------------------------------------------ setup

function autosize() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 160) + "px";
  input.classList.toggle("scroll", input.scrollHeight > 160);
}

function selectTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  $("#tab-workspace").hidden = name !== "workspace";
  $("#tab-log").hidden = name !== "log";
  if (name === "log") refreshLog();
}

async function init() {
  $("#composer").addEventListener("submit", (e) => { e.preventDefault(); send(input.value); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(input.value); }
  });
  input.addEventListener("input", autosize);
  // Fill the example buttons' icon slots (data-icon="...").
  document.querySelectorAll("[data-icon]").forEach((slot) => { slot.innerHTML = icon(slot.dataset.icon); });
  $("#chips").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-prompt]");
    if (btn) send(btn.dataset.prompt);
  });
  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => selectTab(b.dataset.tab)));
  $("#file-close").addEventListener("click", () => $("#file-dialog").close());
  $("#file-dialog").addEventListener("click", (e) => { if (e.target.id === "file-dialog") e.target.close(); });
  $("#reset-btn").addEventListener("click", async () => {
    if (busy || !confirm("Reset your workspace? This restores the starter files, clears tasks and the chat.")) return;
    try {
      const res = await api("/api/reset", { method: "POST" });
      messagesEl.querySelectorAll(".msg").forEach((m) => m.remove());
      emptyEl.hidden = false;
      currentBubble = null;
      knownFiles = new Set(); knownTasks = new Set();
      renderWorkspace(res.workspace);
      refreshLog();
      toast("Workspace reset.");
    } catch (err) { toast(err.message); }
  });

  try {
    const state = await api("/api/state");
    const pill = $("#model-pill");
    pill.textContent = state.mock ? "Demo mode · no API key" : state.model;
    pill.classList.add(state.mock ? "demo" : "live");
    if (state.mock) pill.title = "No OPENROUTER_API_KEY is set, so a rule-based demo brain stands in for the LLM.";
    for (const entry of state.transcript) {
      if (entry.role === "user") addUser(entry.text);
      else renderEntry(entry);
    }
    renderWorkspace(state.workspace);
  } catch (err) {
    toast(err.message);
  }
  input.focus();
}

init();
