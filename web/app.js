"use strict";

const { applyStatic: applyStaticTranslations, setLanguage, source: sourceText, t } = window.FenI18n;
const pages = {
  routes: ["nav.routes", "page.routes.description", "page.routes.action"],
  actions: ["nav.actions", "page.actions.description", "page.actions.action"],
  integrations: ["nav.integrations", "page.integrations.description", ""],
  appearance: ["nav.appearance", "page.appearance.description", ""],
  settings: ["nav.settings", "page.settings.description", ""],
  diagnostics: ["nav.diagnostics", "page.diagnostics.description", ""],
  about: ["nav.about", "page.about.description", ""]
};

const state = { page: "routes", snapshot: null, revision: -1, polling: false, failures: 0, editor: null, importMenuOpen: false, language: "" };
const content = document.querySelector("#content");
const notices = document.querySelector("#notice-region");
const primaryAction = document.querySelector("#primary-action");
const editorDialog = document.querySelector("#editor-dialog");
const editorForm = document.querySelector("#editor-form");
const confirmDialog = document.querySelector("#confirm-dialog");
const slotDialog = document.querySelector("#slot-dialog");
const slotForm = document.querySelector("#slot-form");
const choiceDialog = document.querySelector("#choice-dialog");
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
function optionText(field, option) {
  const label = safeText(option.label, String(option.value ?? ""));
  const key = safeText(field.key || field.id);
  const baseKey = key.split("__").at(-1);
  const endpointId = option.value && typeof option.value === "object" ? safeText(option.value.endpoint_id) : "";
  const isTranslatedEnum = baseKey === "route_type" || baseKey === "mode" || baseKey === "startup_input" || (baseKey === "endpoint" && endpointId.startsWith("fensoundswitch:"));
  return isTranslatedEnum ? sourceText(label) : label;
}
function applyLanguage() {
  const language = safeText(state.snapshot?.settings?.resolved_ui_language, "en");
  if (state.language === language) return;
  state.language = language;
  setLanguage(language);
  applyStaticTranslations();
  const [title, description, action] = pages[state.page];
  document.querySelector("#page-title").textContent = t(title);
  document.querySelector("#page-description").textContent = t(description);
  primaryAction.textContent = action ? t(action) : "";
}
function homeAssistantId(value) {
  return safeText(value)
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "_")
    .replace(/^[_-]+|[_-]+$/g, "")
    .slice(0, 64);
}
function hotkeyLabel(value) {
  if (!value || typeof value !== "object") return t("common.notSet");
  const parts = [];
  if (value.modifiers & 2) parts.push(sourceText("Ctrl"));
  if (value.modifiers & 1) parts.push(sourceText("Alt"));
  if (value.modifiers & 4) parts.push(sourceText("Shift"));
  if (value.modifiers & 8) parts.push(sourceText("Win"));
  const key = Number(value.virtual_key);
  const names = { 8: "Backspace", 9: "Tab", 13: "Enter", 19: "Pause", 20: "Caps Lock", 27: "Escape", 32: "Space", 33: "Page Up", 34: "Page Down", 35: "End", 36: "Home", 37: "Left", 38: "Up", 39: "Right", 40: "Down", 44: "Print Screen", 45: "Insert", 46: "Delete", 91: "Left Win", 92: "Right Win", 93: "Menu", 144: "Num Lock", 145: "Scroll Lock" };
  parts.push(sourceText(names[key]) || (key >= 65 && key <= 90 ? String.fromCharCode(key) : key >= 48 && key <= 57 ? String.fromCharCode(key) : key >= 96 && key <= 105 ? `${sourceText("Numpad")} ${key - 96}` : key >= 112 && key <= 135 ? `F${key - 111}` : `VK 0x${key.toString(16).toUpperCase().padStart(2, "0")}`));
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
  if (!window.pywebview?.api?.request) throw new Error(t("error.bridge"));
  const response = await window.pywebview.api.request(method, params);
  if (!response || response.ok !== true) {
    const raw = safeText(response?.error?.message);
    if (!raw) throw new Error(t("error.request"));
    const translated = sourceText(raw);
    throw new Error(translated !== raw || state.language === "en" ? translated : t("error.requestDetail", { detail: raw }));
  }
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
  if (retry) box.append(element("button", { class: "secondary", type: "button", text: t("common.reconnect"), onclick: pollSnapshot }));
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
      applyLanguage();
      state.failures = 0;
      setConnection("online", t("common.connected"));
      showNotice("");
      render();
    }
  } catch (error) {
    state.failures += 1;
    setConnection("offline", t("common.disconnected"));
    showNotice(sourceText(safeText(error.message, t("error.connection"))), true, true);
    if (!state.snapshot) renderError(t("error.connect"));
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
  document.querySelector("#page-title").textContent = t(title);
  document.querySelector("#page-description").textContent = t(description);
  primaryAction.textContent = action ? t(action) : "";
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
  content.replaceChildren(element("div", { class: "empty-state" }, [element("span", { class: "empty-icon", "aria-hidden": "true", text: "!" }), element("h2", { text: t("error.unableLoad") }), element("p", { class: "muted", text: message })]));
}

function emptyState(icon, title, message, action, handler) {
  const children = [element("span", { class: "empty-icon", "aria-hidden": "true", text: icon }), element("h2", { text: title }), element("p", { class: "muted", text: message })];
  if (action) children.push(element("button", { class: "primary", type: "button", text: action, onclick: handler }));
  return element("div", { class: "empty-state" }, children);
}

function renderRoutes() {
  const routes = safeArray(state.snapshot.routes);
  if (!routes.length) { content.replaceChildren(emptyState("⇄", t("route.emptyTitle"), t("route.emptyBody"), t("route.create"), openRouteWizard)); return; }
  const grid = element("div", { class: "card-grid" });
  for (const route of routes) {
    const id = safeText(route.id);
    const edit = element("button", { class: "text-button", type: "button", text: t("common.configure"), onclick: () => openEntityEditor("route", route) });
    const remove = element("button", { class: "text-button", type: "button", text: t("common.remove"), onclick: () => confirmAction(t("route.removeTitle"), t("route.removeMessage", { name: safeText(route.name, t("route.unnamed")) }), "route.delete", { id }) });
    const flow = element("div", { class: "route-flow" }, [
      endpoint(t("route.input"), route.input), element("span", { class: "route-arrow", "aria-hidden": "true", text: "↓" }), endpoint(t("route.output"), route.output)
    ]);
    const card = element("article", { class: "card" }, [
      element("div", { class: "card-body" }, [element("div", { class: "card-header" }, [element("div", {}, [element("h3", { text: safeText(route.name, t("route.unnamed")) }), element("p", { class: "muted", text: sourceText(safeText(route.description, t("route.description"))) })]), element("div", { class: "card-actions" }, [edit, remove])]), flow]),
      element("footer", { class: "card-footer" }, [element("span", { class: "muted", text: sourceText(safeText(route.summary, t("common.ready"))) }), badge(route.enabled === false ? t("common.disabled") : t("common.active"), route.enabled === false ? "muted" : "")])
    ]);
    grid.append(card);
  }
  content.replaceChildren(grid);
}

