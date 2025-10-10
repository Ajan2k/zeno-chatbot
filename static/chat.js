const messagesEl = document.getElementById("messages");
const inputEl = document.getElementById("user-input");
const sendBtn = document.getElementById("send-btn");
const closeBtn = document.getElementById("close-btn");
const chatWidget = document.getElementById("chat-widget");

closeBtn.onclick = () => (chatWidget.style.display = "none");

function appendMessage(text, sender = "bot") {
  const div = document.createElement("div");
  div.className = `message ${sender}`;
  div.textContent = text;
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function createButtons(options) {
  const container = document.createElement("div");
  container.className = "options";
  options.forEach(opt => {
    const btn = document.createElement("button");
    btn.className = "option-btn";
    btn.textContent = opt.label;
    btn.onclick = opt.onClick;
    container.appendChild(btn);
  });
  messagesEl.appendChild(container);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

let state = {
  step: 0,
  name: null,
  phone: null,
  email: null,
  path: null,
  requirement_text: null,
  platform: null,
  budget: null,
  cv_filename: null,
};

// ---------------- Initial Greeting ----------------
appendMessage("👋 Hello! Welcome. May I know your name?");

// ---------------- Event Listeners ----------------
sendBtn.addEventListener("click", () => {
  const text = inputEl.value.trim();
  if (!text) return;
  appendMessage(text, "user");
  inputEl.value = "";
  handleTextResponse(text);
});

inputEl.addEventListener("keydown", e => {
  if (e.key === "Enter") {
    e.preventDefault();
    sendBtn.click();
  }
});

// ---------------- Handlers ----------------
function handleTextResponse(text) {
  switch (state.step) {
    case 0:
      state.name = text;
      state.step = 1;
      appendMessage(`Nice to meet you, ${state.name}! Please enter your contact number.`);
      break;
    case 1:
      const phonePattern = /^(\+91[\-\s]?)?[0]?(91)?[6-9]\d{9}$/;
      if (!phonePattern.test(text)) {
        appendMessage("⚠️ Please enter a valid 10-digit Indian mobile number.");
        return;
      }
      state.phone = text;
      state.step = 2;
      appendMessage("Great. Please enter your email address.");
      break;
    case 2:
      if (!/^[^@]+@[^@]+\.[^@]+$/.test(text)) {
        appendMessage("⚠️ Please enter a valid email address.");
        return;
      }
      state.email = text;
      state.step = 3;
      appendMessage("Thanks for sharing your info!");
      setTimeout(showMainOptions, 600);
      break;
    case 6:
      state.requirement_text = text;
      showPlatformOptions();
      break;
  }
}

// ---------------- Main Options ----------------
function showMainOptions() {
  appendMessage("Are you looking for a job or a service/product?");
  createButtons([
    { label: "Looking for a Job", onClick: handleJobOption },
    { label: "Looking for a Service/Product", onClick: handleServiceOption },
  ]);
}

function handleJobOption() {
  appendMessage("I'm looking for a Job", "user");
  state.path = "job";
  appendMessage("Please upload your CV (PDF ≤5 MB).");
  showUploadUI();
}

function handleServiceOption() {
  appendMessage("I'm looking for a Service/Product", "user");
  state.path = "product";
  appendMessage("Do you have specific requirements?");
  createButtons([
    { label: "Yes", onClick: () => handleRequirement(true) },
    { label: "No", onClick: () => handleRequirement(false) },
  ]);
}

function handleRequirement(hasReq) {
  appendMessage(hasReq ? "Yes" : "No", "user");
  appendMessage(hasReq ? "Please tell your specific requirements." : "Please describe your product or service idea.");
  state.step = 6;
}

// ---------------- Platform / Budget ----------------
function showPlatformOptions() {
  appendMessage("Are you looking for a Web App, Mobile App, or Both?");
  createButtons([
    { label: "Web App", onClick: () => selectPlatform("Web App") },
    { label: "Mobile App", onClick: () => selectPlatform("Mobile App") },
    { label: "Both", onClick: () => selectPlatform("Both") },
  ]);
}

function selectPlatform(p) {
  appendMessage(p, "user");
  state.platform = p;
  showBudgetOptions();
}

function showBudgetOptions() {
  appendMessage("What is your project budget?");
  createButtons([
    { label: "< ₹50K", onClick: () => selectBudget("< ₹50K") },
    { label: "₹50K – ₹1L", onClick: () => selectBudget("₹50K – ₹1L") },
    { label: "₹1L – ₹5L", onClick: () => selectBudget("₹1L – ₹5L") },
    { label: "> ₹5L", onClick: () => selectBudget("> ₹5L") },
  ]);
}

function selectBudget(b) {
  appendMessage(b, "user");
  state.budget = b;
  summarizeDetails();
}

// ---------------- Upload UI ----------------
function showUploadUI() {
  const box = document.createElement("div");
  box.innerHTML = `
    <div style="margin-top:6px;">
      <input id="cv-file" type="file" accept="application/pdf" />
      <button id="upload-btn" class="option-btn">Upload</button>
      <div id="upload-status" class="small"></div>
    </div>`;
  messagesEl.appendChild(box);

  const fileInput = box.querySelector("#cv-file");
  const uploadBtn = box.querySelector("#upload-btn");
  const status = box.querySelector("#upload-status");

  uploadBtn.addEventListener("click", () => {
    const file = fileInput.files[0];
    if (!file) return (status.textContent = "Please choose a file.");
    if (file.type !== "application/pdf") return (status.textContent = "PDF only.");
    if (file.size > 5 * 1024 * 1024) return (status.textContent = "Max 5 MB.");
    const form = new FormData();
    form.append("file", file);
    status.textContent = "Uploading...";
    fetch("/upload_cv", { method: "POST", body: form })
      .then(r => r.json())
      .then(d => {
        if (d.ok) {
          state.cv_filename = d.filename;
          appendMessage("✅ CV uploaded successfully!");
          showDeclaration();
        } else status.textContent = d.error || "Upload failed.";
      });
  });
}

// ---------------- Summary / Declaration ----------------
function summarizeDetails() {
  fetch("/summarize", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(state),
  })
    .then(r => r.json())
    .then(d => {
      if (!d.ok) return appendMessage("Error generating summary.");
      appendMessage("Here’s what you’ve entered:");
      const div = document.createElement("div");
      div.className = "summary-block";
      div.textContent = d.summary;
      messagesEl.appendChild(div);
      showDeclaration();
    });
}

function showDeclaration() {
  appendMessage("Please confirm your details are correct:");
  createButtons([
    { label: "✅ Yes, I agree", onClick: saveUserData },
    { label: "❌ No, I want to edit", onClick: () => appendMessage("You can restart the chat to edit details.") },
  ]);
}

function saveUserData() {
  fetch("/save_user_data", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(state),
  })
    .then(r => r.json())
    .then(d => {
      if (d.ok)
        appendMessage("✅ Your details have been saved! Our team will contact you within 30 mins.");
      else appendMessage("⚠️ Error saving details.");
    });
}
