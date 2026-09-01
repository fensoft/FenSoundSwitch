"use strict";

const pages = {
  routes: ["Routes", "Connect an input to the volume output it controls.", "New route"],
  actions: ["Actions", "Configure integrations and keyboard shortcuts.", ""],
  appearance: ["Appearance", "Choose how volume changes appear on screen.", ""],
  settings: ["Settings", "Manage startup and configuration backups.", ""],
  diagnostics: ["Diagnostics", "Review bounded application health information.", ""]
};

const state = { page: "routes", snapshot: null, revision: -1, polling: false, failures: 0, editor: null };
const content = document.querySelector("#content");
const notices = document.querySelector("#notice-region");
const primaryAction = document.querySelector("#primary-action");
const editorDialog = document.querySelector("#editor-dialog");
const editorForm = document.querySelector("#editor-form");
const confirmDialog = document.querySelector("#confirm-dialog");

function element(tag, options = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(options)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (value !== undefined && value !== null) node.setAttribute(key, String(value));
  }
  for (const child of children) node.append(child);
  return node;
}

function safeArray(value) { return Array.isArray(value) ? value : []; }
function safeText(value, fallback = "") { return typeof value === "string" ? value : fallback; }
function hotkeyLabel(value) {
  if (!value || typeof value !== "object") return "Not set";
  const parts = [];
  if (value.modifiers & 2) parts.push("Ctrl");
  if (value.modifiers & 1) parts.push("Alt");
  if (value.modifiers & 4) parts.push("Shift");
  if (value.modifiers & 8) parts.push("Win");
  const key = Number(value.virtual_key);
  const names = { 8: "Backspace", 9: "Tab", 13: "Enter", 19: "Pause", 20: "Caps Lock", 27: "Escape", 32: "Space", 33: "Page Up", 34: "Page Down", 35: "End", 36: "Home", 37: "Left", 38: "Up", 39: "Right", 40: "Down", 44: "Print Screen", 45: "Insert", 46: "Delete", 91: "Left Win", 92: "Right Win", 93: "Menu", 144: "Num Lock", 145: "Scroll Lock" };
  parts.push(names[key] || (key >= 65 && key <= 90 ? String.fromCharCode(key) : key >= 48 && key <= 57 ? String.fromCharCode(key) : key >= 96 && key <= 105 ? `Numpad ${key - 96}` : key >= 112 && key <= 135 ? `F${key - 111}` : `VK 0x${key.toString(16).toUpperCase().padStart(2, "0")}`));
  return parts.join("+");
}
function virtualKeyFromEvent(event) {
  const code = safeText(event.code);
  let match = /^F(\d{1,2})$/.exec(code);
  if (match) { const number = Number(match[1]); if (number >= 1 && number <= 24) return 111 + number; }
  match = /^Key([A-Z])$/.exec(code);
  if (match) return match[1].charCodeAt(0);
  match = /^Digit([0-9])$/.exec(code);
  if (match) return 48 + Number(match[1]);
  match = /^Numpad([0-9])$/.exec(code);
  if (match) return 96 + Number(match[1]);
  const named = { Enter: 13, Escape: 27, Space: 32, PageUp: 33, PageDown: 34, End: 35, Home: 36, ArrowLeft: 37, ArrowUp: 38, ArrowRight: 39, ArrowDown: 40, Insert: 45 };
  return named[code] || Number(event.keyCode || event.which || 0);
}

async function nativeRequest(method, params = {}) {
  if (!window.pywebview?.api?.request) throw new Error("Native bridge is not ready.");
  const response = await window.pywebview.api.request(method, params);
  if (!response || response.ok !== true) throw new Error(response?.error?.message || "The request failed.");
  return response.result;
}

function setConnection(mode, label) {
  const target = document.querySelector("#connection-status");
  target.className = `connection is-${mode}`;
  target.lastElementChild.textContent = label;
  target.hidden = mode === "online";
}

