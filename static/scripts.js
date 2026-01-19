/***************************************************************
 * scripts.js - Consolidated Code
 ***************************************************************/

/* ============================= *
* 1) SPINNER & ERROR HANDLING   *
* ============================= */
function showLoadingSpinner() {
  const spinnerOverlay = document.createElement('div');
  spinnerOverlay.className = 'spinner-overlay';
  spinnerOverlay.innerHTML = '<div class="spinner"></div>';
  const container = document.getElementById('spinnerContainer') || document.body;
  container.appendChild(spinnerOverlay);
}

function hideLoadingSpinner() {
  const spinnerOverlay = document.querySelector('.spinner-overlay');
  if (spinnerOverlay) {
    spinnerOverlay.remove();
  }
}

function handleError(error, elementId, message) {
  console.error('Error:', error);
  console.error('Message:', message);
  const targetElement = document.getElementById(elementId);
  if (targetElement) {
    targetElement.innerHTML = `<p><b>Error:</b> ${message}<br><b>Details:</b> ${error}</p>`;
  }
}

function getCookie(name) {
  const value = `; ${document.cookie}`;
  const parts = value.split(`; ${name}=`);
  if (parts.length === 2) return parts.pop().split(';').shift();
}

function escapeHtml(unsafe) {
  return String(unsafe)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function renderMarkdownSafe(markdownText) {
  if (typeof marked === 'undefined') {
    return escapeHtml(markdownText);
  }
  return marked.parse(escapeHtml(markdownText));
}

/* ============================================= *
  * 2) GATELESS GATE LIST PAGE (gg.html)          *
  *    - Load the list of Koans into #caseList    *
  * ============================================= */
function initGGList() {
  showLoadingSpinner();
  fetch('/static/mmnk.json')
    .then(response => response.json())
    .then(data => {
      const tocDiv = document.getElementById('caseList');
      if (!tocDiv) return;
      tocDiv.innerHTML = '';
      const ul = document.createElement('ul');
      data.cases.forEach(koan => {
        const li = document.createElement('li');
        const a = document.createElement('a');
        a.href = '/gg/' + koan.id;
        a.textContent = koan.id + '. ' + koan.title;
        li.appendChild(a);
        ul.appendChild(li);
      });
      tocDiv.appendChild(ul);
    })
    .catch(error => {
      console.error("Error loading JSON:", error);
      const tocDiv = document.getElementById('caseList');
      if (tocDiv) {
        tocDiv.textContent = "Failed to load cases.";
      }
    })
    .finally(() => {
      hideLoadingSpinner();
    });
}

/* ========================================================== *
  * 3) SINGLE KOAN PAGE (ggcase.html)                         *
  *    - Load a specific Koan into koan-container             *
  * ========================================================== */
function initGGCasePage() {
  //showLoadingSpinner();

  const container = document.getElementById('koan-container');
  if (!container) return;

  // Retrieve case_id from cookies or fallback to data attribute
  let case_id = getCookie('case_id');
  if (!case_id) {
    case_id = container.getAttribute('data-case-id');
  }

  fetch('/static/mmnk.json')
    .then(response => response.json())
    .then(data => {
      const koan = data.cases.find(k => k.id.toString() === case_id.toString());
      if (!koan) {
        container.textContent = "Koan not found with caseID " + case_id + ".";
        return;
      }
      container.innerHTML = '';

      const titleEl = document.createElement('div');
      titleEl.className = 'koan-title';
      titleEl.textContent = [koan.id, koan.title].join('. ');

      const bodyEl = document.createElement('div');
      bodyEl.className = 'koan-body';
      bodyEl.textContent = koan.body;

      const commentEl = document.createElement('div');
      commentEl.className = 'koan-comment';
      commentEl.textContent = "Mumon's comment:\n" + koan.comment;

      const verseEl = document.createElement('div');
      verseEl.className = 'koan-verse';
      verseEl.textContent = koan.verse.join('\n');

      container.appendChild(titleEl);
      container.appendChild(bodyEl);
      container.appendChild(commentEl);
      container.appendChild(verseEl);

      // "Bring to Dokusan" button => POST /chat_case => redirect
      const discussBtn = document.createElement('button');
      discussBtn.textContent = 'Bring to a Dokusan Session';
      discussBtn.addEventListener('click', () => {
      fetch(`/chat_case/${koan.id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ case_id: case_id })
      })
      .then(r => {
        if (!r.ok) {
          throw new Error(`Server responded with status: ${r.status}`);
        }
        return r.json();
      })
      .then(res => {
          if (res.error) {
            // alert("res.error: " + res.error);
            console.error("Error fetching /chat_case: " + res.error);
          } else {
            // Reset cookies for conversation_id and case_id
            document.cookie = `conversation_id=${res.conversation_id}; path=/`;
            document.cookie = `case_id=${res.case_id}; path=/`;
            // Redirect without query parameters
            window.location.href = '/chatter';
          }
        })
        .catch(err => {
          console.error("Error starting Koan chat:", err);
          // alert("Failed to start Koan chat. Check console.");
        });
  });
      container.appendChild(document.createElement('br'));
      container.appendChild(discussBtn);
    })
    .catch(error => {
      console.error("Error loading JSON:", error);
      container.textContent = "Failed to load koan.";
    })
  }

/* ==================================== *
  * 4) CHAT PAGE (chatter.html)          *
  * ==================================== */
function initChatterPage() {
  // Read cookies
  const convId = getCookie('conversation_id');
  const case_id = getCookie('case_id');
  if (!convId) {
  // Handle the case where no conversation exists
    console.info("No conversation ID found in cookies; assuming no koan chosen.");
  }
  // If case_id is present, fetch Koan to show in #chatPreface
  // case_id should be present only when calling from /chat_case() route (and api)
  if (case_id) {
    showLoadingSpinner();
    fetch('/static/mmnk.json')
      .then(res => res.json())
      .then(data => {
        const matching = data.cases.find(k => k.id.toString() === case_id.toString());
        if (matching) {
          const preface = document.getElementById('chatPreface');
          if (preface) {
            preface.innerHTML = '';
            const koanDiv = document.createElement('div');
            koanDiv.className = 'koan-preface';

            const titleDiv = document.createElement('div');
            const titleU = document.createElement('u');
            titleU.textContent = `Case #${matching.id}: ${matching.title}`;
            titleDiv.appendChild(titleU);

            const bodyDiv = document.createElement('div');
            bodyDiv.textContent = matching.body;

            koanDiv.appendChild(titleDiv);
            koanDiv.appendChild(bodyDiv);
            preface.appendChild(koanDiv);
          }
        }
      })
      .catch(err => {
        console.error("Failed to load Koan in chatter:", err);
      })
      .finally(() => {
        hideLoadingSpinner();
      });
  }

  // Set up chat events (Send/End/Clear)
  // Clear button
  // Restore button to chatter.html too
  const endChatButton = document.getElementById('chatEnd');
  if (endChatButton) {
    endChatButton.addEventListener('click', clearChat);
  }
  // Save button
  const saveChatButton = document.getElementById('chatSave');
  if (saveChatButton) {
    saveChatButton.addEventListener('click', saveChat);
    saveChatButton.disabled = !convId;
  }
  // Send button
  const chatButton = document.getElementById('chatSend');
  const chatInput = document.getElementById('chatInput');
  if (chatInput) {
    chatInput.focus();
  }
  if (chatButton && chatInput) {
    chatButton.addEventListener('click', () => {
      const prompt = chatInput.value.trim();
      if (prompt) startChat(prompt);
    });
    chatInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        chatButton.click();
      }
    });
  }
}

