import os
import datetime
import base64
import json
import logging
from email.message import EmailMessage
from urllib.parse import quote_plus

from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv

# Optional integrations
try:
    from groq import Groq
except Exception:
    Groq = None

try:
    from flask_cors import CORS
except Exception:
    CORS = None

from pymongo import MongoClient
from sendgrid import SendGridAPIClient
from werkzeug.utils import secure_filename

# -----------------------------------------------------------------------------
# App & Config
# -----------------------------------------------------------------------------
load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = Flask(__name__, template_folder=TEMPLATES_DIR, static_folder=STATIC_DIR)

# Logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("zeno-app")

# CORS (optional)
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "").strip()  # e.g., "https://yoursite.com,https://other.com" or "*"
if CORS and CORS_ORIGINS:
    if CORS_ORIGINS == "*":
        CORS(app, resources={r"/*": {"origins": "*"}})
        log.info("[CORS] Enabled for all origins")
    else:
        origins = [o.strip() for o in CORS_ORIGINS.split(",") if o.strip()]
        CORS(app, resources={r"/*": {"origins": origins}})
        log.info(f"[CORS] Enabled for origins: {origins}")

# -----------------------------------------------------------------------------
# Environment Config
# -----------------------------------------------------------------------------
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "no-reply@infinitecard.in")
SALES_EMAILS = [e.strip() for e in os.getenv("SALES_EMAILS", "partha@infinitetechai.com").split(",") if e.strip()]
SENDGRID_TRANSPORT = os.getenv("SENDGRID_TRANSPORT", "auto").lower()  # auto | api | smtp
SENDGRID_SANDBOX = os.getenv("SENDGRID_SANDBOX", "0") == "1"

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.sendgrid.net")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "apikey")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", SENDGRID_API_KEY)

# Contact info in summary emails
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "partha@infinitetechai.com")
CONTACT_PHONE = os.getenv("CONTACT_PHONE", "+91 98847 77171")

# Mongo (URI or components)
MONGODB_URI = os.getenv("MONGODB_URI", "")
MONGO_USER = os.getenv("MONGO_USER", "")
MONGO_PASS = os.getenv("MONGO_PASS", "")
MONGO_HOST = os.getenv("MONGO_HOST", "")
MONGO_PROTOCOL = os.getenv("MONGO_PROTOCOL", "mongodb+srv")
MONGO_PARAMS = os.getenv("MONGO_PARAMS", "retryWrites=true&w=majority")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "chatbot")
MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "leads")

# File uploads
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads", "cvs")
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
ALLOWED_EXTENSIONS = {"pdf"}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE  # Flask will enforce 413

# -----------------------------------------------------------------------------
# Clients
# -----------------------------------------------------------------------------
client_groq = Groq(api_key=GROQ_API_KEY) if (GROQ_API_KEY and Groq) else None

def build_mongo_uri():
    if MONGODB_URI:
        return MONGODB_URI
    if not (MONGO_USER and MONGO_PASS and MONGO_HOST):
        return None
    user = quote_plus(MONGO_USER)
    pwd = quote_plus(MONGO_PASS)
    params = MONGO_PARAMS or ""
    return f"{MONGO_PROTOCOL}://{user}:{pwd}@{MONGO_HOST}/?{params}"

mongo_client = None
mongo_db = None
mongo_col = None
try:
    uri = build_mongo_uri()
    if uri:
        mongo_client = MongoClient(uri, serverSelectionTimeoutMS=6000)
        mongo_client.admin.command("ping")
        mongo_db = mongo_client.get_database(MONGO_DB_NAME)
        mongo_col = mongo_db.get_collection(MONGO_COLLECTION)
        log.info(f"[Mongo] Connected: db={MONGO_DB_NAME}, col={MONGO_COLLECTION}")
    else:
        log.info("[Mongo] Skipped (no URI/credentials)")
except Exception as e:
    log.error("[Mongo] init error: %s", str(e))
    mongo_client = mongo_db = mongo_col = None

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def inr(n: int) -> str:
    try:
        return "₹" + format(int(n), ",")
    except Exception:
        return f"₹{n}"

