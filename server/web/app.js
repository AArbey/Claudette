"use strict";

const listElement = document.querySelector("#conversation-list");
const serverListElement = document.querySelector("#server-list");
const countElement = document.querySelector("#conversation-count");
const connectionDot = document.querySelector("#connection-dot");
const connectionText = document.querySelector("#connection-text");
const transcript = document.querySelector("#transcript");
const titleElement = document.querySelector("#conversation-title");
const metaElement = document.querySelector("#conversation-meta");
const statusBadge = document.querySelector("#status-badge");
const menuButton = document.querySelector("#menu-button");
const sidebarShade = document.querySelector("#sidebar-shade");
const conversationsViewButton = document.querySelector("#conversations-view");
const serversViewButton = document.querySelector("#servers-view");
const memoriesViewButton = document.querySelector("#memories-view");
const serversElement = document.querySelector("#servers");
const memoriesElement = document.querySelector("#memories");
const memoryRunnerListElement = document.querySelector("#memory-runner-list");
const filterElement = document.querySelector("#conversation-filter");
const filterLabel = filterElement.closest("label");
const actionsElement = document.querySelector("#conversation-actions");
const answersOnlyButton = document.querySelector("#answers-only");
const conversationInfo = document.querySelector("#conversation-info");
const archiveButton = document.querySelector("#archive-button");
const deleteButton = document.querySelector("#delete-button");
const runnerPicker = document.querySelector("#runner-picker");
const runnerPickerSummary = document.querySelector("#runner-picker-summary");
const runnerMenu = document.querySelector("#runner-menu");
const newConversationButton = document.querySelector("#new-conversation");
const addServerButton = document.querySelector("#add-server");
const newMemoryButton = document.querySelector("#new-memory");
const addServerDialog = document.querySelector("#add-server-dialog");
const addServerClose = document.querySelector("#add-server-close");
const addServerDone = document.querySelector("#add-server-done");
const addServerCommand = document.querySelector("#add-server-command");
const addServerCopy = document.querySelector("#add-server-copy");
const addServerFeedback = document.querySelector("#add-server-feedback");
const newConversationDialog = document.querySelector("#new-conversation-dialog");
const newConversationForm = document.querySelector("#new-conversation-form");
const newConversationCancel = document.querySelector("#new-conversation-cancel");
const newConversationBack = document.querySelector("#new-conversation-back");
const newRunnerSelect = document.querySelector("#new-runner-select");
const newRunnerOptions = document.querySelector("#new-runner-options");
const newConversationSelection = document.querySelector("#new-conversation-selection");
const createConversationButton = document.querySelector("#create-conversation");
const manageRunnersButton = document.querySelector("#manage-runners");
const messageForm = document.querySelector("#message-form");
const messageInput = document.querySelector("#message-input");
const messageSend = document.querySelector("#message-send");
const messageStop = document.querySelector("#message-stop");
const messageHint = document.querySelector("#message-hint");
const responseStatus = document.querySelector("#response-status");
const contextMeter = document.querySelector("#context-meter");
const contextMeterFill = document.querySelector("#context-meter-fill");
const contextMeterLabel = document.querySelector("#context-meter-label");
const contextAdd = document.querySelector("#context-add");
const contextDialog = document.querySelector("#context-dialog");
const contextClose = document.querySelector("#context-close");
const contextFile = document.querySelector("#context-file");
const contextUploadButton = document.querySelector("#context-upload-button");
const contextServerPath = document.querySelector("#context-server-path");
const contextServerAdd = document.querySelector("#context-server-add");
const contextMemory = document.querySelector("#context-memory");
const contextFeedback = document.querySelector("#context-feedback");
const contextReferences = document.querySelector("#context-references");
const contextTargetNote = document.querySelector("#context-target-note");
const contextTargetOpen = document.querySelector("#context-target-open");
const memoryDialog = document.querySelector("#memory-dialog");
const memoryForm = document.querySelector("#memory-form");
const memoryKeyInput = document.querySelector("#memory-key");
const memoryValueInput = document.querySelector("#memory-value");
const memoryScopeInput = document.querySelector("#memory-scope");
const memoryFeedback = document.querySelector("#memory-feedback");
const memorySaveButton = document.querySelector("#memory-save");
const aiConfigButton = document.querySelector("#ai-config-button");
const aiModelValue = document.querySelector("#ai-model-value");
const aiConfigDialog = document.querySelector("#ai-config-dialog");
const aiConfigClose = document.querySelector("#ai-config-close");
const aiServerList = document.querySelector("#ai-server-list");
const aiServerAdd = document.querySelector("#ai-server-add");
const aiServerForm = document.querySelector("#ai-server-form");
const aiServerId = document.querySelector("#ai-server-id");
const aiServerName = document.querySelector("#ai-server-name");
const aiServerEndpoint = document.querySelector("#ai-server-endpoint");
const aiServerKey = document.querySelector("#ai-server-key");
const aiKeyNote = document.querySelector("#ai-key-note");
const aiConfigFeedback = document.querySelector("#ai-config-feedback");
const aiServerSave = document.querySelector("#ai-server-save");
const webToolsForm = document.querySelector("#web-tools-form");
const searxngUrl = document.querySelector("#searxng-url");
const searxngResults = document.querySelector("#searxng-results");
const researchModel = document.querySelector("#research-model");
const researchModelNote = document.querySelector("#research-model-note");
const webToolsFeedback = document.querySelector("#web-tools-feedback");
const searxngTest = document.querySelector("#searxng-test");
const webToolsSave = document.querySelector("#web-tools-save");
const researchDialog = document.querySelector("#research-dialog");
const researchDialogBody = document.querySelector("#research-dialog-body");
const researchDialogQuestion = document.querySelector("#research-dialog-question");
const researchDismissed = new Set();
const researchAutoOpened = new Set();
let webToolsConfig = null;

const confirmDialog = document.querySelector("#confirm-dialog");
const confirmForm = document.querySelector("#confirm-form");
const confirmTitle = document.querySelector("#confirm-title");
const confirmDescription = document.querySelector("#confirm-description");
const confirmFeedback = document.querySelector("#confirm-feedback");
const confirmSubmit = document.querySelector("#confirm-submit");

let conversations = [];
let servers = [];
let runners = [];
let memories = [];
let aiServers = [];
let aiConfigBusy = false;
let aiConfigFeedbackTimer = null;
let aiConfigRequestVersion = 0;
let activeModelMenu = null;
let activeModelTrigger = null;
let confirmation = null;
let routedHash = null;

function closeModelPicker(restoreFocus = false) {
  const menu = activeModelMenu;
  const trigger = activeModelTrigger;
  activeModelMenu = null;
  activeModelTrigger = null;
  if (trigger) trigger.setAttribute("aria-expanded", "false");
  if (menu) {
    menu.classList.remove("open");
    if (typeof menu.hidePopover === "function") {
      try { menu.hidePopover(); } catch (_) {}
    }
  }
  if (restoreFocus && trigger?.isConnected) trigger.focus({ preventScroll: true });
}

function positionModelPicker() {
  if (!activeModelMenu || !activeModelTrigger?.isConnected) return;
  const menu = activeModelMenu;
  const trigger = activeModelTrigger;
  const rect = trigger.getBoundingClientRect();
  const gap = 7;
  const margin = 12;
  const viewportWidth = document.documentElement.clientWidth;
  const viewportHeight = document.documentElement.clientHeight;
  const below = Math.max(0, viewportHeight - rect.bottom - gap - margin);
  const above = Math.max(0, rect.top - gap - margin);
  const desiredHeight = Math.min(360, Math.max(180, menu.scrollHeight || 280));
  const openAbove = below < Math.min(220, desiredHeight) && above > below;
  const availableHeight = Math.max(120, openAbove ? above : below);
  const width = Math.min(Math.max(rect.width, 300), Math.max(240, viewportWidth - margin * 2));
  const left = Math.min(Math.max(margin, rect.left), Math.max(margin, viewportWidth - margin - width));

  menu.style.width = `${width}px`;
  menu.style.maxHeight = `${Math.min(360, availableHeight)}px`;
  menu.style.left = `${left}px`;
  menu.style.right = "auto";
  if (openAbove) {
    menu.style.top = "auto";
    menu.style.bottom = `${viewportHeight - rect.top + gap}px`;
    menu.dataset.side = "top";
  } else {
    menu.style.top = `${rect.bottom + gap}px`;
    menu.style.bottom = "auto";
    menu.dataset.side = "bottom";
  }
}

function openModelPicker(trigger, menu) {
  if (activeModelMenu === menu) {
    closeModelPicker(true);
    return;
  }
  closeModelPicker(false);
  activeModelMenu = menu;
  activeModelTrigger = trigger;
  trigger.setAttribute("aria-expanded", "true");
  menu.classList.add("open");
  if (typeof menu.showPopover === "function") {
    try { menu.showPopover(); } catch (_) {}
  }
  positionModelPicker();
  setTimeout(() => {
    positionModelPicker();
    const search = menu.querySelector(".model-picker-search");
    const selected = menu.querySelector(".model-picker-option.selected");
    if (search) search.focus({ preventScroll: true });
    else {
      const target = selected || menu.querySelector(".model-picker-option:not(.hidden)");
      target?.focus({ preventScroll: true });
      target?.scrollIntoView({ block: "nearest" });
    }
  }, 100);
}

function moveModelPickerFocus(menu, current, key) {
  const options = [...menu.querySelectorAll(".model-picker-option:not(.hidden):not(:disabled)")];
  if (!options.length) return;
  const currentIndex = options.indexOf(current);
  let nextIndex = currentIndex < 0 ? 0 : currentIndex;
  if (key === "Home") nextIndex = 0;
  else if (key === "End") nextIndex = options.length - 1;
  else if (key === "ArrowDown" || key === "ArrowRight") nextIndex = (nextIndex + 1) % options.length;
  else if (key === "ArrowUp" || key === "ArrowLeft") nextIndex = (nextIndex - 1 + options.length) % options.length;
  for (const option of options) option.tabIndex = option === options[nextIndex] ? 0 : -1;
  options[nextIndex].focus({ preventScroll: true });
  options[nextIndex].scrollIntoView({ block: "nearest" });
}
let currentView = location.hash.startsWith("#servers") ? "servers"
  : location.hash.startsWith("#memories") ? "memories" : "conversations";
let selectedId = validSessionId(location.hash.slice(1)) ? location.hash.slice(1) : null;
let selectedServerIp = serverIpFromHash();
let selectedMemoryRunnerId = memoryRunnerIdFromHash();
let conversationStream = null;
let streamedSessionId = null;
let currentDetail = null;
let conversationFilter = filterElement.value;
const sessionStates = new Map();
let draftSessionId = null;
let connected = false;
const queries = { conversations: "", servers: "", memories: "" };
const searchInput = document.querySelector("#sidebar-search");
const feedbackElement = document.querySelector("#page-feedback");
const approvalBanner = document.querySelector("#approval-banner");
const jumpLatest = document.querySelector("#jump-latest");
const conversationMenu = document.querySelector("#conversation-menu");
const themePicker = document.querySelector("#theme-picker");
const themePickerSummary = document.querySelector("#theme-picker-summary");
const themeMenu = document.querySelector("#theme-menu");
const editDialog = document.querySelector("#edit-dialog");
let editAction = null;
let editingMemoryId = null;
let newConversationBusy = false;
let newRunnerLoading = false;
let streamFrame = 0;
const streamDeltas = { reasoning: "", content: "" };
const disclosureState = new Map();
const activityState = new Map();
const trustEditors = new Map();
const enrollmentEditors = new Map();

function sessionState(id = selectedId) {
  if (!sessionStates.has(id)) sessionStates.set(id, {
    messageBusy: false, stopBusy: false, branchBusy: false, commandBusy: false, actionBusy: false,
    failure: "", feedback: "", notice: "", nextCommandId: "", editingMessageIndex: null,
    editingMessageDraft: "", references: [], answersOnly: null, answersKey: "",
  });
  return sessionStates.get(id);
}

function activityStorageKey(key) {
  return `brain.activity.${key}`;
}

function answersOnlyKey(detail = currentDetail) {
  return detail ? `brain.answers.${detail.session_id}.${detail.active_branch_id || "legacy"}` : "";
}

function answersOnlyEnabled(detail = currentDetail) {
  if (!detail) return false;
  const state = sessionState(detail.session_id);
  const key = answersOnlyKey(detail);
  if (state.answersOnly === null || state.answersKey !== key) {
    state.answersKey = key;
    state.answersOnly = storageRead("sessionStorage", key, "") === "1";
  }
  return state.answersOnly;
}

function storageRead(storage, key, fallback = "") {
  try { return window[storage].getItem(key) ?? fallback; } catch (_) { return fallback; }
}

function storageWrite(storage, key, value) {
  try {
    if (value) window[storage].setItem(key, value);
    else window[storage].removeItem(key);
  } catch (_) { /* In-memory drafts still work when browser storage is unavailable. */ }
}

function saveDraft() {
  if (!draftSessionId) return;
  const state = sessionState(draftSessionId);
  state.draft = messageInput.value;
  storageWrite("sessionStorage", `brain.draft.${draftSessionId}`, messageInput.value);
  storageWrite("sessionStorage", `brain.refs.${draftSessionId}`, JSON.stringify(state.references || []));
}

function loadDraft(id) {
  if (draftSessionId === id) return;
  saveDraft();
  draftSessionId = id;
  const state = sessionState(id);
  messageInput.value = state.draft ?? storageRead("sessionStorage", `brain.draft.${id}`);
  try { state.references = JSON.parse(storageRead("sessionStorage", `brain.refs.${id}`, "[]")) || []; } catch (_) { state.references = []; }
  resizeComposer();
}

function referenceKey(reference) {
  return [reference.type || "", reference.id || reference.path || reference.label || ""].join(":");
}

function storeReferences(sessionId, references) {
  const state = sessionState(sessionId);
  state.references = references;
  storageWrite("sessionStorage", `brain.refs.${sessionId}`, JSON.stringify(references));
}

function removeContextReference(sessionId, key) {
  const state = sessionState(sessionId);
  storeReferences(sessionId, (state.references || []).filter(reference => referenceKey(reference) !== key));
  if (currentDetail?.session_id === sessionId) {
    renderContextReferences(currentDetail);
    renderContextMeter(currentDetail);
  }
}

function renderReferencePills(references, removable = false, sessionId = "") {
  const fragment = document.createDocumentFragment();
  for (const reference of references || []) {
    const pill = element("span", removable ? "context-reference" : "message-reference");
    const type = reference.type === "attachment" ? "File" : reference.type === "server_file" ? "Server" : "Memory";
    const label = element("span", "context-reference-label", `${type}: ${reference.label}`);
    pill.append(label);
    if (removable) {
      const remove = element("button", "context-reference-remove", "×");
      remove.type = "button";
      remove.setAttribute("aria-label", `Remove ${reference.label}`);
      remove.addEventListener("click", () => removeContextReference(sessionId, referenceKey(reference)));
      pill.append(remove);
    }
    fragment.append(pill);
  }
  return fragment;
}

function renderContextReferences(detail) {
  const references = sessionState(detail.session_id).references || [];
  contextReferences.classList.toggle("hidden", !references.length);
  contextReferences.replaceChildren(renderReferencePills(references, true, detail.session_id));
}

function resizeComposer() {
  messageInput.style.height = "auto";
  messageInput.style.height = `${Math.min(messageInput.scrollHeight, 180)}px`;
}

function compactTokens(value) {
  if (value < 1024) return String(value);
  const thousands = value / 1024;
  return `${thousands.toFixed(thousands < 10 ? 1 : 0).replace(/\.0$/, "")}k`;
}

function renderContextMeter(detail) {
  const usage = detail?.context_usage || {};
  const maximum = Number(usage.max_tokens);
  if (!Number.isFinite(maximum) || maximum < 1024) {
    const state = usage.discovery || "unknown";
    contextMeter.hidden = !["loading", "unavailable"].includes(state);
    contextMeterFill.style.width = "0";
    contextMeterLabel.textContent = state === "loading"
      ? "Detecting context…" : state === "unavailable" ? "Context size unavailable" : "";
    contextMeter.removeAttribute("aria-valuenow");
    contextMeter.title = contextMeterLabel.textContent;
    return;
  }
  const state = sessionState(detail?.session_id);
  const referenceCharacters = (state.references || []).reduce((total, ref) =>
    total + (typeof ref.snapshot === "string" ? ref.snapshot.length : Number(ref.context_chars) || 0), 0);
  const draftTokens = Math.ceil((messageInput.value.length + referenceCharacters) / 4);
  const used = (Number(usage.estimated_tokens) || 0) + draftTokens;
  const percent = Math.min(100, Math.round(used * 100 / maximum));
  contextMeter.hidden = false;
  contextMeterFill.style.width = `${percent}%`;
  contextMeter.dataset.level = percent >= 90 ? "critical" : percent >= 75 ? "warning" : "normal";
  contextMeter.setAttribute("aria-valuenow", String(percent));
  contextMeterLabel.textContent = `~${compactTokens(used)} / ${compactTokens(maximum)} · ${percent}%`;
  contextMeter.title = `Estimated context usage: ${used.toLocaleString()} of ${maximum.toLocaleString()} tokens`;
}

function serverIpFromHash() {
  try { return location.hash.startsWith("#servers/") ? decodeURIComponent(location.hash.slice(9)) : null; }
  catch (_) { return null; }
}

function memoryRunnerIdFromHash() {
  if (!location.hash.startsWith("#memories/")) return null;
  try {
    const value = decodeURIComponent(location.hash.slice(10));
    if (value === "global") return "global";
    return /^[A-Za-z0-9_-]{32}$/.test(value) ? value : null;
  } catch (_) { return null; }
}

function showFeedback(message, retry = null, scope = feedbackElement) {
  scope.replaceChildren();
  scope.classList.toggle("hidden", !message);
  if (!message) return;
  scope.append(element("span", "", message));
  if (retry) {
    const button = element("button", "", "Retry");
    button.type = "button";
    button.addEventListener("click", retry);
    scope.append(button);
  }
}

function showSessionError(id, message) {
  sessionState(id).feedback = message;
  if (selectedId === id && currentView === "conversations") showFeedback(message);
}

