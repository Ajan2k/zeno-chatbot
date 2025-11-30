#file is been upto date
import os
import datetime
import base64
import json
import logging
import ssl
import certifi
from functools import lru_cache

from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

# -----------------------------------------------------------------------------
# App & Config
# -----------------------------------------------------------------------------
load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = Flask(__name__, template_folder=TEMPLATES_DIR, static_folder=STATIC_DIR)
app.url_map.strict_slashes = False

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("zeno-app")

# -----------------------------------------------------------------------------
# Environment Config
# -----------------------------------------------------------------------------
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "no-reply@infinitecard.in")
SALES_EMAILS = [e.strip() for e in os.getenv("SALES_EMAILS", "partha@infinitetechai.com").split(",") if e.strip()]
SENDGRID_TRANSPORT = os.getenv("SENDGRID_TRANSPORT", "auto").lower()
SENDGRID_SANDBOX = os.getenv("SENDGRID_SANDBOX", "0") == "1"

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.sendgrid.net")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "apikey")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", SENDGRID_API_KEY)

CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "partha@infinitetechai.com")
CONTACT_PHONE = os.getenv("CONTACT_PHONE", "+91 98847 77171")

MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "chatbot").strip()
MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "leads").strip()
MONGO_APPLICATIONS_COLLECTION = os.getenv("MONGO_APPLICATIONS_COLLECTION", "applications").strip()

DATA_API_URL = os.getenv("DATA_API_URL", "").rstrip("/")
DATA_API_KEY = os.getenv("DATA_API_KEY", "")
DATA_API_DATA_SOURCE = os.getenv("DATA_API_DATA_SOURCE", "")
DATA_API_DB = os.getenv("DATA_API_DB", MONGO_DB_NAME) or MONGO_DB_NAME
DATA_API_ENABLED = bool(DATA_API_URL and DATA_API_KEY and DATA_API_DATA_SOURCE)

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads", "cvs")
MAX_FILE_SIZE = 5 * 1024 * 1024
ALLOWED_EXTENSIONS = {"pdf"}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE

# CORS (lazy load)
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "").strip()
if CORS_ORIGINS:
    try:
        from flask_cors import CORS
        if CORS_ORIGINS == "*":
            CORS(app, resources={r"/*": {"origins": "*"}})
        else:
            origins = [o.strip() for o in CORS_ORIGINS.split(",") if o.strip()]
            CORS(app, resources={r"/*": {"origins": origins}})
        log.info(f"[CORS] Enabled")
    except ImportError:
        log.warning("[CORS] flask-cors not installed")

# -----------------------------------------------------------------------------
# Lazy MongoDB Connection
# -----------------------------------------------------------------------------
mongo_client = None
mongo_db = None
mongo_col = None
mongo_applications_col = None
MONGO_READY = False
MONGO_LAST_ERROR = ""

def get_mongo_client():
    global mongo_client, mongo_db, mongo_col, mongo_applications_col, MONGO_READY, MONGO_LAST_ERROR
    
    if MONGO_READY and mongo_client:
        return True
    
    if not MONGODB_URI:
        MONGO_LAST_ERROR = "MONGODB_URI not set"
        return False
    
    try:
        from pymongo import MongoClient, errors as pymongo_errors
        
        mongo_client = MongoClient(
            MONGODB_URI,
            tls=True,
            tlsCAFile=certifi.where(),
            serverSelectionTimeoutMS=10000,
            connectTimeoutMS=10000,
            maxPoolSize=10,
            retryWrites=True,
            appname="zeno-chatbot",
        )
        mongo_client.admin.command("ping")
        
        try:
            mongo_db = mongo_client.get_default_database()
        except Exception:
            mongo_db = mongo_client.get_database(MONGO_DB_NAME or "chatbot")
        
        mongo_col = mongo_db.get_collection(MONGO_COLLECTION or "leads")
        mongo_applications_col = mongo_db.get_collection(MONGO_APPLICATIONS_COLLECTION or "applications")
        
        # Create indexes asynchronously
        try:
            mongo_col.create_index("created_at")
            mongo_col.create_index("email")
            mongo_applications_col.create_index("created_at")
            mongo_applications_col.create_index("email")
        except Exception:
            pass
        
        MONGO_READY = True
        MONGO_LAST_ERROR = ""
        log.info(f"[Mongo] Connected to {mongo_db.name}")
        return True
    except Exception as e:
        MONGO_LAST_ERROR = str(e)
        log.error(f"[Mongo] Error: {MONGO_LAST_ERROR}")
        return False

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
    rows_html, total_sum = [], 0
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
    rows_html, total_sum = [], 0
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
# Data API fallback
# -----------------------------------------------------------------------------
def data_api_insert_one(collection: str, document: dict):
    if not DATA_API_ENABLED:
        return False, None, "Data API not configured"
    try:
        import requests
        url = f"{DATA_API_URL}/action/insertOne"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "api-key": DATA_API_KEY,
        }
        payload = {
            "dataSource": DATA_API_DATA_SOURCE,
            "database": DATA_API_DB,
            "collection": collection,
            "document": document,
        }
        r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=15)
        if 200 <= r.status_code < 300:
            body = r.json()
            inserted_id = body.get("insertedId") or body.get("documentId")
            return True, inserted_id, ""
        return False, None, f"{r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, None, str(e)

