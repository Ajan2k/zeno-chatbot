(() => {
  const root = document.getElementById("zeno-chat");
  const API_BASE =
    root?.dataset.apiBase ||
    (window.ZENO_CHAT && window.ZENO_CHAT.apiBase) ||
    "";

  const messagesEl = document.getElementById("messages");
  const inputEl = document.getElementById("user-input");
  const sendBtn = document.getElementById("send-btn");
  const chatWidget = document.getElementById("chat-widget");
  const inputArea = document.getElementById("input-area");
  const restartIcon = document.getElementById("restart-icon");

  if (chatWidget) chatWidget.style.display = "flex";

  // Input state management
  function disableInput() {
    inputEl.disabled = true;
    sendBtn.disabled = true;
    inputArea.classList.add("disabled");
    inputEl.placeholder = "Please select an option above...";
  }

  function enableInput(placeholder = "Type your answer...") {
    inputEl.disabled = false;
    sendBtn.disabled = false;
    inputArea.classList.remove("disabled");
    inputEl.placeholder = placeholder;
    inputEl.focus();
  }

  function appendMessage(text, sender = "bot") {
    const div = document.createElement("div");
    div.className = "message " + sender;
    div.textContent = String(text);
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function createButtons(options) {
    const container = document.createElement("div");
    container.className = "options";
    
    // Disable text input when buttons are shown
    disableInput();
    
    options.forEach(opt => {
      const btn = document.createElement("button");
      btn.className = "option-btn";
      btn.type = "button";
      btn.textContent = opt.label;
      btn.onclick = () => {
        // Disable all buttons in this group
        Array.from(container.querySelectorAll("button")).forEach(b => (b.disabled = true));
        
        // Execute callback
        if (typeof opt.onClick === "function") {
          opt.onClick();
        }
      };
      container.appendChild(btn);
    });
    messagesEl.appendChild(container);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function getInitialState() {
    return {
      step: 0,
      name: null,
      company_name: null,
      phone: null,
      email: null,
      path: null,
      has_requirements: null,
      requirement_text: null,
      category: null,
      employee_size: null,
      budget: null,
      budget_amount: null,
      start_time: null,
      cv_filename: null,
      closing_ready: false,
      closing_sent: false,
      awaiting_custom_budget: false,
      conversation_ended: false
    };
  }
  let state = getInitialState();

  const CLOSING_REGEX = /\b(thanks|thank\s*you|thanku|thx|ty|ok|okay|k|sure|great|awesome|cool|perfect|done|noted|sounds\s*good|cheers|good|nice|alright|fine)\b/i;

  function maybeHandleClosing(userText) {
    if (!state.closing_ready || state.closing_sent) return false;
    if (!CLOSING_REGEX.test(userText)) return false;

    const name = state.name ? `, ${state.name}` : "";
    const company = state.company_name ? ` with ${state.company_name}` : "";
    appendMessage(
      `You're most welcome${name}! We're delighted to work${company}. Our team will reach out within 30 minutes. You can also contact us at partha@infinitetechai.com | +91 98847 77171. Have a great day!`
    );
    
    // NOW mark conversation as ended
    state.closing_sent = true;
    state.conversation_ended = true;
    
    // Show restart icon
    restartIcon.classList.add("visible");
    
    // Disable input
    disableInput();
    inputEl.placeholder = "Conversation ended. Click restart icon to begin again.";
    
    return true;
  }

  function startIntro() {
    appendMessage("👋 Hello! Welcome. May I know your name?");
    state.step = 0;
    enableInput("Enter your name...");
  }

  function restartConversation() {
    // Clear messages
    messagesEl.innerHTML = "";
    
    // Reset state
    state = getInitialState();
    
    // Hide restart icon
    restartIcon.classList.remove("visible");
    
    // Start fresh
    startIntro();
  }

  // Restart icon click handler
  restartIcon.addEventListener("click", restartConversation);

  startIntro();

  sendBtn.addEventListener("click", () => {
    const text = inputEl.value.trim();
    if (!text) return;
    
    appendMessage(text, "user");
    inputEl.value = "";

    // Check if this is a closing acknowledgment
    if (maybeHandleClosing(text)) return;
    
    handleTextResponse(text);
  });

  inputEl.addEventListener("keydown", e => {
    if (e.key === "Enter") {
      e.preventDefault();
      sendBtn.click();
    }
  });

  function handleTextResponse(text) {
    switch (state.step) {
      case 0:
        state.name = text;
        state.step = 1;
        appendMessage(`Nice to meet you, ${state.name}! What is your company name?`);
        enableInput("Enter your company name...");
        break;

      case 1:
        state.company_name = text;
        state.step = 2;
        appendMessage("Please enter your contact number.");
        enableInput("Enter your phone number...");
        break;

      case 2: {
        const phonePattern = /^(?:\+91[-\s]?)?(?:0)?[6-9]\d{9}$/;
        if (!phonePattern.test(text)) {
          appendMessage("⚠️ Please enter a valid 10-digit Indian mobile number.");
          enableInput("Enter valid phone number...");
          return;
        }
        state.phone = text;
        state.step = 3;
        appendMessage("Great. Please enter your email address.");
        enableInput("Enter your email...");
        break;
      }

      case 3: {
        const emailOk = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(text);
        if (!emailOk) {
          appendMessage("⚠️ Please enter a valid email address.");
          enableInput("Enter valid email...");
          return;
        }
        state.email = text;
        state.step = 4;
        appendMessage("Thanks for sharing your info!");
        disableInput();
        setTimeout(showMainOptions, 500);
        break;
      }

      case 6:
        state.requirement_text = text;
        state.step = 0;
        appendMessage("Noted your requirements.");
        disableInput();
        showEmployeeSizeOptions();
        break;

      case 8: {
        // Custom budget amount entry
        const cleanText = text.replace(/[₹,\s]/g, "");
        const amt = parseFloat(cleanText);
        
        if (!amt || amt <= 0 || isNaN(amt)) {
          appendMessage("⚠️ Please enter a valid amount in numbers (e.g., 125000 or 1.25L)");
          enableInput("Enter budget amount...");
          return;
        }
        
        state.budget = "Custom";
        state.budget_amount = Math.round(amt);
        state.awaiting_custom_budget = false;
        state.step = 0;
        
        appendMessage(
          `₹${new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 }).format(
            state.budget_amount
          )}`,
          "user"
        );
        
        disableInput();
        askStartTime();
        break;
      }

      default:
        // If conversation hasn't started or ended, ignore random input
        if (state.conversation_ended) {
          // Do nothing, conversation is over
        } else if (state.step === 0 && !state.name) {
          // Restart intro if somehow lost
          startIntro();
        }
        break;
    }
  }

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
    showServiceCategoryOptions();
  }

  function showServiceCategoryOptions() {
    appendMessage("Select a service category:");
    createButtons([
      { label: "AI", onClick: () => handleCategorySelect("AI") },
      { label: "Digital Marketing", onClick: () => handleCategorySelect("Digital Marketing") },
      { label: "SEO", onClick: () => handleCategorySelect("SEO") },
      { label: "Software Development", onClick: () => handleCategorySelect("Software Development") },
      { label: "Web Development", onClick: () => handleCategorySelect("Web Development") },
      { label: "App Development", onClick: () => handleCategorySelect("App Development") },
    ]);
  }

  function handleCategorySelect(cat) {
    appendMessage(cat, "user");
    state.category = cat;
    askRequirementsThenEmployees();
  }

  function askRequirementsThenEmployees() {
    appendMessage("Do you have specific requirements?");
    createButtons([
      {
        label: "Yes",
        onClick: () => {
          appendMessage("Yes", "user");
          state.has_requirements = true;
          appendMessage("Please share your requirements.");
          state.step = 6;
          enableInput("Enter your requirements...");
        },
      },
      {
        label: "No",
        onClick: () => {
          appendMessage("No", "user");
          state.has_requirements = false;
          showEmployeeSizeOptions();
        },
      },
    ]);
  }

  function showEmployeeSizeOptions() {
    appendMessage("How many employees does your company have?");
    createButtons([
      { label: "0-10", onClick: () => selectEmployeeSize("0-10") },
      { label: "10-100", onClick: () => selectEmployeeSize("10-100") },
      { label: "100+", onClick: () => selectEmployeeSize("100+") },
    ]);
  }

  function selectEmployeeSize(label) {
    appendMessage(label, "user");
    state.employee_size = label;

    if (["AI", "Software Development", "Web Development", "App Development"].includes(state.category)) {
      showBudgetOptions();
    } else {
      askStartTime();
    }
  }

  function showBudgetOptions() {
    appendMessage("What is your project budget?");
    createButtons([
      { label: "0 < ₹50K", onClick: () => selectBudget("0 < ₹50K") },
      { label: "₹50K – ₹1L", onClick: () => selectBudget("₹50K – ₹1L") },
      { label: "₹1L – ₹5L", onClick: () => selectBudget("₹1L – ₹5L") },
      { label: "> ₹5L", onClick: () => selectBudget("> ₹5L") },
      { label: "Other (enter amount)", onClick: enterCustomBudget },
    ]);
  }

  function selectBudget(b) {
    appendMessage(b, "user");
    state.budget = b;
    delete state.budget_amount;
    state.awaiting_custom_budget = false;
    askStartTime();
  }

  function enterCustomBudget() {
    appendMessage("Other (enter amount)", "user");
    appendMessage("Please enter your estimated budget amount in ₹ (e.g., 125000 or 1.25L)");
    state.step = 8;
    state.awaiting_custom_budget = true;
    enableInput("Enter budget amount...");
  }

  function askStartTime() {
    appendMessage("When do you plan to start the project?");
    createButtons([
      { label: "Immediately", onClick: () => selectStartTime("Immediately") },
      { label: "1 week", onClick: () => selectStartTime("1 week") },
      { label: "2 weeks", onClick: () => selectStartTime("2 weeks") },
      { label: "1 month", onClick: () => selectStartTime("1 month") },
    ]);
  }

  function selectStartTime(time) {
    appendMessage(time, "user");
    state.start_time = time;
    summarizeDetails();
  }

  function showUploadUI() {
    const box = document.createElement("div");
    box.innerHTML = `
      <div style="margin-top:8px;">
        <input id="cv-file" type="file" accept="application/pdf" style="font-family:inherit;font-size:14px;" />
        <button id="upload-btn" class="option-btn" type="button">Upload</button>
        <div id="upload-status" class="small"></div>
      </div>
    `;
    messagesEl.appendChild(box);
    
    // Keep input disabled during file upload
    disableInput();

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
      form.append("state_json", JSON.stringify(state));

      status.textContent = "Uploading...";
      uploadBtn.disabled = true;

      fetch(API_BASE + "/upload_cv", { method: "POST", body: form })
        .then(r => r.json())
        .then(d => {
          if (d.ok) {
            state.cv_filename = d.filename;
            appendMessage("✅ CV uploaded successfully!");
            if (d.email_sent) {
              appendMessage("📧 CV emailed to our HR team.");
            } else if (d.email_error) {
              appendMessage("⚠️ CV email failed: " + d.email_error);
            }
            
            // Show thank you message and WAIT for user response
            appendMessage("Thank you for applying! Our team will contact you within 30 minutes.");
            
            // Enable closing ready (waiting for user to say thanks/ok)
            state.closing_ready = true;
            
            // Enable input so user can respond
            enableInput("Type 'ok' or 'thanks'...");
            
          } else {
            status.textContent = d.error || "Upload failed.";
            uploadBtn.disabled = false;
          }
        })
        .catch(() => {
          status.textContent = "Upload failed.";
          uploadBtn.disabled = false;
        });
    });
  }

  function summarizeDetails() {
    disableInput();
    
    fetch(API_BASE + "/summarize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state)
    })
      .then(r => r.json())
      .then(d => {
        if (!d.ok) return appendMessage("Error generating summary.");
        appendMessage("Here's your estimated cost breakdown:");
        const div = document.createElement("div");
        div.className = "summary-block";
        div.innerHTML = d.summary;
        messagesEl.appendChild(div);
        messagesEl.scrollTop = messagesEl.scrollHeight;
        if (state.path === "product") showDeclaration();
      })
      .catch(() => appendMessage("Error generating summary."));
  }

  function showDeclaration() {
    appendMessage("Please confirm your details are correct:");
    createButtons([
      { label: "✅ Yes, I agree", onClick: saveUserData },
      { label: "❌ No, I want to edit", onClick: restartConversation },
    ]);
  }

  function saveUserData() {
    appendMessage("📧 Sending your details to our team...", "bot");
    disableInput();
    
    fetch(API_BASE + "/save_user_data", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state),
    })
      .then(r => r.json())
      .then(d => {
        if (d.ok) {
          const emailMsg = d.email_sent
            ? "and emailed to our team."
            : `but email delivery failed${d.email_error ? " (" + d.email_error + ")" : ""}.`;
          
          appendMessage(`✅ Your details have been saved ${emailMsg}`);
          appendMessage("Our team will contact you within 30 minutes. Thank you!");
          
          // Enable closing ready (waiting for user to say thanks/ok)
          state.closing_ready = true;
          
          // Enable input so user can respond
          enableInput("Type 'ok' or 'thanks'...");
          
        } else {
          appendMessage("⚠️ Error saving details. Please try again or contact us directly.");
          enableInput();
        }
      })
      .catch(() => {
        appendMessage("⚠️ Error saving details.");
        enableInput();
      });
  }
})();