/**
 * Browser entrypoint for the public Zenbot web shell.
 *
 * This file coordinates four UI surfaces:
 * - koan listing and case pages backed by `/static/mmnk.json`
 * - the main chatter UI backed by `/chat`, `/chat_case/<id>`, and `/save_chat`
 * - browser session state mirrored between cookies and localStorage
 * - session defaults and locked snapshots supplied by the backend
 */

const SESSION_SETTINGS_STORAGE_KEY = "zenbot.session_settings";
const ACTIVE_SESSION_SETTINGS_STORAGE_KEY = "zenbot.active_session_settings";

let chatOptionsPromise = null;

function embeddedChatOptions() {
  const options = window.PUBLIC_SESSION_OPTIONS;
  return options && typeof options === "object" ? options : null;
}

function showLoadingSpinner() {
  const spinnerOverlay = document.createElement("div");
  spinnerOverlay.className = "spinner-overlay";
  spinnerOverlay.innerHTML = '<div class="spinner"></div>';
  const container = document.getElementById("spinnerContainer") || document.body;
  container.appendChild(spinnerOverlay);
}

function hideLoadingSpinner() {
  const spinnerOverlay = document.querySelector(".spinner-overlay");
  if (spinnerOverlay) {
    spinnerOverlay.remove();
  }
}

function handleError(error, elementId, message) {
  console.error("Error:", error);
  const targetElement = document.getElementById(elementId);
  if (targetElement) {
    targetElement.innerHTML = `<p><b>Error:</b> ${message}<br><b>Details:</b> ${escapeHtml(error?.message || String(error))}</p>`;
  }
}

function getCookie(name) {
  const value = `; ${document.cookie}`;
  const parts = value.split(`; ${name}=`);
  if (parts.length === 2) return parts.pop().split(";").shift();
}

function setCookie(name, value) {
  document.cookie = `${name}=${value}; path=/`;
}

function clearCookie(name) {
  document.cookie = `${name}=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT`;
}

function getSessionValue(key) {
  const fromStorage = window.localStorage.getItem(key);
  if (fromStorage) return fromStorage;
  return getCookie(key) || "";
}

function setSessionValue(key, value) {
  if (!value) return;
  window.localStorage.setItem(key, value);
  setCookie(key, value);
}

function clearSessionValue(key) {
  window.localStorage.removeItem(key);
  clearCookie(key);
}

function getJsonStorage(key) {
  const raw = window.localStorage.getItem(key);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch (_error) {
    window.localStorage.removeItem(key);
    return null;
  }
}

function setJsonStorage(key, value) {
  if (!value) {
    window.localStorage.removeItem(key);
    return;
  }
  window.localStorage.setItem(key, JSON.stringify(value));
}

function clearJsonStorage(key) {
  window.localStorage.removeItem(key);
}

function getRememberedSessionSettings() {
  return getJsonStorage(SESSION_SETTINGS_STORAGE_KEY);
}

function setRememberedSessionSettings(settings) {
  setJsonStorage(SESSION_SETTINGS_STORAGE_KEY, settings);
}

function getActiveSessionSettings() {
  return getJsonStorage(ACTIVE_SESSION_SETTINGS_STORAGE_KEY);
}

function setActiveSessionSettings(settings) {
  setJsonStorage(ACTIVE_SESSION_SETTINGS_STORAGE_KEY, settings);
}

function clearActiveSessionSettings() {
  clearJsonStorage(ACTIVE_SESSION_SETTINGS_STORAGE_KEY);
}

function escapeHtml(unsafe) {
  return String(unsafe)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function renderMarkdownSafe(markdownText) {
  if (typeof marked === "undefined") {
    return escapeHtml(markdownText);
  }
  return marked.parse(escapeHtml(markdownText));
}

function appendThinkingIndicator() {
  const chatResults = document.getElementById("chatResults");
  if (!chatResults) return null;

  const div = document.createElement("div");
  div.className = "zenbot-thinking";
  div.innerHTML = `
    <div class="zenbot-thinking-label">Mumonbot is considering your words</div>
    <div class="zenbot-thinking-breath" aria-hidden="true">
      <span></span><span></span><span></span>
    </div>
  `;
  chatResults.appendChild(div);
  chatResults.scrollTop = chatResults.scrollHeight;
  return div;
}

function setPrefacePanelVisibility(visible) {
  const prefacePanel = document.getElementById("chatPrefacePanel");
  if (!prefacePanel) return;
  prefacePanel.hidden = !visible;
}

function removeThinkingIndicator(node) {
  if (node && node.parentNode) {
    node.parentNode.removeChild(node);
  }
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let details = `${response.status}`;
    try {
      const payload = await response.json();
      if (payload?.message) details = payload.message;
      else if (payload?.error) details = payload.error;
    } catch (_error) {
      try {
        details = await response.text();
      } catch (_ignored) {
        details = `${response.status}`;
      }
    }
    throw new Error(details || `HTTP ${response.status}`);
  }
  return response.json();
}