function restoreFocus(scope, focus) {
  if (!focus) return;
  const match = [...scope.querySelectorAll("button, input, textarea, summary, select")]
    .find(node => node.dataset.focus === focus);
  match?.focus({ preventScroll: true });
}

function replaceList(list, fragment) {
  const key = list.contains(document.activeElement) ? document.activeElement?.dataset.focus : null;
  list.replaceChildren(fragment);
  restoreFocus(list, key);
}

function setSidebar(open, returnFocus = true) {
  const sidebar = document.querySelector("#sidebar");
  const mobile = matchMedia("(max-width: 899px)").matches;
  document.body.classList.toggle("sidebar-open", open && mobile);
  menuButton.setAttribute("aria-expanded", String(open && mobile));
  sidebar.inert = mobile && !open;
  document.querySelector(".main").inert = mobile && open;
  if (open && mobile) document.querySelector("#sidebar-close").focus();
  else if (mobile && returnFocus) menuButton.focus();
}

async function copyText(text, button) {
  const iconName = button.dataset.icon;
  const label = button.dataset.label || button.getAttribute("aria-label");
  try {
    if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(text);
    else {
      const active = document.activeElement;
      const field = element("textarea", "clipboard-buffer");
      field.value = text;
      (document.querySelector("dialog[open]") || document.body).append(field);
      field.select();
      const copied = document.execCommand("copy");
      field.remove();
      active?.focus({ preventScroll: true });
      if (!copied) throw new Error("Select text and copy manually.");
    }
    if (iconName) {
      button.replaceChildren(actionIcon("check"));
      button.setAttribute("aria-label", "Copied");
      button.title = "Copied";
    } else button.textContent = "Copied";
    document.querySelector("#copy-status").textContent = "Copied to clipboard.";
    setTimeout(() => {
      if (iconName) {
        button.replaceChildren(actionIcon(iconName));
        button.setAttribute("aria-label", label);
        button.title = label;
      } else button.textContent = "Copy";
    }, 1500);
    return true;
  } catch (_) {
    if (iconName) button.title = "Clipboard unavailable";
    else button.textContent = "Select to copy";
    document.querySelector("#copy-status").textContent = "Clipboard unavailable. Select text and copy manually.";
    return false;
  }
}

function actionIcon(name) {
  const paths = {
    check: '<path d="m5 12 4 4L19 6"/>',
    copy: '<rect width="13" height="13" x="9" y="9" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L8 18l-4 1 1-4Z"/>',
    resend: '<path d="M20 11a8.1 8.1 0 1 0 1 4"/><path d="M20 4v7h-7"/>',
  };
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = paths[name];
  return svg;
}

function actionButton(name, label) {
  const button = element("button", "message-action");
  button.type = "button";
  button.setAttribute("aria-label", label);
  button.title = label;
  button.dataset.icon = name;
  button.dataset.label = label;
  button.append(actionIcon(name));
  return button;
}

function copyButton(text, label = "Copy", iconOnly = false) {
  const button = iconOnly ? actionButton("copy", label) : element("button", "copy-button", "Copy");
  button.type = "button";
  if (!iconOnly) button.setAttribute("aria-label", label);
  button.addEventListener("click", () => void copyText(text, button));
  return button;
}

function markdownContent(text) {
  const node = element("div", "message-content markdown");
  if (!window.marked || !window.DOMPurify) { node.textContent = text; return node; }
  const renderer = new marked.Renderer();
  renderer.html = () => "";
  renderer.image = () => "";
  const html = marked.parse(text, { renderer });
  node.append(DOMPurify.sanitize(html, {
    RETURN_DOM_FRAGMENT: true,
    ALLOWED_TAGS: ["p", "br", "strong", "em", "del", "blockquote", "ul", "ol", "li", "pre", "code", "a", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "table", "thead", "tbody", "tr", "th", "td"],
    ALLOWED_ATTR: ["href", "title", "start"],
    ALLOW_DATA_ATTR: false,
  }));
  for (const link of node.querySelectorAll("a")) {
    const href = link.getAttribute("href") || "";
    if (!/^(https?:|mailto:)/i.test(href)) link.replaceWith(document.createTextNode(link.textContent));
    else { link.target = "_blank"; link.rel = "noopener noreferrer"; }
  }
  for (const block of node.querySelectorAll("pre")) {
    const wrapper = element("div", "code-block");
    block.replaceWith(wrapper);
    wrapper.append(copyButton(block.textContent, "Copy code"), block);
  }
  for (const table of node.querySelectorAll("table")) {
    const wrapper = element("div", "table-scroll");
    table.replaceWith(wrapper);
    wrapper.append(table);
  }
  return node;
}

function updateJump() {
  jumpLatest.classList.toggle("hidden", currentView !== "conversations" || !currentDetail
    || transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 80);
}