# def save_doc_resilient(collection: str, doc: dict):
    get_mongo_client()
    
    target_col = None
    if collection == (MONGO_APPLICATIONS_COLLECTION or "applications"):
        target_col = mongo_applications_col
    elif collection == (MONGO_COLLECTION or "leads"):
        target_col = mongo_col
    
    if MONGO_READY and target_col is not None:
        try:
            res = target_col.insert_one(doc)
            return {"ok": True, "backend": "mongo", "id": str(res.inserted_id), "error": ""}
        except Exception as e:
            log.error(f"[Mongo] Insert error: {str(e)}")
    
    ok, inserted_id, err = data_api_insert_one(collection, doc)
    if ok:
        return {"ok": True, "backend": "data_api", "id": inserted_id, "error": ""}
    
    return {"ok": False, "backend": "none", "id": None, "error": err or MONGO_LAST_ERROR}
# In app.py

def save_doc_resilient(collection: str, doc: dict):
    """
    MOCK VERSION: Bypasses database saving completely.
    Returns success immediately to allow the app to run without MongoDB.
    """
    log.info(f"[Mock Save] Skipping DB save for collection: {collection}")
    return {"ok": True, "backend": "mock", "id": "mock_id_123", "error": ""}
# -----------------------------------------------------------------------------
# Email
# -----------------------------------------------------------------------------
def send_via_sendgrid_api(to_email, subject, html, reply_to=None, sandbox=False, attachments=None):
    if not SENDGRID_API_KEY:
        return False, "Missing SENDGRID_API_KEY"
    try:
        from sendgrid import SendGridAPIClient
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
        return False, f"API status={getattr(resp,'status_code',None)}"
    except Exception as e:
        return False, f"API exception: {str(e)}"

def send_via_smtp(to_email, subject, html, reply_to=None, attachments=None):
    if not SMTP_PASSWORD:
        return False, "Missing SMTP password"
    try:
        from email.message import EmailMessage
        import smtplib
        
        msg = EmailMessage()
        msg["From"] = FROM_EMAIL
        msg["To"] = to_email
        msg["Subject"] = subject
        if reply_to:
            msg["Reply-To"] = reply_to
        msg.set_content("HTML email.")
        msg.add_alternative(html, subtype="html")

        if attachments:
            for att in attachments:
                mime = att.get("type", "application/octet-stream")
                maintype, subtype = mime.split("/", 1) if "/" in mime else ("application", "octet-stream")
                msg.add_attachment(att["content"], maintype=maintype, subtype=subtype,
                                   filename=att.get("filename", "attachment"))

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

# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/health")
def health():
    return jsonify({"ok": True, "status": "healthy", "timestamp": datetime.datetime.utcnow().isoformat()})

@app.route("/ping")
def ping():
    return jsonify({"ok": True, "pong": True})