function showNotice(message, error = false, retry = false) {
  notices.replaceChildren();
  if (!message) return;
  const box = element("div", { class: `notice${error ? " is-error" : ""}`, role: error ? "alert" : "status" });
  box.append(element("p", { text: message }));
  if (retry) box.append(element("button", { class: "secondary", type: "button", text: "Reconnect", onclick: pollSnapshot }));
  notices.append(box);
}

function badge(text, tone = "") { return element("span", { class: `badge${tone ? ` is-${tone}` : ""}`, text }); }

async function pollSnapshot() {
  if (state.polling) return;
  state.polling = true;
  try {
    const snapshot = await nativeRequest("snapshot.get", { revision: state.revision });
    if (snapshot && typeof snapshot === "object" && !Array.isArray(snapshot)) {
      if (Number.isInteger(snapshot.revision) && snapshot.revision >= state.revision) state.revision = snapshot.revision;
      if (snapshot.changed !== false) state.snapshot = snapshot;
      state.failures = 0;
      setConnection("online", "Connected");
      showNotice("");
      render();
    }
  } catch (error) {
    state.failures += 1;
    setConnection("offline", "Disconnected");
    showNotice(safeText(error.message, "Connection lost."), true, true);
    if (!state.snapshot) renderError("The command center could not connect to FenSoundSwitch.");
  } finally {
    state.polling = false;
    window.setTimeout(pollSnapshot, state.failures ? 3000 : 900);
  }
}

function switchPage(page) {
  if (!pages[page]) return;
  state.page = page;
  document.querySelector("#workspace").dataset.page = page;
  for (const button of document.querySelectorAll("[data-page]")) {
    const selected = button.dataset.page === page;
    button.classList.toggle("is-active", selected);
    if (selected) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
  }
  const [title, description, action] = pages[page];
  document.querySelector("#page-title").textContent = title;
  document.querySelector("#page-description").textContent = description;
  primaryAction.textContent = action;
  primaryAction.hidden = !action;
  render();
  document.querySelector("#workspace").focus({ preventScroll: true });
}

function render() {
  content.setAttribute("aria-busy", "false");
  if (!state.snapshot) return;
  const renderer = { routes: renderRoutes, actions: renderActions, appearance: renderAppearance, settings: renderSettings, diagnostics: renderDiagnostics }[state.page];
  renderer();
}

function renderError(message) {
  content.setAttribute("aria-busy", "false");
  content.replaceChildren(element("div", { class: "empty-state" }, [element("span", { class: "empty-icon", "aria-hidden": "true", text: "!" }), element("h2", { text: "Unable to load" }), element("p", { class: "muted", text: message })]));
}

function emptyState(icon, title, message, action, handler) {
  const children = [element("span", { class: "empty-icon", "aria-hidden": "true", text: icon }), element("h2", { text: title }), element("p", { class: "muted", text: message })];
  if (action) children.push(element("button", { class: "primary", type: "button", text: action, onclick: handler }));
  return element("div", { class: "empty-state" }, children);
}

function renderRoutes() {
  const routes = safeArray(state.snapshot.routes);
  if (!routes.length) { content.replaceChildren(emptyState("⇄", "No routes yet", "Create a route to connect an input with an output.", "Create route", () => openEntityEditor("route", null))); return; }
  const grid = element("div", { class: "card-grid" });
  for (const route of routes) {
    const id = safeText(route.id);
    const edit = element("button", { class: "text-button", type: "button", text: "Edit", "aria-label": `Edit ${safeText(route.name, "route")}`, onclick: () => openEntityEditor("route", route) });
    const remove = element("button", { class: "text-button", type: "button", text: "Remove", "aria-label": `Remove ${safeText(route.name, "route")}`, onclick: () => confirmAction("Remove route?", `Remove ${safeText(route.name, "this route")}? Volume keys assigned to it will stop working.`, "route.delete", { id }) });
    const flow = element("div", { class: "route-flow" }, [
      endpoint("Input", route.input), element("span", { class: "route-arrow", "aria-hidden": "true", text: "↓" }), endpoint("Output", route.output)
    ]);
    const card = element("article", { class: "card" }, [
      element("div", { class: "card-body" }, [element("div", { class: "card-header" }, [element("div", {}, [element("h3", { text: safeText(route.name, "Unnamed route") }), element("p", { class: "muted", text: safeText(route.description, "Audio volume route") })]), element("div", { class: "card-actions" }, [edit, remove])]), flow]),
      element("footer", { class: "card-footer" }, [element("span", { class: "muted", text: safeText(route.summary, "Ready") }), badge(route.enabled === false ? "Disabled" : "Active", route.enabled === false ? "muted" : "")])
    ]);
    grid.append(card);
  }
  content.replaceChildren(grid);
}