async function updateMetadata(id, changes) {
  const state = sessionState(id);
  if (state.actionBusy) return false;
  state.actionBusy = true;
  try {
    const response = await fetch(`/v1/conversations/${encodeURIComponent(id)}/metadata`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-Brain-UI": "1" },
      body: JSON.stringify(changes),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not save conversation.");
    if (currentDetail?.session_id === id) { currentDetail = result; renderDetail(result); }
    await refreshList();
    return true;
  } catch (error) {
    if (editDialog.open) document.querySelector("#edit-feedback").textContent = error.message;
    else showSessionError(id, error.message);
    return false;
  } finally {
    state.actionBusy = false;
    if (currentDetail?.session_id === id) renderDetail(currentDetail);
  }
}

function openEdit() {
  if (!currentDetail) return;
  conversationMenu.open = false;
  const id = currentDetail.session_id;
  editAction = { action: "rename", id };
  document.querySelector("#edit-title").textContent = "Rename conversation";
  document.querySelector("#edit-description").textContent = "Choose a title you can find later.";
  const input = document.querySelector("#edit-input");
  input.value = currentDetail.title || "";
  input.hidden = false;
  input.required = true;
  document.querySelector("#edit-label").hidden = false;
  const submit = document.querySelector("#edit-submit");
  submit.textContent = "Save title";
  submit.classList.remove("danger");
  document.querySelector("#edit-feedback").textContent = "";
  editDialog.showModal();
  input.focus();
  input.select();
}

function openConfirmation({ title, description, confirmLabel = "Delete", run, returnFocus = null }) {
  confirmation = { run, returnFocus };
  confirmTitle.textContent = title;
  confirmDescription.textContent = description;
  confirmFeedback.textContent = "";
  confirmSubmit.textContent = confirmLabel;
  confirmSubmit.disabled = false;
  confirmDialog.showModal();
  document.querySelector("#confirm-cancel").focus();
}

function confirmConversationDelete() {
  if (!currentDetail) return;
  const id = currentDetail.session_id;
  conversationMenu.open = false;
  openConfirmation({
    title: "Delete conversation?",
    description: `“${currentDetail.title || shortId(id)}” will be permanently deleted. This cannot be undone.`,
    confirmLabel: "Delete conversation",
    run: () => deleteConversation(id),
    returnFocus: conversationMenu.querySelector("summary"),
  });
}

function validSessionId(value) {
  return /^[A-Za-z0-9_-]{32}$/.test(value || "");
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function shortId(id) {
  return id ? id.slice(0, 8) : "unknown";
}

function timeAgo(timestamp) {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return "unknown";
  const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
  if (seconds < 10) return "now";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}

function fullTime(timestamp) {
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? timestamp : date.toLocaleString();
}

function statusText(status) {
  const values = {
    ready: "Idle",
    awaiting_tool_results: "Awaiting terminal",
    continuation_pending: "Needs resume",
  };
  return values[status] || status;
}

function runnerLabel(runner) {
  return `${runner.client_name} · ${runner.server_ip}:${runner.port}`;
}

function activeRunners() {
  return runners.filter((runner) => ["online", "busy"].includes(runner.status));
}

function conversationRunnerOption(runner, selected, busy) {
  const runnerId = runner?.runner_id || "";
  const active = !runner || ["online", "busy"].includes(runner.status);
  const button = element("button", `runner-menu-option${runnerId === selected ? " selected" : ""}`);
  button.type = "button";
  button.dataset.runnerId = runnerId;
  button.setAttribute("aria-pressed", String(runnerId === selected));
  button.disabled = busy || !active;

  const status = element("span", `runner-menu-status ${runner?.status || "local"}`);
  status.setAttribute("aria-hidden", "true");
  const copy = element("span", "runner-menu-copy");
  copy.append(
    element("strong", "", runner ? runner.client_name : "Chat only"),
    element("span", "", runner ? `${runner.server_ip}:${runner.port}` : "No command execution"),
  );
  const state = element("span", `runner-menu-state ${runner?.status || "local"}`,
    runner ? runnerStatusText(runner) : "No commands");
  button.append(status, copy, state);
  button.addEventListener("click", () => void changeConversationRunner(runnerId));
  return button;
}

function renderConversationRunnerPicker(detail, state) {
  const selected = (detail.runner_change_pending ? detail.pending_runner_id : detail.runner_id) || "";
  const runner = runners.find((item) => item.runner_id === selected)
    || (detail.runner?.runner_id === selected ? detail.runner : null);
  const queued = detail.runner_change_pending;
  document.querySelector("#runner-picker-label").textContent = queued ? "Target · queued" : "Target";
  document.querySelector("#runner-picker-value").textContent = runner ? runner.client_name : "Chat only";
  document.querySelector("#runner-picker-icon").textContent = runner ? ">_" : "◇";
  runnerPicker.classList.toggle("queued", queued);
  runnerPickerSummary.setAttribute("aria-label",
    `Change execution target. Current: ${runner ? runnerLabel(runner) : "Chat only"}${queued ? ". Change queued" : ""}`);

  const fragment = document.createDocumentFragment();
  const heading = element("div", "runner-menu-heading");
  heading.append(element("strong", "", "Execution target"),
    element("span", "", queued ? "Change applies after current reply" : "Choose where commands run"));
  fragment.append(heading, conversationRunnerOption(null, selected, state.actionBusy));
  const choices = activeRunners();
  if (runner && !choices.some((item) => item.runner_id === runner.runner_id)) choices.push(runner);
  for (const choice of choices) fragment.append(conversationRunnerOption(choice, selected, state.actionBusy));
  const manage = element("button", "runner-menu-manage", "Manage runners");
  manage.type = "button";
  manage.disabled = state.actionBusy;
  manage.addEventListener("click", () => {
    runnerPicker.open = false;
    showServers(runner?.server_ip || detail.runner?.server_ip || null);
  });
  if (runner) {
    const manageMemory = element("button", "runner-menu-manage", "Manage this runner’s memories");
    manageMemory.type = "button";
    manageMemory.disabled = state.actionBusy;
    manageMemory.addEventListener("click", () => {
      runnerPicker.open = false;
      showMemories(runner.runner_id);
    });
    fragment.append(manageMemory);
  }
  fragment.append(manage);
  runnerMenu.replaceChildren(fragment);
}

function selectNewRunner(runnerId) {
  newRunnerSelect.value = runnerId;
  for (const option of newRunnerOptions.querySelectorAll(".target-option")) {
    const selected = option.dataset.runnerId === runnerId;
    option.classList.toggle("selected", selected);
    option.setAttribute("aria-checked", String(selected));
    option.tabIndex = selected ? 0 : -1;
  }
  const runner = runners.find((item) => item.runner_id === runnerId);
  newConversationSelection.textContent = runner
    ? `${runner.client_name} selected`
    : "Chat only selected";
}

function newRunnerOption(runner = null) {
  const runnerId = runner?.runner_id || "";
  const button = element("button", "target-option");
  button.type = "button";
  button.dataset.runnerId = runnerId;
  button.disabled = Boolean(runner && !["online", "busy"].includes(runner.status));
  button.setAttribute("role", "radio");

  const icon = element("span", "target-option-icon", runner ? ">_" : "◇");
  icon.setAttribute("aria-hidden", "true");
  const copy = element("span", "target-option-copy");
  copy.append(
    element("strong", "", runner ? runner.client_name : "Chat only"),
    element("span", "", runner
      ? `${runner.server_ip}:${runner.port}`
      : "No command execution"),
  );
  const status = element("span", `target-option-status ${runner?.status || "local"}`,
    runner ? runnerStatusText(runner) : "No commands");
  button.append(icon, copy, status);
  button.addEventListener("click", () => selectNewRunner(runnerId));
  return button;
}

function preferredNewRunner() {
  const currentRunnerId = currentDetail?.runner_id || currentDetail?.runner?.runner_id || "";
  const savedRunnerId = storageRead("localStorage", "brain.lastRunner", "");
  return activeRunners().some(runner => runner.runner_id === currentRunnerId) ? currentRunnerId
    : activeRunners().some(runner => runner.runner_id === savedRunnerId) ? savedRunnerId : "";
}

function renderNewRunnerOptions(preferred = preferredNewRunner()) {
  const fragment = document.createDocumentFragment();
  fragment.append(newRunnerOption());
  for (const runner of runners) fragment.append(newRunnerOption(runner));
  newRunnerOptions.replaceChildren(fragment);
  selectNewRunner(preferred);
}

function setAIConfigFeedback(message = "", state = "info", autoHide = false) {
  if (aiConfigFeedbackTimer) {
    clearTimeout(aiConfigFeedbackTimer);
    aiConfigFeedbackTimer = null;
  }
  aiConfigFeedback.textContent = message;
  if (!message) {
    aiConfigFeedback.removeAttribute("data-state");
    return;
  }
  aiConfigFeedback.dataset.state = state;
  if (autoHide) {
    const shownMessage = message;
    aiConfigFeedbackTimer = window.setTimeout(() => {
      if (aiConfigFeedback.textContent === shownMessage) {
        aiConfigFeedback.textContent = "";
        aiConfigFeedback.removeAttribute("data-state");
      }
      aiConfigFeedbackTimer = null;
    }, 2600);
  }
}

function renderAIHeader() {
  const active = aiServers.find(server => server.active);
  aiModelValue.textContent = active?.selected_model || "Not configured";
  aiConfigButton.classList.toggle("error", !active?.selected_model);
  aiConfigButton.title = active
    ? `${active.name} · ${active.selected_model}`
    : "Configure OpenAI-compatible server";
  if (currentDetail && currentView === "conversations") renderComposer(currentDetail);
}

function createAIModelPicker(server, models, initialModel, label, note, onChoose) {
  let selectedModel = models.includes(initialModel) ? initialModel : (models[0] || "");
  const field = element("div", "ai-model-field");
  const labelRow = element("div", "ai-model-label-row");
  labelRow.append(element("span", "ai-model-label", label));
  if (note) labelRow.append(element("span", "ai-model-note", note));
  field.append(labelRow);

  const picker = element("div", "model-picker");
  const trigger = element("button", "model-picker-summary");
  let ignoreKeyboardClick = false;
  trigger.type = "button";
  trigger.setAttribute("aria-label", `Choose ${label.toLowerCase()} on ${server.name}`);
  trigger.setAttribute("aria-haspopup", "listbox");
  trigger.setAttribute("aria-expanded", "false");
  const menuId = `model-menu-${server.server_id}-${label.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  trigger.setAttribute("aria-controls", menuId);
  const summaryValue = element("span", "model-picker-value", selectedModel || "No models discovered");
  trigger.append(
    element("span", "model-picker-dot", ""),
    summaryValue,
    element("span", "model-picker-chevron", "⌄"),
  );

  const menu = element("div", "model-picker-menu");
  menu.id = menuId;
  menu.dataset.modelPopover = "true";
  menu.setAttribute("popover", "manual");
  menu.setAttribute("role", "listbox");
  menu.setAttribute("aria-label", `${label} options on ${server.name}`);
  const optionList = element("div", "model-picker-options");
  let noResults = null;
  if (models.length) {
    if (models.length > 8) {
      const searchWrap = element("div", "model-picker-search-wrap");
      const search = element("input", "model-picker-search");
      search.type = "search";
      search.placeholder = `Search ${models.length} models…`;
      search.setAttribute("aria-label", `Search ${label.toLowerCase()} options on ${server.name}`);
      search.autocomplete = "off";
      searchWrap.append(search);
      menu.append(searchWrap);
      noResults = element("div", "model-picker-no-results hidden", "No matching models");
      search.addEventListener("input", () => {
        const query = search.value.trim().toLowerCase();
        let visible = 0;
        for (const item of optionList.querySelectorAll(".model-picker-option")) {
          const match = !query || item.dataset.model.toLowerCase().includes(query);
          item.classList.toggle("hidden", !match);
          if (match) visible += 1;
        }
        noResults.classList.toggle("hidden", visible !== 0);
      });
      search.addEventListener("keydown", event => {
        if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        moveModelPickerFocus(menu, null, event.key === "ArrowUp" ? "End" : event.key);
      });
    }
    for (const model of models) {
      const option = element("button", `model-picker-option${model === selectedModel ? " selected" : ""}`);
      option.type = "button";
      option.dataset.model = model;
      option.setAttribute("role", "option");
      option.setAttribute("aria-selected", String(model === selectedModel));
      option.tabIndex = model === selectedModel ? 0 : -1;
      option.append(
        element("span", "model-picker-option-name", model),
        element("span", "model-picker-check", model === selectedModel ? "✓" : ""),
      );
      option.addEventListener("click", () => {
        selectedModel = model;
        summaryValue.textContent = model;
        for (const item of optionList.querySelectorAll(".model-picker-option")) {
          const chosen = item === option;
          item.classList.toggle("selected", chosen);
          item.setAttribute("aria-selected", String(chosen));
          item.tabIndex = chosen ? 0 : -1;
          item.querySelector(".model-picker-check").textContent = chosen ? "✓" : "";
        }
        closeModelPicker(true);
        onChoose(model);
      });
      option.addEventListener("keydown", event => {
        if (["ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
          event.preventDefault();
          moveModelPickerFocus(menu, option, event.key);
        } else if (event.key === "Escape") {
          event.preventDefault();
          closeModelPicker(true);
        }
      });
      optionList.append(option);
    }
    if (noResults) optionList.append(noResults);
  } else {
    optionList.append(element("div", "model-picker-empty", "No models cached yet. Choose Refresh to query this server."));
  }
  menu.append(optionList);
  trigger.addEventListener("click", event => {
    event.preventDefault();
    if (ignoreKeyboardClick) {
      ignoreKeyboardClick = false;
      return;
    }
    openModelPicker(trigger, menu);
  });
  trigger.addEventListener("keydown", event => {
    if (!["Enter", " "].includes(event.key)) return;
    event.preventDefault();
  });
  trigger.addEventListener("keyup", event => {
    if (!["Enter", " "].includes(event.key)) return;
    event.preventDefault();
    ignoreKeyboardClick = true;
    setTimeout(() => { ignoreKeyboardClick = false; }, 250);
    openModelPicker(trigger, menu);
  });
  picker.append(trigger);
  field.append(picker);
  return { field, menu, selectedModel: () => selectedModel };
}

function renderResearchModelOptions(preserveSelection = false) {
  const current = preserveSelection ? researchModel.value : null;
  const saved = webToolsConfig?.research_server_id && webToolsConfig?.research_model
    ? JSON.stringify([webToolsConfig.research_server_id, webToolsConfig.research_model]) : "";
  const fragment = document.createDocumentFragment();
  fragment.append(element("option", "", "Active chat model"));
  fragment.firstChild.value = "";
  for (const server of aiServers) {
    for (const model of server.models || []) {
      const option = element("option", "", `${server.name} · ${model}`);
      option.value = JSON.stringify([server.server_id, model]);
      fragment.append(option);
    }
  }
  researchModel.replaceChildren(fragment);
  const selection = current === null ? saved : current;
  researchModel.value = selection;
  if (researchModel.value !== selection) researchModel.value = "";
  researchModelNote.textContent = webToolsConfig?.research_model_fallback
    ? "Selected research model unavailable. Active chat model used."
    : "Research runs independently from main chat model.";
}

async function refreshWebToolsConfig() {
  try {
    webToolsConfig = await getJson("/v1/web-tools/config");
    searxngUrl.value = webToolsConfig.searxng_url || "";
    searxngResults.value = webToolsConfig.default_results || 8;
    renderResearchModelOptions();
    webToolsFeedback.textContent = webToolsConfig.searxng_url
      ? "SearXNG configured. Test connection to check JSON search."
      : "Set SearXNG URL to enable Web tools.";
  } catch (error) {
    webToolsFeedback.textContent = error.message || "Web settings unavailable.";
  }
}

function renderAIConfig() {
  const openAdvanced = new Set([...aiServerList.querySelectorAll(".ai-server-card")]
    .filter(card => card.querySelector(".ai-server-advanced")?.open)
    .map(card => card.dataset.serverId));
  closeModelPicker(false);
  document.querySelectorAll('.model-picker-menu[data-model-popover="true"]').forEach(node => node.remove());
  const fragment = document.createDocumentFragment();
  const popovers = [];
  if (!aiServers.length) {
    const empty = element("div", "ai-server-empty");
    empty.append(element("strong", "", "No AI server"), element("p", "", "Add endpoint to discover models."));
    fragment.append(empty);
  }
  for (const server of aiServers) {
    const card = element("section", `ai-server-card${server.active ? " active" : ""}`);
    card.dataset.serverId = server.server_id;
    const details = element("div", "ai-server-details");
    const title = element("div", "ai-server-title");
    title.append(element("strong", "", server.name));
    if (server.active) title.append(element("span", "ai-server-active-badge", "Active"));
    details.append(title, element("span", "ai-server-endpoint", server.endpoint_url));

    const actions = element("div", "ai-server-actions");
    const edit = element("button", "ai-server-action", "Edit");
    edit.type = "button";
    edit.addEventListener("click", () => openAIServerForm(server));
    const refresh = element("button", "ai-server-action", "Refresh");
    refresh.type = "button";
    refresh.addEventListener("click", () => void refreshAIModels(server.server_id, refresh));
    const remove = element("button", "ai-server-action danger", "Delete");
    remove.type = "button";
    remove.addEventListener("click", () => deleteAIServer(server, remove));
    actions.append(edit, refresh, remove);

    const models = Array.isArray(server.models) ? server.models : [];
    const modelRow = element("div", "ai-server-model-row");
    const modelPicker = createAIModelPicker(
      server,
      models,
      server.selected_model,
      "Model",
      "Chat · saves automatically",
      model => {
        if (!server.active || model !== server.selected_model) {
          void selectAIModel(server.server_id, model);
        }
      },
    );
    modelRow.append(modelPicker.field);

    const advanced = element("details", "ai-server-advanced");
    advanced.open = openAdvanced.has(server.server_id);
    advanced.append(element("summary", "", "Advanced"));
    const supportRow = element("div", "ai-server-support-row");
    const supportPicker = createAIModelPicker(
      server,
      models,
      server.support_model || server.selected_model,
      "Support model",
      "Conversation names",
      model => {
        if (model !== server.support_model) void selectAISupportModel(server.server_id, model);
      },
    );
    const waitToggle = element("label", "ai-support-wait");
    const waitInput = element("input", "ai-support-wait-input");
    waitInput.type = "checkbox";
    waitInput.checked = Boolean(server.support_wait_for_main);
    waitInput.setAttribute("aria-label", `Wait for main LLM completion on ${server.name}`);
    const waitCopy = element("span", "ai-support-wait-copy");
    waitCopy.append(
      element("strong", "", "Wait for main LLM completion"),
      element("span", "", "Generate title after first answer finishes"),
    );
    waitInput.addEventListener("change", () => {
      waitInput.disabled = true;
      void setAISupportWait(server.server_id, waitInput.checked);
    });
    waitToggle.append(waitInput, waitCopy);
    supportRow.append(supportPicker.field, waitToggle);
    advanced.append(supportRow);
    popovers.push(modelPicker.menu, supportPicker.menu);
    card.append(details, actions, modelRow, advanced);
    fragment.append(card);
  }
  aiServerList.replaceChildren(fragment);
  // Keep model popovers as DOM descendants of the modal dialog. A modal <dialog>
  // makes everything outside itself inert; appending these to document.body made
  // the top-layer menu visible but unable to receive pointer interaction. Popover
  // rendering still lifts the menu out of the dialog's clipping/scrolling region.
  for (const menu of popovers) aiConfigDialog.append(menu);
  renderAIHeader();
  renderResearchModelOptions(true);
}

function openAIServerForm(server = null) {
  aiServerId.value = server?.server_id || "";
  aiServerName.value = server?.name || "";
  aiServerEndpoint.value = server?.endpoint_url || "";
  aiServerKey.value = "";
  aiKeyNote.textContent = server?.has_api_key ? "(leave blank to keep saved key)" : "(optional)";
  setAIConfigFeedback();
  aiServerForm.classList.remove("hidden");
  aiServerAdd.classList.add("hidden");
  aiServerName.focus();
}

function closeAIServerForm() {
  aiServerForm.classList.add("hidden");
  aiServerAdd.classList.remove("hidden");
  setAIConfigFeedback();
}

async function refreshAIConfig(discover = false, preserveOpenDialog = false) {
  const requestVersion = ++aiConfigRequestVersion;
  try {
    const result = await getJson("/v1/ai/config");
    if (requestVersion !== aiConfigRequestVersion) return;
    aiServers = Array.isArray(result.servers) ? result.servers : [];
    if (preserveOpenDialog && aiConfigDialog.open) {
      renderAIHeader();
      return;
    }
    renderAIConfig();
    if (discover && !aiConfigBusy && aiServers.length) {
      aiConfigBusy = true;
      await Promise.allSettled(aiServers.map(server => refreshAIModels(server.server_id)));
      aiConfigBusy = false;
    }
  } catch (_) {
    aiModelValue.textContent = "Unavailable";
  }
}

async function refreshAIModels(serverId, button = null, preserveOpenDialog = false) {
  if (button) button.disabled = true;
  try {
    const result = await aiWrite(`/v1/ai/servers/${encodeURIComponent(serverId)}/models`);
    const index = aiServers.findIndex(server => server.server_id === serverId);
    if (index >= 0) aiServers[index] = result.server;
    if (preserveOpenDialog && aiConfigDialog.open) renderAIHeader();
    else renderAIConfig();
  } catch (error) {
    setAIConfigFeedback(error.message || "Could not query models.", "error");
  } finally {
    if (button?.isConnected) button.disabled = false;
  }
}

async function selectAIModel(serverId, model) {
  setAIConfigFeedback("Updating model…", "info");
  try {
    await aiWrite("/v1/ai/selection", { server_id: serverId, model });
    await refreshAIConfig(false);
    setAIConfigFeedback("Model updated", "success", true);
  } catch (error) {
    await refreshAIConfig(false);
    setAIConfigFeedback(error.message || "Could not select model.", "error");
  }
}

async function selectAISupportModel(serverId, model) {
  setAIConfigFeedback("Updating support model…", "info");
  try {
    await aiWrite("/v1/ai/support-selection", { server_id: serverId, model });
    await refreshAIConfig(false);
    setAIConfigFeedback("Support model updated", "success", true);
  } catch (error) {
    await refreshAIConfig(false);
    setAIConfigFeedback(error.message || "Could not select support model.", "error");
  }
}

async function setAISupportWait(serverId, waitForMain) {
  setAIConfigFeedback("Updating title timing…", "info");
  try {
    await aiWrite("/v1/ai/support-settings", {
      server_id: serverId,
      wait_for_main: waitForMain,
    });
    await refreshAIConfig(false);
    setAIConfigFeedback("Title timing updated", "success", true);
  } catch (error) {
    await refreshAIConfig(false);
    setAIConfigFeedback(error.message || "Could not update title timing.", "error");
  }
}

function deleteAIServer(server, returnFocus = null) {
  openConfirmation({
    title: "Delete AI server?",
    description: `“${server.name}” and its saved endpoint configuration will be permanently deleted.`,
    confirmLabel: "Delete AI server",
    returnFocus,
    run: async () => {
    await aiWrite(`/v1/ai/servers/${encodeURIComponent(server.server_id)}`, {}, "DELETE");
    await refreshAIConfig(false);
      return true;
    },
  });
}

function setConnection(online) {
  connected = online;
  connectionDot.classList.toggle("online", online);
  connectionDot.classList.toggle("offline", !online);
  connectionText.textContent = online ? "Connected" : "Reconnecting";
  document.querySelector("#connection-retry").classList.toggle("hidden", online);
  if (currentDetail && currentView === "conversations") renderComposer(currentDetail);
}

function sidebarItem({ id, selected, title, time = "", preview = "", state = "", live = false, onClick }) {
  const button = element("button", "conversation-item");
  button.type = "button";
  button.dataset.focus = id;
  button.classList.toggle("selected", selected);
  if (selected) button.setAttribute("aria-current", "page");
  button.addEventListener("click", onClick);
  const top = element("div", "item-top");
  if (live) top.append(element("span", "live-dot"));
  top.append(element("span", "item-id", title));
  if (time !== "") top.append(element("span", "item-time", time));
  button.append(top, element("div", "item-preview", preview));
  if (state) button.append(element("span", "item-state", state));
  return button;
}

function renderConversationList() {
  const fragment = document.createDocumentFragment();
  const query = queries.conversations.trim().toLowerCase();
  const visible = visibleConversations().filter(item => `${item.title || ""} ${item.preview || ""}`.toLowerCase().includes(query));
  visible.sort((a, b) => Number(b.pinned) - Number(a.pinned) || Number(b.active) - Number(a.active));
  let group = null;
  for (const conversation of visible) {
    const nextGroup = conversation.pinned ? "Pinned" : "Conversations";
    if (nextGroup !== group) { fragment.append(element("p", "list-heading", nextGroup)); group = nextGroup; }
    fragment.append(sidebarItem({
      id: conversation.session_id,
      selected: conversation.session_id === selectedId,
      title: conversation.title || shortId(conversation.session_id),
      time: timeAgo(conversation.updated_at),
      preview: conversation.preview,
      live: conversation.active,
      state: conversation.status === "continuation_pending" ? "Needs resume"
        : conversation.status === "awaiting_tool_results" ? "Command pending" : "",
      onClick: () => selectConversation(conversation.session_id),
    }));
  }
  if (!visible.length) fragment.append(element("p", "list-empty", query ? "No matching conversations. Try another search." : "No conversations in this view."));
  replaceList(listElement, fragment);
  if (currentView === "conversations") countElement.textContent = String(visible.length);
}

function renderServerList() {
  const fragment = document.createDocumentFragment();
  for (const server of servers.filter(item => `${item.name} ${item.server_ip}`.toLowerCase().includes(queries.servers.trim().toLowerCase()))) {
    fragment.append(sidebarItem({
      id: server.server_ip,
      selected: server.server_ip === selectedServerIp,
      title: server.name || server.server_ip,
      time: timeAgo(server.last_seen_at),
      preview: server.name ? server.server_ip : "Unnamed server",
      onClick: () => selectServer(server.server_ip),
    }));
  }
  if (!fragment.childNodes.length) fragment.append(element("p", "list-empty", "No matching servers. Connect a client to get started."));
  replaceList(serverListElement, fragment);
  if (currentView === "servers") countElement.textContent = String(servers.length);
}

function memoryCountForRunner(runnerId) {
  return memories.filter((memory) => memory.runner_id === runnerId).length;
}

function renderMemoryRunnerList() {
  const fragment = document.createDocumentFragment();
  const query = queries.memories.trim().toLowerCase();
  const visible = runners.filter((runner) => {
    const related = memories.filter((memory) => memory.runner_id === runner.runner_id);
    return `${runner.client_name} ${runner.server_ip} ${related.map((item) => `${item.key} ${item.value}`).join(" ")}`
      .toLowerCase().includes(query);
  });
  const globalMemories = memories.filter((memory) => memory.runner_id == null);
  if (!query || `global ${globalMemories.map((m) => `${m.key} ${m.value}`).join(" ")}`.toLowerCase().includes(query)) {
    fragment.append(sidebarItem({
      id: "global", selected: selectedMemoryRunnerId === "global", title: "Global",
      time: String(globalMemories.length), preview: "Available in every chat",
      onClick: () => selectMemoryRunner("global"),
    }));
  }
  for (const runner of visible) {
    fragment.append(sidebarItem({
      id: runner.runner_id,
      selected: runner.runner_id === selectedMemoryRunnerId,
      title: runner.client_name,
      time: String(memoryCountForRunner(runner.runner_id)),
      preview: `${runner.server_ip}:${runner.port}`,
      onClick: () => selectMemoryRunner(runner.runner_id),
    }));
  }
  if (!fragment.childNodes.length) {
    fragment.append(element("p", "list-empty", query
      ? "No runner or memory matches this search."
      : "No runners are installed yet."));
  }
  replaceList(memoryRunnerListElement, fragment);
  if (currentView === "memories") countElement.textContent = String(memories.length);
}

function visibleConversations() {
  if (conversationFilter === "resume") {
    return conversations.filter((item) => !item.archived
      && item.status === "continuation_pending");
  }
  if (conversationFilter === "archived") {
    return conversations.filter((item) => item.archived);
  }
  if (conversationFilter === "all") return conversations;
  return conversations.filter((item) => !item.archived);
}

function routeUrl(hash) {
  return hash ? `#${hash}` : location.pathname;
}

function navigateRoute(hash, replace = false) {
  const next = hash ? `#${hash}` : "";
  if (!replace && location.hash === next) {
    setSidebar(false);
    return;
  }
  if (replace) history.replaceState(null, "", routeUrl(hash));
  else if (location.hash !== next) history.pushState(null, "", routeUrl(hash));
  applyRoute(true);
}

function selectConversation(sessionId) {
  navigateRoute(sessionId);
}

function selectServer(serverIp) {
  navigateRoute(`servers/${encodeURIComponent(serverIp)}`);
}

function selectMemoryRunner(runnerId) {
  navigateRoute(`memories/${encodeURIComponent(runnerId)}`);
}

function showServers(serverIp = null) {
  if (typeof serverIp === "string") selectedServerIp = serverIp;
  navigateRoute(selectedServerIp ? `servers/${encodeURIComponent(selectedServerIp)}` : "servers");
}

function showMemories(runnerId = null) {
  if (typeof runnerId === "string") selectedMemoryRunnerId = runnerId;
  navigateRoute(selectedMemoryRunnerId
    ? `memories/${encodeURIComponent(selectedMemoryRunnerId)}` : "memories");
}

async function openAddServer() {
  addServerCommand.textContent = "Preparing command…";
  addServerCopy.disabled = true;
  addServerCopy.textContent = "Copy";

  addServerFeedback.classList.remove("error");
  addServerFeedback.textContent = "Generating setup command…";

  addServerDialog.showModal();

  try {
    const response = await fetch("/v1/server-setup", {
      cache: "no-store",
    });

    const result = await response.json();

    if (!response.ok) {
      throw new Error(result.error || `HTTP ${response.status}`);
    }

    addServerCommand.textContent = result.command;
    addServerCopy.disabled = false;

    const copied = await copyText(result.command, addServerCopy);

    addServerFeedback.textContent = copied
      ? "Command copied automatically. Paste it into Bash on the new server."
      : "Command ready. Use Copy, or select the command manually.";
  } catch (error) {
    addServerCommand.textContent = "Could not generate setup command.";

    addServerFeedback.classList.add("error");
    addServerFeedback.textContent =
      error.message || "Could not generate setup command.";
  }
}

function renderView() {
  const showServerPolicies = currentView === "servers";
  const showMemories = currentView === "memories";
  const showConversations = currentView === "conversations";
  searchInput.value = queries[currentView];
  searchInput.placeholder = showServerPolicies ? "Search servers…"
    : showMemories ? "Search memories…" : "Search conversations…";
  conversationsViewButton.setAttribute("aria-pressed", String(showConversations));
  serversViewButton.setAttribute("aria-pressed", String(showServerPolicies));
  memoriesViewButton.setAttribute("aria-pressed", String(showMemories));
  showFeedback(showConversations ? sessionState().feedback : "");
  approvalBanner.classList.toggle("hidden", !showConversations || !currentDetail?.pending_tool_calls?.some(call => call.ui?.remote));
  updateJump();
  conversationsViewButton.classList.toggle("selected", showConversations);
  serversViewButton.classList.toggle("selected", showServerPolicies);
  memoriesViewButton.classList.toggle("selected", showMemories);
  listElement.classList.toggle("hidden", !showConversations);
  serverListElement.classList.toggle("hidden", !showServerPolicies);
  memoryRunnerListElement.classList.toggle("hidden", !showMemories);
  filterLabel.classList.toggle("hidden", !showConversations);
  newConversationButton.classList.toggle("hidden", !showConversations);
  addServerButton.classList.toggle("hidden", !showServerPolicies);
  newMemoryButton.classList.toggle("hidden", !showMemories);
  transcript.classList.toggle("hidden", !showConversations);
  messageForm.classList.toggle("hidden", !showConversations || !currentDetail);
  serversElement.classList.toggle("hidden", !showServerPolicies);
  memoriesElement.classList.toggle("hidden", !showMemories);
  if (showServerPolicies) {
    titleElement.textContent = "Servers";
    metaElement.textContent = "Trusted command prefixes shared by source IP.";
    statusBadge.classList.add("hidden");
    actionsElement.classList.add("hidden");
    renderServerList();
    renderServers();
  } else if (showMemories) {
    statusBadge.classList.add("hidden");
    actionsElement.classList.add("hidden");
    renderMemoryRunnerList();
    renderMemories();
  }
}

function emptyState(title, text, showStart = false) {
  const wrapper = element("div", "empty-state");
  wrapper.append(
    element("div", "empty-icon", "⌁"),
    element("h3", "", title),
    element("p", "", text),
  );
  if (showStart) {
    const start = element("button", "primary", "New conversation");
    start.type = "button";
    start.addEventListener("click", () => void openNewConversation());
    wrapper.append(start);
  }
  return wrapper;
}

function rememberDisclosures(scope) {
  for (const details of scope.querySelectorAll("details[data-key]")) {
    disclosureState.set(details.dataset.key, details.open);
  }
}

function disclosure(className, key, initiallyOpen = false) {
  const details = element("details", className);
  details.dataset.key = `${selectedId}:${key}`;
  details.open = disclosureState.get(details.dataset.key) ?? initiallyOpen;
  details.addEventListener("toggle", () => {
    if (details.isConnected) disclosureState.set(details.dataset.key, details.open);
  });
  return details;
}

function activityDisclosure(key, live) {
  const details = element("details", "response-activity");
  details.dataset.key = key;
  let saved = activityState.get(key);
  if (saved === undefined) {
    const stored = storageRead("sessionStorage", activityStorageKey(key), "");
    saved = stored === "open" ? true : stored === "closed" ? false : undefined;
    if (saved !== undefined) activityState.set(key, saved);
  }
  details.open = saved ?? live;
  details.addEventListener("toggle", () => {
    if (!details.isConnected) return;
    activityState.set(key, details.open);
    storageWrite("sessionStorage", activityStorageKey(key), details.open ? "open" : "closed");
  });
  return details;
}

function formatCommand(tokens) {
  return tokens.map((token) => /^[A-Za-z0-9_@%+=:,./-]+$/.test(token)
    ? token : `'${token.replaceAll("'", "'\\''")}'`).join(" ");
}

function commandArguments(call) {
  try {
    const args = JSON.parse(call.function.arguments);
    if (typeof args?.program !== "string" || !Array.isArray(args.arguments)
        || !args.arguments.every((token) => typeof token === "string")) return null;
    return args;
  } catch (_error) {
    return null;
  }
}

function memoryToolArguments(call) {
  if (!["save_memory", "recall_memory", "delete_memory"].includes(call.function?.name)) return null;
  try {
    const args = JSON.parse(call.function.arguments);
    return args && typeof args === "object" ? args : null;
  } catch (_error) { return null; }
}

function renderMemoryTool(call, result, key) {
  const args = memoryToolArguments(call) || {};
  const labels = {
    save_memory: "Save memory",
    recall_memory: "Recall memory",
    delete_memory: "Delete memory",
  };
  const card = disclosure("tool-card", key, false);
  const summary = element("summary", "tool-summary");
  const target = typeof args.key === "string" ? args.key
    : typeof args.query === "string" && args.query ? `“${args.query}”` : "runner memory";
  summary.append(
    element("code", "command-line", `${labels[call.function?.name] || "Memory"} · ${target}`),
    element("span", "command-badge success", result ? "Completed" : "Working"),
  );
  card.append(summary);
  const body = element("div", "tool-details");
  if (call.function?.name === "save_memory" && typeof args.value === "string") {
    body.append(element("p", "command-note", args.value));
  }
  if (result?.content) {
    let text = result.content;
    try {
      const payload = JSON.parse(result.content);
      if (payload.ok === false) text = payload.error || "Memory operation failed.";
      else if (Array.isArray(payload.memories)) {
        text = payload.memories.length
          ? payload.memories.map((item) => `${item.key}: ${item.value}`).join("\n")
          : "No matching memories.";
      } else if (payload.action) text = `${payload.action}${payload.key ? `: ${payload.key}` : ""}`;
    } catch (_error) {}
    body.append(element("pre", "tool-body", text));
  }
  card.append(body);
  return card;
}

function webToolArguments(call) {
  if (!["search_searxng", "load_web_page", "deep_research"].includes(call.function?.name)) return null;
  try {
    const args = JSON.parse(call.function.arguments);
    return args && typeof args === "object" ? args : {};
  } catch (_) { return {}; }
}

function safeWebLink(url, label) {
  try {
    const parsed = new URL(url);
    if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password)
      return element("span", "", label);
    const link = element("a", "web-source-link", label);
    link.href = parsed.href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    return link;
  } catch (_) { return element("span", "", label); }
}

function renderWebTool(call, result, key) {
  const args = webToolArguments(call) || {};
  const name = call.function?.name;
  const title = {search_searxng: "Search Web", load_web_page: "Read page", deep_research: "Deep research"}[name];
  const target = args.query || args.url || args.question || "";
  const card = disclosure("tool-card web-tool-card", key, false);
  let data = {};
  try { data = JSON.parse(result?.content || "{}"); } catch (_) {}
  const stopped = Boolean(result?.ui?.stopped || result?.ui?.research?.status === "stopped");
  const failed = !stopped && (data.ok === false || result?.ui?.research?.status === "failed");
  const state = !result ? ["pending", "Working"] : stopped ? ["failed", "Stopped"]
    : failed ? ["failed", "Failed"] : ["success", "Finished"];
  const summary = element("summary", "tool-summary");
  summary.append(element("span", "command-line", `${title} · ${target}`),
    element("span", `command-badge ${state[0]}`, state[1]));
  card.append(summary);
  const body = element("div", "tool-details");
  if (stopped) body.append(element("p", "command-note", "Request stopped."));
  if (data.ok === false) body.append(element("p", "command-note error", data.error || "Web request failed."));
  if (name === "search_searxng") {
    for (const item of data.results || []) {
      const row = element("div", "web-result");
      row.append(safeWebLink(item.url, item.title || item.url));
      if (item.snippet) row.append(element("p", "command-note", item.snippet));
      body.append(row);
    }
    if (data.ok && !data.results?.length) body.append(element("p", "command-note", "No results."));
  } else if (name === "load_web_page") {
    if (data.url) body.append(safeWebLink(data.url, data.title || data.url));
    if (data.text) body.append(element("pre", "tool-body", data.text));
    if (data.truncated) body.append(element("p", "command-note", "Text truncated at 100,000 characters."));
  } else if (name === "deep_research") {
    const trace = result?.ui?.research;
    if (trace) {
      body.append(element("p", "command-note", `${trace.steps?.length || 0} steps · ${trace.sources?.length || 0} sources`));
      const button = element("button", "research-open-button", "Open research chat");
      button.type = "button";
      button.addEventListener("click", () => openResearchDialog(trace, call.id));
      body.append(button);
    }
    if (data.answer) body.append(element("pre", "tool-body", data.answer));
    for (const [index, source] of (data.sources || []).entries()) {
      body.append(safeWebLink(source.url, `[${index + 1}] ${source.title || source.url}`));
    }
  }
  card.append(body);
  return card;
}

function renderResearchDialog(trace) {
  if (!trace) return;
  const nearBottom = researchDialogBody.scrollHeight - researchDialogBody.scrollTop - researchDialogBody.clientHeight < 30;
  const oldScroll = researchDialogBody.scrollTop;
  researchDialogQuestion.textContent = trace.question || "";
  document.querySelector("#research-stop").classList.toggle("hidden", trace.status !== "running");
  const fragment = document.createDocumentFragment();
  fragment.append(element("p", `research-state ${trace.status || "running"}`,
    trace.status === "completed" ? "Completed" : trace.status === "failed" ? "Failed" : trace.status === "stopped" ? "Stopped" : trace.status === "interrupted" ? "Interrupted" : "Researching…"));
  const steps = element("ol", "research-steps");
  for (const step of trace.steps || []) {
    steps.append(element("li", "", `${step.kind === "search_searxng" ? "Search" : "Read"}: ${step.target} · ${step.status}`));
  }
  fragment.append(steps);
  if (trace.error) fragment.append(element("p", "command-note error", trace.error));
  const chat = element("details", "research-full-chat");
  chat.open = researchDialogBody.querySelector(".research-full-chat")?.open || false;
  chat.append(element("summary", "", "Full research chat"));
  for (const message of trace.messages || []) {
    const row = element("div", "research-message");
    row.append(element("strong", "", message.role === "tool" ? `Tool · ${message.name}` : message.role === "user" ? "Question" : "Research model"));
    if (message.content) row.append(element("pre", "", message.content));
    for (const call of message.tool_calls || []) row.append(element("p", "command-note", `${call.name}: ${call.arguments}`));
    chat.append(row);
  }
  fragment.append(chat);
  if (trace.sources?.length) {
    const sources = element("div", "research-sources");
    sources.append(element("strong", "", "Sources"));
    trace.sources.forEach((source, index) => sources.append(safeWebLink(source.url, `[${index + 1}] ${source.title || source.url}`)));
    fragment.append(sources);
  }
  researchDialogBody.replaceChildren(fragment);
  researchDialogBody.scrollTop = nearBottom ? researchDialogBody.scrollHeight : oldScroll;
}

function openResearchDialog(trace, callId) {
  researchDialog.dataset.callId = callId;
  researchDialog.dataset.sessionId = currentDetail?.session_id || "";
  renderResearchDialog(trace);
  if (!researchDialog.open) researchDialog.showModal();
}

function syncResearchDialog(detail) {
  const live = detail.live?.research;
  if (researchDialog.open && (researchDialog.dataset.sessionId !== detail.session_id ||
      detail.session_id !== selectedId || currentView !== "conversations")) {
    researchDismissed.add(`${researchDialog.dataset.sessionId}:${researchDialog.dataset.callId}`);
    researchDialog.close();
  }
  if (detail.session_id !== selectedId) return;
  if (live && currentView === "conversations") {
    const key = `${detail.session_id}:${live.call_id}`;
    if (!researchDialog.open && !researchDismissed.has(key) && !researchAutoOpened.has(key)) {
      researchAutoOpened.add(key);
      openResearchDialog(live, live.call_id);
    } else if (researchDialog.open && researchDialog.dataset.callId === live.call_id) renderResearchDialog(live);
    return;
  }
  if (!researchDialog.open) return;
  const saved = [...detail.messages].reverse().find(message =>
    message.role === "tool" && message.tool_call_id === researchDialog.dataset.callId && message.ui?.research);
  if (saved) renderResearchDialog(saved.ui.research);
}

function approvalBadge(approval) {
  const labels = {
    trusted: "Trusted", trusted_now: "Trust saved", allowed_once: "Allowed once",
    denied: "Denied", cancelled: "Cancelled", invalid: "Rejected",
  };
  return element("span", `command-badge ${approval?.decision || ""}`,
    labels[approval?.decision] || "Approval not recorded");
}

function resultStatus(result) {
  if (!result) return ["pending", "Awaiting client"];
  const decision = result.ui?.approval?.decision;
  if (["denied", "cancelled", "invalid"].includes(decision)) return ["muted", "Not run"];
  const match = /^exit_code=(\d+)(?:\n|$)/.exec(result.content || "");
  if (!match) return ["muted", "Result received"];
  if (match[1] === "0") return ["success", "Completed"];
  if (match[1] === "124") return ["failed", "Timed out"];
  return ["failed", `Exit ${match[1]}`];
}

async function remoteCommandAction(callId, decision) {
  if (!currentDetail || currentDetail.active) return;
  const sessionId = currentDetail.session_id;
  const state = sessionState(sessionId);
  if (state.commandBusy || state.messageBusy) return;
  state.commandBusy = true;
  state.feedback = "";
  const resolvedCard = [...transcript.querySelectorAll("[data-call-id]")]
    .find((node) => node.dataset.callId === callId);
  const resolvedKey = resolvedCard?.dataset.key;
  renderDetail(currentDetail);
  try {
    const response = await fetch(`/v1/conversations/${encodeURIComponent(sessionId)}/commands/${encodeURIComponent(callId)}`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-Brain-UI": "1" },
      body: JSON.stringify({ decision }),
    });
    const body = await response.text();
    if (!response.ok || !/(^|\r?\n)event: (done|tool_calls)\r?$/m.test(body)) throw new Error(turnError(body));
    const detail = await getJson(`/v1/conversations/${encodeURIComponent(sessionId)}`);
    if (currentDetail?.session_id === sessionId) {
      if (resolvedKey) disclosureState.set(resolvedKey, false);
      state.nextCommandId = detail.pending_tool_calls?.find(call => call.ui?.remote)?.id || "";
      currentDetail = detail;
      renderDetail(detail);
    }
  } catch (error) { showSessionError(sessionId, error.message || "Command action failed. Try again from the command card."); }
  finally {
    state.commandBusy = false;
    if (currentDetail?.session_id === sessionId) renderDetail(currentDetail);
  }
}

