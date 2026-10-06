const STEPS = [
  { id: "document_parser", label: "문서 분석 중" },
  { id: "document_understanding", label: "요구사항 추출" },
  { id: "action_extractor", label: "Action Item 생성" },
  { id: "task_planner", label: "업무 계획 생성" },
  { id: "tool_executor", label: "Tool 실행" },
  { id: "responder", label: "완료" },
];

const CHIPS = [
  "이 문서 확인해서 우리가 해야 할 일 정리하고 진행해줘.",
  "아직 해야 할 일이 뭐야?",
  "완료보고서 초안 만들어줘.",
];

const NODE_TO_STEP = {
  intent_router: "document_parser",
  document_parser: "document_parser",
  document_understanding: "document_understanding",
  action_extractor: "action_extractor",
  task_planner: "task_planner",
  planner: "task_planner",
  tool_selector: "tool_executor",
  tool_executor: "tool_executor",
  verifier: "tool_executor",
  state_updater: "tool_executor",
  responder: "responder",
};

const LS_PROJECT = "docaction.projectId";
const LS_SESSION = "docaction.sessionId";

const state = {
  documents: [],
  documentId: null,
  sessionId: null,
  projectId: null,
  projectName: "",
  sessions: [],
  refDocs: [],
  running: false,
  activeStep: null,
  attachments: [],
  converting: false,
  editingChatId: null,
  chatMenuId: null,
};

const $ = (id) => document.getElementById(id);

function statusLine(text) {
  $("status-line").textContent = text;
}

function setBusy(busy) {
  state.running = busy;
  const input = $("chat-input");
  const btn = $("send-btn");
  if (input) input.disabled = busy;
  if (btn) btn.disabled = busy;
}

function stepIndex(id) {
  const idx = STEPS.findIndex((s) => s.id === id);
  return idx < 0 ? 0 : idx;
}

function renderProgress(label) {
  const idx = state.activeStep ? stepIndex(state.activeStep) : -1;
  const total = STEPS.length;
  const pct = state.running ? Math.max(8, Math.round(((idx + 1) / total) * 100)) : state.activeStep === "responder" ? 100 : 0;
  const fill = $("progress-fill");
  const lab = $("progress-label");
  if (fill) fill.style.width = `${state.running || state.activeStep === "responder" ? pct : 0}%`;
  if (lab) {
    if (state.running) lab.textContent = label || STEPS[Math.max(idx, 0)].label + "…";
    else if (state.activeStep === "responder") lab.textContent = "완료";
    else lab.textContent = "대기 중";
  }
}

function renderStepper() {
  $("stepper").innerHTML = STEPS.map((step) => {
    const active = state.activeStep === step.id;
    const done = STEPS.findIndex((s) => s.id === state.activeStep) > STEPS.findIndex((s) => s.id === step.id);
    const cls = active
      ? "bg-slate-900 text-white"
      : done
        ? "bg-slate-200 text-slate-700"
        : "bg-slate-50 text-slate-400 border border-slate-200";
    return `<li class="text-[11px] px-2 py-1 rounded-full ${cls}">${step.label}</li>`;
  }).join("");
  renderProgress();
}

function appendChat(role, content) {
  const wrap = document.createElement("div");
  wrap.className = `max-w-[90%] text-sm px-3 py-2 rounded-xl whitespace-pre-wrap ${role === "user" ? "msg-user ml-auto" : "msg-ai"}`;
  wrap.textContent = content;
  $("chat-list").appendChild(wrap);
  $("chat-list").scrollTop = $("chat-list").scrollHeight;
  return wrap;
}

function appendHtml(html) {
  const wrap = document.createElement("div");
  wrap.className = "max-w-[90%] msg-ai px-3 py-2 rounded-xl text-sm";
  wrap.innerHTML = html;
  $("chat-list").appendChild(wrap);
  $("chat-list").scrollTop = $("chat-list").scrollHeight;
  return wrap;
}

function notifyError(message) {
  appendChat("assistant", message);
  statusLine(message);
}

function showThinking(label) {
  hideThinking();
  const wrap = document.createElement("div");
  wrap.id = "thinking-bubble";
  wrap.className = "max-w-[90%] msg-ai px-3 py-2 rounded-xl flex items-center gap-2";
  wrap.innerHTML = `
    <span class="flex items-center gap-1">
      <span class="thinking-dot"></span>
      <span class="thinking-dot"></span>
      <span class="thinking-dot"></span>
    </span>
    <span class="text-xs text-slate-500" id="thinking-label">${escapeHtml(label || "생각 중")}</span>
  `;
  $("chat-list").appendChild(wrap);
  $("chat-list").scrollTop = $("chat-list").scrollHeight;
}