async function getChatOptions() {
  const embedded = embeddedChatOptions();
  if (embedded) {
    return embedded;
  }
  if (!chatOptionsPromise) {
    chatOptionsPromise = fetchJson("/chat/options", {
      method: "GET",
      credentials: "include",
    });
  }
  return chatOptionsPromise;
}

function findPresetOption(options, presetId) {
  return (options?.presets || []).find((preset) => preset.id === presetId) || null;
}

function findModelOption(options, modelName) {
  return (options?.models || []).find((model) => model.id === modelName) || null;
}

function sanitizeSessionSettings(settings, options) {
  const defaults = options?.defaults || {};
  const incoming = settings && typeof settings === "object" ? settings : {};
  const resolved = {
    preset_id: String(incoming.preset_id || defaults.preset_id || "").trim(),
    model_name: String(incoming.model_name || defaults.model_name || "").trim(),
    reasoning_effort: String(
      incoming.reasoning_effort || defaults.reasoning_effort || ""
    ).trim(),
    temperature: incoming.temperature ?? defaults.temperature ?? null,
    top_p: incoming.top_p ?? defaults.top_p ?? null,
    max_output_tokens:
      incoming.max_output_tokens ?? defaults.max_output_tokens ?? "",
    enable_function_tools:
      incoming.enable_function_tools ?? defaults.enable_function_tools ?? false,
    enable_file_search:
      incoming.enable_file_search ?? defaults.enable_file_search ?? false,
    enable_web_search:
      incoming.enable_web_search ?? defaults.enable_web_search ?? false,
    enable_background_critic:
      incoming.enable_background_critic ??
      defaults.enable_background_critic ??
      false,
  };

  const model = findModelOption(options, resolved.model_name) || {
    supports_reasoning: false,
    supports_sampling_controls: false,
  };
  if (!model.supports_reasoning) {
    resolved.reasoning_effort = "";
  }
  if (!model.supports_sampling_controls) {
    resolved.temperature = null;
    resolved.top_p = null;
  }
  return resolved;
}

function sessionSettingsSummary(settings, options) {
  const resolved = sanitizeSessionSettings(settings, options);
  const preset = findPresetOption(options, resolved.preset_id);
  const enabledTools = [
    resolved.enable_function_tools ? "local tools" : "",
    resolved.enable_file_search ? "file search" : "",
    resolved.enable_web_search ? "web search" : "",
    resolved.enable_background_critic ? "background critic" : "",
  ].filter(Boolean);
  const parts = [
    preset ? preset.label : resolved.preset_id,
    resolved.model_name,
    resolved.reasoning_effort ? `reasoning ${resolved.reasoning_effort}` : "",
    resolved.temperature !== null ? `temp ${resolved.temperature}` : "",
    resolved.top_p !== null ? `top_p ${resolved.top_p}` : "",
    resolved.max_output_tokens ? `${resolved.max_output_tokens} max tokens` : "",
    enabledTools.length ? enabledTools.join(", ") : "no extra tools",
  ].filter(Boolean);
  return parts.join(" • ");
}

function getSessionSettingsElements() {
  return {
    panel: document.getElementById("sessionSettingsPanel"),
    status: document.getElementById("sessionSettingsStatus"),
    summary: document.getElementById("sessionSettingsSummary"),
    preset: document.getElementById("sessionPreset"),
    model: document.getElementById("sessionModel"),
    reasoningWrap: document.getElementById("sessionReasoningWrap"),
    reasoning: document.getElementById("sessionReasoning"),
    temperatureWrap: document.getElementById("sessionTemperatureWrap"),
    temperature: document.getElementById("sessionTemperature"),
    topPWrap: document.getElementById("sessionTopPWrap"),
    topP: document.getElementById("sessionTopP"),
    maxTokens: document.getElementById("sessionMaxTokens"),
    enableFunctionTools: document.getElementById("sessionEnableFunctionTools"),
    enableFileSearch: document.getElementById("sessionEnableFileSearch"),
    enableWebSearch: document.getElementById("sessionEnableWebSearch"),
    enableBackgroundCritic: document.getElementById("sessionEnableBackgroundCritic"),
  };
}