function renderTool(call, result, key) {
  if (webToolArguments(call)) return renderWebTool(call, result, key);
  if (memoryToolArguments(call)) return renderMemoryTool(call, result, key);
  const uiState = sessionState();
  const args = commandArguments(call);
  const command = args ? formatCommand([args.program, ...args.arguments]) : "Command unavailable";
  const pending = currentDetail?.pending_tool_calls?.find((item) => item.id === call.id);
  const remotePending = currentDetail?.pending_tool_calls?.filter(item => item.ui?.remote) || [];
  const pendingIndex = remotePending.findIndex(item => item.id === call.id);
  const isNext = pendingIndex === 0;
  const card = disclosure("tool-card", key, Boolean(!result && pending?.ui?.remote && isNext));
  if (uiState.nextCommandId === call.id) {
    card.open = true;
    disclosureState.set(card.dataset.key, true);
  }
  const summary = element("summary", "tool-summary");
  const commandLabel = element("code", "command-line", command);
  commandLabel.title = command;
  let [statusState, label] = resultStatus(result);
  if (!result && pending?.ui?.remote) {
    label = pending.ui.state === "failed" ? "Runner unavailable" : "Awaiting approval";
    statusState = pending.ui.state === "failed" ? "failed" : "pending";
  }
  const badges = element("span", "command-badges");
  if (result) badges.append(approvalBadge(result.ui?.approval));
  if (!result && pending?.ui?.remote && remotePending.length > 1) {
    badges.append(element("span", "command-badge queue-position",
      `${pendingIndex + 1} of ${remotePending.length}`));
  }
  badges.append(element("span", `command-badge ${statusState}`, label));
  summary.append(commandLabel, badges);
  card.append(summary);

  const body = element("div", "tool-details");
  card.dataset.callId = call.id;
  body.append(copyButton(command, "Copy command"), element("pre", "command-full", command));
  if (typeof args?.reason === "string" && args.reason) {
    body.append(element("p", "command-reason", args.reason));
  }
  const approval = result?.ui?.approval;
  const prefix = approval?.prefix?.length ? approval.prefix : args?.trust_prefix;
  if (Array.isArray(prefix) && prefix.length && prefix.every((token) => typeof token === "string")) {
    const prefixRow = element("div", "prefix-row");
    prefixRow.append(
      element("span", "", approval?.prefix?.length ? "Trusted prefix" : "Suggested prefix"),
      element("code", "", formatCommand(prefix)),
    );
    body.append(prefixRow);
    if (!result && pending?.ui?.remote) body.append(element("p", "command-note",
      `Trust saves this exact prefix for ${currentDetail.runner?.server_ip || currentDetail.client?.server_ip || "the selected server"}. Matching commands can run without asking.`));
  }
  if (result) {
    const output = (result.content || "").replace(/^exit_code=\d+\n?/, "") || "No output.";
    body.append(element("pre", "tool-body", output), copyButton(output, "Copy output"));
  } else if (pending?.ui?.remote) {
    if (pending.ui.error) body.append(element("p", "command-note error", pending.ui.error));
    body.append(element(
      "p", "command-note",
      `Target: ${currentDetail.runner?.client_name || currentDetail.client?.name || currentDetail.client?.server_ip || "selected server"}`,
    ));
    const controls = element("div", "command-actions");
    controls.classList.toggle("approval-actions", pending.ui.state !== "failed");
    const actions = pending.ui.state === "failed"
      ? [["retry", "Retry"], ["cancel", "Cancel"]]
      : [["allow_once", "Allow once"], ["trust", "Trust"], ["deny", "Deny"]];
    for (const [decision, text] of actions) {
      const button = element("button", decision === "deny" || decision === "cancel" ? "danger" : "", text);
      button.type = "button";
      button.disabled = sessionState().commandBusy || sessionState().messageBusy || Boolean(currentDetail?.active || currentDetail?.archived);
      button.dataset.focus = `${call.id}:${decision}`;
      button.addEventListener("click", () => void remoteCommandAction(call.id, decision));
      controls.append(button);
    }
    body.append(controls);
  } else {
    body.append(element("p", "command-note", "Approval and execution happen in the client terminal."));
  }
  card.append(body);
  return card;
}

function renderReasoning(text, key, live = false) {
  const details = disclosure("reasoning", key, live);
  details.append(element("summary", "", "Thinking"), element("pre", "", text));
  return details;
}