function openRouteWizard() {
  state.editor = { kind: "route-wizard", step: 0, values: { name: "", route_type: "other", input_id: "", input_parameters: {}, provider_id: "", output_parameters: {} }, documents: {} };
  renderRouteWizard();
  editorDialog.showModal();
}

function routeWizardFields() {
  const routeForm = state.snapshot?.forms?.route || {};
  const fields = safeArray(routeForm.fields);
  return { name: fields.find(field => field.key === "name"), type: fields.find(field => field.key === "route_type"), input: fields.find(field => field.key === "input_id"), output: fields.find(field => field.key === "provider_id") };
}

async function loadRouteWizardDocument(endpoint) {
  const wizard = state.editor;
  const pluginId = endpoint === "input" ? wizard.values.input_id : wizard.values.provider_id;
  if (!pluginId) return null;
  const result = await nativeRequest("route.endpoint-form", { endpoint, plugin_id: pluginId, parameters: wizard.values[`${endpoint}_parameters`] || {} });
  wizard.documents[endpoint] = result?.document || null;
  const automatic = safeArray(wizard.documents[endpoint]?.actions).find(action => action.kind === "action" && action.auto === true);
  if (automatic) {
    const update = await nativeRequest("route.endpoint-action", { endpoint, plugin_id: pluginId, action_id: safeText(automatic.id), values: wizard.values[`${endpoint}_parameters`] || {} });
    if (update?.status === "update" && update.document) wizard.documents[endpoint] = update.document;
  }
  return wizard.documents[endpoint];
}

async function invokeRouteWizardAction(endpoint, actionId, button) {
  const wizard = state.editor;
  if (!wizard || wizard.kind !== "route-wizard") return;
  const pluginId = endpoint === "input" ? wizard.values.input_id : wizard.values.provider_id;
  const values = collectEditorValues();
  wizard.values[`${endpoint}_parameters`] = values;
  button.disabled = true;
  try {
    const result = await nativeRequest("route.endpoint-action", { endpoint, plugin_id: pluginId, action_id: actionId, values });
    if (result?.status === "update" && result.document) wizard.documents[endpoint] = result.document;
    renderRouteWizard();
  } catch (error) {
    const target = document.querySelector("#editor-error");
    target.textContent = sourceText(safeText(error.message, t("error.pluginDiscovery"))); target.hidden = false; target.focus();
    button.disabled = false;
  }
}

function renderRouteWizard() {
  const wizard = state.editor;
  const labels = ["wizard.name", "wizard.type", "wizard.inputPlugin", "wizard.inputConfig", "wizard.outputPlugin", "wizard.outputConfig"];
  const fields = routeWizardFields();
  document.querySelector("#editor-kicker").textContent = t("wizard.step", { current: wizard.step + 1, total: 6 });
  document.querySelector("#editor-title").textContent = t(labels[wizard.step]);
  const description = document.querySelector("#editor-description");
  description.textContent = ""; description.hidden = true;
  const container = document.querySelector("#editor-fields"); container.replaceChildren();
  const back = document.querySelector("#editor-back"); back.hidden = wizard.step === 0;
  const save = document.querySelector("#editor-save");
  save.hidden = wizard.step === 2 || wizard.step === 4;
  save.textContent = wizard.step === 5 ? t("route.create") : t("wizard.next");
  if (wizard.step === 0 && fields.name) container.append(renderField(fields.name, wizard.values.name));
  if (wizard.step === 1 && fields.type) container.append(renderField(fields.type, wizard.values.route_type));
  if (wizard.step === 2 || wizard.step === 4) {
    const endpoint = wizard.step === 2 ? "input" : "output";
    const options = safeArray(endpoint === "input" ? fields.input?.options : fields.output?.options);
    description.textContent = t(endpoint === "input" ? "wizard.chooseInput" : "wizard.chooseOutput"); description.hidden = false;
    for (const option of options) {
      container.append(element("button", { class: "choice-card", type: "button", onclick: async () => {
        try {
          wizard.values[endpoint === "input" ? "input_id" : "provider_id"] = safeText(option.value);
          wizard.values[`${endpoint}_parameters`] = {};
          await loadRouteWizardDocument(endpoint);
          wizard.step += 1; renderRouteWizard();
        } catch (error) {
          const target = document.querySelector("#editor-error");
          target.textContent = sourceText(safeText(error.message, t("error.pluginConfig"))); target.hidden = false; target.focus();
        }
      } }, [
        element("strong", { text: sourceText(safeText(option.label, String(option.value ?? ""))) }),
        element("span", { text: sourceText(safeText(option.description, t("wizard.noConfig"))) }),
      ]));
    }
  }
  if (wizard.step === 3 || wizard.step === 5) {
    const endpoint = wizard.step === 3 ? "input" : "output";
    const formDocument = wizard.documents[endpoint];
    if (formDocument) {
      description.textContent = sourceText(safeText(formDocument.description)); description.hidden = !description.textContent;
      for (const field of safeArray(formDocument.fields)) {
        const typeMap = { integer: "number", choice: "select" };
        const current = wizard.values[`${endpoint}_parameters`]?.[field.id] ?? field.value;
        container.append(renderField({ ...field, key: field.id, type: typeMap[field.type] || field.type }, current));
      }
      const actions = safeArray(formDocument.actions).filter(action => action.kind === "action");
      if (actions.length) {
        const buttons = element("div", { class: "button-row" });
        for (const action of actions) {
          const button = element("button", { class: "secondary", type: "button", text: sourceText(safeText(action.label, t("common.refresh"))) });
          button.addEventListener("click", () => invokeRouteWizardAction(endpoint, safeText(action.id), button));
          buttons.append(button);
        }
        container.append(buttons);
      }
      if (endpoint === "input" && wizard.values.input_id === "mqtt") {
        const name = container.querySelector('[name="ha_name"]');
        const identifier = container.querySelector('[name="ha_id"]');
        const maximum = container.querySelector('[name="max_value"]');
        if (maximum && !maximum.value) maximum.value = "100";
        if (name && identifier) {
          identifier.dataset.autogenerated = identifier.value ? "false" : "true";
          if (!identifier.value) identifier.value = homeAssistantId(name.value);
          name.addEventListener("input", () => {
            if (identifier.dataset.autogenerated === "true") identifier.value = homeAssistantId(name.value);
          });
          identifier.addEventListener("input", () => { identifier.dataset.autogenerated = "false"; });
        }
      }
    } else container.append(element("p", { class: "muted", text: t("wizard.none") }));
  }
  document.querySelector("#editor-error").hidden = true;
  window.setTimeout(() => container.querySelector("input, select, textarea")?.focus(), 0);
}