function updateThinking(label) {
  const el = $("thinking-label");
  if (el && label) el.textContent = label;
  else if (!$("thinking-bubble")) showThinking(label);
}

function hideThinking() {
  $("thinking-bubble")?.remove();
}

function ensureStreamBubble() {
  let wrap = $("stream-bubble");
  if (wrap) return wrap;
  hideThinking();
  wrap = document.createElement("div");
  wrap.id = "stream-bubble";
  wrap.className = "max-w-[90%] text-sm px-3 py-2 rounded-xl whitespace-pre-wrap msg-ai streaming-caret";
  wrap.textContent = "";
  $("chat-list").appendChild(wrap);
  return wrap;
}

function appendToken(text) {
  const wrap = ensureStreamBubble();
  wrap.textContent += text;
  $("chat-list").scrollTop = $("chat-list").scrollHeight;
}

function finishStream(fullText) {
  const wrap = $("stream-bubble");
  if (wrap) {
    wrap.classList.remove("streaming-caret");
    wrap.removeAttribute("id");
    if (fullText) wrap.textContent = fullText;
  } else if (fullText) {
    appendChat("assistant", fullText);
  }
}

function appendReportDownload(attachment) {
  if (!attachment || !attachment.id || !window.FileService) return;
  const name = attachment.filename || "주간 품질 현황 보고서.docx";
  const url = window.FileService.fileUrl(attachment.id, true);
  appendHtml(
    `<p class="text-sm font-medium">품질 현황 보고서가 생성되었습니다.</p>
     <article class="mt-2 border border-slate-200 rounded-lg px-3 py-2 bg-slate-50">
       <div class="text-sm font-medium truncate">${escapeHtml(name)}</div>
     </article>
     <div class="mt-2">
       <a class="text-[11px] px-2 py-1 rounded-md border border-slate-200 text-slate-600" href="${url}">다운로드</a>
     </div>`
  );
}

function renderChips() {
  $("chips").innerHTML = CHIPS.map(
    (text) =>
      `<button type="button" class="text-[11px] px-2 py-1 rounded-full border border-slate-200 text-slate-600 hover:bg-slate-50" data-chip="${encodeURIComponent(text)}">${text}</button>`
  ).join("");
  $("chips").querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => {
      $("chat-input").value = decodeURIComponent(btn.dataset.chip);
      $("chat-input").focus();
    });
  });
}

function priorityClass(p) {
  if (p === "P1") return "bg-red-50 text-red-700";
  if (p === "P3") return "bg-slate-100 text-slate-600";
  return "bg-amber-50 text-amber-700";
}

function renderActions(actions) {
  if (!actions.length) {
    $("action-list").innerHTML = `<p class="text-xs text-slate-400 p-2">아직 추출된 Action이 없습니다.</p>`;
    return;
  }
  $("action-list").innerHTML = actions
    .map(
      (a) => `
      <article class="border border-slate-200 rounded-lg p-3">
        <div class="flex items-start justify-between gap-2">
          <h3 class="text-sm font-semibold">${escapeHtml(a.title)}</h3>
          <span class="text-[10px] px-1.5 py-0.5 rounded ${priorityClass(a.priority)}">${a.priority}</span>
        </div>
        <p class="text-xs text-slate-500 mt-1">${escapeHtml(a.description || "")}</p>
        <div class="mt-2 flex flex-wrap gap-1 text-[10px] text-slate-500">
          <span class="px-1.5 py-0.5 bg-slate-50 rounded">마감 ${a.deadline || "-"}</span>
          <span class="px-1.5 py-0.5 bg-slate-50 rounded">${a.status}</span>
          <span class="px-1.5 py-0.5 bg-slate-50 rounded">${escapeHtml(a.deliverable || "")}</span>
        </div>
      </article>`
    )
    .join("");
}