function renderMessage(message, index, results = [], options = {}) {
  const role = message.role;
  const row = element("article", `message-row ${role}`);
  const stack = element("div", "message-stack");
  const labels = { user: "You", assistant: "Brain", tool: "Tool result", system: "Target" };
  const label = element("div", "message-label", labels[role] || role);
  if (message.ui?.stopped) label.append(element("span", "message-stopped", "Stopped"));
  stack.append(label);
  if (message.ui?.reasoning && options.reasoning !== false) {
    stack.append(renderReasoning(message.ui.reasoning, `message:${index}:thinking`));
  }

  const content = role === "user" && typeof message.ui?.display_content === "string"
    ? message.ui.display_content : message.content;
  if (typeof content === "string" && content.length) {
    if (role === "tool") {
      const output = disclosure("tool-output", `message:${index}:output`);
      output.append(element("summary", "", "Output"), element("pre", "tool-body", content), copyButton(content, "Copy output"));
      stack.append(output);
    } else {
      const bubble = element("div", "bubble");
      const state = sessionState();
      const editing = role === "user" && state.editingMessageIndex === index;
      if (editing) {
        const editor = element("form", "message-editor");
        const textarea = element("textarea", "message-editor-input");
        textarea.value = state.editingMessageDraft;
        textarea.rows = 3;
        textarea.maxLength = 100000;
        textarea.setAttribute("aria-label", "Edit message");
        textarea.dataset.focus = `message:${index}:edit`;
        textarea.addEventListener("input", () => {
          state.editingMessageDraft = textarea.value;
          submit.disabled = !textarea.value.trim();
        });
        textarea.addEventListener("keydown", event => {
          if (event.key === "Escape") {
            event.preventDefault();
            cancelMessageEdit(index);
          }
        });
        const controls = element("div", "message-editor-actions");
        const cancel = element("button", "", "Cancel");
        cancel.type = "button";
        cancel.addEventListener("click", () => cancelMessageEdit(index));
        const submit = element("button", "primary", "Send");
        submit.type = "submit";
        submit.disabled = !textarea.value.trim();
        editor.addEventListener("submit", event => {
          event.preventDefault();
          if (!submit.disabled) void branchFromMessage(index, textarea.value);
        });
        controls.append(cancel, submit);
        editor.append(textarea, controls);
        bubble.append(editor);
      } else {
        bubble.append(role === "assistant" ? markdownContent(content) : element("pre", "message-content", content));
        if (role === "user" && Array.isArray(message.references) && message.references.length) {
          const referenceList = element("div", "message-reference-list");
          referenceList.append(renderReferencePills(message.references));
          bubble.append(referenceList);
        }
        if (role !== "user") bubble.append(copyButton(content, "Copy message", true));
      }
      stack.append(bubble);
      if (role === "user" && !editing) {
        const controls = element("div", "message-actions");
        const branch = message.ui?.branch;
        if (branch?.choices?.length > 1) {
          const navigation = element("div", "branch-navigation");
          const previous = element("button", "branch-arrow", "‹");
          previous.type = "button";
          previous.setAttribute("aria-label", "Previous response branch");
          previous.disabled = branch.current === 0 || !canChangeBranch();
          previous.dataset.focus = `message:${index}:previous`;
          previous.addEventListener("click", () => void switchMessageBranch(
            branch.choices[branch.current - 1]?.branch_id
          ));
          const count = element("span", "branch-count", `${branch.current + 1} / ${branch.choices.length}`);
          count.setAttribute("aria-live", "polite");
          const next = element("button", "branch-arrow", "›");
          next.type = "button";
          next.setAttribute("aria-label", "Next response branch");
          next.disabled = branch.current >= branch.choices.length - 1 || !canChangeBranch();
          next.dataset.focus = `message:${index}:next`;
          next.addEventListener("click", () => void switchMessageBranch(
            branch.choices[branch.current + 1]?.branch_id
          ));
          navigation.append(previous, count, next);
          controls.append(navigation);
        }
        const copy = copyButton(content, "Copy message", true);
        const edit = actionButton("edit", "Edit message and create new response branch");
        edit.disabled = !canChangeBranch();
        edit.dataset.focus = `message:${index}:edit-button`;
        edit.addEventListener("click", () => startMessageEdit(index, content));
        const resend = actionButton("resend", "Resend message and create new response branch");
        resend.disabled = !canChangeBranch();
        resend.dataset.focus = `message:${index}:resend`;
        resend.addEventListener("click", () => void branchFromMessage(index, content));
        controls.append(copy, edit, resend);
        stack.append(controls);
      }
    }
  }

  if (Array.isArray(message.tool_calls) && options.tools !== false) {
    const commandGroup = element("div", message.tool_calls.length > 1 ? "command-group" : "");
    if (message.tool_calls.length > 1) {
      const ids = new Set(message.tool_calls.map(call => call.id));
      const hasMemoryCalls = message.tool_calls.some(call => memoryToolArguments(call) || webToolArguments(call));
      const reviewCount = currentDetail?.pending_tool_calls?.filter(
        call => ids.has(call.id) && call.ui?.remote
      ).length || 0;
      const groupHeader = element("div", "command-group-header");
      groupHeader.append(
        element("strong", "", `${message.tool_calls.length} ${hasMemoryCalls ? "actions" : "commands"}`),
        element("span", "", reviewCount
          ? `${reviewCount} ${reviewCount === 1 ? "needs" : "need"} review`
          : `${results.length} finished`),
      );
      commandGroup.append(groupHeader);
    }
    for (const [callIndex, call] of message.tool_calls.entries()) {
      commandGroup.append(renderTool(
        call,
        results.find((result) => result.tool_call_id === call.id),
        `message:${index}:command:${callIndex}`,
      ));
    }
    stack.append(commandGroup);
  }

  row.append(stack);
  return row;
}

function canChangeBranch() {
  if (!currentDetail || !connected) return false;
  const state = sessionState();
  return !currentDetail.active && !currentDetail.archived && !state.messageBusy
    && !state.branchBusy && !state.commandBusy && !state.actionBusy;
}

function startMessageEdit(index, content) {
  if (!canChangeBranch()) return;
  const state = sessionState();
  state.editingMessageIndex = index;
  state.editingMessageDraft = content;
  renderDetail(currentDetail);
  transcript.querySelector(`[data-focus="message:${index}:edit"]`)?.focus({ preventScroll: true });
}

function cancelMessageEdit(index) {
  const state = sessionState();
  state.editingMessageIndex = null;
  state.editingMessageDraft = "";
  renderDetail(currentDetail);
  transcript.querySelector(`[data-focus="message:${index}:edit-button"]`)?.focus({ preventScroll: true });
}