function collectEditorValues() {
  const values = {};
  for (const input of editorForm.querySelectorAll("[name]")) values[input.getAttribute("name")] = typeof input._sequenceValue === "function" ? input._sequenceValue() : typeof input._triggerValue === "function" ? input._triggerValue() : input.dataset.hotkey !== undefined ? JSON.parse(input.dataset.hotkey) : input.type === "checkbox" ? input.checked : input.type === "number" && input.value !== "" ? Number(input.value) : input.selectedOptions?.[0]?.dataset.json === "true" ? JSON.parse(input.value) : input.value;
  return values;
}

async function advanceRouteWizard() {
  const wizard = state.editor;
  const values = collectEditorValues();
  if (wizard.step === 0) wizard.values.name = safeText(values.name).trim();
  if (wizard.step === 1) wizard.values.route_type = safeText(values.route_type, "other");
  if (wizard.step === 2) { wizard.values.input_id = safeText(values.input_id); wizard.values.input_parameters = {}; await loadRouteWizardDocument("input"); }
  if (wizard.step === 4) { wizard.values.provider_id = safeText(values.provider_id); wizard.values.output_parameters = {}; await loadRouteWizardDocument("output"); }
  if (wizard.step === 3) wizard.values.input_parameters = values;
  if (wizard.step === 5) wizard.values.output_parameters = values;
  if (wizard.step === 5) {
    await nativeRequest("route.save", { values: wizard.values });
    editorDialog.close(); state.editor = null; state.revision = -1; await pollSnapshot();
    return;
  }
  wizard.step += 1; renderRouteWizard();
}

function endpoint(label, data) {
  const value = data && typeof data === "object" ? data : {};
  return element("div", { class: "endpoint" }, [element("small", { text: label }), element("strong", { text: sourceText(safeText(value.name, t("common.notSelected"))) }), element("span", { class: "muted", text: sourceText(safeText(value.summary, "")) })]);
}

function renderActions() {
  const signals = safeArray(state.snapshot.signals);
  if (!signals.length) { content.replaceChildren(emptyState("→", t("automation.emptyTitle"), t("automation.emptyBody"), t("automation.create"), () => openEntityEditor("signal", null))); return; }
  const stack = element("div", { class: "stack" });
  for (const signal of signals) {
    const id = safeText(signal.id);
    const slots = safeArray(signal.values?.slots);
    const steps = slots.map(slot => {
      if (slot.kind === "wait") return `${t("editor.wait")} ${Number(slot.milliseconds || 0) / 1000} s`;
      const pluginName = sourceText(safeText(slot.plugin_name));
      const actionLabel = sourceText(safeText(slot.action_label, safeText(slot.target)));
      const summary = slot.summary_translatable ? sourceText(safeText(slot.summary)) : safeText(slot.summary);
      return `${pluginName ? `${pluginName}: ` : ""}${actionLabel}${summary ? ` (${summary})` : ""}`;
    });
    const summary = steps.length ? element("ol", { class: "automation-summary" }, steps.map(step => element("li", { text: step }))) : element("p", { class: "muted", text: sourceText(safeText(signal.description, t("automation.orderedSteps"))) });
    const triggerParts = safeArray(signal.values?.triggers).map(trigger => trigger.kind === "app-start" ? sourceText("App start") : trigger.kind === "keyboard" ? hotkeyLabel(trigger.hotkey) : trigger.kind === "tray" ? `${sourceText("Tray menu option")}: ${safeText(trigger.label)}` : trigger.kind === "mqtt" ? sourceText("MQTT / Home Assistant") : "").filter(Boolean);
    stack.append(element("article", { class: "card setting-row" }, [
      element("div", {}, [element("h3", { text: safeText(signal.name, t("automation.name")) }), summary, element("p", { class: "signal-trigger", text: triggerParts.join(" + ") || t("automation.noTrigger") })]),
      element("div", { class: "button-row" }, [element("button", { class: "secondary", type: "button", text: t("common.run"), onclick: () => execute("signal.run", { id }) }), element("button", { class: "secondary", type: "button", text: t("common.edit"), onclick: () => openEntityEditor("signal", signal) }), element("button", { class: "text-button", type: "button", text: t("common.remove"), onclick: () => confirmAction(t("automation.removeTitle"), t("automation.removeMessage", { name: safeText(signal.name, t("automation.name")) }), "signal.delete", { id }) })])
    ]));
  }
  content.replaceChildren(stack);
}