def parse_inr_string(s: str):
    if s is None:
        return None
    clean = str(s).strip().upper().replace("₹", "").replace(",", "").replace(" ", "")
    num = ""
    unit = ""
    for ch in clean:
        if ch.isdigit() or ch == ".":
            num += ch
        else:
            unit += ch
    if not num:
        return None
    val = float(num)
    mul = 1
    if unit == "K":
        mul = 1_000
    elif unit in ("L", "LAKH", "LAKHS", "LAC"):
        mul = 100_000
    elif unit in ("CR", "CRORE"):
        mul = 10_000_000
    return int(round(val * mul))

def budget_to_desc(state):
    b = state.get("budget")
    amt = state.get("budget_amount")
    if amt:
        return {"type": "fixed", "amount": int(amt)}
    if not b:
        return None
    b = str(b)
    if "0 <" in b:
        return {"type": "range", "min": 0, "max": parse_inr_string("50K")}
    if "₹50K" in b and "₹1L" in b:
        return {"type": "range", "min": parse_inr_string("50K"), "max": parse_inr_string("1L")}
    if "₹1L" in b and "₹5L" in b:
        return {"type": "range", "min": parse_inr_string("1L"), "max": parse_inr_string("5L")}
    if ">" in b:
        return {"type": "min", "min": parse_inr_string("5L")}
    return None

def materialize_budget_amount(desc):
    if not desc:
        return None
    if desc["type"] == "fixed":
        return int(desc["amount"])
    if desc["type"] == "range":
        return int(round((desc["min"] + desc["max"]) / 2))
    if desc["type"] == "min":
        return int(desc["min"])
    return None

def build_app_like_table(title, core_label, budget_desc):
    mapping = [
        ("UI/UX Design", 15),
        (core_label, 30),
        ("Dashboard Development", 30),
        ("Testing", 10),
        ("Deployment", 10),
        ("API & hosting", 5),
    ]
    base = materialize_budget_amount(budget_desc)
    rows_html = []
    total_sum = 0
    for label, p in mapping:
        if base is None:
            cost_cell = "-"
        else:
            amt = round(base * p / 100)
            total_sum += amt
            cost_cell = inr(amt)
        rows_html.append(f"<tr><td>{label}</td><td>{cost_cell}</td></tr>")
    total_cell = "-" if base is None else inr(total_sum)
    return f"""
<div class="estimate-title">{title}</div>
<table class="estimate-table">
  <thead><tr><th>Component</th><th>Estimated Cost</th></tr></thead>
  <tbody>{''.join(rows_html)}</tbody>
  <tfoot><tr><th>Total</th><th>{total_cell}</th></tr></tfoot>
</table>
"""

def build_web_table(budget_desc):
    mapping = [
        ("UI/UX Design", 20),
        ("Web Development", 50),
        ("Testing", 10),
        ("Deployment", 10),
        ("API & hosting", 10),
    ]
    base = materialize_budget_amount(budget_desc)
    rows_html = []
    total_sum = 0
    for label, p in mapping:
        if base is None:
            cost_cell = "-"
        else:
            amt = round(base * p / 100)
            total_sum += amt
            cost_cell = inr(amt)
        rows_html.append(f"<tr><td>{label}</td><td>{cost_cell}</td></tr>")
    total_cell = "-" if base is None else inr(total_sum)
    return f"""
<div class="estimate-title">Web Development</div>
<table class="estimate-table">
  <thead><tr><th>Component</th><th>Estimated Cost</th></tr></thead>
  <tbody>{''.join(rows_html)}</tbody>
  <tfoot><tr><th>Total</th><th>{total_cell}</th></tr></tfoot>
</table>
"""

def build_dm_table(company_size):
    price_map = {"0-10": 25_000, "10-100": 40_000, "100+": 70_000}
    amt = price_map.get(company_size)
    return f"""
<div class="estimate-title">Digital Marketing</div>
<table class="estimate-table">
  <thead><tr><th>Item</th><th>Estimated Cost</th></tr></thead>
  <tbody><tr><td>Monthly Retainer</td><td>{inr(amt)}/ month</td></tr></tbody>
</table>
"""