function renderDocs() {
  const root = $("doc-list");
  if (!root) return;
  const refIds = new Set(state.refDocs.map((d) => d.id));
  if (!state.documents.length) {
    root.innerHTML = `<p class="text-[11px] text-slate-400 px-2 py-3">업로드된 문서가 없습니다.</p>`;
    return;
  }
  root.innerHTML = state.documents
    .map((d) => {
      const active = d.id === state.documentId ? "bg-slate-900 text-white" : "hover:bg-slate-50";
      const muted = d.id === state.documentId ? "text-slate-300" : "text-slate-400";
      const inChat = refIds.has(d.id);
      const fileKind = window.FileTypes.kind(d.filename);
      const typeLabel = fileKind === "spreadsheet" ? "Excel 데이터 파일" : ((d.file_type || "").toUpperCase() || "FILE");
      return `<div class="rounded-lg ${active}">
        <button type="button" data-preview="${d.id}" class="w-full text-left text-xs px-3 py-2">
          <div class="font-medium truncate">${fileKind === "spreadsheet" ? "📊" : "📄"} ${escapeHtml(d.filename)}</div>
          <div class="${muted}">${typeLabel} · ${window.FileTypes.formatSize(d.size_bytes)}</div>
        </button>
        <div class="flex gap-1 px-2 pb-2">
          <button type="button" data-add="${d.id}" class="text-[10px] px-1.5 py-0.5 rounded border ${d.id === state.documentId ? "border-white/30 text-white" : "border-slate-200 text-slate-600"} ${inChat ? "opacity-40" : ""}">${inChat ? "참조 중" : "현재 채팅에 추가"}</button>
          <button type="button" data-del="${d.id}" class="text-[10px] px-1.5 py-0.5 rounded ${d.id === state.documentId ? "text-white/80" : "text-slate-400"}">×</button>
        </div>
      </div>`;
    })
    .join("");
  root.querySelectorAll("[data-preview]").forEach((btn) => {
    btn.addEventListener("click", () => previewDocument(btn.dataset.preview));
  });
  root.querySelectorAll("[data-add]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      addDocToChat(btn.dataset.add);
    });
  });
  root.querySelectorAll("[data-del]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteProjectDocument(btn.dataset.del);
    });
  });
}

function applySessionTitle(id, title, source) {
  const row = state.sessions.find((s) => s.id === id);
  if (row && row.title_source === "user" && source !== "user") return;
  if (row) {
    row.title = title;
    if (source) row.title_source = source;
  }
  if (state.sessionId === id) setChatTitle(title);
  if (state.editingChatId !== id) renderChats();
}

function renderChats() {
  const root = $("chat-nav");
  if (!root) return;
  if (!state.sessions.length) {
    root.innerHTML = `<p class="text-[11px] text-slate-400 px-2 py-3">대화가 없습니다. 새 채팅을 만들어 보세요.</p>`;
    return;
  }
  root.innerHTML = state.sessions
    .map((s) => {
      const active = s.id === state.sessionId ? "bg-slate-900 text-white" : "hover:bg-slate-50";
      const muted = s.id === state.sessionId ? "text-white/80" : "text-slate-400";
      if (state.editingChatId === s.id) {
        return `<div class="flex items-center gap-1 rounded-lg ${active} px-1 py-1">
          <input data-rename-input="${s.id}" class="flex-1 min-w-0 text-xs px-2 py-1 rounded-md border border-slate-200 text-slate-900 outline-none" value="${escapeHtml(s.title || "새 대화")}" />
          <button type="button" data-rename-ok="${s.id}" class="shrink-0 w-6 h-6 text-xs ${muted}" aria-label="저장">✓</button>
          <button type="button" data-rename-cancel="${s.id}" class="shrink-0 w-6 h-6 text-xs ${muted}" aria-label="취소">×</button>
        </div>`;
      }
      const menuOpen = state.chatMenuId === s.id;
      return `<div class="relative flex items-center gap-0.5 rounded-lg ${active}">
        <button type="button" data-chat="${s.id}" class="flex-1 min-w-0 text-left text-xs px-3 py-2 truncate">${escapeHtml(s.title || "새 대화")}</button>
        <button type="button" data-chat-menu="${s.id}" class="shrink-0 w-7 h-7 text-xs ${muted}" aria-label="대화 메뉴">⋯</button>
        ${
          menuOpen
            ? `<div class="absolute right-1 top-8 z-30 w-28 rounded-lg border border-slate-200 bg-white shadow-lg py-1">
                <button type="button" data-rename="${s.id}" class="w-full text-left text-xs px-3 py-1.5 text-slate-700 hover:bg-slate-50">이름 변경</button>
                <button type="button" data-chat-del="${s.id}" class="w-full text-left text-xs px-3 py-1.5 text-slate-700 hover:bg-slate-50">삭제</button>
              </div>`
            : ""
        }
      </div>`;
    })
    .join("");
  root.querySelectorAll("[data-chat]").forEach((btn) => {
    btn.addEventListener("click", () => openChat(btn.dataset.chat));
  });
  root.querySelectorAll("[data-chat-menu]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      state.chatMenuId = state.chatMenuId === btn.dataset.chatMenu ? null : btn.dataset.chatMenu;
      renderChats();
    });
  });
  root.querySelectorAll("[data-rename]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      startRenameChat(btn.dataset.rename);
    });
  });
  root.querySelectorAll("[data-chat-del]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      state.chatMenuId = null;
      deleteChat(btn.dataset.chatDel);
    });
  });
  const input = root.querySelector("[data-rename-input]");
  if (input) {
    input.focus();
    input.select();
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        saveRenameChat(input.dataset.renameInput, input.value);
      }
      if (e.key === "Escape") {
        e.preventDefault();
        cancelRenameChat();
      }
    });
    input.addEventListener("click", (e) => e.stopPropagation());
  }
  root.querySelectorAll("[data-rename-ok]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const field = root.querySelector("[data-rename-input]");
      saveRenameChat(btn.dataset.renameOk, field ? field.value : "");
    });
  });
  root.querySelectorAll("[data-rename-cancel]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      cancelRenameChat();
    });
  });
}