function hasSessionSettingsPanel() {
  return Boolean(document.getElementById("sessionSettingsPanel"));
}

function sessionSettingsInputElements(elements) {
  return [
    elements.preset,
    elements.model,
    elements.reasoning,
    elements.temperature,
    elements.topP,
    elements.maxTokens,
    elements.enableFunctionTools,
    elements.enableFileSearch,
    elements.enableWebSearch,
    elements.enableBackgroundCritic,
  ].filter(Boolean);
}

function setSessionSettingsStatus(elements, text, tone = "neutral") {
  if (!elements.status) return;
  elements.status.textContent = text;
  elements.status.dataset.tone = tone;
}

function isSessionSettingsLocked(elements) {
  return elements.panel?.dataset.locked === "true";
}

function setSessionSettingsLocked(elements, locked) {
  if (!elements.panel) return;
  elements.panel.dataset.locked = locked ? "true" : "false";
  elements.panel.classList.toggle("is-locked", locked);
}

function updateSelectOptions(select, values, selectedValue, formatter = null) {
  if (!select) return;
  select.innerHTML = "";
  values.forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = formatter ? formatter(value) : value;
    if (String(value) === String(selectedValue)) {
      option.selected = true;
    }
    select.appendChild(option);
  });
}

function populateSessionSettingsPanel(options) {
  const elements = getSessionSettingsElements();
  if (!elements.panel) return elements;

  updateSelectOptions(
    elements.preset,
    (options.presets || []).map((preset) => preset.id),
    options.defaults?.preset_id || "",
    (presetId) => {
      const preset = findPresetOption(options, presetId);
      return preset ? preset.label : presetId;
    }
  );
  updateSelectOptions(
    elements.model,
    (options.models || []).map((model) => model.id),
    options.defaults?.model_name || "",
    (modelId) => {
      const model = findModelOption(options, modelId);
      return model ? model.label : modelId;
    }
  );
  return elements;
}

function readNumericInputValue(input) {
  if (!input) return null;
  const raw = String(input.value || "").trim();
  if (!raw) return null;
  const numeric = Number(raw);
  return Number.isFinite(numeric) ? numeric : null;
}

function readIntegerInputValue(input) {
  if (!input) return null;
  const raw = String(input.value || "").trim();
  if (!raw) return null;
  const numeric = Number.parseInt(raw, 10);
  return Number.isFinite(numeric) ? numeric : null;
}

function readSessionSettingsFromPanel(options) {
  const elements = getSessionSettingsElements();
  if (!elements.panel) {
    return sanitizeSessionSettings(options?.defaults || {}, options);
  }
  return sanitizeSessionSettings(
    {
      preset_id: elements.preset?.value || "",
      model_name: elements.model?.value || "",
      reasoning_effort: elements.reasoning?.value || "",
      temperature: readNumericInputValue(elements.temperature),
      top_p: readNumericInputValue(elements.topP),
      max_output_tokens: readIntegerInputValue(elements.maxTokens),
      enable_function_tools: Boolean(elements.enableFunctionTools?.checked),
      enable_file_search: Boolean(elements.enableFileSearch?.checked),
      enable_web_search: Boolean(elements.enableWebSearch?.checked),
      enable_background_critic: Boolean(elements.enableBackgroundCritic?.checked),
    },
    options
  );
}

function applySessionSettingsToPanel(settings, options) {
  const elements = populateSessionSettingsPanel(options);
  if (!elements.panel) return;

  const resolved = sanitizeSessionSettings(settings, options);
  if (elements.preset) elements.preset.value = resolved.preset_id || "";
  if (elements.model) elements.model.value = resolved.model_name || "";
  if (elements.maxTokens) {
    elements.maxTokens.value = String(resolved.max_output_tokens || "");
  }
  if (elements.temperature) {
    elements.temperature.value =
      resolved.temperature === null ? "" : String(resolved.temperature);
  }
  if (elements.topP) {
    elements.topP.value = resolved.top_p === null ? "" : String(resolved.top_p);
  }
  if (elements.enableFunctionTools) {
    elements.enableFunctionTools.checked = Boolean(resolved.enable_function_tools);
  }
  if (elements.enableFileSearch) {
    elements.enableFileSearch.checked = Boolean(resolved.enable_file_search);
  }
  if (elements.enableWebSearch) {
    elements.enableWebSearch.checked = Boolean(resolved.enable_web_search);
  }
  if (elements.enableBackgroundCritic) {
    elements.enableBackgroundCritic.checked = Boolean(
      resolved.enable_background_critic
    );
  }

  const model = findModelOption(options, resolved.model_name);
  const reasoningChoices =
    model?.supports_reasoning && Array.isArray(model.reasoning_efforts)
      ? model.reasoning_efforts
      : [];
  if (elements.reasoning) {
    updateSelectOptions(
      elements.reasoning,
      reasoningChoices.length ? reasoningChoices : [""],
      resolved.reasoning_effort || "",
      (value) => value || "default"
    );
  }

  refreshSessionSettingsPanel(options);
}