function renderIntegrations() {
  const stack = element("div", { class: "stack" });
  const profiles = safeArray(state.snapshot.mqtt_profiles);
  stack.append(element("article", { class: "card setting-row" }, [
    element("div", {}, [element("h3", { text: sourceText("MQTT / Home Assistant") }), element("p", { class: "muted", text: t("integration.saved", { count: profiles.length }) })]),
    element("button", { class: "secondary", type: "button", text: t("common.configure"), onclick: openMqttIntegration })
  ]));
  const integrations = safeArray(state.snapshot.integrations);
  for (const action of integrations) {
    const buttons = [];
    if (safeArray(action.form?.fields).length || action.form?.ui_action) buttons.push(element("button", { class: "secondary", type: "button", text: t("common.configure"), onclick: () => openEntityEditor("action", action) }));
    for (const command of safeArray(action.ui_actions).filter(item => item.kind === "action")) {
      const params = { id: action.id, action_id: command.id, values: action.values || {} };
      buttons.push(element("button", { class: "secondary", type: "button", text: sourceText(safeText(command.label, t("common.run"))), onclick: () => command.confirm ? confirmAction(sourceText(safeText(command.label, t("dialog.confirm"))), sourceText(command.confirm), "plugin.action", params) : execute("plugin.action", params) }));
    }
    stack.append(element("article", { class: "card setting-row" }, [
      element("div", {}, [element("h3", { text: sourceText(safeText(action.name, "Action")) }), element("p", { class: "muted", text: sourceText(safeText(action.description, "Trusted plugin action")) })]),
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
    list.append(element("p", { class: "muted", text: t("integration.none") }));
    return;
  }
  for (const profile of profiles) {
    const id = safeText(profile.id);
    list.append(element("article", { class: "profile-row" }, [
      element("div", {}, [element("strong", { text: safeText(profile.name, "MQTT / HA") }), element("span", { class: "muted", text: safeText(profile.description) })]),
      element("div", { class: "button-row" }, [element("button", { class: "secondary", type: "button", text: t("common.edit"), onclick: () => editMqttProfile(profile) }), element("button", { class: "text-button", type: "button", text: t("common.remove"), onclick: () => removeMqttProfile(id) })])
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
  document.querySelector("#mqtt-profile-title").textContent = profile ? t("editor.edit", { name: safeText(profile.name, t("settings.configuration")) }) : t("integration.add");
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
  save.disabled = true; save.textContent = t("common.saving");
  const values = {};
  for (const input of document.querySelector("#mqtt-profile-fields").querySelectorAll("[name]")) values[input.getAttribute("name")] = input.type === "checkbox" ? input.checked : input.type === "number" && input.value !== "" ? Number(input.value) : input.value;
  try {
    await nativeRequest("mqtt.profile.save", { id: state.mqttProfileId, values });
    state.revision = -1; await pollSnapshot(); showMqttProfileList();
  } catch (error) {
    const target = document.querySelector("#mqtt-error"); target.textContent = sourceText(safeText(error.message, t("error.save"))); target.hidden = false; target.focus();
  } finally { save.disabled = false; save.textContent = t("integration.save"); }
}

async function removeMqttProfile(id) {
  try {
    await nativeRequest("mqtt.profile.delete", { id });
    state.revision = -1; await pollSnapshot(); showMqttProfileList();
  } catch (error) {
    document.querySelector("#mqtt-list-view").prepend(element("div", { class: "inline-error", role: "alert", text: sourceText(safeText(error.message, t("integration.removeError"))) }));
  }
}

function renderAppearance() {
  const appearance = state.snapshot.appearance && typeof state.snapshot.appearance === "object" ? state.snapshot.appearance : {};
  const renderers = safeArray(appearance.renderers);
  const card = element("section", { class: "card" });
  const rows = renderers.map(renderer => element("div", { class: "setting-row" }, [
    element("div", {}, [element("h3", { text: sourceText(safeText(renderer.name, t("appearance.overlay"))) }), element("p", { class: "muted", text: sourceText(safeText(renderer.description, t("appearance.description"))) })]),
     element("div", { class: "button-row" }, [
       renderer.form ? element("button", { class: "secondary", type: "button", text: t("common.configure"), onclick: () => openEntityEditor("action", renderer) }) : null,
       ...safeArray(renderer.ui_actions).filter(item => item.kind === "action").map(action => element("button", { class: "secondary", type: "button", text: sourceText(safeText(action.label, t("common.run"))), onclick: () => action.confirm ? confirmAction(sourceText(safeText(action.label, t("dialog.confirm"))), sourceText(action.confirm), "plugin.action", { id: renderer.id, action_id: action.id, values: renderer.values || {} }) : execute("plugin.action", { id: renderer.id, action_id: action.id, values: renderer.values || {} }) })),
       element("button", { class: renderer.selected ? "primary" : "secondary", type: "button", text: renderer.selected ? t("common.selected") : t("common.useThis"), disabled: renderer.selected ? "" : null, onclick: () => execute("appearance.save", { renderer_id: renderer.id }) })
     ].filter(Boolean))
  ]));
  if (rows.length) card.append(...rows); else card.append(element("div", { class: "setting-row" }, [element("p", { class: "muted", text: t("appearance.none") })]));
  content.replaceChildren(card);
}

function renderSettings() {
  const settings = state.snapshot.settings && typeof state.snapshot.settings === "object" ? state.snapshot.settings : {};
  const startup = element("section", { class: "card" }, [element("div", { class: "setting-row" }, [element("div", {}, [element("h3", { text: sourceText(safeText(settings.startup_label, "Start with Windows")) }), element("p", { class: "muted", text: sourceText(safeText(settings.startup_description, "Launch quietly in the notification area when you sign in.")) })]), element("button", { class: settings.start_with_windows ? "primary" : "secondary", type: "button", role: "switch", "aria-checked": String(Boolean(settings.start_with_windows)), text: settings.start_with_windows ? t("common.on") : t("common.off"), onclick: () => execute("settings.save", { start_with_windows: !settings.start_with_windows }) })])]);
  const languageSelect = element("select", { class: "settings-select", "aria-label": t("settings.language") });
  for (const option of safeArray(settings.language_options)) languageSelect.append(element("option", { value: safeText(option.value), text: sourceText(safeText(option.label, option.value)), selected: option.value === settings.ui_language ? "" : null }));
  languageSelect.addEventListener("change", () => execute("settings.save", { ui_language: languageSelect.value }));
  const language = element("section", { class: "card" }, [element("div", { class: "setting-row" }, [element("div", {}, [element("h3", { text: t("settings.language") }), element("p", { class: "muted", text: t("settings.languageDescription") })]), languageSelect])]);
  const recent = safeArray(settings.recent_configurations);
  if (!recent.length) state.importMenuOpen = false;
  const recentItems = element("div", { class: "split-menu-items", hidden: state.importMenuOpen ? null : "" }, recent.map(item => element("button", { type: "button", text: safeText(item.name, t("settings.configuration")), onclick: () => { state.importMenuOpen = false; confirmAction(t("settings.importTitle"), t("settings.importMessage", { name: safeText(item.name, t("settings.configuration")) }), "config.import", { path: item.path }); } })));
  const recentLabel = recent.length ? t("settings.recent") : t("settings.noRecent");
  const arrowButton = element("button", { class: "secondary split-arrow", type: "button", title: recentLabel, "aria-label": recentLabel, "aria-expanded": String(state.importMenuOpen), disabled: recent.length ? null : "", text: "▼", onclick: event => { event.stopPropagation(); state.importMenuOpen = !state.importMenuOpen; recentItems.hidden = !state.importMenuOpen; arrowButton.setAttribute("aria-expanded", String(state.importMenuOpen)); } });
  const recentMenu = element("div", { class: "split-menu" }, [arrowButton, recentItems]);
  const importSplit = element("div", { class: "split-control", role: "group", "aria-label": t("settings.importTitle") }, [element("button", { class: "secondary", type: "button", text: t("settings.import"), onclick: importConfiguration }), recentMenu]);
  const configuration = element("section", { class: "card" }, [element("div", { class: "setting-row configuration-row" }, [element("div", {}, [element("h3", { text: t("settings.configuration") }), element("p", { class: "muted", text: t("settings.configurationDescription") })]), element("div", { class: "button-row configuration-actions" }, [element("button", { class: "primary", type: "button", text: t("settings.export"), onclick: exportConfiguration }), importSplit, element("button", { class: "secondary", type: "button", text: t("settings.restore"), title: t("settings.restoreTitle"), onclick: () => confirmAction(t("settings.restoreConfirm"), t("settings.restoreMessage"), "config.restore-default", {}) })])])]);
  content.replaceChildren(element("div", { class: "stack" }, [startup, language, configuration]));
}

function renderDiagnostics() {
  const previous = content.querySelector(".diagnostic-output");
  const wasAtBottom = !previous || previous.scrollHeight - previous.scrollTop - previous.clientHeight <= 12;
  const previousScrollTop = previous?.scrollTop || 0;
  const diagnostics = state.snapshot.diagnostics && typeof state.snapshot.diagnostics === "object" ? state.snapshot.diagnostics : {};
  const text = safeText(diagnostics.text, t("diagnostics.empty"));
  if (previous) {
    const summary = content.querySelector(".diagnostic-summary");
    const statusBadge = content.querySelector(".diagnostic-status");
    if (summary) summary.textContent = sourceText(safeText(diagnostics.summary, t("diagnostics.description")));
    if (statusBadge) { statusBadge.textContent = sourceText(safeText(diagnostics.status, t("common.unknown"))); statusBadge.className = `badge diagnostic-status${diagnostics.status === "Error" ? " is-error" : ""}`; }
    if (previous.textContent !== text) previous.textContent = text;
    previous.scrollTop = wasAtBottom ? previous.scrollHeight : previousScrollTop;
    return;
  }
  const status = element("section", { class: "card setting-row" }, [element("div", {}, [element("h3", { text: t("diagnostics.health") }), element("p", { class: "muted diagnostic-summary", text: sourceText(safeText(diagnostics.summary, t("diagnostics.description"))) })]), element("span", { class: `badge diagnostic-status${diagnostics.status === "Error" ? " is-error" : ""}`, text: sourceText(safeText(diagnostics.status, t("common.unknown"))) })]);
  const output = element("section", { class: "card" }, [element("pre", { class: "diagnostic-output", tabindex: "0", "aria-label": t("diagnostics.output"), text })]);
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
      element("p", { class: "eyebrow", text: t("about.eyebrow") }),
      element("h2", { text: name }),
      element("p", { class: "about-version", text: t("about.version", { version }) }),
      element("p", { class: "muted", text: t("about.description") })
    ])
  ]));
}

function openEntityEditor(kind, entity) {
  const schemas = state.snapshot?.forms && typeof state.snapshot.forms === "object" ? state.snapshot.forms : {};
  const schema = kind === "route" ? (entity?.form || schemas.route) : kind === "signal" ? (entity?.form || schemas.signal) : kind === "mqtt-profile" ? (entity?.form || schemas.mqtt_profile) : entity?.form;
  const fields = safeArray(schema?.fields);
  state.editor = { kind, id: entity?.id || null, method: safeText(schema?.method, kind === "route" ? "route.save" : kind === "signal" ? "signal.save" : kind === "mqtt-profile" ? "mqtt.profile.save" : "action.save") };
  editorDialog.dataset.kind = kind;
  const localizedKind = kind === "signal" ? t("automation.name") : kind === "mqtt-profile" ? t("editor.mqttConfig") : kind === "route" ? t("route.description") : sourceText("Action");
  document.querySelector("#editor-kicker").textContent = localizedKind.toUpperCase();
  document.querySelector("#editor-title").textContent = entity ? t("editor.edit", { name: sourceText(safeText(entity.name, localizedKind)) }) : t("editor.create", { kind: localizedKind.toLowerCase() });
  const editorDescription = document.querySelector("#editor-description");
  editorDescription.textContent = sourceText(safeText(schema?.description));
  editorDescription.hidden = !editorDescription.textContent;
  document.querySelector("#editor-error").hidden = true;
  document.querySelector("#editor-back").hidden = true;
  document.querySelector("#editor-save").hidden = false;
  document.querySelector("#editor-save").textContent = kind === "route" && !entity ? t("editor.saveConfigure") : t("common.save");
  const container = document.querySelector("#editor-fields");
  container.replaceChildren();
  const values = entity?.values && typeof entity.values === "object" ? entity.values : {};
  for (const field of fields) container.append(renderField(field, values[field.key]));
  if (kind === "mqtt-profile") {
    groupEditorFields(container, ["host", "port"], "is-host-port");
    groupEditorFields(container, ["username", "password"], "");
  }
  syncConditionalFields(container);
  for (const controller of container.querySelectorAll('input[type="checkbox"], select')) controller.addEventListener("change", () => syncConditionalFields(container));
  if (!fields.length) container.append(element("p", { class: "muted", text: t("editor.noFields") }));
  editorDialog.showModal();
  window.setTimeout(() => container.querySelector("input, select, textarea")?.focus(), 0);
}

function renderField(field, current) {
  const key = safeText(field.key);
  const type = safeText(field.type, "text");
  const wrapper = element("div", { class: type === "boolean" ? "check-field" : "field" });
  wrapper.dataset.fieldKey = key;
  if (field.visible_when) wrapper.dataset.visibleWhen = safeText(field.visible_when);
  let input;
  if (type === "hotkey") {
    input = renderHotkeyInput(`field-${key}`, current);
    input.name = key;
  }
  else if (type === "sequence") {
    input = renderSequenceField(key, field, current);
  }
  else if (type === "trigger-list") {
    input = renderTriggerListField(key, field, current);
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
        input.append(element("option", { value: encoded, text: optionText(field, option), selected: encoded === previous ? "" : null, "data-json": "true" }));
      }
    };
    if (field.depends_on) input.dataset.dependsOn = safeText(field.depends_on); else input._renderOptions("");
  } else if (type === "textarea") {
    input = element("textarea", { id: `field-${key}`, name: key, required: field.required ? "" : null, maxlength: Number.isInteger(field.max_length) ? field.max_length : 4096 }); input.value = safeText(current);
  } else {
    input = element("input", { id: `field-${key}`, name: key, type: type === "boolean" ? "checkbox" : (["number", "password"].includes(type) ? type : "text"), required: field.required ? "" : null, min: field.min, max: field.max, maxlength: Number.isInteger(field.max_length) ? field.max_length : 512, autocomplete: type === "password" ? "off" : "on" });
    if (type === "boolean") input.checked = Boolean(current ?? field.default);
    else {
      const initial = current ?? field.default;
      input.value = typeof initial === "string" || typeof initial === "number" ? initial : "";
    }
  }
  const label = element("label", { for: `field-${key}`, text: sourceText(safeText(field.label, key)) });
  if (type === "boolean") wrapper.append(input, label); else wrapper.append(label, input);
  if (field.description) wrapper.append(element("small", { text: sourceText(safeText(field.description)) }));
  return wrapper;
}

