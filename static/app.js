/* Voice-first coding chat UI */
(function () {
  "use strict";

  const API = "/api/web";

  // ---------- state ----------
  const $ = (sel) => document.querySelector(sel);
  const loginView = $("#login-view");
  const chatView = $("#chat-view");
  const messagesEl = $("#messages");
  const inputEl = $("#input");
  const micBtn = $("#mic-btn");
  const recHint = $("#rec-hint");
  const sendBtn = $("#send-btn");
  const newSessionBtn = $("#new-session");

  function getSession() {
    let s = localStorage.getItem("dac_session");
    if (!s) {
      s = "web-" + crypto.randomUUID().slice(0, 12);
      localStorage.setItem("dac_session", s);
    }
    return s;
  }
  function getToken() { return localStorage.getItem("dac_token") || ""; }

  let speechRecognition = null;
  let mediaRecorder = null;
  let mediaChunks = [];
  let isRecording = false;

  // ---------- auth ----------
  async function checkStatus() {
    const res = await fetch(`${API}/status`);
    const data = await res.json();
    if (data.auth_required && !getToken()) {
      loginView.classList.remove("hidden");
      chatView.classList.add("hidden");
    } else {
      loginView.classList.add("hidden");
      chatView.classList.remove("hidden");
    }
  }

  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const username = $("#login-username").value;
    const code = $("#login-code").value;
    const res = await fetch(`${API}/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, code }),
    });
    if (res.ok) {
      const data = await res.json();
      localStorage.setItem("dac_token", data.token);
      localStorage.setItem("dac_username", username);
      $("#login-error").classList.add("hidden");
      loginView.classList.add("hidden");
      chatView.classList.remove("hidden");
    } else {
      $("#login-error").classList.remove("hidden");
    }
  });

  // ---------- chat helpers ----------
  function scrollToBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function addMsg(cls, html) {
    const el = document.createElement("div");
    el.className = "msg " + cls;
    el.innerHTML = html;
    messagesEl.appendChild(el);
    scrollToBottom();
    return el;
  }

  function addThinking() {
    const el = document.createElement("div");
    el.className = "msg agent thinking";
    el.id = "thinking";
    el.textContent = "Thinking…";
    messagesEl.appendChild(el);
    scrollToBottom();
    return el;
  }
  function removeThinking() {
    const t = $("#thinking");
    if (t) t.remove();
  }

  // ---------- markdown + mermaid rendering ----------
  function renderMermaid(container) {
    const blocks = container.querySelectorAll("pre.mermaid-source");
    blocks.forEach((pre) => {
      const code = pre.textContent.trim();
      const wrap = document.createElement("div");
      wrap.className = "mermaid-wrap";
      const svg = document.createElement("div");
      svg.className = "mermaid";
      svg.textContent = code;
      wrap.appendChild(svg);
      pre.replaceWith(wrap);
      try {
        mermaid.run({ nodes: [svg] });
      } catch (err) {
        wrap.innerHTML = `<pre><code>${code}</code></pre><p style="color:#f85149">⚠️ diagram failed to render</p>`;
      }
    });
  }

  function renderAgentText(text) {
    const el = document.createElement("div");
    el.className = "msg agent";
    // Convert ```mermaid fences into placeholders we can extract safely.
    const mermaidBlocks = [];
    const escaped = text.replace(
      /```mermaid\s*\n?([\s\S]*?)```/g,
      (_, code) => {
        mermaidBlocks.push(code.trim());
        return `@@MERMAID_${mermaidBlocks.length - 1}@@`;
      }
    );
    let html = marked.parse(escaped || "");
    html = DOMPurify.sanitize(html);
    // Re-insert mermaid blocks as text (safe, mermaid reads textContent).
    html = html.replace(/@@MERMAID_(\d+)@@/g, (_, i) => {
      const code = mermaidBlocks[Number(i)] || "";
      const esc = code.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      return `<pre class="mermaid-source">${esc}</pre>`;
    });
    el.innerHTML = html;
    messagesEl.appendChild(el);
    renderMermaid(el);
    scrollToBottom();
    return el;
  }

  // ---------- action cards ----------
  function addAskCard(question, options) {
    const el = document.createElement("div");
    el.className = "msg agent";
    const optHtml = (options || []).length
      ? `<div class="action-card"><div class="options">${options
          .map((o, i) => `<button class="chip" data-i="${i}">${DOMPurify.sanitize(o)}</button>`)
          .join("")}</div></div>`
      : "";
    el.innerHTML = `<div class="q">❓ ${DOMPurify.sanitize(question)}</div>${optHtml}<p class="hint" style="color:var(--text-dim);font-size:12px;margin-top:6px">or just type your answer</p>`;
    messagesEl.appendChild(el);
    el.querySelectorAll(".chip").forEach((btn) => {
      btn.addEventListener("click", () => {
        const i = Number(btn.dataset.i);
        const option = (options || [])[i];
        if (option) sendMessage(option);
      });
    });
    scrollToBottom();
  }

  function addPushCard(repoPath, branch, commitMessage) {
    const el = document.createElement("div");
    el.className = "msg agent";
    el.innerHTML = `
      <div class="push-card">
        <strong>🚀 Approve this push?</strong>
        <div class="push-meta">${DOMPurify.sanitize(repoPath || "")}\nbranch: ${DOMPurify.sanitize(branch || "main")}\nmessage: ${DOMPurify.sanitize(commitMessage || "")}</div>
        <div class="push-btns">
          <button class="btn btn-approve" id="approve-btn">✅ Approve</button>
          <button class="btn btn-cancel" id="cancel-btn">❌ Cancel</button>
        </div>
      </div>`;
    messagesEl.appendChild(el);
    $("#approve-btn").addEventListener("click", () => sendMessage("yes"));
    $("#cancel-btn").addEventListener("click", () => sendMessage("no"));
    scrollToBottom();
  }

  // ---------- sending ----------
  async function api(path, opts = {}) {
    const headers = Object.assign(
      { "X-Auth-Token": getToken() },
      opts.headers || {}
    );
    const res = await fetch(API + path, Object.assign({}, opts, { headers }));
    if (res.status === 401) {
      localStorage.removeItem("dac_token");
      checkStatus();
      throw new Error("session expired — please log in again");
    }
    return res;
  }

  function addUserBubble(text) {
    const el = document.createElement("div");
    el.className = "msg user";
    el.textContent = text;
    messagesEl.appendChild(el);
    scrollToBottom();
  }

  // Parse a chunked SSE byte stream from a fetch Response body.
  async function readSse(res, onEvent) {
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let sep;
      while ((sep = buf.indexOf("\n\n")) !== -1) {
        const block = buf.slice(0, sep);
        buf = buf.slice(sep + 2);
        let event = "message";
        let data = "";
        for (const line of block.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) data += line.slice(5).trim();
        }
        if (!data) continue;
        let payload;
        try { payload = JSON.parse(data); } catch { continue; }
        onEvent(event, payload);
      }
    }
  }

  function renderResult(data) {
    if (data.type === "ask") {
      addAskCard(data.question || data.text, data.options || []);
    } else if (data.type === "push_approval") {
      const p = data.payload || {};
      addPushCard(p.repo_path, p.branch, p.commit_message);
    } else {
      renderAgentText(data.text || "(no response)");
    }
  }

  async function sendMessage(text) {
    text = (text || "").trim();
    if (!text) return;
    addUserBubble(text);
    inputEl.value = "";
    inputEl.style.height = "auto";
    addThinking();

    let res;
    try {
      res = await api(`${API}/message`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: getSession(), text }),
      });
    } catch (err) {
      removeThinking();
      addMsg("agent", `<p style="color:var(--danger)">⚠️ ${DOMPurify.sanitize(err.message)}</p>`);
      return;
    }

    const ctype = res.headers.get("content-type") || "";
    if (ctype.includes("text/event-stream")) {
      // SSE streaming path: stage events, then the final result.
      let finalResult = null;
      try {
        await readSse(res, (event, payload) => {
          if (event === "status" && payload.state === "thinking") {
            const t = $("#thinking");
            if (t) t.textContent = "Working…";
          } else if (event === "result") {
            finalResult = payload;
          } else if (event === "error") {
            removeThinking();
            addMsg("agent", `<p style="color:var(--danger)">⚠️ ${DOMPurify.sanitize(payload.message || "something went wrong")}</p>`);
          }
        });
      } catch (err) {
        removeThinking();
        addMsg("agent", `<p style="color:var(--danger)">⚠️ ${DOMPurify.sanitize(err.message)}</p>`);
        return;
      }
      removeThinking();
      if (finalResult) renderResult(finalResult);
      else addMsg("agent", "<p>(no response)</p>");
    } else {
      // Fallback: plain JSON response.
      removeThinking();
      const data = await res.json().catch(() => ({}));
      renderResult(data);
    }
  }

  // ---------- input handling ----------
  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(inputEl.value);
    }
  });
  sendBtn.addEventListener("click", () => sendMessage(inputEl.value));
  inputEl.addEventListener("input", () => {
    inputEl.style.height = "auto";
    inputEl.style.height = Math.min(inputEl.scrollHeight, 140) + "px";
  });
  newSessionBtn.addEventListener("click", () => {
    if (!confirm("Start a new session? The current conversation (and its sandbox) will be left behind.")) return;
    localStorage.removeItem("dac_session");
    messagesEl.innerHTML = "";
    addMsg("agent", "<p>🆕 New session started. What are we building?</p>");
  });

  // ---------- voice ----------
  function hasSpeechRecognition() {
    return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
  }

  async function startRecording() {
    if (hasSpeechRecognition()) {
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      speechRecognition = new SR();
      speechRecognition.lang = "en-US";
      speechRecognition.interimResults = true;
      speechRecognition.continuous = false;
      let finalText = "";
      speechRecognition.onresult = (event) => {
        let interim = "";
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const t = event.results[i][0].transcript;
          if (event.results[i].isFinal) finalText += t;
          else interim += t;
        }
        inputEl.value = (finalText + interim).trim();
      };
      speechRecognition.onend = () => {
        isRecording = false;
        micBtn.classList.remove("recording");
        recHint.classList.add("hidden");
        if (inputEl.value.trim()) sendMessage(inputEl.value);
      };
      speechRecognition.onerror = (e) => {
        // Fall back to MediaRecorder path if speech API failed (e.g. permission).
        if (e.error && e.error !== "aborted" && e.error !== "no-speech") {
          startMediaRecording();
        }
      };
      isRecording = true;
      micBtn.classList.add("recording");
      recHint.textContent = "Listening… speak now";
      recHint.classList.remove("hidden");
      speechRecognition.start();
      return;
    }
    await startMediaRecording();
  }

  function stopRecording() {
    if (speechRecognition && isRecording) {
      speechRecognition.stop();
      return;
    }
    if (mediaRecorder && mediaRecorder.state !== "inactive") {
      mediaRecorder.stop();
    }
  }

  async function startMediaRecording() {
    if (isRecording) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mime = ["audio/ogg;codecs=opus", "audio/webm;codecs=opus", "audio/mp4", "audio/ogg"]
        .find((m) => MediaRecorder.isTypeSupported(m)) || "";
      mediaRecorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      mediaChunks = [];
      mediaRecorder.ondataavailable = (e) => { if (e.data.size) mediaChunks.push(e.data); };
      mediaRecorder.onstop = async () => {
        isRecording = false;
        micBtn.classList.remove("recording");
        recHint.classList.add("hidden");
        stream.getTracks().forEach((t) => t.stop());
        const type = mediaRecorder.mimeType.split(";")[0] || "audio/ogg";
        const blob = new Blob(mediaChunks, { type });
        await transcribeAndSend(blob, type);
      };
      isRecording = true;
      micBtn.classList.add("recording");
      recHint.textContent = "Recording… tap mic to stop";
      recHint.classList.remove("hidden");
      mediaRecorder.start();
    } catch (err) {
      addMsg("agent", `<p style="color:var(--danger)">⚠️ Microphone unavailable: ${DOMPurify.sanitize(err.message)}</p>`);
    }
  }

  async function transcribeAndSend(blob, mime) {
    addThinking();
    try {
      const res = await api(`${API}/transcribe`, {
        method: "POST",
        headers: { "X-Audio-Mime": mime },
        body: blob,
      });
      removeThinking();
      const data = await res.json().catch(() => ({}));
      const text = (data.text || "").trim();
      if (!text) {
        addMsg("agent", "<p>🎧 I couldn't hear anything — try again or type instead.</p>");
        return;
      }
      // sendMessage adds the user bubble itself.
      sendMessage(text);
    } catch (err) {
      removeThinking();
      addMsg("agent", `<p style="color:var(--danger)">⚠️ Transcription failed: ${DOMPurify.sanitize(err.message)}</p>`);
    }
  }

  micBtn.addEventListener("click", () => {
    if (isRecording) stopRecording();
    else startRecording();
  });

  // ---------- boot ----------
  marked.setOptions({ breaks: true, gfm: true });
  mermaid.initialize({ startOnLoad: false, theme: "dark" });

  checkStatus().then(() => {
    if (!messagesEl.children.length) {
      addMsg(
        "agent",
        "<p>👋 Hey — I'm your coding agent. Type, or hold the mic to talk. I can draft code, explain plans, and draw diagrams right here.</p>"
      );
    }
  });
})();