function updateToolToggleAvailability(elements, options, locked) {
  const caps = options?.tool_caps || {};
  const items = [
    {
      input: elements.enableFunctionTools,
      cap: Boolean(caps.enable_function_tools),
      title: "Local Zenbot function tools",
    },
    {
      input: elements.enableFileSearch,
      cap: Boolean(caps.enable_file_search),
      title: "OpenAI file search",
    },
    {
      input: elements.enableWebSearch,
      cap: Boolean(caps.enable_web_search),
      title: "OpenAI web search",
    },
    {
      input: elements.enableBackgroundCritic,
      cap: Boolean(caps.enable_background_critic),
      title: "Background critic",
    },
  ];

  items.forEach(({ input, cap, title }) => {
    if (!input) return;
    input.disabled = locked || !cap;
    const wrapper = input.closest(".session-toggle");
    if (wrapper) {
      wrapper.classList.toggle("is-disabled", !cap);
      wrapper.title = cap ? title : `${title} unavailable in this deployment`;
    }
    if (!cap) {
      input.checked = false;
    }
  });
}

function refreshSessionSettingsPanel(options) {
  const elements = getSessionSettingsElements();
  if (!elements.panel) return;

  const locked = isSessionSettingsLocked(elements);
  const current = readSessionSettingsFromPanel(options);
  const model = findModelOption(options, current.model_name) || {
    supports_reasoning: false,
    reasoning_efforts: [],
    supports_sampling_controls: false,
  };
  if (elements.reasoning) {
    const reasoningChoices =
      model.supports_reasoning && Array.isArray(model.reasoning_efforts)
        ? model.reasoning_efforts
        : [""];
    const existingValues = Array.from(elements.reasoning.options).map(
      (option) => option.value
    );
    const desiredValues = reasoningChoices.map((value) => String(value));
    if (existingValues.join("|") !== desiredValues.join("|")) {
      updateSelectOptions(
        elements.reasoning,
        reasoningChoices,
        current.reasoning_effort || "",
        (value) => value || "default"
      );
    }
  }

  if (elements.reasoningWrap) {
    elements.reasoningWrap.hidden = !model.supports_reasoning;
  }
  if (elements.temperatureWrap) {
    elements.temperatureWrap.hidden = !model.supports_sampling_controls;
  }
  if (elements.topPWrap) {
    elements.topPWrap.hidden = !model.supports_sampling_controls;
  }

  sessionSettingsInputElements(elements).forEach((input) => {
    if (!input) return;
    input.disabled = locked;
  });
  updateToolToggleAvailability(elements, options, locked);

  if (elements.reasoning) {
    elements.reasoning.disabled = locked || !model.supports_reasoning;
  }
  if (elements.temperature) {
    elements.temperature.disabled = locked || !model.supports_sampling_controls;
  }
  if (elements.topP) {
    elements.topP.disabled = locked || !model.supports_sampling_controls;
  }

  if (elements.summary) {
    elements.summary.textContent = sessionSettingsSummary(current, options);
  }
}

function sessionStatusText(settings, locked, pendingSnapshot) {
  if (pendingSnapshot) {
    return "Active session detected. Settings are locked; the saved server snapshot will appear after the next reply.";
  }
  if (locked) {
    if (settings?.enable_function_tools) {
      return "Locked to this session. Local function tools are enabled, so replies arrive after tool work completes. Reset to change settings.";
    }
    return "Locked to this session. Reset to change model or tool settings.";
  }
  if (settings?.enable_function_tools) {
    return "Session Setup will lock on first turn. Local function tools are enabled, so replies arrive as one completed chunk.";
  }
  return "Session Setup will lock on first turn. Reset starts a fresh session.";
}