function endpoint(label, data) {
  const value = data && typeof data === "object" ? data : {};
  return element("div", { class: "endpoint" }, [element("small", { text: label }), element("strong", { text: safeText(value.name, "Not selected") }), element("span", { class: "muted", text: safeText(value.summary, "") })]);
}

function renderActions() {
  const actions = safeArray(state.snapshot.actions);
  if (!actions.length) { content.replaceChildren(emptyState("⌁", "No actions available", "Action plugins will appear here when enabled.", "", null)); return; }
  const stack = element("div", { class: "stack" });
  for (const action of actions) {
    const shortcut = safeText(action.shortcut).trim();
    const buttons = [element("button", { class: "secondary", type: "button", text: "Configure", onclick: () => openEntityEditor("action", action) })];
    if (shortcut) buttons.unshift(badge(shortcut));
    for (const command of safeArray(action.ui_actions).filter(item => item.kind === "action")) {
      const params = { id: action.id, action_id: command.id, values: action.values || {} };
      buttons.push(element("button", { class: "secondary", type: "button", text: safeText(command.label, "Run"), onclick: () => command.confirm ? confirmAction(safeText(command.label, "Confirm"), command.confirm, "plugin.action", params) : execute("plugin.action", params) }));
    }
    stack.append(element("article", { class: "card setting-row" }, [
      element("div", {}, [element("h3", { text: safeText(action.name, "Action") }), element("p", { class: "muted", text: safeText(action.description, "Trusted plugin action") })]),
       element("div", { class: "button-row" }, buttons)
    ]));
  }
  content.replaceChildren(stack);
}

function renderAppearance() {
  const appearance = state.snapshot.appearance && typeof state.snapshot.appearance === "object" ? state.snapshot.appearance : {};
  const renderers = safeArray(appearance.renderers);
  const card = element("section", { class: "card" });
  const rows = renderers.map(renderer => element("div", { class: "setting-row" }, [
    element("div", {}, [element("h3", { text: safeText(renderer.name, "Overlay") }), element("p", { class: "muted", text: safeText(renderer.description, "Volume change presentation") })]),
    element("div", { class: "button-row" }, [
      renderer.form ? element("button", { class: "secondary", type: "button", text: "Configure", onclick: () => openEntityEditor("action", renderer) }) : null,
      element("button", { class: renderer.selected ? "primary" : "secondary", type: "button", text: renderer.selected ? "Selected" : "Use this", disabled: renderer.selected ? "" : null, onclick: () => execute("appearance.save", { renderer_id: renderer.id }) })
    ].filter(Boolean))
  ]));
  if (rows.length) card.append(...rows); else card.append(element("div", { class: "setting-row" }, [element("p", { class: "muted", text: "No appearance renderers are available." })]));
  content.replaceChildren(card);
}