def build_seo_table(company_size):
    price_map = {"0-10": 10_000, "10-100": 15_000, "100+": 20_000}
    monthly = price_map.get(company_size)
    return f"""
<div class="estimate-title">SEO</div>
<table class="estimate-table">
  <thead><tr><th>Item</th><th>Estimated Cost</th></tr></thead>
  <tbody><tr><td>Monthly Retainer</td><td>{inr(monthly)}/ month</td></tr></tbody>
</table>
"""

def build_estimate_table_only(data):
    category = data.get("category")
    employee_size = data.get("employee_size")
    budget_desc = budget_to_desc(data)

    if category == "AI":
        return build_app_like_table("AI Development", "AI Development", budget_desc)
    if category == "Software Development":
        return build_app_like_table("Software Development", "Software Development", budget_desc)
    if category == "App Development":
        return build_app_like_table("App Development", "App Development", budget_desc)
    if category == "Web Development":
        return build_web_table(budget_desc)
    if category == "Digital Marketing":
        return build_dm_table(employee_size)
    if category == "SEO":
        return build_seo_table(employee_size)
    return """
<div class="estimate-title">Estimate</div>
<table class="estimate-table">
  <thead><tr><th>Item</th><th>Estimated Cost</th></tr></thead>
  <tbody><tr><td>-</td><td>-</td></tr></tbody>
</table>
"""

# -----------------------------------------------------------------------------
# Email (SendGrid API + SMTP fallback)
# -----------------------------------------------------------------------------
def send_via_sendgrid_api(to_email, subject, html, reply_to=None, sandbox=False, attachments=None):
    if not SENDGRID_API_KEY:
        return False, "Missing SENDGRID_API_KEY"
    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        body = {
            "personalizations": [{"to": [{"email": to_email}], "subject": subject}],
            "from": {"email": FROM_EMAIL},
            "content": [{"type": "text/html", "value": html}],
            "tracking_settings": {"click_tracking": {"enable": False, "enable_text": False}},
        }
        if reply_to:
            body["reply_to"] = {"email": reply_to}
        if sandbox:
            body["mail_settings"] = {"sandbox_mode": {"enable": True}}
        if attachments:
            att_list = []
            for att in attachments:
                b64 = base64.b64encode(att["content"]).decode("ascii")
                att_list.append({
                    "content": b64,
                    "type": att.get("type", "application/octet-stream"),
                    "filename": att.get("filename", "attachment"),
                    "disposition": "attachment"
                })
            body["attachments"] = att_list

        resp = sg.client.mail.send.post(request_body=body)
        ok = 200 <= getattr(resp, "status_code", 0) < 300
        if ok:
            return True, ""
        err = ""
        try:
            err = getattr(resp, "body", b"").decode("utf-8")
        except Exception:
            err = str(getattr(resp, "status_code", ""))
        return False, f"API status={getattr(resp,'status_code',None)} {err}"
    except Exception as e:
        return False, f"API exception: {str(e)}"

def send_via_smtp(to_email, subject, html, reply_to=None, attachments=None):
    if not SMTP_PASSWORD:
        return False, "Missing SMTP password (SENDGRID_API_KEY)"
    try:
        msg = EmailMessage()
        msg["From"] = FROM_EMAIL
        msg["To"] = to_email
        msg["Subject"] = subject
        if reply_to:
            msg["Reply-To"] = reply_to
        msg.set_content("HTML email. Please view in an HTML-compatible client.")
        msg.add_alternative(html, subtype="html")

        if attachments:
            for att in attachments:
                mime = att.get("type", "application/octet-stream")
                maintype, subtype = mime.split("/", 1) if "/" in mime else ("application", "octet-stream")
                msg.add_attachment(att["content"], maintype=maintype, subtype=subtype,
                                   filename=att.get("filename", "attachment"))

        import smtplib
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        return True, ""
    except Exception as e:
        return False, f"SMTP exception: {str(e)}"

