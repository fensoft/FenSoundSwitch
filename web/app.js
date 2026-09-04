"use strict";

const pages = {
  routes: ["Routes", "Connect an input to the volume output it controls.", "New route"],
  actions: ["Automations", "Run ordered steps from app start, keyboard, tray, or MQTT/HA.", "New automation"],
  integrations: ["Integrations", "Configure connections used by routes and automations.", ""],
  appearance: ["Appearance", "Choose how volume changes appear on screen.", ""],
  settings: ["Settings", "Manage startup and configuration backups.", ""],
  diagnostics: ["Diagnostics", "Review bounded application health information.", ""],
  about: ["About", "Application identity and build information.", ""]
};

const state = { page: "routes", snapshot: null, revision: -1, polling: false, failures: 0, editor: null };
const content = document.querySelector("#content");
const notices = document.querySelector("#notice-region");
const primaryAction = document.querySelector("#primary-action");
const editorDialog = document.querySelector("#editor-dialog");
const editorForm = document.querySelector("#editor-form");
const confirmDialog = document.querySelector("#confirm-dialog");
const slotDialog = document.querySelector("#slot-dialog");
const slotForm = document.querySelector("#slot-form");
const mqttDialog = document.querySelector("#mqtt-dialog");
const mqttProfileForm = document.querySelector("#mqtt-profile-form");

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
  const renderer = { routes: renderRoutes, actions: renderActions, integrations: renderIntegrations, appearance: renderAppearance, settings: renderSettings, diagnostics: renderDiagnostics, about: renderAbout }[state.page];
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
  const signals = safeArray(state.snapshot.signals);
  if (!signals.length) { content.replaceChildren(emptyState("→", "No automations yet", "Choose one or more triggers, then add action and wait steps.", "Create automation", () => openEntityEditor("signal", null))); return; }
  const stack = element("div", { class: "stack" });
  for (const signal of signals) {
    const id = safeText(signal.id);
    const steps = safeArray(signal.steps);
    const summary = steps.length ? element("ol", { class: "automation-summary" }, steps.map(step => element("li", { text: safeText(step) }))) : element("p", { class: "muted", text: safeText(signal.description, "Ordered steps") });
    stack.append(element("article", { class: "card setting-row" }, [
      element("div", {}, [element("h3", { text: safeText(signal.name, "Automation") }), summary, element("p", { class: "signal-trigger", text: safeText(signal.trigger, "No trigger") })]),
      element("div", { class: "button-row" }, [element("button", { class: "secondary", type: "button", text: "Run", onclick: () => execute("signal.run", { id }) }), element("button", { class: "secondary", type: "button", text: "Edit", onclick: () => openEntityEditor("signal", signal) }), element("button", { class: "text-button", type: "button", text: "Remove", onclick: () => confirmAction("Remove automation?", `Remove ${safeText(signal.name, "this automation")}? Its triggers will stop working.`, "signal.delete", { id }) })])
    ]));
  }
  content.replaceChildren(stack);
}