function renderSettings() {
  const settings = state.snapshot.settings && typeof state.snapshot.settings === "object" ? state.snapshot.settings : {};
  const startup = element("section", { class: "card" }, [element("div", { class: "setting-row" }, [element("div", {}, [element("h3", { text: "Start with Windows" }), element("p", { class: "muted", text: "Launch quietly in the notification area when you sign in." })]), element("button", { class: settings.start_with_windows ? "primary" : "secondary", type: "button", role: "switch", "aria-checked": String(Boolean(settings.start_with_windows)), text: settings.start_with_windows ? "On" : "Off", onclick: () => execute("settings.save", { start_with_windows: !settings.start_with_windows }) })])]);
  const recent = safeArray(settings.recent_configurations);
  const recentMenu = element("details", { class: `split-menu${recent.length ? "" : " is-empty"}` }, [element("summary", { class: "secondary", title: recent.length ? "Recent configurations" : "No recent configurations", "aria-label": recent.length ? "Recent configurations" : "No recent configurations", "aria-disabled": recent.length ? "false" : "true", text: "▼" }), element("div", { class: "split-menu-items" }, recent.map(item => element("button", { type: "button", text: safeText(item.name, "Configuration"), onclick: () => confirmAction("Import configuration?", `Import ${safeText(item.name, "this configuration")}? The application will restart.`, "config.import", { path: item.path }) })))]);
  const importSplit = element("div", { class: "split-control" }, [element("button", { class: "secondary", type: "button", text: "Import", onclick: importConfiguration }), recentMenu]);
  const configuration = element("section", { class: "card" }, [element("div", { class: "setting-row" }, [element("div", {}, [element("h3", { text: "Configuration" }), element("p", { class: "muted", text: "Export a backup, import one, or restore bundled defaults." })]), element("div", { class: "button-row" }, [element("button", { class: "primary", type: "button", text: "Export", onclick: exportConfiguration }), importSplit, element("button", { class: "secondary", type: "button", text: "Restore default", onclick: () => confirmAction("Restore default configuration?", "Saved routes and non-secret plugin settings will be replaced, then the application may restart.", "config.restore-default", {}) })])])]);
  content.replaceChildren(element("div", { class: "stack" }, [startup, configuration]));
}

function renderDiagnostics() {
  const previous = content.querySelector(".diagnostic-output");
  const wasAtBottom = !previous || previous.scrollHeight - previous.scrollTop - previous.clientHeight <= 12;
  const previousScrollTop = previous?.scrollTop || 0;
  const diagnostics = state.snapshot.diagnostics && typeof state.snapshot.diagnostics === "object" ? state.snapshot.diagnostics : {};
  const text = safeText(diagnostics.text, "No diagnostic entries.");
  if (previous) {
    const summary = content.querySelector(".diagnostic-summary");
    const statusBadge = content.querySelector(".diagnostic-status");
    if (summary) summary.textContent = safeText(diagnostics.summary, "Diagnostics are available from the parent application.");
    if (statusBadge) { statusBadge.textContent = safeText(diagnostics.status, "Unknown"); statusBadge.className = `badge diagnostic-status${diagnostics.status === "Error" ? " is-error" : ""}`; }
    if (previous.textContent !== text) previous.textContent = text;
    previous.scrollTop = wasAtBottom ? previous.scrollHeight : previousScrollTop;
    return;
  }
  const status = element("section", { class: "card setting-row" }, [element("div", {}, [element("h3", { text: "Application health" }), element("p", { class: "muted diagnostic-summary", text: safeText(diagnostics.summary, "Diagnostics are available from the parent application.") })]), element("span", { class: `badge diagnostic-status${diagnostics.status === "Error" ? " is-error" : ""}`, text: safeText(diagnostics.status, "Unknown") })]);
  const output = element("section", { class: "card" }, [element("pre", { class: "diagnostic-output", tabindex: "0", text })]);
  content.replaceChildren(element("div", { class: "diagnostics-stack" }, [status, output]));
  const current = content.querySelector(".diagnostic-output");
  if (current) window.requestAnimationFrame(() => { current.scrollTop = wasAtBottom ? current.scrollHeight : Math.min(previousScrollTop, Math.max(0, current.scrollHeight - current.clientHeight)); });
}

