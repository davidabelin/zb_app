/***************************************************************
 * scripts.js
 ***************************************************************/

const CHAT_API_BASE = (window.CHAT_API_BASE_URL || "").replace(/\/$/, "");

function apiUrl(path) {
  if (!CHAT_API_BASE) return path;
  return `${CHAT_API_BASE}${path}`;
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
    targetElement.innerHTML = `<p><b>Error:</b> ${message}<br><b>Details:</b> ${error}</p>`;
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

function removeThinkingIndicator(node) {
  if (node && node.parentNode) {
    node.parentNode.removeChild(node);
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
      discussBtn.addEventListener("click", async () => {
        try {
          const response = await fetch(apiUrl(`/chat_case/${koan.id}`), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ case_id: caseId }),
            credentials: "include",
          });
          if (!response.ok) {
            throw new Error(`Server responded with status: ${response.status}`);
          }

          const res = await response.json();
          if (res.error) {
            throw new Error(res.error);
          }

          setSessionValue("conversation_id", res.conversation_id);
          setSessionValue("case_id", res.case_id);
          window.location.href = "/chatter";
        } catch (err) {
          console.error("Error starting Koan chat:", err);
        }
      });

      container.appendChild(document.createElement("br"));
      container.appendChild(discussBtn);
    })
    .catch((error) => {
      console.error("Error loading JSON:", error);
      container.textContent = "Failed to load koan.";
    });
}

function initChatterPage() {
  const convId = getSessionValue("conversation_id");
  const caseId = getSessionValue("case_id");

  if (caseId) {
    fetch("/static/mmnk.json")
      .then((res) => res.json())
      .then((data) => {
        const matching = data.cases.find((k) => k.id.toString() === caseId.toString());
        if (matching) {
          const preface = document.getElementById("chatPreface");
          if (preface) {
            preface.innerHTML = "";
            const koanDiv = document.createElement("div");
            koanDiv.className = "koan-preface";

            const titleDiv = document.createElement("div");
            const titleU = document.createElement("u");
            titleU.textContent = `Case #${matching.id}: ${matching.title}`;
            titleDiv.appendChild(titleU);

            const bodyDiv = document.createElement("div");
            bodyDiv.textContent = matching.body;

            koanDiv.appendChild(titleDiv);
            koanDiv.appendChild(bodyDiv);
            preface.appendChild(koanDiv);
          }
        }
      })
      .catch((err) => {
        console.error("Failed to load Koan in chatter:", err);
      });
  }

  const endChatButton = document.getElementById("chatEnd");
  if (endChatButton) {
    endChatButton.addEventListener("click", clearChat);
  }

  const saveChatButton = document.getElementById("chatSave");
  if (saveChatButton) {
    saveChatButton.addEventListener("click", saveChat);
    saveChatButton.disabled = !convId;
  }

  const chatButton = document.getElementById("chatSend");
  const chatInput = document.getElementById("chatInput");
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

async function startChat(prompt) {
  const chatButton = document.getElementById("chatSend");
  const chatInput = document.getElementById("chatInput");
  const saveChatButton = document.getElementById("chatSave");

  let thinkingNode = null;

  try {
    if (chatInput) chatInput.value = "";
    appendChatMessage("Student", prompt);
    if (chatButton) chatButton.disabled = true;

    thinkingNode = appendThinkingIndicator();

    const response = await fetch(apiUrl("/chat"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: prompt,
        conversation_id: getSessionValue("conversation_id") || "",
      }),
      credentials: "include",
    });

    if (!response.ok) {
      const fallback = await response.text();
      throw new Error(`HTTP ${response.status}: ${fallback}`);
    }

    const contentType = (response.headers.get("content-type") || "").toLowerCase();

    if (contentType.includes("text/event-stream")) {
      const messageDiv = document.createElement("div");
      messageDiv.className = "zenbot-message";
      messageDiv.innerHTML = "<strong>Mumonbot:</strong><br><span class=\"message-content\"></span>";
      const contentEl = messageDiv.querySelector(".message-content");

      const chatResults = document.getElementById("chatResults");
      if (chatResults) {
        chatResults.appendChild(messageDiv);
        chatResults.scrollTop = chatResults.scrollHeight;
      }

      let buffer = "";
      let fullText = "";
      const reader = response.body.getReader();
      const decoder = new TextDecoder();

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop() || "";

        for (const evt of events) {
          for (const line of evt.split("\n")) {
            if (!line.startsWith("data:")) continue;
            const payload = line.slice(5).trim();
            if (!payload) continue;

            let data;
            try {
              data = JSON.parse(payload);
            } catch (_e) {
              continue;
            }

            const chunk = data?.response;
            if (data?.event === "start") {
              if (data?.conversation_id) {
                setSessionValue("conversation_id", data.conversation_id);
              }
              removeThinkingIndicator(thinkingNode);
              thinkingNode = null;
              continue;
            }

            if (data?.event === "error") {
              removeThinkingIndicator(thinkingNode);
              thinkingNode = null;
              appendChatMessage("System", data?.error || "Chat stream failed.");
              continue;
            }

            if (chunk === "[DONE]") {
              if (contentEl) contentEl.innerHTML = renderMarkdownSafe(fullText);
              continue;
            }

            if (typeof chunk === "string") {
              removeThinkingIndicator(thinkingNode);
              thinkingNode = null;
              fullText += chunk;
              if (contentEl) contentEl.textContent = fullText;
              if (chatResults) chatResults.scrollTop = chatResults.scrollHeight;
            }
          }
        }
      }
    } else {
      const data = await response.json();
      removeThinkingIndicator(thinkingNode);
      thinkingNode = null;
      appendChatMessage("Mumonbot", data.response);
      if (data.conversation_id) {
        setSessionValue("conversation_id", data.conversation_id);
      }
    }

    const convIdAfter = getCookie("conversation_id");
    if (convIdAfter) {
      setSessionValue("conversation_id", convIdAfter);
    }

    if (chatInput) {
      chatInput.focus();
    }
  } catch (error) {
    removeThinkingIndicator(thinkingNode);
    appendChatMessage("System", `Error: ${error?.message || String(error)}`);
    handleError(error, "chatPreface", "Error starting chat.");
  } finally {
    if (chatButton) chatButton.disabled = false;
    if (chatInput) chatInput.focus();
    if (saveChatButton) saveChatButton.disabled = !getSessionValue("conversation_id");
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
    messageDiv.innerHTML = `<strong>${sender}</strong> caused error:<br>${error}`;
  } finally {
    chatResults.appendChild(messageDiv);
    chatResults.scrollTop = chatResults.scrollHeight;
  }
}