function startRenameChat(id) {
  state.chatMenuId = null;
  state.editingChatId = id;
  renderChats();
}

async function saveRenameChat(id, value) {
  const title = String(value || "").trim();
  if (!title) {
    cancelRenameChat();
    return;
  }
  try {
    const session = await api(`/api/sessions/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
    state.editingChatId = null;
    applySessionTitle(id, session.title, "user");
    renderChats();
  } catch (err) {
    notifyError(`제목 변경 실패: ${err.message}`);
  }
}

function cancelRenameChat() {
  state.editingChatId = null;
  renderChats();
}

function renderRefDocs() {
  const root = $("ref-docs");
  if (!root) return;
  if (!state.refDocs.length) {
    root.innerHTML = "";
    root.classList.add("hidden");
    return;
  }
  root.classList.remove("hidden");
  root.innerHTML = `<div class="text-[11px] text-slate-400 mb-1">참조 문서</div>
    <div class="flex flex-wrap gap-1.5">${state.refDocs
      .map(
        (d) => `<span class="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded-md border border-slate-200 bg-slate-50">
          <button type="button" data-ref-preview="${d.id}" class="truncate max-w-[10rem]">📄 ${escapeHtml(d.filename)}</button>
          <button type="button" data-ref-remove="${d.id}" class="text-slate-400" aria-label="참조에서 제외">×</button>
        </span>`
      )
      .join("")}</div>`;
  root.querySelectorAll("[data-ref-preview]").forEach((btn) => {
    btn.addEventListener("click", () => previewDocument(btn.dataset.refPreview));
  });
  root.querySelectorAll("[data-ref-remove]").forEach((btn) => {
    btn.addEventListener("click", () => removeDocFromChat(btn.dataset.refRemove));
  });
}

function setChatTitle(title) {
  const el = $("chat-title");
  if (el) el.textContent = title || "새 대화";
}

function renderSidebar() {
  const name = $("project-name");
  if (name) name.textContent = state.projectName || "프로젝트";
  renderChats();
  renderDocs();
}

function renderTimeline(events) {
  if (!events.length) {
    $("timeline").innerHTML = `<p class="text-xs text-slate-400">Tool 실행 후 Mock Calendar 일정이 여기에 표시됩니다.</p>`;
    return;
  }
  $("timeline").innerHTML = events
    .map(
      (e) => `<div class="flex gap-3 py-1.5 border-b border-slate-100">
        <div class="w-28 shrink-0 text-xs text-slate-500">${(e.start_at || "").slice(0, 10)}</div>
        <div class="text-sm">${escapeHtml(e.title)}</div>
      </div>`
    )
    .join("");
}

function appendLog(label, extra = "") {
  const now = new Date().toLocaleTimeString("ko-KR", { hour12: false });
  const line = document.createElement("div");
  line.textContent = `[${now}] ${label}${extra ? " · " + extra : ""}`;
  $("log").appendChild(line);
  $("log").scrollTop = $("log").scrollHeight;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = await res.text();
    try {
      detail = JSON.parse(detail).detail || detail;
    } catch {
      /* keep text */
    }
    throw new Error(detail);
  }
  if (res.headers.get("content-type")?.includes("application/json")) {
    return res.json();
  }
  return res;
}

async function refreshDocuments() {
  if (!state.projectId) {
    state.documents = await api("/api/documents");
  } else {
    state.documents = await api(`/api/projects/${state.projectId}/documents`);
  }
  renderDocs();
}

async function refreshSessions() {
  if (!state.projectId) {
    state.sessions = [];
    renderChats();
    return;
  }
  state.sessions = await api(`/api/projects/${state.projectId}/sessions`);
  renderChats();
}

async function refreshPanels() {
  if (!state.sessionId) return;
  const [actions, calendar, events] = await Promise.all([
    api(`/api/sessions/${state.sessionId}/actions`),
    api(`/api/sessions/${state.sessionId}/calendar`),
    api(`/api/sessions/${state.sessionId}/events`),
  ]);
  renderActions(actions);
  renderTimeline(calendar);
  if (events.length && !$("log").childElementCount) {
    events.forEach((e) => appendLog(e.label || e.step));
  }
}

function renderMessages(messages) {
  const root = $("chat-list");
  root.innerHTML = "";
  if (!messages.length) {
    appendHtml(`<p class="text-xs text-slate-400">새 대화입니다. 왼쪽 문서를 현재 채팅에 추가한 뒤 질문하세요.</p>`);
    return;
  }
  messages.forEach((m) => appendChat(m.role === "user" ? "user" : "assistant", m.content));
}

async function previewDocument(id) {
  const doc = state.documents.find((d) => d.id === id) || state.refDocs.find((d) => d.id === id);
  if (!doc) return;
  state.documentId = id;
  renderDocs();
  window.PreviewPanel.open({
    id: doc.id,
    filename: doc.filename,
    kind: window.FileTypes.kind(doc.filename),
    size_bytes: doc.size_bytes || 0,
    documentId: doc.id,
  });
}

async function openChat(id) {
  if (!id) return;
  state.sessionId = id;
  state.chatMenuId = null;
  if (state.editingChatId && state.editingChatId !== id) state.editingChatId = null;
  localStorage.setItem(LS_SESSION, id);
  const [session, messages, refs] = await Promise.all([
    api(`/api/sessions/${id}`),
    api(`/api/sessions/${id}/messages`),
    api(`/api/sessions/${id}/documents`),
  ]);
  state.refDocs = refs;
  setChatTitle(session.title);
  renderMessages(messages);
  renderRefDocs();
  renderSidebar();
  $("log").innerHTML = "";
  await refreshPanels();
  statusLine("대화를 불러왔습니다.");
}

async function createChat() {
  if (!state.projectId) throw new Error("프로젝트가 없습니다.");
  const session = await api("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project_id: state.projectId, title: "새 대화" }),
  });
  await refreshSessions();
  await openChat(session.id);
}

async function deleteChat(id) {
  if (!confirm("이 대화만 삭제할까요? 프로젝트 문서는 그대로 유지됩니다.")) return;
  await api(`/api/sessions/${id}`, { method: "DELETE" });
  if (state.sessionId === id) {
    state.sessionId = null;
    localStorage.removeItem(LS_SESSION);
  }
  await refreshSessions();
  if (state.sessions[0]) await openChat(state.sessions[0].id);
  else await createChat();
}

async function addDocToChat(documentId) {
  await ensureSession();
  if (state.refDocs.some((d) => d.id === documentId)) {
    statusLine("이미 이 채팅에서 참조 중인 문서입니다.");
    return;
  }
  const session = await api(`/api/sessions/${state.sessionId}/documents`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ document_id: documentId }),
  });
  state.refDocs = await api(`/api/sessions/${state.sessionId}/documents`);
  const found = state.sessions.find((s) => s.id === session.id);
  if (found) found.document_ids = session.document_ids;
  renderRefDocs();
  renderDocs();
  statusLine("현재 채팅에 문서를 추가했습니다.");
}

async function removeDocFromChat(documentId) {
  if (!state.sessionId) return;
  await api(`/api/sessions/${state.sessionId}/documents/${documentId}`, { method: "DELETE" });
  state.refDocs = state.refDocs.filter((d) => d.id !== documentId);
  renderRefDocs();
  renderDocs();
}

async function deleteProjectDocument(id) {
  if (!confirm("프로젝트 문서 저장소에서 이 파일을 삭제할까요? 각 채팅의 참조만 함께 해제됩니다.")) return;
  await api(`/api/documents/${id}`, { method: "DELETE" });
  state.documents = state.documents.filter((d) => d.id !== id);
  state.refDocs = state.refDocs.filter((d) => d.id !== id);
  if (state.documentId === id) state.documentId = null;
  renderRefDocs();
  renderDocs();
}

async function ensureSession() {
  if (state.sessionId) return true;
  await createChat();
  return Boolean(state.sessionId);
}

async function ingestFile(file, { fromChat = false } = {}) {
  const invalid = window.FileService.validate(file);
  if (invalid) {
    notifyError(invalid);
    return null;
  }
  statusLine("문서 업로드 중...");
  const uploaded = await window.FileService.upload(file);
  uploaded.size_bytes = uploaded.size_bytes || file.size;
  if (uploaded.kind === "image") {
    syncAttachment(uploaded);
    if (fromChat) {
      appendChat("assistant", `${uploaded.filename} 이미지를 첨부했습니다. 미리보기로 확인할 수 있습니다.`);
    }
    statusLine("이미지 첨부 완료");
    return uploaded;
  }
  let doc = null;
  try {
    doc = await window.FileService.promote(uploaded.id, state.projectId);
  } catch (err) {
    syncAttachment(uploaded);
    if (uploaded.kind === "hwp") {
      appendHtml(
        `<div class="font-medium">${escapeHtml(uploaded.filename)}</div>
         <p class="text-xs text-slate-500 mt-1">HWP 파일입니다. PDF 변환 후 프로젝트 문서에 올라갑니다. [PDF로 변환]을 눌러 주세요.</p>`
      );
      statusLine("HWP 첨부 완료");
      return uploaded;
    }
    throw err;
  }
  uploaded.documentId = doc.id;
  await refreshDocuments();
  if (fromChat) {
    await addDocToChat(doc.id);
    appendChat("assistant", `${doc.filename}을 프로젝트 문서와 현재 채팅 참조에 추가했습니다.`);
  } else {
    statusLine(`${doc.filename}을 프로젝트 문서에 추가했습니다.`);
  }
  return uploaded;
}

async function uploadFile(file) {
  await ingestFile(file, { fromChat: false });
}

async function ensureDocumentForChat() {
  return ensureSession();
}

function parseSseChunk(buffer, onEvent) {
  const parts = buffer.split("\n\n");
  const rest = parts.pop() || "";
  for (const part of parts) {
    let event = "message";
    let data = "";
    for (const line of part.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      if (line.startsWith("data:")) data += line.slice(5).trim();
    }
    if (data) {
      try {
        onEvent(event, JSON.parse(data));
      } catch {
        onEvent(event, { raw: data });
      }
    }
  }
  return rest;
}

async function sendMessage(content) {
  if (window.FileTypes.isConvertCommand(content)) {
    const hwp = window.AttachmentBar.hwpItem();
    if (!hwp) {
      notifyError("변환할 HWP 파일이 첨부되어 있지 않습니다.");
      return;
    }
    appendChat("user", content);
    await convertHwp(hwp.id);
    return;
  }
  appendChat("user", content);
  if (!(await ensureDocumentForChat())) {
    notifyError("대화를 준비하지 못했습니다. 페이지를 새로고침해 보세요.");
    return;
  }
  if (state.running) return;
  setBusy(true);
  state.activeStep = "document_parser";
  renderStepper();
  renderProgress("생각 중…");
  showThinking("생각 중");
  appendLog("사용자 요청", content.slice(0, 40));
  statusLine("Agent 실행 중…");

  const payload = { content };
  const documentIds = state.refDocs.map((d) => d.id).filter(Boolean);
  if (documentIds.length) payload.document_ids = documentIds;
  const res = await fetch(`/api/sessions/${state.sessionId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    hideThinking();
    setBusy(false);
    throw new Error(await res.text());
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalText = "";
  let startedTokens = false;
  let replyReady = false;

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    buffer = parseSseChunk(buffer, (event, data) => {
      const node = data.node;
      if (node && NODE_TO_STEP[node]) {
        state.activeStep = NODE_TO_STEP[node];
        renderStepper();
      }
      if (data.label) {
        renderProgress(data.label + "…");
        if (!startedTokens) updateThinking(data.label);
      }
      if (event === "understanding") appendLog("요구사항 추출", (data.understanding && data.understanding.doc_type) || "");
      if (event === "actions") {
        appendLog("Action Item 생성", `${(data.actions || []).length}건`);
        renderActions(data.actions || []);
      }
      if (event === "plan") appendLog("업무 계획 생성", `${(data.tasks || []).length}개 Task`);
      if (event === "tool") appendLog(data.label || "Tool 실행", JSON.stringify(data.execution_results || data.tool_calls || "").slice(0, 80));
      if (event === "status") {
        appendLog(data.label || "진행 중");
      }
      if (event === "token" && data.content) {
        startedTokens = true;
        state.activeStep = "responder";
        renderStepper();
        renderProgress("답변 작성 중…");
        appendToken(data.content);
      }
      if (event === "done") {
        finalText = data.content || finalText;
        state.activeStep = "responder";
        renderStepper();
        hideThinking();
        finishStream(finalText);
        if (data.attachment && data.attachment.id) {
          appendReportDownload(data.attachment);
        }
        setBusy(false);
        renderProgress("완료");
        statusLine("Agent 실행 완료");
        replyReady = true;
        refreshPanels().catch(() => {});
      }
      if (event === "title" && data.title) {
        applySessionTitle(data.session_id || state.sessionId, data.title, data.title_source || "auto");
      }
      if (event === "error") {
        hideThinking();
        appendLog("오류", data.detail || "");
        statusLine(data.detail || "실행 실패");
      }
      if (data.label && event === "step") appendLog(data.label);
    });
  }

  if (!replyReady) {
    hideThinking();
    finishStream(finalText);
    await refreshPanels();
    setBusy(false);
    renderProgress("완료");
    statusLine("Agent 실행 완료");
  }
}

$("file").addEventListener("change", async (e) => {
  const file = e.target.files?.[0];
  if (!file) return;
  try {
    await uploadFile(file);
  } catch (err) {
    statusLine(err.message);
  } finally {
    e.target.value = "";
  }
});

$("chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const value = $("chat-input").value.trim();
  if (!value) return;
  $("chat-input").value = "";
  try {
    await sendMessage(value);
  } catch (err) {
    hideThinking();
    setBusy(false);
    notifyError(`실행 실패: ${err.message}`);
  }
});

function syncAttachment(item) {
  state.attachments = item ? [item] : [];
  renderAttachments();
}

function renderAttachments() {
  window.AttachmentBar.setItems(state.attachments);
}

function removeAttachment(id) {
  state.attachments = state.attachments.filter((item) => item.id !== id);
  renderAttachments();
  if (window.PreviewPanel?._item?.id === id) window.PreviewPanel.close();
}

function previewAttachment(item) {
  if (!item) return;
  window.PreviewPanel.open(item);
}

async function attachFiles(files) {
  const list = [...files];
  if (!list.length) return;
  if (list.length > 1) {
    notifyError("한 번에 하나의 파일만 첨부할 수 있습니다. 첫 번째 파일만 등록합니다.");
  }
  try {
    await ingestFile(list[0], { fromChat: true });
  } catch (err) {
    notifyError(`업로드 실패: ${err.message}`);
  }
}

async function convertHwp(id) {
  const item = state.attachments.find((a) => a.id === id) || window.AttachmentBar.hwpItem();
  if (!item) {
    notifyError("변환할 HWP 파일이 첨부되어 있지 않습니다.");
    return;
  }
  if (state.converting) return;
  state.converting = true;
  const card = appendHtml(
    `<div class="font-medium truncate">${escapeHtml(item.filename)}</div>
     <p class="text-xs text-slate-500 mt-1">PDF로 변환하고 있습니다...</p>
     <div class="indet-track mt-3"><div class="indet-bar"></div></div>`
  );
  appendLog("HWP → PDF 변환 시작", item.filename);
  try {
    const pdf = await window.FileService.convertPdf(id);
    const doc = await window.FileService.promote(pdf.id, state.projectId);
    pdf.documentId = doc.id;
    pdf.size_bytes = pdf.size_bytes || 0;
    syncAttachment(pdf);
    await refreshDocuments();
    await addDocToChat(doc.id);
    card.innerHTML = `
      <p class="text-sm font-medium">PDF 변환이 완료되었습니다. 문서 목록에도 올렸습니다.</p>
      <article class="mt-2 border border-slate-200 rounded-lg px-3 py-2 bg-slate-50">
        <div class="text-sm font-medium truncate">${escapeHtml(pdf.filename)}</div>
        <div class="text-[11px] text-slate-500 mt-0.5">PDF · ${window.FileTypes.formatSize(pdf.size_bytes)}</div>
      </article>
      <div class="mt-2 flex gap-1.5">
        <button type="button" class="text-[11px] px-2 py-1 rounded-md bg-slate-900 text-white" data-preview="${pdf.id}" data-name="${escapeHtml(pdf.filename)}">미리보기</button>
        <a class="text-[11px] px-2 py-1 rounded-md border border-slate-200 text-slate-600" href="${window.FileService.fileUrl(pdf.id, true)}">다운로드</a>
      </div>
      <p class="text-xs text-slate-500 mt-2">이어서 "자료 정리해줘"라고 하면 Action Item을 추출합니다.</p>`;
    card.querySelector("[data-preview]")?.addEventListener("click", (e) => {
      const btn = e.currentTarget;
      const pdfItem = {
        id: btn.dataset.preview,
        filename: btn.dataset.name,
        kind: "pdf",
        size_bytes: pdf.size_bytes,
        documentId: pdf.documentId,
      };
      window.PreviewPanel.open(pdfItem);
    });
    appendLog("PDF 변환 완료", pdf.filename);
    statusLine("PDF 변환 완료 · 문서 목록에 반영됨");
  } catch (err) {
    card.innerHTML = `<p class="text-sm">PDF 변환 실패: ${escapeHtml(err.message)}</p>`;
    statusLine(`PDF 변환 실패: ${err.message}`);
  } finally {
    state.converting = false;
  }
}

async function inspectAttachment(id) {
  try {
    const data = await window.FileService.readText(id);
    const preview = (data.text || "").slice(0, 1200);
    appendChat("assistant", `${data.filename} 내용입니다.\n\n${preview}${data.text.length > 1200 ? "\n\n…(이후 생략)" : ""}`);
  } catch (err) {
    notifyError(`문서 내용을 확인하지 못했습니다: ${err.message}`);
  }
}

async function summarizeAttachment(id) {
  try {
    statusLine("문서 요약 중...");
    const data = await window.FileService.summarize(id);
    appendChat("assistant", `${data.filename} 요약\n\n${data.summary}`);
    statusLine("요약 완료");
  } catch (err) {
    notifyError(`문서 요약 실패: ${err.message}`);
  }
}

$("chat-file")?.addEventListener("change", async (e) => {
  const file = e.target.files?.[0];
  try {
    if (file) await attachFiles([file]);
  } finally {
    e.target.value = "";
  }
});

function hideAttachMenu() {
  $("attach-menu")?.classList.add("hidden");
}

$("attach-toggle")?.addEventListener("click", (e) => {
  e.stopPropagation();
  $("attach-menu")?.classList.toggle("hidden");
});
$("attach-upload")?.addEventListener("click", () => {
  hideAttachMenu();
  $("chat-file")?.click();
});
$("attach-from-project")?.addEventListener("click", () => {
  hideAttachMenu();
  openDocPicker();
});
document.addEventListener("click", (e) => {
  if (!e.target.closest("#attach-toggle") && !e.target.closest("#attach-menu")) hideAttachMenu();
  if (state.chatMenuId && !e.target.closest("[data-chat-menu]") && !e.target.closest("[data-rename]")) {
    state.chatMenuId = null;
    renderChats();
  }
});

function openDocPicker() {
  const modal = $("doc-picker");
  const list = $("doc-picker-list");
  if (!modal || !list) return;
  const refIds = new Set(state.refDocs.map((d) => d.id));
  const available = state.documents.filter((d) => !refIds.has(d.id));
  if (!available.length) {
    list.innerHTML = `<p class="text-xs text-slate-400 p-2">추가할 수 있는 프로젝트 문서가 없습니다.</p>`;
  } else {
    list.innerHTML = available
      .map(
        (d) => `<button type="button" data-pick="${d.id}" class="w-full text-left text-sm px-3 py-2 rounded-lg hover:bg-slate-50">
          📄 ${escapeHtml(d.filename)}
        </button>`
      )
      .join("");
    list.querySelectorAll("[data-pick]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await addDocToChat(btn.dataset.pick);
        modal.classList.add("hidden");
      });
    });
  }
  modal.classList.remove("hidden");
}