/* ============================== *
  * CORE CHAT FUNCTIONS         *
  * ============================== */
async function startChat(prompt) {
  const chatButton = document.getElementById('chatSend');
  const chatInput = document.getElementById('chatInput');
  const saveChatButton = document.getElementById('chatSave');

  try {
    if (chatInput) chatInput.value = '';
    appendChatMessage('Student', prompt);
    if (chatButton) chatButton.disabled = true;
    showLoadingSpinner();

    const response = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: prompt,
        conversation_id: getCookie('conversation_id') || ''
      })
    });

    if (!response.ok) {
      const fallback = await response.text();
      throw new Error(`HTTP ${response.status}: ${fallback}`);
    }

    const contentType = (response.headers.get('content-type') || '').toLowerCase();

    // Streaming response (SSE)
    if (contentType.includes('text/event-stream')) {
      const messageDiv = document.createElement('div');
      messageDiv.className = 'zenbot-message';
      messageDiv.innerHTML = `<strong>Mumonbot:</strong><br><span class="message-content"></span>`;
      const contentEl = messageDiv.querySelector('.message-content');

      const chatResults = document.getElementById('chatResults');
      if (chatResults) {
        chatResults.appendChild(messageDiv);
        chatResults.scrollTop = chatResults.scrollHeight;
      }

      let buffer = '';
      let fullText = '';
      const reader = response.body.getReader();
      const decoder = new TextDecoder();

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split('\n\n');
        buffer = events.pop() || '';

        for (const evt of events) {
          for (const line of evt.split('\n')) {
            if (!line.startsWith('data:')) continue;
            const payload = line.slice(5).trim();
            if (!payload) continue;

            let data;
            try {
              data = JSON.parse(payload);
            } catch (e) {
              console.warn('Bad SSE JSON payload:', payload);
              continue;
            }

            const chunk = data?.response;
            if (chunk === '[DONE]') {
              if (contentEl) contentEl.innerHTML = renderMarkdownSafe(fullText);
              continue;
            }

            if (typeof chunk === 'string') {
              fullText += chunk;
              if (contentEl) contentEl.textContent = fullText;
              if (chatResults) chatResults.scrollTop = chatResults.scrollHeight;
            }
          }
        }
      }
    } else {
      // Non-streaming JSON response
      const data = await response.json();
      appendChatMessage('Mumonbot', data.response);
    }

    const inputEl = document.getElementById('chatInput');
    if (inputEl) {
      inputEl.focus();
    }
    /* Feature On hold
    if (data.conversation_id) {
      const prefaceEl = document.getElementById('chatPreface');
      if (prefaceEl) {
        prefaceEl.innerHTML = 'Session ID: ' + data.conversation_id;
      }
    }
    */
  } catch (error) {
    appendChatMessage('System', `Error: ${error?.message || String(error)}`);
    handleError(error, 'chatPreface', 'Error starting chat.');
  } finally {
    hideLoadingSpinner();
    if (chatButton) chatButton.disabled = false;
    if (chatInput) chatInput.focus();
    if (saveChatButton) saveChatButton.disabled = !(getCookie('conversation_id'));
  }
}