def send_sales_email(data, html_content, subject, attachments=None):
    recipients = SALES_EMAILS or []
    if not recipients:
        return False, "No recipients configured"

    successes = 0
    last_err = ""
    reply_to = data.get("email") or None
    try_api = SENDGRID_TRANSPORT in ("auto", "api")
    try_smtp = SENDGRID_TRANSPORT in ("auto", "smtp")

    if try_api:
        for to in recipients:
            ok, err = send_via_sendgrid_api(to, subject, html_content, reply_to, SENDGRID_SANDBOX, attachments)
            if ok:
                successes += 1
            else:
                last_err = err

    if successes == 0 and try_smtp:
        for to in recipients:
            ok, err = send_via_smtp(to, subject, html_content, reply_to, attachments)
            if ok:
                successes += 1
            else:
                last_err = err

    return (successes > 0), last_err

def build_lead_overview_html(data):
    rows = ""
    for label, key in [
        ("Name", "name"),
        ("Company", "company_name"),
        ("Email", "email"),
        ("Phone", "phone"),
        ("Path", "path"),
        ("Category", "category"),
        ("Employee Size", "employee_size"),
        ("Budget", "budget"),
        ("Custom Amount", "budget_amount"),
        ("Start Time", "start_time"),
        ("Requirements", "requirement_text"),
        ("CV Filename", "cv_filename"),
    ]:
        val = data.get(key, "")
        if val is None:
            val = ""
        rows += f"<tr><td style='padding:6px 8px;border:1px solid #eee;'>{label}</td><td style='padding:6px 8px;border:1px solid #eee;'>{val}</td></tr>"
    return f"<h3 style='margin:10px 0 6px;'>Lead Details</h3><table style='border-collapse:collapse;font-size:14px;'><tbody>{rows}</tbody></table>"

# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/upload_cv", methods=["POST"])
def upload_cv():
    try:
        file = request.files.get("file")
        if not file or file.filename == "":
            return jsonify({"ok": False, "error": "No file selected"}), 400
        if not allowed_file(file.filename):
            return jsonify({"ok": False, "error": "Only PDF files allowed"}), 400

        # Read bytes and enforce size (Flask also enforces MAX_CONTENT_LENGTH)
        file_bytes = file.read()
        if len(file_bytes) > MAX_FILE_SIZE:
            return jsonify({"ok": False, "error": "File size exceeds 5 MB"}), 413

        # Save file
        safe_name = secure_filename(file.filename)
        filename = f"{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{safe_name}"
        save_path = os.path.join(UPLOAD_FOLDER, filename)
        with open(save_path, "wb") as f:
            f.write(file_bytes)

        # Parse state
        user_data = {}
        data_json = request.form.get("state_json")
        if data_json:
            try:
                user_data = json.loads(data_json)
            except Exception:
                user_data = {}

        user_data["cv_filename"] = filename
        user_data["path"] = user_data.get("path") or "job"

        # Email with attachment
        overview = build_lead_overview_html(user_data)
        html_body = (
            "<div style='font-family:Arial,Helvetica,sans-serif;line-height:1.45;color:#222'>"
            "<h2 style='margin:0 0 10px'>New CV Upload</h2>"
            f"{overview}"
            "<p style='margin:8px 0 0;'>CV attached.</p>"
            "</div>"
        )
        attachments = [{"filename": filename, "type": "application/pdf", "content": file_bytes}]
        subject = f"New CV Upload: {user_data.get('name', 'Candidate')}"

        email_ok, email_err = send_sales_email(user_data, html_body, subject, attachments=attachments)

        # Save minimal record
        if mongo_col is not None:
            try:
                mongo_col.insert_one({
                    "type": "cv_upload",
                    "name": user_data.get("name"),
                    "company_name": user_data.get("company_name"),
                    "email": user_data.get("email"),
                    "phone": user_data.get("phone"),
                    "cv_filename": filename,
                    "created_at": datetime.datetime.utcnow().isoformat()
                })
            except Exception as e:
                log.error("[Mongo] save cv_upload error: %s", str(e))

        return jsonify({
            "ok": True,
            "filename": filename,
            "email_sent": email_ok,
            "email_error": (email_err[:180] if email_err else "")
        })
    except Exception as e:
        log.exception("upload_cv error")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/summarize", methods=["POST"])