@app.route("/upload_cv", methods=["POST"])
def upload_cv():
    try:
        file = request.files.get("file")
        if not file or file.filename == "":
            return jsonify({"ok": False, "error": "No file selected"}), 400
        if not allowed_file(file.filename):
            return jsonify({"ok": False, "error": "Only PDF files allowed"}), 400

        file_bytes = file.read()
        if len(file_bytes) > MAX_FILE_SIZE:
            return jsonify({"ok": False, "error": "File size exceeds 5 MB"}), 413

        safe_name = secure_filename(file.filename)
        filename = f"{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{safe_name}"
        save_path = os.path.join(UPLOAD_FOLDER, filename)
        with open(save_path, "wb") as f:
            f.write(file_bytes)

        user_data = {}
        data_json = request.form.get("state_json")
        if data_json:
            try:
                user_data = json.loads(data_json)
            except Exception:
                user_data = {}
        user_data["cv_filename"] = filename
        user_data["path"] = user_data.get("path") or "job"
        user_data["created_at"] = datetime.datetime.utcnow().isoformat()

        overview = build_lead_overview_html(user_data)
        html_body = (
            "<div style='font-family:Arial,sans-serif;line-height:1.45;color:#222'>"
            "<h2 style='margin:0 0 10px'>New CV Upload</h2>"
            f"{overview}<p style='margin:8px 0 0;'>CV attached.</p></div>"
        )
        attachments = [{"filename": filename, "type": "application/pdf", "content": file_bytes}]
        subject = f"New CV Upload: {user_data.get('name', 'Candidate')}"

        email_ok, email_err = send_sales_email(user_data, html_body, subject, attachments=attachments)

        doc = {
            "type": "cv_upload",
            "name": user_data.get("name"),
            "company_name": user_data.get("company_name"),
            "email": user_data.get("email"),
            "phone": user_data.get("phone"),
            "cv_filename": filename,
            "created_at": user_data["created_at"]
        }
        store = save_doc_resilient(MONGO_APPLICATIONS_COLLECTION or "applications", doc)

        return jsonify({
            "ok": True,
            "filename": filename,
            "db_saved": store["ok"],
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

        # Optional: Use Groq if available
        if GROQ_API_KEY:
            try:
                from groq import Groq
                client_groq = Groq(api_key=GROQ_API_KEY)
                completion = client_groq.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[
                        {"role": "system", "content": "Return exactly the HTML content provided."},
                        {"role": "user", "content": summary_html},
                    ],
                    temperature=0.0,
                )
                summary_html = (completion.choices[0].message.content or "").strip() or summary_html
            except Exception as e:
                log.warning(f"[Groq] Fallback: {str(e)}")

        return jsonify({"ok": True, "summary": summary_html})
    except Exception as e:
        log.exception("summarize error")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/save_user_data", methods=["POST"])
def save_user_data():
    data = request.get_json(silent=True) or {}
    try:
        data["created_at"] = datetime.datetime.utcnow().isoformat()
        store = save_doc_resilient(MONGO_COLLECTION or "leads", data)

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
            "<div style='font-family:Arial,sans-serif;line-height:1.45;color:#222'>"
            "<h2 style='margin:0 0 10px'>New Lead Received</h2>"
            f"{overview}<div style='margin:12px 0'>{summary_html}</div></div>"
        )
        subject = f"New Lead: {data.get('company_name', '')} - {data.get('category', 'Service')}"

        email_ok, email_err = send_sales_email(data, html_body, subject, attachments=None)

        return jsonify({
            "ok": True,
            "message": "User data processed",
            "db_saved": store["ok"],
            "email_sent": email_ok,
            "email_error": (email_err[:180] if email_err else "")
        })
    except Exception as e:
        log.exception("save_user_data error")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.errorhandler(404)
def not_found(e):
    return jsonify({"ok": False, "error": "Not found"}), 404

@app.errorhandler(500)
def internal_error(e):
    return jsonify({"ok": False, "error": "Internal error"}), 500

@app.errorhandler(413)
def too_large(e):
    return jsonify({"ok": False, "error": "File too large"}), 413

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(debug=False, host="0.0.0.0", port=port)