/* function formatMessage(message) {return message.replace(/\n/g, '<br>');} */

function appendChatMessage(sender, message) {
  const chatResults = document.getElementById('chatResults');
  if (!chatResults) return;

  const messageDiv = document.createElement('div');
  messageDiv.className = (sender === 'Student') ? 'user-message' : 'zenbot-message';

  try {
    const text = String(message ?? '');
    const htmlContent = renderMarkdownSafe(text);
    messageDiv.innerHTML = `<strong>${escapeHtml(sender)}:</strong><br>${htmlContent}`;
  } catch (error) {
    // handleError(error, 'chatPreface', 'Formatting error in appendChatMessage script.');
    messageDiv.innerHTML = `<strong>${sender}</strong> caused error:<br>${error}`;
  } finally {
    // Add to the bottom
    chatResults.appendChild(messageDiv);
    chatResults.scrollTop = chatResults.scrollHeight;
  }
}

function clearChat() {
  let resetButton = document.getElementById('chatEnd');
  if (resetButton) {
    resetButton.disabled = true;
    setTimeout(() => resetButton.disabled = false, 3000); // Re-enable after 3s
  }

  // To avoid accidental button clicks
  if (confirm("Are you sure you want to reset? This cannot be undone.")) {
    // Remove cookies by setting an expiration in the past
    document.cookie = 'conversation_id=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT';
    document.cookie = 'case_id=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT';
    // Clear chat display
    const chatResults = document.getElementById('chatResults');
    const chatInput = document.getElementById('chatInput');
    const chatPreface = document.getElementById('chatPreface');
    if (chatResults) chatResults.innerHTML = '';
    if (chatInput) chatInput.value = '';
    if (chatPreface) chatPreface.innerHTML = '';
    // Reset URL to just /chatter
    window.history.replaceState(null, '', '/chatter');
 }
}

async function saveChat() {
  const conversationId = getCookie('conversation_id');
  if (!conversationId) {
    alert("There is no conversation to save!");
    return;
  }
  try {
    showLoadingSpinner();
    console.log("saveChat() called");
    console.log("conversationId:", conversationId);
    const response = await fetch('/save_chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });
    console.log("saveChat(): response: ", response);
    const data = await response.json();
    console.log("saveChat(): response.json(): ", data);
    if (data.status === 'success') {
      alert('Chat successfully saved.');
      const conversationId = getCookie('conversation_id');
      console.log("saveChat() success; conversationId should be blank: '", conversationId, "'");
      document.getElementById('chatResults').innerHTML = '';
      document.getElementById('chatInput').value = '';
      document.getElementById('chatPreface').innerHTML = '';
    } else {
      alert(`Error saving chat: ${data.error}`);
    }
  } catch (error) {
    handleError(error, 'chatPreface', 'Error ending chat.');
  } finally {
    hideLoadingSpinner();
  }
}

async function oldsaveChat() {
  try {
    showLoadingSpinner();
    const response = await fetch('/save_chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });
    const data = await response.json();
    if (data.status === 'success') {
      alert('Chat successfully saved.');
      document.getElementById('chatResults').innerHTML = '';
      document.getElementById('chatInput').value = '';
      document.getElementById('chatPreface').innerHTML = '';
    } else {
      alert(`Error saving chat: ${data.error}`);
    }
  } catch (error) {
    handleError(error, 'chatPreface', 'Error ending chat.');
  } finally {
    hideLoadingSpinner();
  }
}

/* ======================================== *
  * 6) DETECT WHICH PAGE & INIT ACCORDINGLY  *
  * ======================================== */
document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('caseList')) {
    // gg.html
    initGGList();
  }
  else if (document.getElementById('koan-container')) {
    // ggcase.html
    initGGCasePage();
  }
  else if (document.getElementById('chatResults')) {
    // chatter.html
    initChatterPage();
  }

});