def summarize():
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"ok": False, "error": "No data received"}), 400

        estimate_table_html = build_estimate_table_only(data)
        category = data.get("category")
        min_term_note = (
            "<p style='margin:8px 0 0;font-style:italic;'>Minimum engagement for this service is 6 months.</p>"
            if category in ("Digital Marketing", "SEO") else ""
        )
        general_note = (
            "" if category in ("Digital Marketing", "SEO")
            else "<p style='margin:10px 0 6px;'>Note: The above pricing is indicative and may vary after we start working and refine the scope in detail.</p>"
        )
        contact_html = f"<p style='margin:6px 0 0;'>Contact: {CONTACT_EMAIL} | {CONTACT_PHONE}</p>"
        summary_html = f"{estimate_table_html}{min_term_note}{general_note}{contact_html}"

        model_out = summary_html
        if client_groq:
            try:
                completion = client_groq.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[
                        {"role": "system", "content": "Return exactly the HTML content provided by the user. Do not add or modify content."},
                        {"role": "user", "content": summary_html},
                    ],
                    temperature=0.0,
                )
                model_out = (completion.choices[0].message.content or "").strip() or summary_html
            except Exception as e:
                log.warning("[Groq] Fallback to raw summary: %s", str(e))
                model_out = summary_html

        return jsonify({"ok": True, "summary": model_out})
    except Exception as e:
        log.exception("summarize error")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/save_user_data", methods=["POST"])
def save_user_data():
    data = request.get_json(silent=True) or {}
    try:
        data["created_at"] = datetime.datetime.utcnow().isoformat()

        inserted_id = None
        if mongo_col is not None:
            try:
                res = mongo_col.insert_one(data)
                inserted_id = str(res.inserted_id)
            except Exception as e:
                log.error("[Mongo] save lead error: %s", str(e))

        estimate_table_html = build_estimate_table_only(data)
        category = data.get("category")
        min_term_note = (
            "<p style='margin:8px 0 0;font-style:italic;'>Minimum engagement for this service is 6 months.</p>"
            if category in ("Digital Marketing", "SEO") else ""
        )
        general_note = (
            "" if category in ("Digital Marketing", "SEO")
            else "<p style='margin:10px 0 6px;'>Note: The above pricing is indicative and may vary after we start working and refine the scope in detail.</p>"
        )
        contact_html = f"<p style='margin:6px 0 0;'>Contact: {CONTACT_EMAIL} | {CONTACT_PHONE}</p>"
        summary_html = f"{estimate_table_html}{min_term_note}{general_note}{contact_html}"

        overview = build_lead_overview_html(data)
        html_body = (
            "<div style='font-family:Arial,Helvetica,sans-serif;line-height:1.45;color:#222'>"
            "<h2 style='margin:0 0 10px'>New Lead Received</h2>"
            f"{overview}"
            f"<div style='margin:12px 0'>{summary_html}</div>"
            "</div>"
        )
        subject = f"New Lead: {data.get('company_name', '')} - {data.get('category', 'Service')}"

        email_ok, email_err = send_sales_email(data, html_body, subject, attachments=None)

        return jsonify({
            "ok": True,
            "message": "User data saved successfully",
            "mongo_id": inserted_id,
            "email_sent": email_ok,
            "email_error": (email_err[:180] if email_err else "")
        })
    except Exception as e:
        log.exception("save_user_data error")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/health")
def health():
    return jsonify({"ok": True, "status": "healthy"})

# Error handlers
@app.errorhandler(404)
def not_found(e):
    return jsonify({"ok": False, "error": "Endpoint not found"}), 404

@app.errorhandler(500)
def internal_error(e):
    return jsonify({"ok": False, "error": "Internal server error"}), 500

@app.errorhandler(413)
def too_large(e):
    return jsonify({"ok": False, "error": "File too large (max 5 MB)"}), 413

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