function openEntityEditor(kind, entity) {
  const schemas = state.snapshot?.forms && typeof state.snapshot.forms === "object" ? state.snapshot.forms : {};
  const schema = kind === "route" ? (entity?.form || schemas.route) : entity?.form;
  const fields = safeArray(schema?.fields);
  state.editor = { kind, id: entity?.id || null, method: safeText(schema?.method, kind === "route" ? "route.save" : "action.save") };
  document.querySelector("#editor-kicker").textContent = kind === "route" ? "ROUTE" : "ACTION";
  document.querySelector("#editor-title").textContent = entity ? `Edit ${safeText(entity.name, kind)}` : "Create route";
  document.querySelector("#editor-error").hidden = true;
  const container = document.querySelector("#editor-fields");
  container.replaceChildren();
  const values = entity?.values && typeof entity.values === "object" ? entity.values : {};
  for (const field of fields) container.append(renderField(field, values[field.key]));
  if (!fields.length) container.append(element("p", { class: "muted", text: "This item has no configurable fields." }));
  editorDialog.showModal();
  window.setTimeout(() => container.querySelector("input, select, textarea")?.focus(), 0);
}

function renderField(field, current) {
  const key = safeText(field.key);
  const type = safeText(field.type, "text");
  const wrapper = element("div", { class: type === "boolean" ? "check-field" : "field" });
  let input;
  if (type === "hotkey") {
    input = element("input", { id: `field-${key}`, name: key, type: "text", readonly: "", value: hotkeyLabel(current), "data-hotkey": JSON.stringify(current ?? null), placeholder: "Press a key combination" });
    input.addEventListener("keydown", event => {
      event.preventDefault();
      event.stopPropagation();
      if (event.key === "Backspace" || event.key === "Delete") { input.dataset.hotkey = "null"; input.value = "Not set"; return; }
      if (event.code === "AltRight") { input.dataset.rightAlt = "true"; return; }
      const virtualKey = virtualKeyFromEvent(event);
      if (!virtualKey || [16, 17, 18, 91, 92].includes(virtualKey)) return;
      const altGraph = input.dataset.rightAlt === "true" || event.getModifierState?.("AltGraph") === true;
      const value = { modifiers: (event.altKey || altGraph ? 1 : 0) | (event.ctrlKey || altGraph ? 2 : 0) | (event.shiftKey ? 4 : 0) | (event.metaKey ? 8 : 0), virtual_key: virtualKey };
      input.dataset.hotkey = JSON.stringify(value); input.value = hotkeyLabel(value);
    }, true);
    input.addEventListener("keyup", event => { event.preventDefault(); event.stopPropagation(); if (event.code === "AltRight") input.dataset.rightAlt = "false"; }, true);
  }
  else if (type === "select") {
    input = element("select", { id: `field-${key}`, name: key, required: field.required ? "" : null });
    for (const option of safeArray(field.options)) {
      const encoded = JSON.stringify(option.value);
      input.append(element("option", { value: encoded, text: safeText(option.label, String(option.value ?? "")), selected: encoded === JSON.stringify(current) ? "" : null, "data-json": "true" }));
    }
  } else if (type === "textarea") {
    input = element("textarea", { id: `field-${key}`, name: key, required: field.required ? "" : null, maxlength: Number.isInteger(field.max_length) ? field.max_length : 4096 }); input.value = safeText(current);
  } else {
    input = element("input", { id: `field-${key}`, name: key, type: type === "boolean" ? "checkbox" : (["number", "password"].includes(type) ? type : "text"), required: field.required ? "" : null, min: field.min, max: field.max, maxlength: Number.isInteger(field.max_length) ? field.max_length : 512, autocomplete: type === "password" ? "off" : "on" });
    if (type === "boolean") input.checked = Boolean(current); else input.value = current ?? safeText(field.default);
  }
  const label = element("label", { for: `field-${key}`, text: safeText(field.label, key) });
  if (type === "boolean") wrapper.append(input, label); else wrapper.append(label, input);
  if (field.description) wrapper.append(element("small", { text: safeText(field.description) }));
  return wrapper;
}