function setPanelFromRememberedOrDefaults(options) {
  const remembered = getRememberedSessionSettings();
  applySessionSettingsToPanel(remembered || options.defaults || {}, options);
  const elements = getSessionSettingsElements();
  setSessionSettingsLocked(elements, false);
  refreshSessionSettingsPanel(options);
  setSessionSettingsStatus(
    elements,
    sessionStatusText(readSessionSettingsFromPanel(options), false, false),
    "neutral"
  );
}

function setPanelToLockedSnapshot(settings, options) {
  const elements = getSessionSettingsElements();
  applySessionSettingsToPanel(settings, options);
  setSessionSettingsLocked(elements, true);
  refreshSessionSettingsPanel(options);
  setSessionSettingsStatus(
    elements,
    sessionStatusText(sanitizeSessionSettings(settings, options), true, false),
    "locked"
  );
}

function setPanelToPendingLockedState(options) {
  const elements = getSessionSettingsElements();
  const remembered = getRememberedSessionSettings() || options.defaults || {};
  applySessionSettingsToPanel(remembered, options);
  setSessionSettingsLocked(elements, true);
  refreshSessionSettingsPanel(options);
  if (elements.summary) {
    elements.summary.textContent = "server snapshot pending";
  }
  setSessionSettingsStatus(
    elements,
    sessionStatusText(remembered, true, true),
    "locked"
  );
}

function persistRememberedPanelSettings(options) {
  const settings = readSessionSettingsFromPanel(options);
  setRememberedSessionSettings(settings);
  const elements = getSessionSettingsElements();
  if (!isSessionSettingsLocked(elements)) {
    setSessionSettingsStatus(
      elements,
      sessionStatusText(settings, false, false),
      "neutral"
    );
  }
}

function adoptLockedSessionSettings(settings, options) {
  const resolved = sanitizeSessionSettings(settings, options);
  setActiveSessionSettings(resolved);
  if (hasSessionSettingsPanel()) {
    setRememberedSessionSettings(resolved);
  }
  setPanelToLockedSnapshot(resolved, options);
}

function unlockSessionSettings(options) {
  clearActiveSessionSettings();
  setPanelFromRememberedOrDefaults(options);
}

function settingsPayloadForRequest(options) {
  const conversationId = getSessionValue("conversation_id");
  const active = getActiveSessionSettings();
  if (conversationId) {
    return active ? sanitizeSessionSettings(active, options) : null;
  }
  return readSessionSettingsFromPanel(options);
}

function bindSessionSettingsPanel(options) {
  const elements = populateSessionSettingsPanel(options);
  if (!elements.panel) return;
  if (elements.panel.dataset.bound === "true") return;
  elements.panel.dataset.bound = "true";

  const refreshAndRemember = () => {
    refreshSessionSettingsPanel(options);
    if (!isSessionSettingsLocked(elements)) {
      persistRememberedPanelSettings(options);
    }
  };

  if (elements.preset) {
    elements.preset.addEventListener("change", () => {
      const preset = findPresetOption(options, elements.preset.value);
      if (!preset) return;
      applySessionSettingsToPanel(preset.settings, options);
      persistRememberedPanelSettings(options);
    });
  }

  if (elements.model) {
    elements.model.addEventListener("change", refreshAndRemember);
  }

  [
    elements.reasoning,
    elements.temperature,
    elements.topP,
    elements.maxTokens,
    elements.enableFunctionTools,
    elements.enableFileSearch,
    elements.enableWebSearch,
    elements.enableBackgroundCritic,
  ]
    .filter(Boolean)
    .forEach((input) => {
      input.addEventListener("change", refreshAndRemember);
      input.addEventListener("input", refreshAndRemember);
    });
}

async function ensureSessionSettingsPanel() {
  const panel = document.getElementById("sessionSettingsPanel");
  if (!panel) return null;

  const options = await getChatOptions();
  bindSessionSettingsPanel(options);

  const conversationId = getSessionValue("conversation_id");
  const active = getActiveSessionSettings();
  if (conversationId && active) {
    setPanelToLockedSnapshot(active, options);
  } else if (conversationId) {
    setPanelToPendingLockedState(options);
  } else {
    clearActiveSessionSettings();
    setPanelFromRememberedOrDefaults(options);
  }
  return options;
}

function setSaveButtonState() {
  const saveChatButton = document.getElementById("chatSave");
  if (saveChatButton) {
    saveChatButton.disabled = !getSessionValue("conversation_id");
  }
}