$("doc-picker-close")?.addEventListener("click", () => $("doc-picker")?.classList.add("hidden"));
$("doc-picker")?.addEventListener("click", (e) => {
  if (e.target === $("doc-picker")) $("doc-picker").classList.add("hidden");
});

$("new-chat-btn")?.addEventListener("click", async () => {
  try {
    await createChat();
  } catch (err) {
    notifyError(err.message);
  }
});

async function bootWorkspace() {
  let projects = await api("/api/projects");
  if (!projects.length) {
    const created = await api("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "경남 AI 경진대회" }),
    });
    projects = [created];
  }
  const savedProject = localStorage.getItem(LS_PROJECT);
  const project = projects.find((p) => p.id === savedProject) || projects[0];
  state.projectId = project.id;
  state.projectName = project.name;
  localStorage.setItem(LS_PROJECT, project.id);
  await Promise.all([refreshDocuments(), refreshSessions()]);
  renderSidebar();
  const savedSession = localStorage.getItem(LS_SESSION);
  const current = state.sessions.find((s) => s.id === savedSession) || state.sessions[0];
  if (current) await openChat(current.id);
  else await createChat();
}

window.DropOverlay.init(attachFiles);
window.PdfModal.init();
window.PreviewPanel.init();
window.AttachmentBar.init({
  onRemove: removeAttachment,
  onPreview: previewAttachment,
  onConvert: convertHwp,
  onInspect: inspectAttachment,
  onSummarize: summarizeAttachment,
});

renderStepper();
renderChips();
renderActions([]);
renderTimeline([]);
bootWorkspace().catch((err) => statusLine(err.message));