function renderIntegrations() {
  const stack = element("div", { class: "stack" });
  const profiles = safeArray(state.snapshot.mqtt_profiles);
  stack.append(element("article", { class: "card setting-row" }, [
    element("div", {}, [element("h3", { text: "MQTT / Home Assistant" }), element("p", { class: "muted", text: `${profiles.length} saved configuration${profiles.length === 1 ? "" : "s"}. Reuse broker connections for routes and automations.` })]),
    element("button", { class: "secondary", type: "button", text: "Configure", onclick: openMqttIntegration })
  ]));
  const integrations = safeArray(state.snapshot.integrations);
  for (const action of integrations) {
    const buttons = [];
    if (safeArray(action.form?.fields).length || action.form?.ui_action) buttons.push(element("button", { class: "secondary", type: "button", text: "Configure", onclick: () => openEntityEditor("action", action) }));
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

function openMqttIntegration() {
  state.mqttProfileId = null;
  showMqttProfileList();
  mqttDialog.showModal();
  window.setTimeout(() => document.querySelector("#mqtt-add")?.focus(), 0);
}

function showMqttProfileList() {
  mqttProfileForm.hidden = true;
  document.querySelector("#mqtt-list-view").hidden = false;
  const list = document.querySelector("#mqtt-profile-list");
  list.replaceChildren();
  const profiles = safeArray(state.snapshot?.mqtt_profiles);
  if (!profiles.length) {
    list.append(element("p", { class: "muted", text: "No configurations yet." }));
    return;
  }
  for (const profile of profiles) {
    const id = safeText(profile.id);
    list.append(element("article", { class: "profile-row" }, [
      element("div", {}, [element("strong", { text: safeText(profile.name, "MQTT / HA") }), element("span", { class: "muted", text: safeText(profile.description) })]),
      element("div", { class: "button-row" }, [element("button", { class: "secondary", type: "button", text: "Edit", onclick: () => editMqttProfile(profile) }), element("button", { class: "text-button", type: "button", text: "Remove", onclick: () => removeMqttProfile(id) })])
    ]));
  }
}

function editMqttProfile(profile) {
  const schemas = state.snapshot?.forms && typeof state.snapshot.forms === "object" ? state.snapshot.forms : {};
  const schema = profile?.form || schemas.mqtt_profile;
  const fields = safeArray(schema?.fields);
  const values = profile?.values && typeof profile.values === "object" ? profile.values : {};
  state.mqttProfileId = profile?.id || null;
  document.querySelector("#mqtt-list-view").hidden = true;
  mqttProfileForm.hidden = false;
  document.querySelector("#mqtt-profile-title").textContent = profile ? `Edit ${safeText(profile.name, "configuration")}` : "Add configuration";
  const container = document.querySelector("#mqtt-profile-fields");
  container.replaceChildren();
  for (const field of fields) container.append(renderField(field, values[field.key]));
  syncConditionalFields(container);
  document.querySelector("#mqtt-error").hidden = true;
  window.setTimeout(() => container.querySelector("input, select, textarea")?.focus(), 0);
}

async function saveMqttProfile(event) {
  event.preventDefault();
  if (!mqttProfileForm.reportValidity()) return;
  const save = document.querySelector("#mqtt-profile-save");
  save.disabled = true; save.textContent = "Saving...";
  const values = {};
  for (const input of document.querySelector("#mqtt-profile-fields").querySelectorAll("[name]")) values[input.getAttribute("name")] = input.type === "checkbox" ? input.checked : input.type === "number" && input.value !== "" ? Number(input.value) : input.value;
  try {
    await nativeRequest("mqtt.profile.save", { id: state.mqttProfileId, values });
    state.revision = -1; await pollSnapshot(); showMqttProfileList();
  } catch (error) {
    const target = document.querySelector("#mqtt-error"); target.textContent = safeText(error.message, "The configuration could not be saved."); target.hidden = false; target.focus();
  } finally { save.disabled = false; save.textContent = "Save configuration"; }
}

async function removeMqttProfile(id) {
  try {
    await nativeRequest("mqtt.profile.delete", { id });
    state.revision = -1; await pollSnapshot(); showMqttProfileList();
  } catch (error) {
    document.querySelector("#mqtt-list-view").prepend(element("div", { class: "inline-error", role: "alert", text: safeText(error.message, "The configuration could not be removed.") }));
  }
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
  const configuration = element("section", { class: "card" }, [element("div", { class: "setting-row" }, [element("div", {}, [element("h3", { text: "Configuration" }), element("p", { class: "muted", text: "Export a backup, import one, or restore bundled defaults. Protect exports because MQTT credentials are included." })]), element("div", { class: "button-row" }, [element("button", { class: "primary", type: "button", text: "Export", onclick: exportConfiguration }), importSplit, element("button", { class: "secondary", type: "button", text: "Restore default", onclick: () => confirmAction("Restore default configuration?", "Saved routes and plugin settings will be replaced, then the application may restart.", "config.restore-default", {}) })])])]);
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

function renderAbout() {
  const application = state.snapshot.application && typeof state.snapshot.application === "object" ? state.snapshot.application : {};
  const name = safeText(application.name, "FenSoundSwitch");
  const version = safeText(application.version, "dev");
  content.replaceChildren(element("section", { class: "card about-card" }, [
    element("span", { class: "about-mark", "aria-hidden": "true", text: "F" }),
    element("div", { class: "about-copy" }, [
      element("p", { class: "eyebrow", text: "WINDOWS AUDIO CONTROL" }),
      element("h2", { text: name }),
      element("p", { class: "about-version", text: `Version ${version}` }),
      element("p", { class: "muted", text: "Monitor DDC/CI, audio routing, automations, and overlays in one current-user application." })
    ])
  ]));
}

function openEntityEditor(kind, entity) {
  const schemas = state.snapshot?.forms && typeof state.snapshot.forms === "object" ? state.snapshot.forms : {};
  const schema = kind === "route" ? (entity?.form || schemas.route) : kind === "signal" ? (entity?.form || schemas.signal) : kind === "mqtt-profile" ? (entity?.form || schemas.mqtt_profile) : entity?.form;
  const fields = safeArray(schema?.fields);
  state.editor = { kind, id: entity?.id || null, method: safeText(schema?.method, kind === "route" ? "route.save" : kind === "signal" ? "signal.save" : kind === "mqtt-profile" ? "mqtt.profile.save" : "action.save") };
  editorDialog.dataset.kind = kind;
  document.querySelector("#editor-kicker").textContent = kind === "signal" ? "AUTOMATION" : kind === "mqtt-profile" ? "MQTT / HA" : kind.toUpperCase();
  document.querySelector("#editor-title").textContent = entity ? `Edit ${safeText(entity.name, kind)}` : `Create ${kind === "signal" ? "automation" : kind === "mqtt-profile" ? "MQTT/HA configuration" : kind}`;
  document.querySelector("#editor-error").hidden = true;
  const container = document.querySelector("#editor-fields");
  container.replaceChildren();
  const values = entity?.values && typeof entity.values === "object" ? entity.values : {};
  for (const field of fields) container.append(renderField(field, values[field.key]));
  syncConditionalFields(container);
  for (const controller of container.querySelectorAll('input[type="checkbox"], select')) controller.addEventListener("change", () => syncConditionalFields(container));
  if (!fields.length) container.append(element("p", { class: "muted", text: "This item has no configurable fields." }));
  editorDialog.showModal();
  window.setTimeout(() => container.querySelector("input, select, textarea")?.focus(), 0);
}

function renderField(field, current) {
  const key = safeText(field.key);
  const type = safeText(field.type, "text");
  const wrapper = element("div", { class: type === "boolean" ? "check-field" : "field" });
  if (field.visible_when) wrapper.dataset.visibleWhen = safeText(field.visible_when);
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
  else if (type === "sequence") {
    input = renderSequenceField(key, field, current);
  }
  else if (type === "select") {
    input = element("select", { id: `field-${key}`, name: key, required: field.required ? "" : null });
    input._allOptions = safeArray(field.options);
    input._desiredValue = JSON.stringify(current);
    input._renderOptions = controllingValue => {
      const available = field.depends_on ? input._allOptions.filter(option => JSON.stringify(option.when) === controllingValue) : input._allOptions;
      const previous = input.value || input._desiredValue;
      input.replaceChildren();
      for (const option of available) {
        const encoded = JSON.stringify(option.value);
        input.append(element("option", { value: encoded, text: safeText(option.label, String(option.value ?? "")), selected: encoded === previous ? "" : null, "data-json": "true" }));
      }
    };
    if (field.depends_on) input.dataset.dependsOn = safeText(field.depends_on); else input._renderOptions("");
  } else if (type === "textarea") {
    input = element("textarea", { id: `field-${key}`, name: key, required: field.required ? "" : null, maxlength: Number.isInteger(field.max_length) ? field.max_length : 4096 }); input.value = safeText(current);
  } else {
    input = element("input", { id: `field-${key}`, name: key, type: type === "boolean" ? "checkbox" : (["number", "password"].includes(type) ? type : "text"), required: field.required ? "" : null, min: field.min, max: field.max, maxlength: Number.isInteger(field.max_length) ? field.max_length : 512, autocomplete: type === "password" ? "off" : "on" });
    if (type === "boolean") input.checked = Boolean(current ?? field.default); else input.value = current ?? safeText(field.default);
  }
  const label = element("label", { for: `field-${key}`, text: safeText(field.label, key) });
  if (type === "boolean") wrapper.append(input, label); else wrapper.append(label, input);
  if (field.description) wrapper.append(element("small", { text: safeText(field.description) }));
  return wrapper;
}

function syncConditionalFields(container) {
  for (const wrapper of container.querySelectorAll("[data-visible-when]")) {
    const controller = container.querySelector(`#field-${CSS.escape(wrapper.dataset.visibleWhen)}`);
    wrapper.hidden = !(controller instanceof HTMLInputElement && controller.type === "checkbox" && controller.checked);
  }
  for (const select of container.querySelectorAll("select[data-depends-on]")) {
    const controller = container.querySelector(`#field-${CSS.escape(select.dataset.dependsOn)}`);
    if (typeof select._renderOptions === "function") select._renderOptions(controller?.value || "");
  }
}

function renderSequenceField(key, field, current) {
  const root = element("div", { id: `field-${key}`, name: key, class: "sequence-editor" });
  const list = element("div", { class: "sequence-list" });
  const options = safeArray(field.options);
  function addSlot(value) {
    const slot = value && typeof value === "object" ? value : { kind: "action", target: options[0]?.value || "", parameters: {} };
    const kind = slot.kind === "wait" ? "wait" : "action";
    const row = element("div", { class: "sequence-row", "data-kind": kind });
    const index = element("strong", { class: "sequence-index" });
    let control;
    if (kind === "wait") {
      control = element("label", { class: "sequence-wait" }, [element("span", { text: "Wait" }), element("input", { type: "number", min: "0", max: "300000", step: "100", value: Number.isInteger(slot.milliseconds) ? slot.milliseconds : 1000, "aria-label": "Wait milliseconds" }), element("span", { text: "ms" })]);
    } else {
      control = element("select", { "aria-label": "Action step" });
      for (const option of options) control.append(element("option", { value: safeText(option.value), text: safeText(option.label, option.value), selected: option.value === slot.target ? "" : null, disabled: option.disabled ? "" : null, "data-configurable": option.configurable ? "true" : "false" }));
    }
    const configure = element("button", { class: "secondary sequence-config", type: "button", text: "Configure", onclick: () => openSlotEditor(row, control.value) });
    const summary = element("small", { class: "sequence-summary", text: safeText(slot.summary) });
    summary.hidden = !summary.textContent;
    function refreshConfigure() { configure.hidden = kind !== "action" || control.selectedOptions?.[0]?.dataset.configurable !== "true"; }
    if (kind === "action") control.addEventListener("change", () => { row._parameters = {}; summary.textContent = ""; summary.hidden = true; refreshConfigure(); });
    const moveUp = element("button", { class: "icon-button", type: "button", text: "↑", title: "Move up", "aria-label": "Move step up", onclick: () => { row.previousElementSibling?.before(row); refresh(); } });
    const moveDown = element("button", { class: "icon-button", type: "button", text: "↓", title: "Move down", "aria-label": "Move step down", onclick: () => { row.nextElementSibling?.after(row); refresh(); } });
    const remove = element("button", { class: "icon-button", type: "button", text: "×", title: "Remove step", "aria-label": "Remove step", onclick: () => { row.remove(); refresh(); } });
    row._parameters = slot.parameters && typeof slot.parameters === "object" ? slot.parameters : {};
    row._summary = summary;
    row.append(index, control, configure, moveUp, moveDown, remove, summary); list.append(row); refreshConfigure(); refresh();
  }
  function refresh() { [...list.children].forEach((row, index) => { row.querySelector(".sequence-index").textContent = String(index + 1); }); }
  root._sequenceValue = () => [...list.children].map(row => row.dataset.kind === "wait" ? { kind: "wait", milliseconds: Number(row.querySelector("input").value) } : { kind: "action", target: row.querySelector("select").value, parameters: row._parameters || {} });
  for (const slot of safeArray(current)) addSlot(slot);
  const addAction = element("button", { class: "secondary", type: "button", text: "Add action step", disabled: options.length ? null : "", onclick: () => addSlot(null) });
  const addWait = element("button", { class: "secondary", type: "button", text: "Add wait step", onclick: () => addSlot({ kind: "wait", milliseconds: 1000 }) });
  root.append(list, element("div", { class: "button-row sequence-add" }, [addAction, addWait]));
  return root;
}

async function openSlotEditor(row, target) {
  state.slotEditor = { row, target, actionId: "", refreshActionId: "" };
  document.querySelector("#slot-title").textContent = "Configure step";
  document.querySelector("#slot-description").hidden = true;
  document.querySelector("#slot-fields").replaceChildren(element("p", { class: "muted", text: "Discovering monitor inputs. Please wait..." }));
  document.querySelector("#slot-error").hidden = true;
  document.querySelector("#slot-refresh").hidden = true;
  document.querySelector("#slot-save").hidden = true;
  slotDialog.showModal();
  await loadSlotEditor();
}

async function loadSlotEditor() {
  const editor = state.slotEditor;
  if (!editor) return;
  try {
    const form = await nativeRequest("slot.ui", { target: editor.target, parameters: editor.row._parameters || {} });
    if (state.slotEditor !== editor) return;
    editor.actionId = safeText(form.action_id);
    editor.refreshActionId = safeText(safeArray(form.actions).find(action => action.kind === "action")?.id);
    document.querySelector("#slot-title").textContent = safeText(form.title, "Configure step");
    const loading = form.state === "loading";
    const ready = form.state === "ready";
    const description = document.querySelector("#slot-description");
    description.textContent = safeText(form.description);
    description.hidden = loading || !description.textContent;
    const container = document.querySelector("#slot-fields");
    container.replaceChildren();
    if (loading) {
      container.append(element("p", { class: "muted", text: "Discovering monitor inputs. Please wait..." }));
    } else if (ready) {
      const values = form.values && typeof form.values === "object" ? form.values : {};
      for (const field of safeArray(form.fields)) container.append(renderField(field, values[field.key]));
      syncConditionalFields(container);
      for (const controller of container.querySelectorAll('input[type="checkbox"], select')) controller.addEventListener("change", () => syncConditionalFields(container));
    }
    const refresh = document.querySelector("#slot-refresh");
    refresh.hidden = !editor.refreshActionId;
    refresh.disabled = loading;
    document.querySelector("#slot-save").hidden = !ready;
    document.querySelector("#slot-error").hidden = true;
    if (loading) window.setTimeout(() => { if (state.slotEditor === editor) loadSlotEditor(); }, 250);
    else window.setTimeout(() => container.querySelector("input, select, textarea, button")?.focus(), 0);
  } catch (error) {
    if (state.slotEditor !== editor) return;
    const target = document.querySelector("#slot-error");
    target.textContent = safeText(error.message, "Step configuration is unavailable.");
    target.hidden = false;
    target.focus();
  }
}

async function refreshSlotEditor() {
  const editor = state.slotEditor;
  if (!editor?.refreshActionId) return;
  const refresh = document.querySelector("#slot-refresh");
  refresh.disabled = true;
  document.querySelector("#slot-save").hidden = true;
  document.querySelector("#slot-fields").replaceChildren(element("p", { class: "muted", text: "Discovering monitor inputs. Please wait..." }));
  try {
    await nativeRequest("slot.action", { target: editor.target, action_id: editor.refreshActionId, values: {} });
    await loadSlotEditor();
  } catch (error) {
    const target = document.querySelector("#slot-error"); target.textContent = safeText(error.message, "Monitor discovery could not start."); target.hidden = false; target.focus();
    refresh.disabled = false;
  }
}

async function saveSlotEditor(event) {
  event.preventDefault();
  if (event.submitter?.value === "cancel") { slotDialog.close("cancel"); state.slotEditor = null; return; }
  if (!state.slotEditor || !slotForm.reportValidity()) return;
  const save = document.querySelector("#slot-save");
  save.disabled = true; save.textContent = "Saving…";
  const values = {};
  for (const input of document.querySelector("#slot-fields").querySelectorAll("[name]")) values[input.getAttribute("name")] = input.dataset.hotkey !== undefined ? JSON.parse(input.dataset.hotkey) : input.type === "checkbox" ? input.checked : input.type === "number" && input.value !== "" ? Number(input.value) : input.selectedOptions?.[0]?.dataset.json === "true" ? JSON.parse(input.value) : input.value;
  try {
    const result = await nativeRequest("slot.save", { target: state.slotEditor.target, action_id: state.slotEditor.actionId, values });
    state.slotEditor.row._parameters = result.parameters || {};
    state.slotEditor.row._summary.textContent = safeText(result.summary);
    state.slotEditor.row._summary.hidden = !state.slotEditor.row._summary.textContent;
    slotDialog.close(); state.slotEditor = null;
  } catch (error) {
    const target = document.querySelector("#slot-error"); target.textContent = safeText(error.message, "The step could not be saved."); target.hidden = false; target.focus();
  } finally { save.disabled = false; save.textContent = "Save step"; }
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
  for (const input of editorForm.querySelectorAll("[name]")) values[input.getAttribute("name")] = typeof input._sequenceValue === "function" ? input._sequenceValue() : input.dataset.hotkey !== undefined ? JSON.parse(input.dataset.hotkey) : input.type === "checkbox" ? input.checked : input.type === "number" && input.value !== "" ? Number(input.value) : input.selectedOptions?.[0]?.dataset.json === "true" ? JSON.parse(input.value) : input.value;
  if (state.editor.kind === "signal" && !values.on_start && !values.keyboard_enabled && !values.tray_enabled && !values.mqtt_enabled) {
    const target = document.querySelector("#editor-error");
    target.textContent = "Choose at least one trigger."; target.hidden = false; target.focus();
    save.disabled = false; save.textContent = "Save changes";
    return;
  }
  if (state.editor.kind === "signal" && values.keyboard_enabled && !values.hotkey) {
    const target = document.querySelector("#editor-error");
    target.textContent = "Press the key combination that should run this automation."; target.hidden = false; target.focus();
    save.disabled = false; save.textContent = "Save changes";
    return;
  }
  if (state.editor.kind === "signal" && values.tray_enabled && !safeText(values.tray_label).trim()) {
    const target = document.querySelector("#editor-error");
    target.textContent = "Enter the tray menu option text."; target.hidden = false; target.focus();
    save.disabled = false; save.textContent = "Save changes";
    return;
  }
  if (state.editor.kind === "signal" && values.mqtt_enabled && (!safeText(values.mqtt_profile_id).trim() || !safeText(values.mqtt_ha_name).trim() || !safeText(values.mqtt_ha_id).trim())) {
    const target = document.querySelector("#editor-error");
    target.textContent = "Choose an MQTT/HA configuration and enter the Home Assistant name and ID."; target.hidden = false; target.focus();
    save.disabled = false; save.textContent = "Save changes";
    return;
  }
  if (state.editor.kind === "signal" && !safeArray(values.slots).length) {
    const target = document.querySelector("#editor-error");
    target.textContent = "Add at least one action or wait step."; target.hidden = false; target.focus();
    save.disabled = false; save.textContent = "Save changes";
    return;
  }
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
    const directory = safeText(state.snapshot?.settings?.configuration_directory);
    const choice = await window.pywebview.api.pick_save_file({ title: "Export FenSoundSwitch configuration", directory, filename: "FenSoundSwitch.fsc", file_types: ["FenSoundSwitch configuration (*.fsc)"] });
    if (choice?.ok && choice.result) await execute("config.export", { path: choice.result });
    else if (choice?.ok === false) showNotice(choice.error?.message || "The file dialog failed.", true);
  } catch (error) { showNotice(safeText(error?.message, "The native save dialog could not be opened."), true); }
}

async function importConfiguration() {
  const directory = safeText(state.snapshot?.settings?.configuration_directory);
  const choice = await window.pywebview.api.pick_open_file({ title: "Import FenSoundSwitch configuration", directory, file_types: ["FenSoundSwitch configuration (*.fsc)"] });
  if (choice?.ok && choice.result) confirmAction("Import configuration?", "Saved routes and plugin settings will be replaced, then the application may restart.", "config.import", { path: choice.result });
  else if (choice?.ok === false) showNotice(choice.error?.message || "The file dialog failed.", true);
}

document.querySelector("#navigation").addEventListener("click", event => { const button = event.target.closest("[data-page]"); if (button) switchPage(button.dataset.page); });
document.querySelector(".diagnostics-link").addEventListener("click", () => switchPage("diagnostics"));
primaryAction.addEventListener("click", () => { if (state.page === "routes") openEntityEditor("route", null); else if (state.page === "actions") openEntityEditor("signal", null); });
editorForm.addEventListener("submit", saveEditor);
slotForm.addEventListener("submit", saveSlotEditor);
mqttProfileForm.addEventListener("submit", saveMqttProfile);
document.querySelector("#mqtt-add").addEventListener("click", () => editMqttProfile(null));
document.querySelector("#mqtt-back").addEventListener("click", showMqttProfileList);
document.querySelector("#mqtt-profile-cancel").addEventListener("click", showMqttProfileList);
document.querySelector("#mqtt-close").addEventListener("click", () => mqttDialog.close());
document.querySelector("#slot-refresh").addEventListener("click", refreshSlotEditor);
for (const button of document.querySelectorAll("#editor-close, #editor-cancel")) button.addEventListener("click", () => { editorDialog.close("cancel"); state.editor = null; });
for (const button of document.querySelectorAll("#slot-close, #slot-cancel")) button.addEventListener("click", () => { slotDialog.close("cancel"); state.slotEditor = null; });
confirmDialog.addEventListener("close", () => { if (confirmDialog.returnValue === "confirm") execute(confirmDialog.dataset.method, JSON.parse(confirmDialog.dataset.params || "{}")); });
editorDialog.addEventListener("close", () => { state.editor = null; delete editorDialog.dataset.kind; });
slotDialog.addEventListener("close", () => { state.slotEditor = null; });
mqttDialog.addEventListener("close", () => { state.mqttProfileId = null; showMqttProfileList(); });
window.addEventListener("pywebviewready", pollSnapshot, { once: true });