async function saveEditor(event) {
  event.preventDefault();
  if (event.submitter?.value === "cancel") {
    editorDialog.close("cancel");
    state.editor = null;
    return;
  }
  if (!state.editor || !editorForm.reportValidity()) return;
  const save = document.querySelector("#editor-save");
  save.disabled = true; save.textContent = "Saving…";
  const values = {};
  for (const input of editorForm.querySelectorAll("[name]")) values[input.name] = input.dataset.hotkey !== undefined ? JSON.parse(input.dataset.hotkey) : input.type === "checkbox" ? input.checked : input.type === "number" && input.value !== "" ? Number(input.value) : input.selectedOptions?.[0]?.dataset.json === "true" ? JSON.parse(input.value) : input.value;
  try {
    await nativeRequest(state.editor.method, { id: state.editor.id, values });
    editorDialog.close(); state.revision = -1; await pollSnapshot();
  } catch (error) {
    const target = document.querySelector("#editor-error"); target.textContent = safeText(error.message, "Changes could not be saved."); target.hidden = false; target.focus();
  } finally { save.disabled = false; save.textContent = "Save changes"; }
}

function confirmAction(title, message, method, params) {
  document.querySelector("#confirm-title").textContent = title;
  document.querySelector("#confirm-message").textContent = message;
  confirmDialog.dataset.method = method;
  confirmDialog.dataset.params = JSON.stringify(params);
  confirmDialog.showModal();
}

async function execute(method, params) {
  showNotice("Applying changes…");
  try { await nativeRequest(method, params); state.revision = -1; await pollSnapshot(); }
  catch (error) { showNotice(safeText(error.message, "The operation failed."), true); }
}

async function exportConfiguration() {
  try {
    const picker = window.pywebview?.api?.pick_save_file;
    if (typeof picker !== "function") throw new Error("The native save dialog is unavailable.");
    const choice = await window.pywebview.api.pick_save_file({ title: "Export FenSoundSwitch configuration", filename: "FenSoundSwitch.fsc", file_types: ["FenSoundSwitch configuration (*.fsc)"] });
    if (choice?.ok && choice.result) await execute("config.export", { path: choice.result });
    else if (choice?.ok === false) showNotice(choice.error?.message || "The file dialog failed.", true);
  } catch (error) { showNotice(safeText(error?.message, "The native save dialog could not be opened."), true); }
}

async function importConfiguration() {
  const choice = await window.pywebview.api.pick_open_file({ title: "Import FenSoundSwitch configuration", file_types: ["FenSoundSwitch configuration (*.fsc)"] });
  if (choice?.ok && choice.result) confirmAction("Import configuration?", "Saved routes and non-secret plugin settings will be replaced, then the application may restart.", "config.import", { path: choice.result });
  else if (choice?.ok === false) showNotice(choice.error?.message || "The file dialog failed.", true);
}

document.querySelector("#navigation").addEventListener("click", event => { const button = event.target.closest("[data-page]"); if (button) switchPage(button.dataset.page); });
document.querySelector(".diagnostics-link").addEventListener("click", () => switchPage("diagnostics"));
primaryAction.addEventListener("click", () => { if (state.page === "routes") openEntityEditor("route", null); });
editorForm.addEventListener("submit", saveEditor);
for (const button of document.querySelectorAll("#editor-close, #editor-cancel")) button.addEventListener("click", () => { editorDialog.close("cancel"); state.editor = null; });
confirmDialog.addEventListener("close", () => { if (confirmDialog.returnValue === "confirm") execute(confirmDialog.dataset.method, JSON.parse(confirmDialog.dataset.params || "{}")); });
document.addEventListener("keydown", event => { if (event.key === "Escape" && editorDialog.open) editorDialog.close(); });
window.addEventListener("pywebviewready", pollSnapshot, { once: true });