function groupEditorFields(container, keys, extraClass) {
  const fields = keys.map(key => container.querySelector(`[data-field-key="${key}"]`));
  if (fields.some(field => !field)) return;
  const row = element("div", { class: `field-row${extraClass ? ` ${extraClass}` : ""}` });
  fields[0].before(row);
  row.append(...fields);
}

function renderHotkeyInput(id, current) {
  const input = element("input", { id, type: "text", readonly: "", value: hotkeyLabel(current), "data-hotkey": JSON.stringify(current ?? null), placeholder: t("editor.pressKeys") });
  input.addEventListener("keydown", event => {
    event.preventDefault();
    event.stopPropagation();
    if (event.key === "Backspace" || event.key === "Delete") { input.dataset.hotkey = "null"; input.value = t("common.notSet"); return; }
    if (event.code === "AltRight") { input.dataset.rightAlt = "true"; return; }
    const virtualKey = virtualKeyFromEvent(event);
    if (!virtualKey || [16, 17, 18, 91, 92].includes(virtualKey)) return;
    const altGraph = input.dataset.rightAlt === "true" || event.getModifierState?.("AltGraph") === true;
    const value = { modifiers: (event.altKey || altGraph ? 1 : 0) | (event.ctrlKey || altGraph ? 2 : 0) | (event.shiftKey ? 4 : 0) | (event.metaKey ? 8 : 0), virtual_key: virtualKey };
    input.dataset.hotkey = JSON.stringify(value); input.value = hotkeyLabel(value);
  }, true);
  input.addEventListener("keyup", event => { event.preventDefault(); event.stopPropagation(); if (event.code === "AltRight") input.dataset.rightAlt = "false"; }, true);
  return input;
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

function openChoiceDialog(kicker, title, description, options, onSelect) {
  state.choice = { onSelect };
  document.querySelector("#choice-kicker").textContent = kicker;
  document.querySelector("#choice-title").textContent = title;
  document.querySelector("#choice-description").textContent = description;
  const container = document.querySelector("#choice-options");
  container.replaceChildren();
  for (const option of options) {
    const button = element("button", { class: "choice-card", type: "button", disabled: option.disabled ? "" : null, onclick: () => {
      const callback = state.choice?.onSelect;
      choiceDialog.close();
      state.choice = null;
      if (callback) callback(option.value);
    } }, [
      element("strong", { text: sourceText(safeText(option.label, String(option.value ?? ""))) }),
      element("span", { text: sourceText(safeText(option.description, t("wizard.noConfig"))) }),
      ...(option.disabled ? [element("small", { text: sourceText(safeText(option.disabled_reason, t("common.unavailable"))) })] : []),
    ]);
    container.append(button);
  }
  choiceDialog.showModal();
  window.setTimeout(() => container.querySelector("button:not(:disabled)")?.focus(), 0);
}

function renderSequenceField(key, field, current) {
  const root = element("div", { id: `field-${key}`, name: key, class: "sequence-editor" });
  const list = element("div", { class: "sequence-list" });
  const options = safeArray(field.options);
  const optionByTarget = new Map(options.map(option => [safeText(option.value), option]));
  function addSlot(value) {
    if (!value || typeof value !== "object") return;
    const slot = value;
    const kind = slot.kind === "wait" ? "wait" : "action";
    const row = element("div", { class: "sequence-row", "data-kind": kind });
    const index = element("strong", { class: "sequence-index" });
    let control;
    let configurable = false;
    if (kind === "wait") {
      control = element("label", { class: "sequence-wait" }, [element("span", { text: t("editor.wait") }), element("input", { type: "number", min: "0", max: "300000", step: "100", value: Number.isInteger(slot.milliseconds) ? slot.milliseconds : 1000, "aria-label": t("editor.waitMs") }), element("span", { text: "ms" })]);
    } else {
      const option = optionByTarget.get(safeText(slot.target));
      const label = sourceText(safeText(option?.label, t("common.unavailable")));
      const description = sourceText(safeText(option?.description, t("error.operation")));
      configurable = option?.configurable === true;
      row.dataset.target = safeText(slot.target);
      control = element("div", { class: "sequence-action" }, [element("strong", { text: label }), element("small", { text: description })]);
    }
    const configure = element("button", { class: "secondary sequence-config", type: "button", text: t("common.configure"), hidden: !configurable ? "" : null, onclick: () => openSlotEditor(row, row.dataset.target) });
    const summary = element("small", { class: "sequence-summary", text: sourceText(safeText(slot.summary)) });
    summary.hidden = !summary.textContent;
    const moveUp = element("button", { class: "icon-button", type: "button", text: "↑", title: t("editor.moveUp"), "aria-label": t("editor.moveUp"), onclick: () => { row.previousElementSibling?.before(row); refresh(); } });
    const moveDown = element("button", { class: "icon-button", type: "button", text: "↓", title: t("editor.moveDown"), "aria-label": t("editor.moveDown"), onclick: () => { row.nextElementSibling?.after(row); refresh(); } });
    const remove = element("button", { class: "icon-button", type: "button", text: "×", title: t("editor.removeStep"), "aria-label": t("editor.removeStep"), onclick: () => { row.remove(); refresh(); } });
    row._parameters = slot.parameters && typeof slot.parameters === "object" ? slot.parameters : {};
    row._summary = summary;
    row.append(index, control, configure, moveUp, moveDown, remove, summary); list.append(row); refresh();
  }
  function refresh() { [...list.children].forEach((row, index) => { row.querySelector(".sequence-index").textContent = String(index + 1); }); }
  root._sequenceValue = () => [...list.children].map(row => row.dataset.kind === "wait" ? { kind: "wait", milliseconds: Number(row.querySelector("input").value) } : { kind: "action", target: row.dataset.target, parameters: row._parameters || {} });
  for (const slot of safeArray(current)) addSlot(slot);
  const addAction = element("button", { class: "secondary", type: "button", text: t("editor.addAction"), onclick: () => openChoiceDialog(
    t("dialog.addItem"),
    t("editor.chooseAction"),
    t("editor.actionDescription"),
    [...options, { value: "__wait__", label: t("editor.wait"), description: t("editor.actionDescription") }],
    value => addSlot(value === "__wait__" ? { kind: "wait", milliseconds: 1000 } : { kind: "action", target: value, parameters: {} }),
  ) });
  root.append(list, element("div", { class: "button-row sequence-add" }, [addAction]));
  return root;
}

function renderTriggerListField(key, field, current) {
  const root = element("div", { id: `field-${key}`, name: key, class: "trigger-editor" });
  const list = element("div", { class: "trigger-list" });
  const options = safeArray(field.options);
  const optionByKind = new Map(options.map(option => [safeText(option.value), option]));
  const addButton = element("button", { class: "secondary", type: "button", text: t("editor.addTrigger"), onclick: () => {
    const used = new Set([...list.children].map(row => row.dataset.kind));
    openChoiceDialog(
      t("dialog.addItem"),
      t("editor.chooseTrigger"),
      t("editor.triggerDescription"),
      options.map(option => used.has(option.value) ? { ...option, disabled: true, disabled_reason: t("editor.alreadyAdded") } : option),
      kind => {
        const automationName = safeText(document.querySelector("#field-name")?.value);
        addTrigger(kind === "mqtt" ? { kind, ha_name: automationName } : kind === "tray" ? { kind, label: automationName } : { kind });
      },
    );
  } });

  function refresh() {
    const used = new Set([...list.children].map(row => row.dataset.kind));
    addButton.disabled = options.length > 0 && used.size >= options.length;
    [...list.children].forEach((row, index) => { row.querySelector(".trigger-index").textContent = String(index + 1); });
  }

  function addTrigger(value) {
    const trigger = value && typeof value === "object" ? value : {};
    const kind = safeText(trigger.kind);
    const option = optionByKind.get(kind);
    if (!option || [...list.children].some(row => row.dataset.kind === kind)) return;
    const row = element("div", { class: "trigger-row", "data-kind": kind });
    const index = element("strong", { class: "trigger-index" });
    const body = element("div", { class: "trigger-body" });
    body.append(element("strong", { text: sourceText(safeText(option.label, kind)) }));
    if (kind === "app-start") {
      body.append(element("small", { text: t("editor.appStart") }));
      row._triggerValue = () => ({ kind });
    } else if (kind === "keyboard") {
      const hotkey = renderHotkeyInput(`trigger-${kind}-hotkey`, trigger.hotkey);
      const forward = element("input", { id: `trigger-${kind}-forward`, type: "checkbox" });
      forward.checked = trigger.forward_keys !== false;
      body.append(element("label", { for: hotkey.id, text: t("editor.keyCombination") }), hotkey, element("label", { class: "trigger-check", for: forward.id }, [forward, element("span", { text: t("editor.forwardKeys") })]));
      row._triggerValue = () => ({ kind, hotkey: JSON.parse(hotkey.dataset.hotkey), forward_keys: forward.checked });
    } else if (kind === "tray") {
      const label = element("input", { id: `trigger-${kind}-label`, type: "text", maxlength: "80", value: safeText(trigger.label), placeholder: t("editor.trayText") });
      body.append(element("label", { for: label.id, text: t("editor.trayText") }), label);
      row._triggerValue = () => ({ kind, label: label.value });
    } else {
      const profile = element("select", { id: `trigger-${kind}-profile` });
      for (const profileOption of safeArray(field.mqtt_profiles)) profile.append(element("option", { value: safeText(profileOption.value), text: safeText(profileOption.label, profileOption.value), selected: profileOption.value === trigger.profile_id ? "" : null }));
      const name = element("input", { id: `trigger-${kind}-name`, type: "text", maxlength: "80", value: safeText(trigger.ha_name), placeholder: t("editor.haName") });
      const savedIdentifier = safeText(trigger.ha_id).trim();
      const identifier = element("input", { id: `trigger-${kind}-id`, type: "text", maxlength: "64", value: savedIdentifier || homeAssistantId(name.value), placeholder: t("editor.haId"), "data-autogenerated": savedIdentifier ? "false" : "true" });
      name.addEventListener("input", () => { if (identifier.dataset.autogenerated === "true") identifier.value = homeAssistantId(name.value); });
      identifier.addEventListener("input", () => { identifier.dataset.autogenerated = "false"; });
      body.append(element("label", { for: profile.id, text: t("editor.mqttConfig") }), profile, element("label", { for: name.id, text: t("editor.haName") }), name, element("label", { for: identifier.id, text: t("editor.haId") }), identifier);
      row._triggerValue = () => ({ kind, profile_id: profile.value, ha_name: name.value, ha_id: identifier.value });
    }
    const remove = element("button", { class: "icon-button", type: "button", text: "×", title: t("editor.removeTrigger"), "aria-label": t("editor.removeTrigger"), onclick: () => { row.remove(); refresh(); } });
    row.append(index, body, remove);
    list.append(row);
    refresh();
  }

  root._triggerValue = () => [...list.children].map(row => row._triggerValue());
  for (const trigger of safeArray(current)) addTrigger(trigger);
  root.append(list, element("div", { class: "button-row trigger-add" }, [addButton]));
  refresh();
  return root;
}

async function openSlotEditor(row, target) {
  state.slotEditor = { row, target, actionId: "", refreshActionId: "" };
  document.querySelector("#slot-title").textContent = t("dialog.configureStep");
  document.querySelector("#slot-description").hidden = true;
  document.querySelector("#slot-fields").replaceChildren(element("p", { class: "muted", text: t("editor.discovering") }));
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
    document.querySelector("#slot-title").textContent = sourceText(safeText(form.title, t("dialog.configureStep")));
    const loading = form.state === "loading";
    const ready = form.state === "ready";
    const description = document.querySelector("#slot-description");
    description.textContent = sourceText(safeText(form.description));
    description.hidden = loading || !description.textContent;
    const container = document.querySelector("#slot-fields");
    container.replaceChildren();
    if (loading) {
      container.append(element("p", { class: "muted", text: t("editor.discovering") }));
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
    target.textContent = sourceText(safeText(error.message, t("error.pluginConfig")));
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
  document.querySelector("#slot-fields").replaceChildren(element("p", { class: "muted", text: t("editor.discovering") }));
  try {
    await nativeRequest("slot.action", { target: editor.target, action_id: editor.refreshActionId, values: {} });
    await loadSlotEditor();
  } catch (error) {
    const target = document.querySelector("#slot-error"); target.textContent = sourceText(safeText(error.message, t("error.pluginDiscovery"))); target.hidden = false; target.focus();
    refresh.disabled = false;
  }
}

async function saveSlotEditor(event) {
  event.preventDefault();
  if (event.submitter?.value === "cancel") { slotDialog.close("cancel"); state.slotEditor = null; return; }
  if (!state.slotEditor || !slotForm.reportValidity()) return;
  const save = document.querySelector("#slot-save");
  save.disabled = true; save.textContent = t("common.saving");
  const values = {};
  for (const input of document.querySelector("#slot-fields").querySelectorAll("[name]")) values[input.getAttribute("name")] = input.dataset.hotkey !== undefined ? JSON.parse(input.dataset.hotkey) : input.type === "checkbox" ? input.checked : input.type === "number" && input.value !== "" ? Number(input.value) : input.selectedOptions?.[0]?.dataset.json === "true" ? JSON.parse(input.value) : input.value;
  try {
    const result = await nativeRequest("slot.save", { target: state.slotEditor.target, action_id: state.slotEditor.actionId, values });
    state.slotEditor.row._parameters = result.parameters || {};
    state.slotEditor.row._summary.textContent = sourceText(safeText(result.summary));
    state.slotEditor.row._summary.hidden = !state.slotEditor.row._summary.textContent;
    slotDialog.close(); state.slotEditor = null;
  } catch (error) {
    const target = document.querySelector("#slot-error"); target.textContent = sourceText(safeText(error.message, t("error.save"))); target.hidden = false; target.focus();
  } finally { save.disabled = false; save.textContent = t("dialog.saveStep"); }
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
  save.disabled = true; save.textContent = t("common.saving");
  if (state.editor.kind === "route-wizard") {
    try { await advanceRouteWizard(); }
    catch (error) {
      const target = document.querySelector("#editor-error");
      target.textContent = sourceText(safeText(error.message, t("error.save"))); target.hidden = false; target.focus();
    } finally { save.disabled = false; }
    return;
  }
  const values = collectEditorValues();
  if (state.editor.kind === "signal" && !safeArray(values.triggers).length) {
    const target = document.querySelector("#editor-error");
    target.textContent = t("error.addTrigger"); target.hidden = false; target.focus();
    save.disabled = false; save.textContent = t("common.save");
    return;
  }
  const keyboardTrigger = safeArray(values.triggers).find(trigger => trigger.kind === "keyboard");
  if (state.editor.kind === "signal" && keyboardTrigger && !keyboardTrigger.hotkey) {
    const target = document.querySelector("#editor-error");
    target.textContent = t("error.addHotkey"); target.hidden = false; target.focus();
    save.disabled = false; save.textContent = t("common.save");
    return;
  }
  const trayTrigger = safeArray(values.triggers).find(trigger => trigger.kind === "tray");
  if (state.editor.kind === "signal" && trayTrigger && !safeText(trayTrigger.label).trim()) {
    const target = document.querySelector("#editor-error");
    target.textContent = t("error.trayText"); target.hidden = false; target.focus();
    save.disabled = false; save.textContent = t("common.save");
    return;
  }
  const mqttTrigger = safeArray(values.triggers).find(trigger => trigger.kind === "mqtt");
  if (state.editor.kind === "signal" && mqttTrigger && (!safeText(mqttTrigger.profile_id).trim() || !safeText(mqttTrigger.ha_name).trim() || !safeText(mqttTrigger.ha_id).trim())) {
    const target = document.querySelector("#editor-error");
    target.textContent = t("error.mqttTrigger"); target.hidden = false; target.focus();
    save.disabled = false; save.textContent = t("common.save");
    return;
  }
  if (state.editor.kind === "signal" && !safeArray(values.slots).length) {
    const target = document.querySelector("#editor-error");
    target.textContent = t("error.addStep"); target.hidden = false; target.focus();
    save.disabled = false; save.textContent = t("common.save");
    return;
  }
  try {
    const result = await nativeRequest(state.editor.method, { id: state.editor.id, values });
    const createdRouteId = state.editor.kind === "route" && !state.editor.id && typeof result?.route_id === "string" ? result.route_id : null;
    editorDialog.close(); state.revision = -1; await pollSnapshot();
    if (createdRouteId) {
      const createdRoute = safeArray(state.snapshot?.routes).find(route => route.id === createdRouteId);
      if (createdRoute) openEntityEditor("route", createdRoute);
    }
  } catch (error) {
    const target = document.querySelector("#editor-error"); target.textContent = sourceText(safeText(error.message, t("error.save"))); target.hidden = false; target.focus();
  } finally { save.disabled = false; save.textContent = t("common.save"); }
}

function confirmAction(title, message, method, params) {
  document.querySelector("#confirm-title").textContent = title;
  document.querySelector("#confirm-message").textContent = message;
  confirmDialog.dataset.method = method;
  confirmDialog.dataset.params = JSON.stringify(params);
  confirmDialog.showModal();
}

async function execute(method, params) {
  showNotice(t("common.applying"));
  try { await nativeRequest(method, params); state.revision = -1; await pollSnapshot(); }
  catch (error) { showNotice(sourceText(safeText(error.message, t("error.operation"))), true); }
}

async function exportConfiguration() {
  try {
    const picker = window.pywebview?.api?.pick_save_file;
    if (typeof picker !== "function") throw new Error(t("error.bridge"));
    const directory = safeText(state.snapshot?.settings?.configuration_directory);
    const choice = await window.pywebview.api.pick_save_file({ title: `${t("settings.export")} FenSoundSwitch`, directory, filename: "FenSoundSwitch.fsc", file_types: [`FenSoundSwitch ${t("settings.configuration")} (*.fsc)`] });
    if (choice?.ok && choice.result) await execute("config.export", { path: choice.result });
    else if (choice?.ok === false) showNotice(t("error.fileDialog"), true);
  } catch (error) { showNotice(sourceText(safeText(error?.message, t("error.operation"))), true); }
}

async function importConfiguration() {
  const directory = safeText(state.snapshot?.settings?.configuration_directory);
  const choice = await window.pywebview.api.pick_open_file({ title: `${t("settings.import")} FenSoundSwitch`, directory, file_types: [`FenSoundSwitch ${t("settings.configuration")} (*.fsc)`] });
  if (choice?.ok && choice.result) confirmAction(t("settings.importTitle"), t("settings.restoreMessage"), "config.import", { path: choice.result });
  else if (choice?.ok === false) showNotice(t("error.fileDialog"), true);
}

document.querySelector("#navigation").addEventListener("click", event => { const button = event.target.closest("[data-page]"); if (button) switchPage(button.dataset.page); });
document.querySelector(".diagnostics-link").addEventListener("click", () => switchPage("diagnostics"));
primaryAction.addEventListener("click", () => { if (state.page === "routes") openRouteWizard(); else if (state.page === "actions") openEntityEditor("signal", null); });
editorForm.addEventListener("submit", saveEditor);
slotForm.addEventListener("submit", saveSlotEditor);
mqttProfileForm.addEventListener("submit", saveMqttProfile);
document.querySelector("#mqtt-add").addEventListener("click", () => editMqttProfile(null));
document.querySelector("#mqtt-back").addEventListener("click", showMqttProfileList);
document.querySelector("#mqtt-profile-cancel").addEventListener("click", showMqttProfileList);
document.querySelector("#mqtt-close").addEventListener("click", () => mqttDialog.close());
document.querySelector("#slot-refresh").addEventListener("click", refreshSlotEditor);
for (const button of document.querySelectorAll("#editor-close, #editor-cancel")) button.addEventListener("click", () => { editorDialog.close("cancel"); state.editor = null; });
document.querySelector("#editor-back").addEventListener("click", () => {
  if (state.editor?.kind !== "route-wizard" || state.editor.step === 0) return;
  state.editor.step -= 1;
  renderRouteWizard();
});
for (const button of document.querySelectorAll("#slot-close, #slot-cancel")) button.addEventListener("click", () => { slotDialog.close("cancel"); state.slotEditor = null; });
for (const button of document.querySelectorAll("#choice-close, #choice-cancel")) button.addEventListener("click", () => { choiceDialog.close("cancel"); state.choice = null; });
confirmDialog.addEventListener("close", () => { if (confirmDialog.returnValue === "confirm") execute(confirmDialog.dataset.method, JSON.parse(confirmDialog.dataset.params || "{}")); });
editorDialog.addEventListener("close", () => { state.editor = null; delete editorDialog.dataset.kind; });
slotDialog.addEventListener("close", () => { state.slotEditor = null; });
choiceDialog.addEventListener("close", () => { state.choice = null; });
mqttDialog.addEventListener("close", () => { state.mqttProfileId = null; showMqttProfileList(); });
document.addEventListener("click", event => {
  for (const menu of document.querySelectorAll(".split-menu")) {
    if (menu.contains(event.target)) continue;
    state.importMenuOpen = false;
    const items = menu.querySelector(".split-menu-items");
    const arrow = menu.querySelector(".split-arrow");
    if (items) items.hidden = true;
    if (arrow) arrow.setAttribute("aria-expanded", "false");
  }
});
setLanguage(["en", "de", "es", "fr", "it"].includes(navigator.language?.split("-")[0]) ? navigator.language.split("-")[0] : "en");
applyStaticTranslations();
document.querySelector("#page-title").textContent = t(pages[state.page][0]);
document.querySelector("#page-description").textContent = t(pages[state.page][1]);
primaryAction.textContent = t(pages[state.page][2]);
window.addEventListener("pywebviewready", pollSnapshot, { once: true });
// WebKit can expose the bridge shortly after document load without reliably
// delivering pywebviewready to inline documents. Retry the initial snapshot
// until the native API is attached.
window.setTimeout(() => {
  if (!state.snapshot) pollSnapshot();
}, 300);