function appendChatMessage(sender, message) {
  const chatResults = document.getElementById("chatResults");
  if (!chatResults) return;

  const messageDiv = document.createElement("div");
  messageDiv.className = sender === "Student" ? "user-message" : "zenbot-message";

  try {
    const text = String(message ?? "");
    const htmlContent = renderMarkdownSafe(text);
    messageDiv.innerHTML = `<strong>${escapeHtml(sender)}:</strong><br>${htmlContent}`;
  } catch (error) {
    messageDiv.innerHTML = `<strong>${escapeHtml(sender)}</strong> caused error:<br>${escapeHtml(error?.message || String(error))}`;
  } finally {
    chatResults.appendChild(messageDiv);
    chatResults.scrollTop = chatResults.scrollHeight;
  }
}

function renderKoanPreface(koan) {
  const preface = document.getElementById("chatPreface");
  if (!preface || !koan) return;

  preface.innerHTML = "";
  const koanDiv = document.createElement("div");
  koanDiv.className = "koan-preface";

  const titleDiv = document.createElement("div");
  const titleU = document.createElement("u");
  titleU.textContent = `Case #${koan.id}: ${koan.title}`;
  titleDiv.appendChild(titleU);

  const bodyDiv = document.createElement("div");
  bodyDiv.textContent = koan.body;

  koanDiv.appendChild(titleDiv);
  koanDiv.appendChild(bodyDiv);
  preface.appendChild(koanDiv);
  setPrefacePanelVisibility(true);
}

async function loadKoanPreface(caseId) {
  if (!caseId) return;
  try {
    const response = await fetch("/static/mmnk.json");
    const data = await response.json();
    const matching = data.cases.find((k) => k.id.toString() === caseId.toString());
    if (matching) {
      renderKoanPreface(matching);
    }
  } catch (error) {
    console.error("Failed to load Koan in chatter:", error);
  }
}

function clearChatDom() {
  const chatResults = document.getElementById("chatResults");
  const chatInput = document.getElementById("chatInput");
  const chatPreface = document.getElementById("chatPreface");
  if (chatResults) chatResults.innerHTML = "";
  if (chatInput) chatInput.value = "";
  if (chatPreface) chatPreface.innerHTML = "";
  setPrefacePanelVisibility(false);
}

async function resetChatUi() {
  clearSessionValue("conversation_id");
  clearSessionValue("case_id");
  clearActiveSessionSettings();
  clearChatDom();
  setSaveButtonState();
  window.history.replaceState(null, "", "/chatter");
  try {
    const options = await getChatOptions();
    unlockSessionSettings(options);
  } catch (error) {
    console.error("Failed to restore unlocked session settings:", error);
  }
}

function initGGList() {
  showLoadingSpinner();
  fetch("/static/mmnk.json")
    .then((response) => response.json())
    .then((data) => {
      const tocDiv = document.getElementById("caseList");
      if (!tocDiv) return;
      tocDiv.innerHTML = "";
      const ul = document.createElement("ul");
      ul.className = "gg-case-grid";
      data.cases.forEach((koan) => {
        const li = document.createElement("li");
        const a = document.createElement("a");
        a.href = "/gg/" + koan.id;
        a.textContent = koan.id + ". " + koan.title;
        li.appendChild(a);
        ul.appendChild(li);
      });
      tocDiv.appendChild(ul);
    })
    .catch((error) => {
      console.error("Error loading JSON:", error);
      const tocDiv = document.getElementById("caseList");
      if (tocDiv) {
        tocDiv.textContent = "Failed to load cases.";
      }
    })
    .finally(() => {
      hideLoadingSpinner();
    });
}