async function switchMessageBranch(branchId) {
  if (!branchId || !canChangeBranch() || !currentDetail) return;
  const sessionId = currentDetail.session_id;
  const state = sessionState(sessionId);
  state.branchBusy = true;
  state.failure = "";
  renderDetail(currentDetail);
  try {
    const response = await fetch(
      `/v1/conversations/${encodeURIComponent(sessionId)}/branches/${encodeURIComponent(branchId)}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Brain-UI": "1" },
        body: "{}",
      },
    );
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    if (currentDetail?.session_id === sessionId) {
      currentDetail = result;
      state.editingMessageIndex = null;
      renderDetail(result);
    }
  } catch (error) {
    state.failure = error.message || "Could not open response branch.";
  } finally {
    state.branchBusy = false;
    if (currentDetail?.session_id === sessionId) renderDetail(currentDetail);
  }
}

async function branchFromMessage(index, content) {
  if (!canChangeBranch() || !currentDetail || !content.trim()) return;
  const sessionId = currentDetail.session_id;
  const state = sessionState(sessionId);
  state.failure = "";
  state.branchBusy = true;
  let accepted = false;
  renderDetail(currentDetail);
  try {
    const response = await fetch(`/v1/conversations/${encodeURIComponent(sessionId)}/turns`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Brain-UI": "1" },
      body: JSON.stringify({ content, branch_from: index }),
    });
    if (!response.ok) {
      const result = await response.json();
      throw new Error(result.error || `HTTP ${response.status}`);
    }
    accepted = true;
    state.editingMessageIndex = null;
    state.editingMessageDraft = "";
    const body = await response.text();
    if (!/(^|\r?\n)event: (done|tool_calls)\r?$/m.test(body)) throw new Error(turnError(body));
  } catch (error) {
    state.failure = accepted
      ? `${error.message} Branch was created; check transcript before continuing.`
      : error.message || "Could not create response branch.";
  } finally {
    state.branchBusy = false;
    if (currentDetail?.session_id === sessionId) renderDetail(currentDetail);
  }
}

function renderLive(live) {
  const row = element("article", "message-row assistant");
  row.dataset.live = "true";
  const stack = element("div", "message-stack");
  stack.append(element("div", "message-label", "Brain"));

  const bubble = element("div", "bubble");
  if (live.content) {
    bubble.append(element("pre", "message-content", live.content));
    stack.append(bubble);
  }
  row.append(stack);
  return row;
}

function trustEditor(serverIp) {
  if (!trustEditors.has(serverIp)) {
    trustEditors.set(serverIp, {
      draft: "", busy: false, message: "", error: false,
      nameDraft: "", nameDirty: false, nameMessage: "", nameError: false,
    });
  }
  return trustEditors.get(serverIp);
}

function enrollmentEditor(clientId) {
  if (!enrollmentEditors.has(clientId)) {
    enrollmentEditors.set(clientId, {
      busy: false, action: "", command: "", message: "", error: false,
    });
  }
  return enrollmentEditors.get(clientId);
}

function runnerStatusText(runner) {
  if (!runner) return "Not installed";
  const labels = {
    online: "Ready",
    busy: "Running command",
    offline: "Unreachable",
    error: "Check failed",
    upgrade_required: "Update required",
  };
  return labels[runner.status] || runner.status;
}

function runnerVersionText(runner) {
  if (!runner || runner.version_status === "unknown") return "Version unknown · Update available";
  if (runner.runner_version === 0) return `Legacy · Update available (v${runner.latest_runner_version})`;
  const version = `v${runner.runner_version}`;
  if (runner.version_status === "latest") return `${version} · Latest`;
  if (runner.version_status === "newer") return `${version} · Newer than Brain`;
  return `${version} · Update available (v${runner.latest_runner_version})`;
}

function replaceRunner(runner) {
  const index = runners.findIndex((item) => item.runner_id === runner.runner_id);
  if (index >= 0) runners[index] = runner;
  else runners.push(runner);
}

async function checkRunner(runnerId, clientId) {
  const editor = enrollmentEditor(clientId);
  if (editor.busy) return;
  editor.busy = true;
  editor.action = "check";
  editor.message = "Checking connection, credentials, and protocol…";
  editor.error = false;
  renderServers();
  try {
    const response = await fetch(`/v1/runners/${encodeURIComponent(runnerId)}/check`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Brain-UI": "1" },
      body: "{}",
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    replaceRunner(result.runner);
    const working = result.runner.status === "online";
    editor.message = working
      ? "Check passed. Runner ready."
      : result.runner.last_error || "Runner check failed.";
    editor.error = !working;
  } catch (error) {
    editor.message = error.message || "Could not check runner.";
    editor.error = true;
  } finally {
    editor.busy = false;
    editor.action = "";
    renderServers();
  }
}

async function generateEnrollment(clientId) {
  const editor = enrollmentEditor(clientId);
  if (editor.busy) return;
  editor.busy = true;
  editor.action = "install";
  editor.command = "";
  editor.message = "Generating fresh repair command…";
  editor.error = false;
  renderServers();
  try {
    const response = await fetch("/v1/runner-enrollments", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Brain-UI": "1" },
      body: JSON.stringify({ client_id: clientId }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    editor.command = result.command;
    editor.message = `Run on target server. Command expires ${fullTime(result.expires_at)}.`;
  } catch (error) {
    editor.message = error.message || "Could not generate setup command.";
    editor.error = true;
  } finally {
    editor.busy = false;
    editor.action = "";
    renderServers();
  }
}

function replaceServer(server) {
  const index = servers.findIndex((item) => item.server_ip === server.server_ip);
  if (index >= 0) servers[index] = server;
  renderServerList();
}

async function changeTrust(serverIp, edit) {
  const editor = trustEditor(serverIp);
  if (editor.busy) return;
  editor.busy = true;
  editor.message = "Saving…";
  editor.error = false;
  renderServers();
  try {
    const response = await fetch(`/v1/servers/${encodeURIComponent(serverIp)}/trust`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Brain-UI": "1" },
      body: JSON.stringify(edit),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    if (edit.action === "add") editor.draft = "";
    editor.message = edit.action === "add" ? "Prefix saved." : "Prefix removed.";
    replaceServer(result);
  } catch (error) {
    editor.message = error.message || "Could not save. Try again.";
    editor.error = true;
  } finally {
    editor.busy = false;
    renderServers();
    if (edit.action === "add") {
      document.querySelector("#trust-prefix")?.focus({ preventScroll: true });
    }
  }
}

async function changeServerName(serverIp) {
  const editor = trustEditor(serverIp);
  if (editor.busy) return;
  editor.busy = true;
  editor.nameMessage = "Saving…";
  editor.nameError = false;
  renderServers();
  try {
    const response = await fetch(`/v1/servers/${encodeURIComponent(serverIp)}/name`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Brain-UI": "1" },
      body: JSON.stringify({ name: editor.nameDraft }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    editor.nameDraft = result.name;
    editor.nameDirty = false;
    editor.nameMessage = result.name ? "Name saved." : "Name removed.";
    replaceServer(result);
  } catch (error) {
    editor.nameMessage = error.message || "Could not save. Try again.";
    editor.nameError = true;
  } finally {
    editor.busy = false;
    renderServers();
    document.querySelector("#server-name")?.focus({ preventScroll: true });
  }
}

function renderServers() {
  if (currentView !== "servers") return;
  rememberDisclosures(serversElement);
  const oldFocus = serversElement.contains(document.activeElement) ? document.activeElement?.dataset.focus : null;
  const oldInput = document.activeElement?.tagName === "INPUT" ? document.activeElement : null;
  const focusedId = oldInput?.id;
  const selection = oldInput?.tagName === "INPUT" ? [oldInput.selectionStart, oldInput.selectionEnd] : null;
  const fragment = document.createDocumentFragment();
  const server = servers.find((item) => item.server_ip === selectedServerIp);
  if (server) {
    const serverIp = server.server_ip;
    const editor = trustEditor(serverIp);
    const panel = element("article", "server-card");
    panel.dataset.serverIp = serverIp;
    const heading = element("div", "server-heading");
    heading.append(element("h3", "", "Trusted commands"),
      element("span", "count", `${server.trusted_prefixes.length} trusted`));
    titleElement.textContent = server.name || serverIp;
    metaElement.textContent = `${serverIp} · ${server.client_names.join(", ") || "No named clients"} · Last seen ${fullTime(server.last_seen_at)}`;

    const nameForm = element("form", "trust-form server-name-form");
    const nameLabel = element("label", "trust-input-label", "Server name");
    nameLabel.htmlFor = "server-name";
    const nameInput = element("input", "trust-input");
    nameInput.id = "server-name";
    nameInput.type = "text";
    nameInput.placeholder = "e.g. Build server";
    nameInput.autocomplete = "off";
    nameInput.maxLength = 128;
    nameInput.disabled = editor.busy;
    if (!editor.nameDirty) editor.nameDraft = server.name;
    nameInput.value = editor.nameDraft;
    nameInput.addEventListener("input", () => {
      editor.nameDraft = nameInput.value;
      editor.nameDirty = true;
    });
    const saveName = element("button", "trust-add", editor.busy ? "Saving…" : "Save name");
    saveName.type = "submit";
    saveName.disabled = editor.busy;
    const nameControls = element("div", "trust-controls");
    nameControls.append(nameInput, saveName);
    nameForm.append(nameLabel, nameControls);
    nameForm.addEventListener("submit", (event) => {
      event.preventDefault();
      void changeServerName(serverIp);
    });
    const nameFeedback = element("p", `trust-feedback${editor.nameError ? " error" : ""}`, editor.nameMessage);
    nameFeedback.setAttribute("role", editor.nameError ? "alert" : "status");
    panel.append(nameForm, nameFeedback);

    const runnerHeading = element("div", "server-heading");
    runnerHeading.append(element("h3", "", "Runner health"));
    panel.append(runnerHeading);
    const runnerList = element("div", "runner-list");
    for (const client of server.clients || []) {
      const runner = runners.find((item) => item.client_id === client.client_id);
      const editor = enrollmentEditor(client.client_id);
      const item = element("section", `runner-row ${runner?.status || "missing"}`);
      const info = element("div", "runner-info");
      const runnerStatus = element("span", `runner-status ${runner?.status || "missing"}`,
        runnerStatusText(runner));
      const identity = element("strong", "", client.name);
      const endpoint = element("span", "runner-endpoint",
        runner ? `${runner.server_ip}:${runner.port}` : client.client_id.slice(0, 8));
      info.append(identity, endpoint, runnerStatus);
      if (runner) {
        info.append(element("span", `runner-version ${runner.version_status}`,
          runnerVersionText(runner)));
      }
      if (runner?.last_seen_at) {
        const age = timeAgo(runner.last_seen_at);
        info.append(element("span", "runner-last-seen",
          age === "now" ? "Last passed just now" : `Last passed ${age} ago`));
      }
      const actions = element("div", "runner-actions");
      if (runner) {
        const check = element("button", "trust-add",
          editor.action === "check" ? "Checking…" : "Check now");
        check.type = "button";
        check.disabled = editor.busy;
        check.addEventListener("click", () => void checkRunner(runner.runner_id, client.client_id));
        actions.append(check);
      }
      const setup = element("button", "trust-add",
        editor.action === "install" ? "Preparing…" : "Install / repair");
      setup.type = "button";
      setup.disabled = editor.busy;
      setup.addEventListener("click", () => void generateEnrollment(client.client_id));
      actions.append(setup);
      item.append(info, actions);
      if (runner?.last_error) {
        const diagnostic = disclosure("runner-diagnostic", `runner:${runner.runner_id}:diagnostic`);
        diagnostic.append(element("summary", "", "Connection details"), element("p", "", runner.last_error));
        item.append(diagnostic);
      }
      if (editor.command) {
        item.append(element("p", "runner-instruction",
          `Run as ${client.name.split("@")[0] || "target user"}, then press Check now.`));
        const command = element("code", "setup-command", editor.command);
        const copy = element("button", "trust-add", "Copy");
        copy.type = "button";
        copy.addEventListener("click", () => void copyText(editor.command, copy));
        const setupLine = element("div", "setup-line");
        setupLine.append(command, copy);
        item.append(setupLine);
      }
      if (editor.message) {
        const feedback = element("p", `trust-feedback${editor.error ? " error" : ""}`, editor.message);
        feedback.setAttribute("role", editor.error ? "alert" : "status");
        item.append(feedback);
      }
      runnerList.append(item);
    }
    panel.append(runnerList, heading);

    const owner = element("p", "trust-owner");
    owner.append(document.createTextNode(
      `${server.client_names.join(", ") || "No named clients"} · Last seen ${fullTime(server.last_seen_at)}`));
    panel.append(owner);
    const list = element("ul", "trust-list");
    for (const prefix of server.trusted_prefixes) {
      const item = element("li");
      const command = formatCommand(prefix);
      const remove = element("button", "trust-remove", "×");
      remove.type = "button";
      remove.disabled = editor.busy;
      remove.setAttribute("aria-label", `Remove trust for ${command}`);
      remove.title = `Remove ${command}`;
      remove.addEventListener("click", () => void changeTrust(serverIp, { action: "remove", prefix }));
      item.append(element("code", "", command), remove);
      list.append(item);
    }
    panel.append(list);
    if (!server.trusted_prefixes.length) panel.append(element("p", "command-note", "No trusted prefixes. Commands require approval."));
    const form = element("form", "trust-form");
    const label = element("label", "trust-input-label", "Add a command prefix");
    const input = element("input", "trust-input");
    const inputId = "trust-prefix";
    const helpId = "trust-help";
    const feedbackId = "trust-feedback";
    label.htmlFor = inputId;
    input.id = inputId;
    input.type = "text";
    input.placeholder = "e.g. docker ps";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.maxLength = 1024;
    input.required = true;
    input.disabled = editor.busy;
    input.value = editor.draft;
    input.setAttribute("aria-describedby", `${helpId} ${feedbackId}`);
    input.addEventListener("input", () => { editor.draft = input.value; });
    const add = element("button", "trust-add", editor.busy ? "Saving…" : "Add prefix");
    add.type = "submit";
    add.disabled = editor.busy;
    const controls = element("div", "trust-controls");
    controls.append(input, add);
    form.append(label, controls);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void changeTrust(serverIp, { action: "add", command: input.value });
    });
    panel.append(form);
    const help = element("p", "command-note",
      'Matching commands run without asking. Use specific prefixes, such as docker ps. Quote arguments containing spaces. Changes apply before the next command; running commands continue.');
    help.id = helpId;
    const feedback = element("p", `trust-feedback${editor.error ? " error" : ""}`, editor.message);
    feedback.id = feedbackId;
    feedback.setAttribute("role", editor.error ? "alert" : "status");
    panel.append(help, feedback);
    fragment.append(panel);
  }
  if (!server) fragment.append(emptyState("No server selected", servers.length
    ? "Select server from list." : "Servers appear after client connects."));
  for (const [index, control] of [...fragment.querySelectorAll("button, summary, select")].entries()) {
    control.dataset.focus = `${selectedServerIp}:${index}`;
  }
  serversElement.replaceChildren(fragment);
  restoreFocus(serversElement, oldFocus);
  if (focusedId && selection) {
    const input = document.querySelector(`#${CSS.escape(focusedId)}`);
    input?.focus({ preventScroll: true });
    input?.setSelectionRange(...selection);
  }
}

function renderMemories() {
  if (currentView !== "memories") return;
  const runner = runners.find((item) => item.runner_id === selectedMemoryRunnerId);
  const query = queries.memories.trim().toLowerCase();
  const items = memories.filter((memory) => (selectedMemoryRunnerId === "global" ? memory.runner_id == null : memory.runner_id === selectedMemoryRunnerId)
    && `${memory.key} ${memory.value}`.toLowerCase().includes(query));

  if (!runner && selectedMemoryRunnerId !== "global") {
    titleElement.textContent = "Memories";
    metaElement.textContent = runners.length
      ? "Choose a runner to inspect its durable memory."
      : "Install a runner before using durable memory.";
    memoriesElement.replaceChildren(emptyState(
      runners.length ? "Choose a runner" : "No runners available",
      runners.length
        ? "Memory is isolated by execution target. Select a runner from the sidebar."
        : "Runner-scoped memory becomes available after a runner is installed.",
    ));
    newMemoryButton.disabled = true;
    return;
  }

  titleElement.textContent = runner ? `${runner.client_name} memories` : "Global memories";
  metaElement.textContent = runner ? `${runner.server_ip}:${runner.port} · ${items.length} shown` : `${items.length} shown across every conversation`;
  newMemoryButton.disabled = false;
  const fragment = document.createDocumentFragment();
  const intro = element("div", "memory-intro");
  intro.append(
    element("strong", "", runner ? "Runner-scoped durable memory" : "Global durable memory"),
    element("span", "", runner ? "Available in chats targeting this runner." : "Available in every chat."),
  );
  fragment.append(intro);
  if (!items.length) {
    const empty = emptyState(
      query ? "No matching memories" : "No memories yet",
      query ? "Try another search." : "The LLM can save durable facts during a conversation, or you can add one here.",
    );
    if (!query) {
      const add = element("button", "primary", "New memory");
      add.type = "button";
      add.addEventListener("click", () => openMemoryDialog());
      empty.append(add);
    }
    fragment.append(empty);
  } else {
    const grid = element("div", "memory-grid");
    for (const memory of items) {
      const card = element("article", "memory-card");
      const head = element("div", "memory-card-heading");
      const key = element("code", "memory-key", memory.key);
      const actions = element("div", "memory-actions");
      const edit = element("button", "memory-action", "Edit");
      edit.type = "button";
      edit.addEventListener("click", () => openMemoryDialog(memory));
      const remove = element("button", "memory-action danger", "Delete");
      remove.type = "button";
      remove.addEventListener("click", () => deleteMemoryItem(memory, remove));
      actions.append(edit, remove);
      head.append(key, actions);
      const value = element("div", "memory-value", memory.value);
      const footer = element("div", "memory-meta", `Updated ${fullTime(memory.updated_at)}`);
      if (memory.source_session_id) {
        const source = element("button", "memory-source", "Open source conversation");
        source.type = "button";
        source.addEventListener("click", () => selectConversation(memory.source_session_id));
        footer.append(" · ", source);
      }
      card.append(head, value, footer);
      grid.append(card);
    }
    fragment.append(grid);
  }
  memoriesElement.replaceChildren(fragment);
}

function openMemoryDialog(memory = null) {
  const runner = runners.find((item) => item.runner_id === selectedMemoryRunnerId);
  if (!runner && selectedMemoryRunnerId !== "global") return;
  editingMemoryId = memory?.memory_id || null;
  document.querySelector("#memory-dialog-title").textContent = memory ? "Edit memory" : "New memory";
  document.querySelector("#memory-dialog-description").textContent =
    runner ? `Stored only for ${runner.client_name} (${runner.server_ip}:${runner.port}).` : "Stored globally for every conversation.";
  memoryKeyInput.value = memory?.key || "";
  memoryValueInput.value = memory?.value || "";
  memoryScopeInput.value = memory?.runner_id == null ? "global" : "runner";
  memoryScopeInput.querySelector('option[value="runner"]').disabled = selectedMemoryRunnerId === "global";
  memoryScopeInput.disabled = Boolean(memory);
  memoryFeedback.textContent = "";
  memorySaveButton.disabled = false;
  memoryDialog.showModal();
  memoryKeyInput.focus();
}

async function saveMemoryFromDialog() {
  if ((!selectedMemoryRunnerId && selectedMemoryRunnerId !== "global") || memorySaveButton.disabled) return;
  memorySaveButton.disabled = true;
  memoryFeedback.textContent = "";
  try {
    const body = {
      runner_id: memoryScopeInput.value === "global" ? null : selectedMemoryRunnerId,
      key: memoryKeyInput.value,
      value: memoryValueInput.value,
    };
    if (editingMemoryId) body.memory_id = editingMemoryId;
    const response = await fetch("/v1/memories", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Brain-UI": "1" },
      body: JSON.stringify(body),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    memoryDialog.close();
    await refreshMemories();
  } catch (error) {
    memoryFeedback.textContent = error.message || "Could not save memory.";
  } finally {
    memorySaveButton.disabled = false;
  }
}

function deleteMemoryItem(memory, returnFocus = null) {
  const scope = memory.runner_id == null ? "global memory" : "runner memory";
  openConfirmation({
    title: "Delete memory?",
    description: `Delete ${scope} “${memory.key}”? This cannot be undone.`,
    confirmLabel: "Delete memory",
    returnFocus,
    run: async () => {
      const response = await fetch(`/v1/memories/${encodeURIComponent(memory.memory_id)}`, {
        method: "DELETE",
        headers: { "X-Brain-UI": "1" },
      });
      if (!response.ok) {
        let message = `HTTP ${response.status}`;
        try { message = (await response.json()).error || message; } catch (_) {}
        throw new Error(message);
      }
      await refreshMemories();
      return true;
    },
  });
}

const activityLabels = {
  waiting: "Waiting for model",
  thinking: "Thinking",
  preparing_tool: "Preparing command",
  running_tools: "Running commands",
  updating_memory: "Updating memory",
  writing: "Writing response",
  approval: "Approval needed",
  stopped: "Stopped",
  failed: "Failed",
};

function activityLabel(activity, startedAt = "") {
  const phase = activity?.phase || "waiting";
  let label = activityLabels[phase] || "Working";
  const tools = Array.isArray(activity?.tools) ? activity.tools : [];
  if (phase === "running_tools" && tools.length > 1) {
    const complete = tools.filter(tool => tool.state === "completed").length;
    label += ` · ${complete} of ${tools.length} finished`;
  }
  if (["waiting", "thinking", "preparing_tool"].includes(phase) && startedAt) {
    const seconds = Math.max(0, Math.floor((Date.now() - Date.parse(startedAt)) / 1000));
    if (Number.isFinite(seconds)) label += ` · ${seconds}s`;
  }
  return label;
}

function groupConversation(messages) {
  const groups = [];
  let current = null;
  messages.forEach((message, index) => {
    if (message.role === "user") {
      current = { user: { message, index }, entries: [] };
      groups.push(current);
    } else if (current) {
      current.entries.push({ message, index });
    } else {
      groups.push({ user: null, entries: [{ message, index }] });
    }
  });
  return groups;
}

function responseKey(detail, group, position) {
  const id = group.user?.message.ui?.message_id || group.user?.index || `legacy-${position}`;
  return `${detail.session_id}:${detail.active_branch_id || "legacy"}:${id}`;
}

function appendReasoningBlock(container, text) {
  if (!text) return;
  const block = element("div", "activity-thinking");
  block.append(element("strong", "", "Thinking"), element("pre", "", text));
  container.append(block);
}

function renderResponseGroup(detail, group, position, isLast) {
  const fragment = document.createDocumentFragment();
  if (group.user) fragment.append(renderMessage(group.user.message, group.user.index));
  const section = element("section", "response-group");
  const key = responseKey(detail, group, position);
  section.dataset.responseKey = key;

  const assistants = group.entries.filter(entry => entry.message.role === "assistant");
  const stoppedEntry = [...assistants].reverse().find(entry => entry.message.ui?.stopped);
  const finalEntry = [...assistants].reverse().find(entry => (
    typeof entry.message.content === "string" && entry.message.content.length
    && !entry.message.tool_calls?.length
  ));
  const results = group.entries.filter(entry => entry.message.role === "tool").map(entry => entry.message);
  const pendingIds = new Set((detail.pending_tool_calls || []).filter(call => call.ui?.remote).map(call => call.id));
  const activityBody = element("div", "response-activity-body");
  let activityCount = 0;

  for (const entry of assistants) {
    const message = entry.message;
    if (message.ui?.reasoning) {
      appendReasoningBlock(activityBody, message.ui.reasoning);
      activityCount += 1;
    }
    if (entry !== finalEntry && typeof message.content === "string" && message.content.length) {
      const note = element("div", "activity-commentary");
      note.append(markdownContent(message.content));
      activityBody.append(note);
      activityCount += 1;
    }
    const completedCalls = (message.tool_calls || []).filter(call => !pendingIds.has(call.id));
    for (const [callIndex, call] of completedCalls.entries()) {
      activityBody.append(renderTool(
        call,
        results.find(result => result.tool_call_id === call.id),
        `${key}:tool:${entry.index}:${callIndex}`,
      ));
      activityCount += 1;
    }
  }

  const live = isLast ? detail.live : null;
  if (live?.reasoning) {
    appendReasoningBlock(activityBody, live.reasoning);
    activityCount += 1;
  }
  if (live) {
    const status = element("div", "live-activity-state", activityLabel(live.activity, live.started_at));
    activityBody.prepend(status);
    activityCount += 1;
  }

  if (activityCount) {
    const activity = activityDisclosure(`${key}:activity`, Boolean(live));
    const count = activityBody.querySelectorAll(".tool-card").length;
    const summary = element("summary", "response-activity-summary");
    summary.append(
      element("span", "", live ? activityLabel(live.activity, live.started_at) : "Activity"),
      element("span", "activity-count", count ? `${count} ${count === 1 ? "action" : "actions"}` : ""),
    );
    activity.append(summary, activityBody);
    activity.classList.toggle("answers-only-hidden", answersOnlyEnabled(detail));
    section.append(activity);
  }

  for (const entry of group.entries.filter(entry => entry.message.role === "system")) {
    section.append(renderMessage(entry.message, entry.index));
  }

  for (const [pendingIndex, call] of (detail.pending_tool_calls || []).filter(call => (
    call.ui?.remote && assistants.some(entry => entry.message.tool_calls?.some(item => item.id === call.id))
  )).entries()) {
    section.append(renderTool(call, null, `${key}:pending:${pendingIndex}`));
  }

  if (finalEntry) {
    section.append(renderMessage(finalEntry.message, finalEntry.index, [], { reasoning: false, tools: false }));
  }
  if (live?.content) section.append(renderLive(live));
  if (!finalEntry && !live && group.user && activityCount && !pendingIds.size) {
    section.append(element(
      "p",
      stoppedEntry ? "response-ended message-stopped" : "response-ended",
      stoppedEntry ? "Stopped" : "No final answer.",
    ));
  }
  fragment.append(section);
  return fragment;
}

function renderDetail(detail) {
  const state = sessionState(detail.session_id);
  rememberDisclosures(transcript);
  const focusedButton = transcript.contains(document.activeElement) ? document.activeElement?.dataset.focus : null;
  const focusedKey = document.activeElement?.tagName === "SUMMARY"
    ? document.activeElement.closest("details[data-key]")?.dataset.key : null;
  const wasNearBottom = state.restoreScroll ? (state.follow ?? true)
    : transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 24;
  const previousScrollTop = state.restoreScroll ? (state.scrollTop || 0) : transcript.scrollTop;
  state.restoreScroll = false;
  const fragment = document.createDocumentFragment();
  const messages = [...detail.messages];
  if (detail.live) messages.push(...detail.live.transient_messages);
  const groups = groupConversation(messages);
  groups.forEach((group, index) => {
    fragment.append(renderResponseGroup(detail, group, index, index === groups.length - 1));
  });
  if (detail.live && !groups.length) {
    fragment.append(renderResponseGroup(detail, { user: null, entries: [] }, 0, true));
  }
  if (!messages.length && !detail.live) {
    fragment.append(emptyState("Empty conversation", "No client messages yet."));
  }
  transcript.replaceChildren(fragment);
  if (focusedKey) {
    const restored = [...document.querySelectorAll("details[data-key]")]
      .find((details) => details.dataset.key === focusedKey);
    restored?.querySelector("summary")?.focus({ preventScroll: true });
  }
  restoreFocus(transcript, focusedButton);
  transcript.scrollTop = wasNearBottom ? transcript.scrollHeight : previousScrollTop;
  updateJump();

  titleElement.textContent = detail.title || `Conversation ${shortId(detail.session_id)}`;
  titleElement.title = titleElement.textContent;
  conversationInfo.textContent = `Started ${fullTime(detail.created_at)} · ${detail.message_count} messages`;
  metaElement.replaceChildren();
  if (detail.client?.server_ip) {
    const link = element("button", "server-link", `Server ${detail.client.server_ip}`);
    link.type = "button";
    link.addEventListener("click", () => showServers(detail.client.server_ip));
    metaElement.append(link);
  }
  statusBadge.classList.remove("hidden", "live");
  statusBadge.classList.toggle("live", detail.active);
  const remoteCalls = detail.pending_tool_calls?.filter(call => call.ui?.remote) || [];
  const remote = remoteCalls[0];
  const remoteCount = remoteCalls.length;
  statusBadge.textContent = detail.archived ? "Archived" : detail.active ? "Generating"
    : remote ? (remoteCount > 1 ? `${remoteCount} commands pending`
      : remote.ui.state === "failed" ? "Runner unavailable" : "Approval needed")
      : statusText(detail.status);
  approvalBanner.classList.toggle("hidden", !remote || currentView !== "conversations");
  approvalBanner.textContent = remoteCount > 1
    ? `${remoteCount} commands need review · Open next`
    : remote?.ui.state === "failed" ? "Runner unavailable · View command"
      : "Command needs approval · Review command";
  actionsElement.classList.remove("hidden");
  const answersOnly = answersOnlyEnabled(detail);
  answersOnlyButton.setAttribute("aria-pressed", String(answersOnly));
  answersOnlyButton.classList.toggle("active", answersOnly);
  archiveButton.textContent = detail.archived ? "Unarchive" : "Archive";
  archiveButton.disabled = state.actionBusy || detail.active;
  deleteButton.disabled = state.actionBusy || detail.active;
  document.querySelector("#pin-button").textContent = detail.pinned ? "Unpin conversation" : "Pin conversation";
  document.querySelector("#pin-button").disabled = state.actionBusy;
  document.querySelector("#rename-button").disabled = state.actionBusy;
  showFeedback(state.feedback);
  renderConversationRunnerPicker(detail, state);
  renderComposer(detail);
  renderResponseStatus(detail);
  state.nextCommandId = "";
  // Header/composer height may change after rendering a snapshot.
  if (wasNearBottom) transcript.scrollTop = transcript.scrollHeight;
  updateJump();
  syncResearchDialog(detail);
}

function renderResponseStatus(detail) {
  if (currentView !== "conversations") {
    responseStatus.classList.add("hidden");
    return;
  }
  const remote = detail.pending_tool_calls?.filter(call => call.ui?.remote) || [];
  let text = "";
  if (detail.live) text = activityLabel(detail.live.activity, detail.live.started_at);
  else if (remote.length) text = remote.length === 1
    ? "Approval needed" : `Approval needed · ${remote.length} commands`;
  else if (sessionState(detail.session_id).stopBusy) text = "Stopping generation…";
  responseStatus.textContent = text;
  responseStatus.classList.toggle("hidden", !text);
}

function renderComposer(detail) {
  const state = sessionState(detail.session_id);
  if (!detail.active) state.stopBusy = false;
  loadDraft(detail.session_id);
  const aiReady = aiServers.some(server => server.active && server.selected_model);
  const unavailable = detail.archived || detail.active || state.messageBusy
    || state.branchBusy || state.commandBusy || !connected || !aiReady;
  messageForm.classList.toggle("hidden", currentView !== "conversations");
  messageInput.disabled = detail.archived;
  messageSend.classList.toggle("hidden", detail.active);
  messageStop.classList.toggle("hidden", !detail.active);
  messageStop.disabled = state.stopBusy || !connected;
  messageSend.disabled = unavailable || (!messageInput.value.trim() && !(state.references || []).length);
  messageSend.textContent = state.messageBusy ? "Sending…"
    : state.branchBusy ? "Switching…" : state.commandBusy ? "Working…" : "Send";
  messageHint.classList.toggle("error", Boolean(state.failure));
  messageHint.replaceChildren();
  let hint = "";
  if (state.failure) hint = state.failure;
  else if (state.notice && !detail.active) hint = state.notice;
  else if (detail.archived) hint = "Restore conversation before replying.";
  else if (!connected) hint = "Reconnecting. Your draft is kept here.";
  else if (detail.active) hint = state.stopBusy
    ? "Stopping generation…" : "Brain is replying. You can draft your next message.";
  else if (detail.status === "awaiting_tool_results") hint = detail.pending_tool_calls?.some(call => call.ui?.remote)
    ? "Review command above, or send a new instruction to cancel it." : "Sending cancels pending terminal commands.";
  else if (detail.status === "continuation_pending") hint = "Send an instruction to resume this interrupted conversation.";
  else if (!aiReady) {
    hint = "Configure AI model before sending messages.";
    const configure = element("button", "composer-hint-action", "Configure AI");
    configure.type = "button";
    configure.addEventListener("click", openAIConfig);
    messageHint.append(document.createTextNode(`${hint} `), configure);
  } else hint = detail.runner
    ? `Commands run on ${detail.runner.client_name} after approval or a trusted-prefix match.`
    : "Chat only · Select a target to enable commands.";
  if (!messageHint.childNodes.length) messageHint.textContent = hint;
  renderContextReferences(detail);
  renderContextMeter(detail);
}

async function changeConversationRunner(value) {
  if (!currentDetail) return;
  const id = currentDetail.session_id;
  const state = sessionState(id);
  if (state.actionBusy) return;
  const oldValue = (currentDetail.runner_change_pending ? currentDetail.pending_runner_id : currentDetail.runner_id) || "";
  if (value === oldValue) { runnerPicker.open = false; runnerPickerSummary.focus(); return; }
  const runnerId = value || null;
  state.actionBusy = true;
  try {
    const response = await fetch(`/v1/conversations/${encodeURIComponent(id)}/runner`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-Brain-UI": "1" },
      body: JSON.stringify({ runner_id: runnerId }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    if (currentDetail?.session_id === id) {
      currentDetail = result;
      runnerPicker.open = false;
      renderDetail(result);
      runnerPickerSummary.focus();
    }
  } catch (error) {
    showSessionError(id, error.message || "Could not change target. Select a target to retry.");
  } finally {
    state.actionBusy = false;
    if (currentDetail?.session_id === id) renderDetail(currentDetail);
  }
}

async function openNewConversation() {
  if (newRunnerLoading || newConversationBusy) return;
  setSidebar(false, false);
  if (!newConversationDialog.open) newConversationDialog.showModal();
  const feedback = document.querySelector("#new-conversation-feedback");
  const preferred = preferredNewRunner();
  renderNewRunnerOptions(preferred);
  createConversationButton.disabled = false;
  showFeedback(activeRunners().length ? "" : "No available runners. Chat only remains available.", null, feedback);
  newRunnerLoading = true;
  try {
    const result = await getJson("/v1/runners");
    runners = Array.isArray(result.runners) ? result.runners : [];
    const selected = newRunnerSelect.value;
    const stillValid = !selected || activeRunners().some(runner => runner.runner_id === selected);
    renderNewRunnerOptions(stillValid ? selected : preferredNewRunner());
    showFeedback(activeRunners().length ? "" : "No available runners. Continue with chat only, or open Manage runners to set one up.", null, feedback);
  } catch (_) {
    showFeedback("Could not refresh runners. Cached targets remain available.", () => void openNewConversation(), feedback);
  } finally {
    newRunnerLoading = false;
  }
}

async function createConversation() {
  if (newConversationBusy) return;
  newConversationBusy = true;
  const runnerId = newRunnerSelect.value || null;
  createConversationButton.disabled = true;
  createConversationButton.textContent = "Starting…";
  try {
    const response = await fetch("/v1/conversations", {
      method: "POST", headers: { "Content-Type": "application/json", "X-Brain-UI": "1" },
      body: JSON.stringify({ runner_id: runnerId }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    storageWrite("localStorage", "brain.lastRunner", runnerId || "");
    newConversationDialog.close();
    selectConversation(result.session_id);
    await refreshList();
    messageInput.focus({ preventScroll: true });
  } catch (error) {
    showFeedback(error.message || "Could not create conversation. Try again.", null, document.querySelector("#new-conversation-feedback"));
  } finally {
    newConversationBusy = false;
    createConversationButton.disabled = false;
    createConversationButton.textContent = "Start conversation";
  }
}

function turnError(body) {
  try { const parsed = JSON.parse(body); if (parsed.error) return parsed.error; } catch (_) {}
  for (const block of body.split(/\r?\n\r?\n/)) {
    const lines = block.split(/\r?\n/);
    if (!lines.includes("event: error")) continue;
    const data = lines.find((line) => line.startsWith("data: "))?.slice(6);
    try {
      const parsed = JSON.parse(data || "{}");
      if (typeof parsed.message === "string") return parsed.message;
    } catch (_error) {
      return "Invalid Brain response.";
    }
  }
  return "Brain turn ended without completion.";
}

async function sendWebMessage() {
  if (!currentDetail || messageSend.disabled) return;
  const sessionId = currentDetail.session_id;
  const state = sessionState(sessionId);
  if (state.messageBusy || state.branchBusy || state.commandBusy
    || currentDetail.active || currentDetail.archived || !connected) return;
  const content = messageInput.value;
  if (!content.trim() && !(state.references || []).length) return;
  let references = state.references || [];
  if (!references.length) {
    try { references = JSON.parse(storageRead("sessionStorage", `brain.refs.${sessionId}`, "[]")) || []; }
    catch (_) { references = []; }
  }
  saveDraft();
  state.failure = "";
  state.notice = "";
  state.messageBusy = true;
  let accepted = false;
  renderComposer(currentDetail);
  try {
    const response = await fetch(`/v1/conversations/${encodeURIComponent(sessionId)}/turns`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-Brain-UI": "1" },
      body: JSON.stringify({ content, references }),
    });
    if (!response.ok) {
      const result = await response.json();
      throw new Error(result.error || `HTTP ${response.status}`);
    }
    accepted = true;
    // Clear only the submitted draft, never edits made while the request was starting.
    if (state.draft === content) {
      state.draft = "";
      state.references = [];
      storageWrite("sessionStorage", `brain.draft.${sessionId}`, "");
      storageWrite("sessionStorage", `brain.refs.${sessionId}`, "");
      if (draftSessionId === sessionId) { messageInput.value = ""; resizeComposer(); }
    }
    const body = await response.text();
    if (!/(^|\r?\n)event: (done|tool_calls)\r?$/m.test(body)) throw new Error(turnError(body));
  } catch (error) {
    state.failure = accepted
      ? `${error.message} Your message was accepted; check the transcript before continuing.`
      : `${error.message || "Could not send message."} Draft kept. Check the transcript before retrying.`;
  } finally {
    state.messageBusy = false;
    if (currentDetail?.session_id === sessionId) renderComposer(currentDetail);
  }
}

async function stopGeneration() {
  if (!currentDetail?.active) return;
  const sessionId = currentDetail.session_id;
  const state = sessionState(sessionId);
  if (state.stopBusy) return;
  state.stopBusy = true;
  state.failure = "";
  renderComposer(currentDetail);
  try {
    const response = await fetch(`/v1/conversations/${encodeURIComponent(sessionId)}/stop`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-Brain-UI": "1" },
      body: "{}",
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    state.notice = "Generation stopped. Send a message to continue.";
    if (currentDetail?.session_id === sessionId) renderComposer(currentDetail);
  } catch (error) {
    state.stopBusy = false;
    state.failure = error.message || "Could not stop generation.";
    if (currentDetail?.session_id === sessionId) renderComposer(currentDetail);
  }
}

async function refreshList() {
  try {
    const [result, runnerResult] = await Promise.all([
      getJson("/v1/conversations"), getJson("/v1/runners"),
    ]);
    runners = Array.isArray(runnerResult.runners) ? runnerResult.runners : [];
    conversations = Array.isArray(result.conversations) ? result.conversations : [];
    if (currentView !== "conversations") return;
    setConnection(!selectedId || conversationStream?.readyState === EventSource.OPEN);
    const visible = visibleConversations();
    if (selectedId && !visibleConversations().some((item) => item.session_id === selectedId)) {
      selectedId = null;
      closeConversationStream();
      history.replaceState(null, "", location.pathname);
    }
    if (!selectedId && visible.length && currentView === "conversations") {
      selectedId = visible[0].session_id;
      history.replaceState(null, "", `#${selectedId}`);
    }
    renderConversationList();
    if (selectedId && currentView === "conversations") await refreshDetail();
    if (!selectedId && currentView === "conversations") {
      closeConversationStream();
      titleElement.textContent = "Conversations";
      metaElement.textContent = "Client sessions appear here automatically.";
      statusBadge.classList.add("hidden");
      actionsElement.classList.add("hidden");
      messageForm.classList.add("hidden");
      transcript.replaceChildren(emptyState("No conversations", "Client sessions appear here automatically.", true));
    }
    renderView();
  } catch (_error) {
    if (currentView === "conversations") setConnection(false);
  }
}

function closeConversationStream() {
  saveDraft();
  if (currentDetail) {
    rememberDisclosures(transcript);
    const state = sessionState(currentDetail.session_id);
    state.scrollTop = transcript.scrollTop;
    state.follow = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 24;
  }
  if (conversationStream) conversationStream.close();
  conversationStream = null;
  streamedSessionId = null;
  currentDetail = null;
}

function refreshDetail() {
  if (currentView !== "conversations" || !selectedId || streamedSessionId === selectedId) return;
  closeConversationStream();
  sessionState().restoreScroll = true;
  actionsElement.classList.add("hidden");
  messageForm.classList.add("hidden");
  approvalBanner.classList.add("hidden");
  titleElement.textContent = `Conversation ${shortId(selectedId)}`;
  metaElement.textContent = "Connecting to conversation…";
  statusBadge.classList.add("hidden");
  transcript.replaceChildren(emptyState("Loading conversation", "Connecting to live updates…"));
  streamedSessionId = selectedId;
  const stream = new EventSource(`/v1/conversations/${encodeURIComponent(selectedId)}/events`);
  conversationStream = stream;
  stream.addEventListener("snapshot", (event) => {
    if (conversationStream !== stream) return;
    if (streamFrame) cancelAnimationFrame(streamFrame);
    streamFrame = 0;
    streamDeltas.reasoning = "";
    streamDeltas.content = "";
    currentDetail = JSON.parse(event.data);
    renderDetail(currentDetail);
  });
  for (const kind of ["reasoning", "content"]) {
    stream.addEventListener(kind, (event) => {
      if (conversationStream !== stream || !currentDetail?.live) return;
      const delta = JSON.parse(event.data).delta;
      currentDetail.live[kind] += delta;
      streamDeltas[kind] += delta;
      if (streamFrame) return;
      streamFrame = requestAnimationFrame(() => {
        streamFrame = 0;
        const follow = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 24;
        let needsRender = false;
        for (const type of ["reasoning", "content"]) {
          const chunk = streamDeltas[type];
          streamDeltas[type] = "";
          if (!chunk) continue;
          const target = type === "reasoning"
            ? transcript.querySelector(".response-group:last-child .activity-thinking:last-of-type pre")
            : transcript.querySelector('[data-live="true"] .message-content');
          if (target) target.append(document.createTextNode(chunk));
          else needsRender = true;
        }
        if (needsRender && currentDetail) renderDetail(currentDetail);
        else {
          if (follow) transcript.scrollTop = transcript.scrollHeight;
          updateJump();
        }
      });
    });
  }
  stream.addEventListener("activity", event => {
    if (conversationStream !== stream || !currentDetail?.live) return;
    currentDetail.live.activity = JSON.parse(event.data);
    const label = activityLabel(currentDetail.live.activity, currentDetail.live.started_at);
    responseStatus.textContent = label;
    responseStatus.classList.remove("hidden");
    const liveState = transcript.querySelector(".live-activity-state");
    if (liveState) liveState.textContent = label;
    const summary = transcript.querySelector(".response-group:last-child .response-activity-summary span");
    if (summary) summary.textContent = label;
  });
  stream.addEventListener("context", event => {
    if (conversationStream !== stream || !currentDetail) return;
    currentDetail.context_usage = JSON.parse(event.data);
    renderContextMeter(currentDetail);
  });
  stream.addEventListener("deleted", () => {
    if (conversationStream !== stream) return;
    closeConversationStream();
    selectedId = null;
    history.replaceState(null, "", location.pathname);
    void refreshList();
  });
  stream.onopen = () => {
    if (conversationStream === stream) setConnection(true);
  };
  stream.onerror = () => {
    if (conversationStream !== stream) return;
    setConnection(false);
    metaElement.textContent = "Reconnecting to conversation…";
  };
}

async function changeConversationArchive() {
  if (!currentDetail || currentDetail.active) return;
  const id = currentDetail.session_id;
  const state = sessionState(id);
  if (state.actionBusy) return;
  const archived = !currentDetail.archived;
  state.actionBusy = true;
  conversationMenu.open = false;
  renderDetail(currentDetail);
  try {
    const response = await fetch(`/v1/conversations/${encodeURIComponent(id)}/archive`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-Brain-UI": "1" },
      body: JSON.stringify({ archived }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    if (currentDetail?.session_id === id) { currentDetail = result; renderDetail(result); }
    await refreshList();
  } catch (error) { showSessionError(id, error.message || "Could not change archive state. Try again from conversation actions."); }
  finally {
    state.actionBusy = false;
    if (currentDetail?.session_id === id) renderDetail(currentDetail);
  }
}

async function deleteConversation(id) {
  const state = sessionState(id);
  if (state.actionBusy) return false;
  state.actionBusy = true;
  try {
    const response = await fetch(`/v1/conversations/${encodeURIComponent(id)}`, {
      method: "DELETE", headers: { "X-Brain-UI": "1" },
    });
    if (!response.ok) { const result = await response.json(); throw new Error(result.error || `HTTP ${response.status}`); }
    if (selectedId === id) {
      closeConversationStream(); selectedId = null;
      history.replaceState(null, "", location.pathname);
    }
    storageWrite("sessionStorage", `brain.draft.${id}`, "");
    sessionStates.delete(id);
    if (draftSessionId === id) { draftSessionId = null; messageInput.value = ""; }
    await refreshList();
    return true;
  } catch (error) {
    throw new Error(error.message || "Could not delete conversation. Try again.");
  } finally { state.actionBusy = false; }
}

menuButton.addEventListener("click", () => setSidebar(true));
sidebarShade.addEventListener("click", () => setSidebar(false));
document.querySelector("#sidebar-close").addEventListener("click", () => setSidebar(false));
function applyRoute(force = false) {
  if (!force && routedHash === location.hash) return;
  routedHash = location.hash;
  saveDraft();
  const value = location.hash.slice(1);
  if (value === "servers" || value.startsWith("servers/")) {
    currentView = "servers";
    selectedServerIp = serverIpFromHash();
    closeConversationStream();
    renderView();
    void refreshServers();
  } else if (value === "memories" || value.startsWith("memories/")) {
    currentView = "memories";
    selectedMemoryRunnerId = memoryRunnerIdFromHash();
    closeConversationStream();
    renderView();
    void refreshMemories();
  } else {
    currentView = "conversations";
    selectedId = validSessionId(value) ? value : null;
    closeConversationStream();
    renderView();
    renderConversationList();
    refreshDetail();
    void refreshList();
  }
  setSidebar(false, false);
}
window.addEventListener("hashchange", () => applyRoute());
window.addEventListener("popstate", () => applyRoute());

async function refreshServers() {
  try {
    const [serverResult, runnerResult] = await Promise.all([
      getJson("/v1/servers"), getJson("/v1/runners"),
    ]);
    servers = Array.isArray(serverResult.servers) ? serverResult.servers : [];
    runners = Array.isArray(runnerResult.runners) ? runnerResult.runners : [];
    if (currentView !== "servers") return;
    if (!servers.some((server) => server.server_ip === selectedServerIp)) {
      selectedServerIp = servers[0]?.server_ip || null;
      history.replaceState(null, "", selectedServerIp ? `#servers/${encodeURIComponent(selectedServerIp)}` : "#servers");
    }
    if (currentView === "servers") setConnection(true);
    renderServerList();
    renderServers();
  } catch (_error) {
    if (currentView === "servers") setConnection(false);
  }
}

async function refreshMemories() {
  try {
    const result = await getJson("/v1/memories");
    memories = Array.isArray(result.memories) ? result.memories : [];
    runners = Array.isArray(result.runners) ? result.runners : [];
    if (currentView !== "memories") return;
    if (selectedMemoryRunnerId !== "global" && !runners.some((runner) => runner.runner_id === selectedMemoryRunnerId)) {
      selectedMemoryRunnerId = runners.find((runner) => memoryCountForRunner(runner.runner_id))?.runner_id
        || runners[0]?.runner_id || (memories.some((memory) => memory.runner_id == null) ? "global" : null);
      history.replaceState(null, "", selectedMemoryRunnerId
        ? `#memories/${encodeURIComponent(selectedMemoryRunnerId)}` : "#memories");
    }
    setConnection(true);
    renderMemoryRunnerList();
    renderMemories();
  } catch (_error) {
    if (currentView === "memories") setConnection(false);
  }
}

async function refreshDashboard() {
  try {
    await refreshAIConfig(false, true);
    if (currentView === "servers") await refreshServers();
    else if (currentView === "memories") await refreshMemories();
    else await refreshList();
  } finally {
    window.setTimeout(refreshDashboard, 3000);
  }
}

conversationsViewButton.addEventListener("click", () => {
  navigateRoute(selectedId || "");
});
serversViewButton.addEventListener("click", () => showServers());
memoriesViewButton.addEventListener("click", () => showMemories());
filterElement.addEventListener("change", () => {
  conversationFilter = filterElement.value;
  const visible = visibleConversations();
  if (selectedId && !visible.some((item) => item.session_id === selectedId)) {
    selectedId = null;
    closeConversationStream();
    history.replaceState(null, "", location.pathname);
  }
  renderConversationList();
  void refreshList();
});
archiveButton.addEventListener("click", () => void changeConversationArchive());
deleteButton.addEventListener("click", confirmConversationDelete);
newConversationButton.addEventListener("click", () => void openNewConversation());
addServerButton.addEventListener("click", () => void openAddServer());
newMemoryButton.addEventListener("click", () => openMemoryDialog());
function openAIConfig() {
  closeAIServerForm();
  if (!aiConfigDialog.open) aiConfigDialog.showModal();
  void refreshAIConfig(true);
  void refreshWebToolsConfig();
}
aiConfigButton.addEventListener("click", openAIConfig);
aiConfigClose.addEventListener("click", () => aiConfigDialog.close());
aiServerAdd.addEventListener("click", () => openAIServerForm());
document.querySelector("#ai-server-cancel").addEventListener("click", closeAIServerForm);
aiServerForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (aiServerSave.disabled) return;
  aiServerSave.disabled = true;
  setAIConfigFeedback("Checking models…", "info");
  const id = aiServerId.value;
  const payload = {
    name: aiServerName.value,
    endpoint_url: aiServerEndpoint.value,
  };
  if (!id || aiServerKey.value) payload.api_key = aiServerKey.value;
  try {
    await aiWrite(id ? `/v1/ai/servers/${encodeURIComponent(id)}` : "/v1/ai/servers", payload);
    closeAIServerForm();
    await refreshAIConfig(false);
    setAIConfigFeedback(id ? "Server updated" : "Server added", "success", true);
  } catch (error) {
    setAIConfigFeedback(error.message || "Could not save AI server.", "error");
  } finally {
    aiServerSave.disabled = false;
  }
});
document.querySelector("#research-dialog-close").addEventListener("click", () => researchDialog.close());
document.querySelector("#research-stop").addEventListener("click", () => messageStop.click());
researchDialog.addEventListener("close", () => {
  if (researchDialog.dataset.callId && researchDialog.dataset.sessionId) {
    researchDismissed.add(`${researchDialog.dataset.sessionId}:${researchDialog.dataset.callId}`);
  }
});
webToolsForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (webToolsSave.disabled) return;
  webToolsSave.disabled = true;
  webToolsFeedback.textContent = "Saving…";
  let serverId = null, model = null;
  if (researchModel.value) [serverId, model] = JSON.parse(researchModel.value);
  try {
    webToolsConfig = await aiWrite("/v1/web-tools/config", {
      searxng_url: searxngUrl.value.trim(),
      default_results: Number(searxngResults.value),
      research_server_id: serverId,
      research_model: model,
    });
    webToolsFeedback.textContent = "Web settings saved.";
    renderResearchModelOptions();
  } catch (error) {
    webToolsFeedback.textContent = error.message || "Could not save Web settings.";
  } finally { webToolsSave.disabled = false; }
});
searxngTest.addEventListener("click", async () => {
  if (searxngTest.disabled) return;
  searxngTest.disabled = true;
  webToolsFeedback.textContent = "Testing SearXNG…";
  try {
    await aiWrite("/v1/web-tools/test", { searxng_url: searxngUrl.value.trim() });
    webToolsFeedback.textContent = "SearXNG JSON search available.";
  } catch (error) {
    webToolsFeedback.textContent = error.message || "SearXNG test failed.";
  } finally { searxngTest.disabled = false; }
});
addServerClose.addEventListener("click", () => addServerDialog.close());
addServerDone.addEventListener("click", () => addServerDialog.close());
addServerCopy.addEventListener("click", () => void copyText(addServerCommand.textContent, addServerCopy));
newConversationCancel.addEventListener("click", () => newConversationDialog.close());
newConversationBack.addEventListener("click", () => newConversationDialog.close());
manageRunnersButton.addEventListener("click", () => {
  newConversationDialog.close();
  showServers();
});
newRunnerOptions.addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) {
    return;
  }
  const options = [...newRunnerOptions.querySelectorAll(".target-option:not(:disabled)")];
  const current = event.target.closest(".target-option");
  const currentIndex = options.indexOf(current);
  if (currentIndex < 0) return;
  event.preventDefault();
  let nextIndex = event.key === "Home" ? 0 : options.length - 1;
  if (event.key.startsWith("Arrow")) {
    const direction = ["ArrowRight", "ArrowDown"].includes(event.key) ? 1 : -1;
    nextIndex = (currentIndex + direction + options.length) % options.length;
  }
  const next = options[nextIndex];
  selectNewRunner(next.dataset.runnerId);
  next.focus();
});
newConversationForm.addEventListener("submit", (event) => {
  event.preventDefault();
  void createConversation();
});
messageForm.addEventListener("submit", (event) => {
  event.preventDefault();
  void sendWebMessage();
});
messageStop.addEventListener("click", () => void stopGeneration());
memoryForm.addEventListener("submit", (event) => {
  event.preventDefault();
  void saveMemoryFromDialog();
});
document.querySelector("#memory-cancel").addEventListener("click", () => memoryDialog.close());
memoryDialog.addEventListener("close", () => {
  editingMemoryId = null;
  memoryFeedback.textContent = "";
});
messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing && !matchMedia("(pointer: coarse)").matches) {
    event.preventDefault();
    messageForm.requestSubmit();
  }
});

