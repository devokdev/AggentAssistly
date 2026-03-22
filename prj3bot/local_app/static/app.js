(function () {
  const shell = document.getElementById("appShell");
  const messages = document.getElementById("messages");
  const typing = document.getElementById("typing");
  const composer = document.getElementById("composer");
  const input = document.getElementById("messageInput");
  const fileInput = document.getElementById("fileInput");
  const attachFileBtn = document.getElementById("attachFile");
  const recordVoiceBtn = document.getElementById("recordVoice");
  const attachmentTray = document.getElementById("attachmentTray");
  const newChatBtn = document.getElementById("newChat");
  const historyList = document.getElementById("historyList");

  const onboarding = document.getElementById("onboarding");
  const configForm = document.getElementById("configForm");
  const onboardingError = document.getElementById("onboardingError");
  const openSettings = document.getElementById("openSettings");

  const emailModal = document.getElementById("emailModal");
  const emailTo = document.getElementById("emailTo");
  const emailSubject = document.getElementById("emailSubject");
  const emailBody = document.getElementById("emailBody");
  const sendEmailBtn = document.getElementById("sendEmail");
  const cancelEmailBtn = document.getElementById("cancelEmail");
  const emailError = document.getElementById("emailError");
  const emailContext = document.getElementById("emailContext");

  const storageKey = "agentassistly.desktop.session";
  const historyStorageKey = "agentassistly.desktop.history";
  let sessionId = localStorage.getItem(storageKey) || crypto.randomUUID();
  localStorage.setItem(storageKey, sessionId);
  let sessions = JSON.parse(localStorage.getItem(historyStorageKey) || "[]");
  let activeDraftId = "";
  let pendingFiles = [];
  let mediaRecorder = null;
  let recordedChunks = [];

  function show(el) {
    el.classList.remove("hidden");
  }

  function hide(el) {
    el.classList.add("hidden");
  }

  function addMessage(role, text) {
    const bubble = document.createElement("article");
    bubble.className = `bubble ${role}`;
    bubble.dataset.role = role;
    bubble.textContent = text || "";
    messages.appendChild(bubble);
    messages.scrollTop = messages.scrollHeight;
    return bubble;
  }

  function addStructuredMessage(role, className = "") {
    const bubble = document.createElement("article");
    bubble.className = `bubble ${role} ${className}`.trim();
    bubble.dataset.role = role;
    messages.appendChild(bubble);
    messages.scrollTop = messages.scrollHeight;
    return bubble;
  }

  function toggleTyping(isLoading) {
    if (isLoading) {
      show(typing);
      return;
    }
    hide(typing);
  }

  function renderAttachments() {
    attachmentTray.innerHTML = "";
    if (!pendingFiles.length) {
      hide(attachmentTray);
      return;
    }
    show(attachmentTray);
    pendingFiles.forEach((file, index) => {
      const pill = document.createElement("div");
      pill.className = "attachment-pill";
      const label = document.createElement("span");
      label.textContent = file.name;
      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.className = "attachment-remove";
      removeBtn.textContent = "Remove";
      removeBtn.addEventListener("click", () => {
        pendingFiles.splice(index, 1);
        renderAttachments();
      });
      pill.append(label, removeBtn);
      attachmentTray.appendChild(pill);
    });
  }

  function queueFiles(files) {
    for (const file of files) {
      pendingFiles.push(file);
    }
    renderAttachments();
  }

  function saveSessions() {
    localStorage.setItem(historyStorageKey, JSON.stringify(sessions.slice(0, 30)));
  }

  function upsertSessionPreview(text) {
    const cleanText = (text || "").trim() || "New chat";
    const existing = sessions.find((entry) => entry.id === sessionId);
    if (existing) {
      existing.title = existing.title === "New chat" ? cleanText.slice(0, 42) : existing.title;
      existing.updatedAt = Date.now();
    } else {
      sessions.unshift({
        id: sessionId,
        title: cleanText.slice(0, 42),
        updatedAt: Date.now(),
      });
    }
    sessions.sort((a, b) => b.updatedAt - a.updatedAt);
    saveSessions();
    renderHistory();
  }

  function renderHistory() {
    historyList.innerHTML = "";
    if (!sessions.length) {
      const empty = document.createElement("li");
      empty.className = "history-empty";
      empty.textContent = "No chats yet";
      historyList.appendChild(empty);
      return;
    }
    sessions.forEach((entry) => {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.className = `history-item ${entry.id === sessionId ? "active" : ""}`;
      button.type = "button";
      button.textContent = entry.title || "New chat";
      button.addEventListener("click", () => {
        sessionId = entry.id;
        localStorage.setItem(storageKey, sessionId);
        messages.innerHTML = "";
        addMessage("assistant", "Session loaded. Continue the conversation.");
        renderHistory();
      });
      item.appendChild(button);
      historyList.appendChild(item);
    });
  }

  async function streamText(el, text) {
    const source = (text || "").toString();
    if (!source) {
      el.textContent = "";
      return;
    }
    const chunks = source.match(/.{1,4}/g) || [source];
    let acc = "";
    for (const chunk of chunks) {
      acc += chunk;
      el.textContent = acc;
      messages.scrollTop = messages.scrollHeight;
      // subtle streaming feel without backend streaming
      await new Promise((resolve) => setTimeout(resolve, 12));
    }
  }

  async function loadConfigStatus() {
    try {
      const response = await fetch("/config/status");
      const data = await response.json();
      if (data.onboarding_complete) {
        hide(onboarding);
        show(shell);
        if (!messages.children.length) {
          addMessage("assistant", "AgentAssistly is ready.");
        }
      } else {
        show(onboarding);
      }
    } catch (_) {
      show(onboarding);
    }
  }

  function collectConfigPayload() {
    const fields = [
      "geminiApiKey",
      "googleCredentialsJson",
      "imapHost",
      "imapPort",
      "imapUsername",
      "imapPassword",
      "smtpHost",
      "smtpPort",
      "smtpUsername",
      "smtpPassword",
      "fromAddress",
    ];
    const payload = {};
    for (const field of fields) {
      payload[field] = document.getElementById(field).value.trim();
    }
    return payload;
  }

  async function saveConfig(event) {
    event.preventDefault();
    onboardingError.textContent = "";
    const payload = collectConfigPayload();
    try {
      const response = await fetch("/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || "Could not save config");
      }
      await loadConfigStatus();
    } catch (error) {
      onboardingError.textContent = error.message;
    }
  }

  function openEmailModal(preview) {
    activeDraftId = preview.draft_id || "";
    emailTo.value = (preview.to || []).join(", ");
    emailSubject.value = preview.subject || "";
    emailBody.value = preview.body || "";
    emailError.textContent = "";
    if (preview.thread_context) {
      emailContext.textContent = `Reply context loaded from thread:\n\n${preview.thread_context}`;
      show(emailContext);
    } else {
      emailContext.textContent = "";
      hide(emailContext);
    }
    show(emailModal);
  }

  function closeEmailModal() {
    activeDraftId = "";
    emailContext.textContent = "";
    hide(emailContext);
    hide(emailModal);
  }

  function sanitizeEmailBody(body) {
    const source = (body || "").toString().replace(/\r\n/g, "\n").trim();
    if (!source) {
      return "(No preview available)";
    }
    if (source.toLowerCase().startsWith("email received.")) {
      const parts = source.split(/\n\n/, 2);
      return (parts[1] || source).trim();
    }
    return source;
  }

  function renderEmailList(container, data) {
    const items = data.items || data.emails || [];
    container.innerHTML = "";

    const wrap = document.createElement("section");
    wrap.className = "email-list";

    const intro = document.createElement("div");
    intro.className = "email-list-header";
    intro.textContent = items.length
      ? `Showing ${items.length} email${items.length === 1 ? "" : "s"}`
      : "No emails found";
    wrap.appendChild(intro);

    items.forEach((item) => {
      const card = document.createElement("article");
      card.className = "email-card";

      const subject = document.createElement("h3");
      subject.className = "email-card-subject";
      subject.textContent = item.subject || "(No Subject)";
      card.appendChild(subject);

      const meta = document.createElement("dl");
      meta.className = "email-card-meta";

      const fromLabel = document.createElement("dt");
      fromLabel.textContent = "From";
      const fromValue = document.createElement("dd");
      fromValue.textContent = item.from || "Unknown sender";
      meta.append(fromLabel, fromValue);

      if (item.date) {
        const dateLabel = document.createElement("dt");
        dateLabel.textContent = "Date";
        const dateValue = document.createElement("dd");
        dateValue.textContent = item.date;
        meta.append(dateLabel, dateValue);
      }

      card.appendChild(meta);

      const body = document.createElement("div");
      body.className = "email-card-body";
      body.textContent = sanitizeEmailBody(item.body || item.preview || item.snippet || "");
      card.appendChild(body);

      wrap.appendChild(card);
    });

    container.appendChild(wrap);
    messages.scrollTop = messages.scrollHeight;
  }

  function renderDocumentPreview(container, data) {
    container.innerHTML = "";

    const card = document.createElement("section");
    card.className = "doc-preview-card";

    const header = document.createElement("div");
    header.className = "doc-preview-header";

    const headingGroup = document.createElement("div");
    headingGroup.className = "doc-preview-heading";

    const title = document.createElement("h3");
    title.className = "doc-preview-title";
    title.textContent = data.title || "Untitled document";

    const subtitle = document.createElement("p");
    subtitle.className = "doc-preview-subtitle";
    subtitle.textContent = "Saved to Google Docs";

    headingGroup.append(title, subtitle);

    if (data.url) {
      const directLink = document.createElement("a");
      directLink.className = "doc-preview-link";
      directLink.href = data.url;
      directLink.target = "_blank";
      directLink.rel = "noopener noreferrer";
      directLink.textContent = "View Google Doc";
      headingGroup.appendChild(directLink);
    }

    const actions = document.createElement("div");
    actions.className = "doc-preview-actions";

    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "btn ghost doc-action";
    copyBtn.textContent = "Copy";
    copyBtn.addEventListener("click", async () => {
      const text = data.content || "";
      try {
        await navigator.clipboard.writeText(text);
        copyBtn.textContent = "Copied";
        setTimeout(() => {
          copyBtn.textContent = "Copy";
        }, 1400);
      } catch (_) {
        copyBtn.textContent = "Copy failed";
        setTimeout(() => {
          copyBtn.textContent = "Copy";
        }, 1400);
      }
    });

    const pdfBtn = document.createElement("a");
    pdfBtn.className = "btn ghost doc-action";
    pdfBtn.textContent = "Download PDF";
    pdfBtn.href = `/documents/${encodeURIComponent(data.document_id || "")}/download?format=pdf`;

    const docxBtn = document.createElement("a");
    docxBtn.className = "btn ghost doc-action";
    docxBtn.textContent = "Download DOCX";
    docxBtn.href = `/documents/${encodeURIComponent(data.document_id || "")}/download?format=docx`;

    const openBtn = document.createElement("a");
    openBtn.className = "btn doc-action";
    openBtn.textContent = "Open in Google Docs";
    openBtn.href = data.url || "#";
    openBtn.target = "_blank";
    openBtn.rel = "noopener noreferrer";

    const openHereBtn = document.createElement("button");
    openHereBtn.type = "button";
    openHereBtn.className = "btn ghost doc-action";
    openHereBtn.textContent = "Open Here";

    actions.append(copyBtn, pdfBtn, docxBtn, openHereBtn, openBtn);
    header.append(headingGroup, actions);

    const preview = document.createElement("div");
    preview.className = "doc-preview-body";
    preview.textContent = data.content || "";

    const embedWrap = document.createElement("div");
    embedWrap.className = "doc-embed hidden";

    const iframe = document.createElement("iframe");
    iframe.className = "doc-embed-frame";
    if (data.url) {
      iframe.src = data.url.replace(/\/edit(?:\?.*)?$/i, "/preview");
    }
    iframe.loading = "lazy";
    iframe.referrerPolicy = "strict-origin-when-cross-origin";
    embedWrap.appendChild(iframe);

    openHereBtn.addEventListener("click", () => {
      const isHidden = embedWrap.classList.contains("hidden");
      if (!data.url) {
        return;
      }
      if (isHidden) {
        embedWrap.classList.remove("hidden");
        openHereBtn.textContent = "Hide Viewer";
      } else {
        embedWrap.classList.add("hidden");
        openHereBtn.textContent = "Open Here";
      }
      messages.scrollTop = messages.scrollHeight;
    });

    card.append(header, preview, embedWrap);
    container.appendChild(card);
    messages.scrollTop = messages.scrollHeight;
  }

  async function sendEditedEmail() {
    emailError.textContent = "";
    const payload = {
      draft_id: activeDraftId,
      to: emailTo.value,
      subject: emailSubject.value.trim(),
      body: emailBody.value.trim(),
    };
    if (!payload.to || !payload.subject || !payload.body) {
      emailError.textContent = "Recipient, subject, and body are required.";
      return;
    }
    try {
      const response = await fetch("/send-email", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || "Failed to send email");
      }
      addMessage("assistant", data.reply || "Email sent.");
      upsertSessionPreview(emailSubject.value);
      closeEmailModal();
    } catch (error) {
      emailError.textContent = error.message;
    }
  }

  async function sendChatMessage(text) {
    addMessage("user", text);
    upsertSessionPreview(text);
    toggleTyping(true);
    try {
      const hasFiles = pendingFiles.length > 0;
      let response;
      if (hasFiles) {
        const formData = new FormData();
        formData.append("message", text);
        formData.append("session_id", sessionId);
        pendingFiles.forEach((file) => formData.append("files", file, file.name));
        response = await fetch("/chat", {
          method: "POST",
          body: formData,
        });
      } else {
        response = await fetch("/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: text, session_id: sessionId }),
        });
      }
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || "Request failed");
      }

      if (data.type === "email_preview") {
        const msg = addMessage("assistant", "");
        await streamText(msg, "I drafted an email. Please review and send.");
        openEmailModal(data);
      } else if (data.type === "document_preview") {
        const msg = addStructuredMessage("assistant", "document-results");
        renderDocumentPreview(msg, data);
      } else if (data.type === "email_list") {
        const msg = addStructuredMessage("assistant", "email-results");
        renderEmailList(msg, data);
      } else {
        const msg = addMessage("assistant", "");
        await streamText(msg, data.reply || "Done.");
      }
      pendingFiles = [];
      renderAttachments();
      fileInput.value = "";
    } catch (error) {
      const msg = addMessage("assistant", "");
      await streamText(msg, `I could not complete that request: ${error.message}`);
    } finally {
      toggleTyping(false);
    }
  }

  composer.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    input.style.height = "auto";
    sendChatMessage(text);
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      composer.requestSubmit();
    }
  });

  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
  });

  attachFileBtn.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    queueFiles(Array.from(fileInput.files || []));
    fileInput.value = "";
  });

  recordVoiceBtn.addEventListener("click", async () => {
    if (mediaRecorder && mediaRecorder.state === "recording") {
      mediaRecorder.stop();
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      recordedChunks = [];
      mediaRecorder = new MediaRecorder(stream);
      mediaRecorder.addEventListener("dataavailable", (event) => {
        if (event.data && event.data.size) {
          recordedChunks.push(event.data);
        }
      });
      mediaRecorder.addEventListener("stop", () => {
        const blob = new Blob(recordedChunks, { type: mediaRecorder.mimeType || "audio/webm" });
        const ext = blob.type.includes("ogg") ? "ogg" : "webm";
        const file = new File([blob], `voice-note.${ext}`, { type: blob.type || "audio/webm" });
        queueFiles([file]);
        stream.getTracks().forEach((track) => track.stop());
        recordVoiceBtn.textContent = "Voice";
      });
      mediaRecorder.start();
      recordVoiceBtn.textContent = "Stop";
    } catch (_) {
      const msg = addMessage("assistant", "");
      streamText(msg, "Voice recording is unavailable in this browser.");
    }
  });

  newChatBtn.addEventListener("click", () => {
    sessionId = crypto.randomUUID();
    localStorage.setItem(storageKey, sessionId);
    sessions.unshift({ id: sessionId, title: "New chat", updatedAt: Date.now() });
    saveSessions();
    renderHistory();
    messages.innerHTML = "";
    addMessage("assistant", "New chat started.");
    input.focus();
  });

  configForm.addEventListener("submit", saveConfig);
  openSettings.addEventListener("click", () => show(onboarding));
  sendEmailBtn.addEventListener("click", sendEditedEmail);
  cancelEmailBtn.addEventListener("click", closeEmailModal);

  renderHistory();
  loadConfigStatus();
})();