function initGGCasePage() {
  const container = document.getElementById("koan-container");
  if (!container) return;

  let caseId = getSessionValue("case_id");
  if (!caseId) {
    caseId = container.getAttribute("data-case-id");
  }

  fetch("/static/mmnk.json")
    .then((response) => response.json())
    .then((data) => {
      const koan = data.cases.find((k) => k.id.toString() === caseId.toString());
      if (!koan) {
        container.textContent = "Koan not found with caseID " + caseId + ".";
        return;
      }
      container.innerHTML = "";

      const titleEl = document.createElement("div");
      titleEl.className = "koan-title";
      titleEl.textContent = [koan.id, koan.title].join(". ");

      const bodyEl = document.createElement("div");
      bodyEl.className = "koan-body";
      bodyEl.textContent = koan.body;

      const commentEl = document.createElement("div");
      commentEl.className = "koan-comment";
      commentEl.textContent = "Mumon's comment:\n" + koan.comment;

      const verseEl = document.createElement("div");
      verseEl.className = "koan-verse";
      verseEl.textContent = koan.verse.join("\n");

      container.appendChild(titleEl);
      container.appendChild(bodyEl);
      container.appendChild(commentEl);
      container.appendChild(verseEl);

      const discussBtn = document.createElement("button");
      discussBtn.textContent = "Bring to a Dokusan Session";
      const sessionStatus = document.createElement("div");
      sessionStatus.className = "koan-session-status";
      sessionStatus.hidden = true;

      const setSessionStatus = (text, tone = "neutral") => {
        if (!text) {
          sessionStatus.hidden = true;
          sessionStatus.textContent = "";
          delete sessionStatus.dataset.tone;
          return;
        }
        sessionStatus.hidden = false;
        sessionStatus.dataset.tone = tone;
        sessionStatus.textContent = text;
      };

      discussBtn.addEventListener("click", async () => {
        discussBtn.disabled = true;
        setSessionStatus("Opening dokusan session...", "pending");
        try {
          clearSessionValue("conversation_id");
          clearActiveSessionSettings();
          setSessionValue("case_id", String(koan.id));
          setSessionStatus("Case selected. Entering the sanzen room...", "success");
          window.location.href = "/chatter";
        } catch (err) {
          console.error("Error starting Koan chat:", err);
          setSessionStatus(
            `Unable to start dokusan session: ${err?.message || String(err)}`,
            "error"
          );
          discussBtn.disabled = false;
        }
      });

      container.appendChild(document.createElement("br"));
      container.appendChild(discussBtn);
      container.appendChild(sessionStatus);
    })
    .catch((error) => {
      console.error("Error loading JSON:", error);
      container.textContent = "Failed to load koan.";
    });
}

async function initChatterPage() {
  const caseId = getSessionValue("case_id");
  const chatInput = document.getElementById("chatInput");
  const chatButton = document.getElementById("chatSend");
  const endChatButton = document.getElementById("chatEnd");
  const saveChatButton = document.getElementById("chatSave");

  try {
    await ensureSessionSettingsPanel();
  } catch (error) {
    console.error("Unable to initialize session settings panel:", error);
  }

  if (caseId) {
    loadKoanPreface(caseId);
  }

  if (endChatButton) {
    endChatButton.addEventListener("click", clearChat);
  }

  if (saveChatButton) {
    saveChatButton.addEventListener("click", saveChat);
  }
  setSaveButtonState();

  if (chatInput) {
    chatInput.focus();
  }

  if (chatButton && chatInput) {
    chatButton.addEventListener("click", () => {
      const prompt = chatInput.value.trim();
      if (prompt) startChat(prompt);
    });

    chatInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        chatButton.click();
      }
    });
  }
}

async function parseErrorResponse(response, options) {
  const contentType = (response.headers.get("content-type") || "").toLowerCase();
  if (contentType.includes("application/json")) {
    const payload = await response.json();
    if (response.status === 409 && payload?.error === "settings_locked") {
      if (payload.session_settings && options) {
        adoptLockedSessionSettings(payload.session_settings, options);
      }
      throw new Error(
        payload?.message ||
          "This conversation is already locked to a different settings snapshot."
      );
    }
    throw new Error(payload?.message || payload?.error || `HTTP ${response.status}`);
  }
  const fallback = await response.text();
  throw new Error(`HTTP ${response.status}: ${fallback}`);
}

async function handleStreamResponse(response, thinkingNode, options) {
  const chatResults = document.getElementById("chatResults");
  const messageDiv = document.createElement("div");
  messageDiv.className = "zenbot-message";
  messageDiv.innerHTML =
    '<strong>Mumonbot:</strong><br><span class="message-content"></span>';
  const contentEl = messageDiv.querySelector(".message-content");

  if (chatResults) {
    chatResults.appendChild(messageDiv);
    chatResults.scrollTop = chatResults.scrollHeight;
  }

  let buffer = "";
  let fullText = "";
  const reader = response.body.getReader();
  const decoder = new TextDecoder();

  const handlePayload = (data) => {
    const chunk = data?.response;
    if (data?.event === "start") {
      if (data?.conversation_id) {
        setSessionValue("conversation_id", data.conversation_id);
      }
      if (data?.session_settings && options) {
        adoptLockedSessionSettings(data.session_settings, options);
      }
      setSaveButtonState();
      removeThinkingIndicator(thinkingNode);
      return;
    }

    if (data?.event === "error") {
      removeThinkingIndicator(thinkingNode);
      appendChatMessage("System", data?.error || "Chat stream failed.");
      return;
    }

    if (chunk === "[DONE]") {
      if (contentEl) contentEl.innerHTML = renderMarkdownSafe(fullText);
      return;
    }

    if (typeof chunk === "string" && chunk) {
      removeThinkingIndicator(thinkingNode);
      if (data?.event === "fallback") {
        fullText = chunk;
      } else {
        fullText += chunk;
      }
      if (contentEl) {
        contentEl.textContent = fullText;
      }
      if (chatResults) {
        chatResults.scrollTop = chatResults.scrollHeight;
      }
    }
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      buffer += decoder.decode();
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";
    events.forEach((eventBlock) => {
      eventBlock.split("\n").forEach((line) => {
        if (!line.startsWith("data:")) return;
        const payload = line.slice(5).trim();
        if (!payload) return;
        try {
          handlePayload(JSON.parse(payload));
        } catch (_error) {
          /* ignore malformed SSE payloads */
        }
      });
    });
  }

  if (buffer.trim()) {
    buffer.split("\n").forEach((line) => {
      if (!line.startsWith("data:")) return;
      const payload = line.slice(5).trim();
      if (!payload) return;
      try {
        handlePayload(JSON.parse(payload));
      } catch (_error) {
        /* ignore malformed trailing SSE payloads */
      }
    });
  }
}