const themeChoices = {
  system: { label: "System", icon: "◐", description: "Follow device setting" },
  light: { label: "Light", icon: "☀", description: "Always use light theme" },
  dark: { label: "Dark", icon: "☾", description: "Always use dark theme" },
};
let selectedTheme = storageRead("localStorage", "brain.theme", "system");
if (!themeChoices[selectedTheme]) selectedTheme = "system";

function renderThemePicker() {
  const selected = themeChoices[selectedTheme];
  document.querySelector("#theme-picker-icon").textContent = selected.icon;
  document.querySelector("#theme-picker-value").textContent = selected.label;
  themePickerSummary.setAttribute("aria-label", `Change appearance. Current: ${selected.label}`);
  const fragment = document.createDocumentFragment();
  const heading = element("div", "theme-menu-heading");
  heading.append(element("strong", "", "Appearance"), element("span", "", "Choose interface theme"));
  fragment.append(heading);
  for (const [value, choice] of Object.entries(themeChoices)) {
    const button = element("button", `theme-menu-option${value === selectedTheme ? " selected" : ""}`);
    button.type = "button";
    button.dataset.theme = value;
    button.setAttribute("aria-pressed", String(value === selectedTheme));
    const icon = element("span", "theme-menu-icon", choice.icon);
    icon.setAttribute("aria-hidden", "true");
    const copy = element("span", "theme-menu-copy");
    copy.append(element("strong", "", choice.label), element("span", "", choice.description));
    button.append(icon, copy);
    button.addEventListener("click", () => setTheme(value));
    fragment.append(button);
  }
  themeMenu.replaceChildren(fragment);
}