function clearChat() {
  const resetButton = document.getElementById("chatEnd");
  if (resetButton) {
    resetButton.disabled = true;
    setTimeout(() => {
      resetButton.disabled = false;
    }, 3000);
  }

  if (confirm("Are you sure you want to reset? This cannot be undone.")) {
    clearSessionValue("conversation_id");
    clearSessionValue("case_id");

    const chatResults = document.getElementById("chatResults");
    const chatInput = document.getElementById("chatInput");
    const chatPreface = document.getElementById("chatPreface");
    if (chatResults) chatResults.innerHTML = "";
    if (chatInput) chatInput.value = "";
    if (chatPreface) chatPreface.innerHTML = "";
    window.history.replaceState(null, "", "/chatter");
  }
}

async function saveChat() {
  const conversationId = getSessionValue("conversation_id");
  if (!conversationId) {
    alert("There is no conversation to save!");
    return;
  }

  try {
    showLoadingSpinner();
    const response = await fetch(apiUrl("/save_chat"), {
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
      clearSessionValue("conversation_id");
      clearSessionValue("case_id");
      const chatResults = document.getElementById("chatResults");
      const chatInput = document.getElementById("chatInput");
      const chatPreface = document.getElementById("chatPreface");
      if (chatResults) chatResults.innerHTML = "";
      if (chatInput) chatInput.value = "";
      if (chatPreface) chatPreface.innerHTML = "";
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