async function startChat(prompt) {
  const chatButton = document.getElementById("chatSend");
  const chatInput = document.getElementById("chatInput");
  let thinkingNode = null;

  try {
    const options = await getChatOptions();
    const requestSettings = settingsPayloadForRequest(options);

    if (chatInput) chatInput.value = "";
    appendChatMessage("Student", prompt);
    if (chatButton) chatButton.disabled = true;

    thinkingNode = appendThinkingIndicator();

    const response = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: prompt,
        conversation_id: getSessionValue("conversation_id") || "",
        case_id: getSessionValue("case_id") || "",
        settings: requestSettings,
      }),
      credentials: "include",
    });

    if (!response.ok) {
      await parseErrorResponse(response, options);
    }

    const contentType = (response.headers.get("content-type") || "").toLowerCase();
    if (contentType.includes("text/event-stream")) {
      await handleStreamResponse(response, thinkingNode, options);
      thinkingNode = null;
    } else {
      const data = await response.json();
      removeThinkingIndicator(thinkingNode);
      thinkingNode = null;
      appendChatMessage("Mumonbot", data.response);
      if (data.conversation_id) {
        setSessionValue("conversation_id", data.conversation_id);
      }
      if (data.session_settings) {
        adoptLockedSessionSettings(data.session_settings, options);
      }
      setSaveButtonState();
    }

    const conversationId = getSessionValue("conversation_id");
    if (conversationId) {
      setSessionValue("conversation_id", conversationId);
    }

    if (chatInput) {
      chatInput.focus();
    }
  } catch (error) {
    removeThinkingIndicator(thinkingNode);
    if (chatInput && !chatInput.value) {
      chatInput.value = prompt;
    }
    appendChatMessage("System", `Error: ${error?.message || String(error)}`);
    handleError(error, "chatPreface", "Error starting chat.");
  } finally {
    if (chatButton) chatButton.disabled = false;
    if (chatInput) chatInput.focus();
    setSaveButtonState();
  }
}

async function clearChat() {
  const resetButton = document.getElementById("chatEnd");
  if (resetButton) {
    resetButton.disabled = true;
    setTimeout(() => {
      resetButton.disabled = false;
    }, 3000);
  }

  if (!confirm("Are you sure you want to reset? This cannot be undone.")) {
    return;
  }
  await resetChatUi();
}

async function saveChat() {
  const conversationId = getSessionValue("conversation_id");
  if (!conversationId) {
    alert("There is no conversation to save!");
    return;
  }

  try {
    showLoadingSpinner();
    const response = await fetch("/save_chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        conversation_id: conversationId,
        case_id: getSessionValue("case_id") || "",
      }),
      credentials: "include",
    });

    const data = await response.json();
    if (response.ok && data.status === "success") {
      alert("Chat successfully saved.");
      await resetChatUi();
    } else {
      alert(`Error saving chat: ${data.error || response.statusText}`);
    }
  } catch (error) {
    handleError(error, "chatPreface", "Error saving chat.");
  } finally {
    hideLoadingSpinner();
  }
}

document.addEventListener("DOMContentLoaded", () => {
  if (document.getElementById("caseList")) {
    initGGList();
  } else if (document.getElementById("koan-container")) {
    initGGCasePage();
  } else if (document.getElementById("chatResults")) {
    initChatterPage();
  }
});