function setTheme(value) {
  selectedTheme = themeChoices[value] ? value : "system";
  document.documentElement.dataset.theme = selectedTheme;
  storageWrite("localStorage", "brain.theme", selectedTheme);
  themePicker.open = false;
  renderThemePicker();
  themePickerSummary.focus();
}

document.documentElement.dataset.theme = selectedTheme;
renderThemePicker();
searchInput.addEventListener("input", () => {
  queries[currentView] = searchInput.value;
  if (currentView === "servers") renderServerList();
  else if (currentView === "memories") { renderMemoryRunnerList(); renderMemories(); }
  else renderConversationList();
});
messageInput.addEventListener("input", () => {
  saveDraft(); resizeComposer();
  if (currentDetail) renderComposer(currentDetail);
});
function addContextReference(ref) {
  if (!currentDetail) return;
  const sessionId = currentDetail.session_id;
  const state = sessionState(sessionId);
  const references = state.references || [];
  if (references.some(item => referenceKey(item) === referenceKey(ref))) {
    contextFeedback.textContent = `${ref.label} is already attached.`;
    return;
  }
  if (references.length >= 32) {
    contextFeedback.textContent = "Maximum 32 context items.";
    return;
  }
  storeReferences(sessionId, [...references, ref]);
  renderComposer(currentDetail);
  contextDialog.close();
  messageInput.focus({ preventScroll: true });
}

function setContextTab(name, focus = false) {
  const tabs = [...document.querySelectorAll("[data-context-tab]")];
  for (const tab of tabs) {
    const selected = tab.dataset.contextTab === name;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
    if (selected && focus) tab.focus({ preventScroll: true });
  }
  for (const panel of document.querySelectorAll(".context-panel")) {
    panel.classList.toggle("hidden", panel.id !== `context-${name}`);
  }
}

function renderContextMemoryChoices() {
  if (!currentDetail) return;
  const runnerId = currentDetail.runner_id || currentDetail.runner?.runner_id || null;
  const available = memories.filter(memory => memory.runner_id == null || memory.runner_id === runnerId);
  const fragment = document.createDocumentFragment();
  for (const memory of available) {
    const scope = memory.runner_id == null ? "Global" : currentDetail.runner?.client_name || "Selected runner";
    const button = element("button", "context-memory-item", `${memory.key} · ${scope}`);
    button.type = "button";
    button.addEventListener("click", () => addContextReference({
      type: "memory", id: memory.memory_id, label: memory.key, snapshot: memory.value,
    }));
    fragment.append(button);
  }
  if (!available.length) fragment.append(element("p", "context-empty", "No memories available for this conversation."));
  contextMemory.replaceChildren(fragment);
}

async function openContextDialog() {
  if (!currentDetail) return;
  contextFeedback.textContent = "";
  contextFile.value = "";
  contextServerPath.value = "";
  setContextTab("upload");
  const hasRunner = Boolean(currentDetail.runner_id || currentDetail.runner?.runner_id);
  const serverTab = document.querySelector("#context-tab-server");
  serverTab.disabled = !hasRunner;
  serverTab.title = hasRunner ? "" : "Select execution target first";
  contextTargetNote.classList.toggle("hidden", hasRunner);
  contextMemory.replaceChildren(element("p", "context-empty", "Loading memories…"));
  contextDialog.showModal();
  try {
    const result = await getJson("/v1/memories");
    memories = Array.isArray(result.memories) ? result.memories : [];
    if (Array.isArray(result.runners)) runners = result.runners;
    renderContextMemoryChoices();
  } catch (error) {
    contextMemory.replaceChildren(element("p", "context-empty error", "Could not load memories."));
    contextFeedback.textContent = error.message || "Could not load memories.";
  }
}

contextAdd.addEventListener("click", () => void openContextDialog());
contextClose.addEventListener("click", () => contextDialog.close());
document.querySelectorAll("[data-context-tab]").forEach(button => {
  button.addEventListener("click", () => setContextTab(button.dataset.contextTab));
  button.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const tabs = [...document.querySelectorAll("[data-context-tab]:not(:disabled)")];
    const index = tabs.indexOf(button);
    if (index < 0) return;
    event.preventDefault();
    let next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1
      : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    setContextTab(tabs[next].dataset.contextTab, true);
  });
});
contextTargetOpen.addEventListener("click", () => {
  contextDialog.close();
  runnerPicker.open = true;
  runnerPickerSummary.focus({ preventScroll: true });
});
contextServerAdd.addEventListener("click", () => {
  const path = contextServerPath.value.trim(); if (!path) return;
  const runnerId = currentDetail?.runner_id || currentDetail?.runner?.runner_id;
  if (!runnerId) {
    contextFeedback.textContent = "Select execution target before adding server path.";
    return;
  }
  addContextReference({type:"server_file", label:path, path, runner_id:runnerId});
});
contextUploadButton.addEventListener("click", async () => {
  const file = contextFile.files?.[0]; if (!file || !currentDetail) return;
  contextUploadButton.disabled = true; contextFeedback.textContent = "Uploading…";
  try {
    const data = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(",")[1]); reader.onerror = reject; reader.readAsDataURL(file); });
    const response = await fetch(`/v1/conversations/${encodeURIComponent(currentDetail.session_id)}/attachments`, {method:"POST", headers:{"Content-Type":"application/json", "X-Brain-UI":"1"}, body:JSON.stringify({filename:file.name,mime_type:file.type,data})});
    const result = await response.json(); if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    const a = result.attachment; addContextReference({type:"attachment", id:a.id, label:a.filename, context_chars: typeof a.extracted_text === "string" ? a.extracted_text.length : 0});
  } catch (error) { contextFeedback.textContent = error.message || "Upload failed."; }
  finally { contextUploadButton.disabled = false; }
});
transcript.addEventListener("scroll", updateJump, { passive: true });
jumpLatest.addEventListener("click", () => { transcript.scrollTop = transcript.scrollHeight; updateJump(); });
approvalBanner.addEventListener("click", () => {
  const pending = currentDetail?.pending_tool_calls?.find(call => call.ui?.remote);
  const card = [...transcript.querySelectorAll("[data-call-id]")].find(node => node.dataset.callId === pending?.id);
  if (card) { card.open = true; card.scrollIntoView({ block: "center" }); card.querySelector("summary").focus({ preventScroll: true }); }
});
answersOnlyButton.addEventListener("click", () => {
  if (!currentDetail) return;
  const state = sessionState(currentDetail.session_id);
  state.answersOnly = !answersOnlyEnabled(currentDetail);
  storageWrite("sessionStorage", answersOnlyKey(currentDetail), state.answersOnly ? "1" : "");
  renderDetail(currentDetail);
  answersOnlyButton.focus({ preventScroll: true });
});
document.querySelector("#connection-retry").addEventListener("click", () => {
  if (currentView === "servers") void refreshServers();
  else if (currentView === "memories") void refreshMemories();
  else { closeConversationStream(); refreshDetail(); void refreshList(); }
});
document.querySelector("#rename-button").addEventListener("click", openEdit);
document.querySelector("#pin-button").addEventListener("click", () => {
  conversationMenu.open = false;
  if (currentDetail) void updateMetadata(currentDetail.session_id, { pinned: !currentDetail.pinned });
});
document.querySelector("#edit-cancel").addEventListener("click", () => editDialog.close());
document.querySelector("#edit-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (!editAction) return;
  const action = editAction;
  const button = document.querySelector("#edit-submit");
  if (button.disabled) return;
  button.disabled = true;
  const ok = await updateMetadata(action.id, { title: document.querySelector("#edit-input").value });
  button.disabled = false;
  if (ok) editDialog.close();
});
editDialog.addEventListener("close", () => { editAction = null; conversationMenu.querySelector("summary").focus(); });
document.querySelector("#confirm-cancel").addEventListener("click", () => confirmDialog.close());
confirmForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (!confirmation || confirmSubmit.disabled) return;
  confirmSubmit.disabled = true;
  confirmFeedback.textContent = "";
  try {
    const done = await confirmation.run();
    if (done !== false) confirmDialog.close();
  } catch (error) {
    confirmFeedback.textContent = error.message || "Could not complete action.";
  } finally {
    confirmSubmit.disabled = false;
  }
});
confirmDialog.addEventListener("close", () => {
  const returnFocus = confirmation?.returnFocus;
  confirmation = null;
  if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
});
newConversationDialog.addEventListener("close", () => {
  if (!newConversationBusy) {
    if (matchMedia("(max-width: 899px)").matches) menuButton.focus(); else newConversationButton.focus();
  }
});
addServerDialog.addEventListener("close", () => {
  if (currentView === "servers") {
    addServerButton.focus();
  }
});
aiConfigDialog.addEventListener("close", () => { closeModelPicker(false); aiConfigButton.focus(); });
document.addEventListener("click", event => {
  if (!conversationMenu.contains(event.target)) conversationMenu.open = false;
  if (!runnerPicker.contains(event.target)) runnerPicker.open = false;
  if (!themePicker.contains(event.target)) themePicker.open = false;
  if (activeModelMenu && !activeModelMenu.contains(event.target) && !activeModelTrigger?.contains(event.target)) {
    closeModelPicker(false);
  }
});
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && activeModelMenu) {
    event.preventDefault();
    closeModelPicker(true);
    return;
  }
  if (event.key === "Escape") {
    const popupOpen = conversationMenu.open || runnerPicker.open || themePicker.open;
    conversationMenu.open = false;
    runnerPicker.open = false;
    themePicker.open = false;
    if (document.body.classList.contains("sidebar-open") && !popupOpen) setSidebar(false);
  }
  if (event.key === "Tab" && document.body.classList.contains("sidebar-open")) {
    const nodes = [...document.querySelector("#sidebar").querySelectorAll("button, input, select, summary")]
      .filter(node => !node.disabled && node.getClientRects().length
        && (node.tagName === "SUMMARY" || !node.closest("details:not([open])")));
    const first = nodes[0], last = nodes.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
});
window.addEventListener("resize", positionModelPicker);
aiConfigDialog.addEventListener("scroll", positionModelPicker, { passive: true });
matchMedia("(max-width: 899px)").addEventListener("change", () => setSidebar(false, false));
setInterval(() => {
  if (!currentDetail?.live || currentView !== "conversations") return;
  const label = activityLabel(currentDetail.live.activity, currentDetail.live.started_at);
  responseStatus.textContent = label;
  const liveState = transcript.querySelector(".live-activity-state");
  if (liveState) liveState.textContent = label;
  const summary = transcript.querySelector(".response-activity[open] > .response-activity-summary span");
  if (summary) summary.textContent = label;
}, 1000);
setSidebar(false, false);
renderView();
void refreshDashboard();
window.addEventListener("pagehide", closeConversationStream);
window.addEventListener("pageshow", refreshDetail);
