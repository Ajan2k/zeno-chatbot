import os
import datetime
import base64
import json
import logging
import ssl
import certifi
import requests
from email.message import EmailMessage

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

from pymongo import MongoClient, errors as pymongo_errors
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
# Allow both with/without trailing slashes on routes
app.url_map.strict_slashes = False

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("zeno-app")

# CORS (optional)
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "").strip()
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

CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "partha@infinitetechai.com")
CONTACT_PHONE = os.getenv("CONTACT_PHONE", "+91 98847 77171")

# MongoDB Atlas (driver)
MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "chatbot").strip()
MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "leads").strip()
MONGO_APPLICATIONS_COLLECTION = os.getenv("MONGO_APPLICATIONS_COLLECTION", "applications").strip()

# MongoDB Atlas Data API (fallback over HTTPS 443)
DATA_API_URL = os.getenv("DATA_API_URL", "").rstrip("/")  # e.g. https://ap-south-1.aws.data.mongodb-api.com/app/<appId>/endpoint/data/v1
DATA_API_KEY = os.getenv("DATA_API_KEY", "")
DATA_API_DATA_SOURCE = os.getenv("DATA_API_DATA_SOURCE", "")  # e.g., Cluster0
DATA_API_DB = os.getenv("DATA_API_DB", MONGO_DB_NAME) or MONGO_DB_NAME
DATA_API_ENABLED = bool(DATA_API_URL and DATA_API_KEY and DATA_API_DATA_SOURCE)

# File uploads
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads", "cvs")
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
ALLOWED_EXTENSIONS = {"pdf"}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE

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
# Mongo setup (TLS + diagnostics)
# -----------------------------------------------------------------------------
mongo_client = None
mongo_db = None
mongo_col = None
mongo_applications_col = None
MONGO_READY = False
MONGO_LAST_ERROR = ""

def mask_uri(uri: str) -> str:
    if not uri:
        return ""
    masked = uri
    try:
        if "mongodb+srv://" in uri:
            masked = uri.replace("mongodb+srv://", "")
            if "@" in masked:
                cred, rest = masked.split("@", 1)
                masked = "***:***@" + rest
            masked = "mongodb+srv://" + masked
        elif "mongodb://" in uri:
            masked = uri.replace("mongodb://", "")
            if "@" in masked:
                cred, rest = masked.split("@", 1)
                masked = "***:***@" + rest
            masked = "mongodb://" + masked
    except Exception:
        pass
    return masked

def check_srv_dependency(uri: str):
    if uri.startswith("mongodb+srv://"):
        try:
            import dns.resolver  # noqa: F401
            return True, ""
        except Exception:
            return False, "dnspython not installed. Install with: pip install 'pymongo[srv]' dnspython"
    return True, ""

def connect_mongo():
    global mongo_client, mongo_db, mongo_col, mongo_applications_col, MONGO_READY, MONGO_LAST_ERROR

    log.info(f"[SSL] Python/OpenSSL: {ssl.OPENSSL_VERSION}")

    if not MONGODB_URI:
        MONGO_LAST_ERROR = "MONGODB_URI is empty. Set it in your .env"
        log.warning("[Mongo] %s", MONGO_LAST_ERROR)
        return

    ok_srv, msg_srv = check_srv_dependency(MONGODB_URI)
    if not ok_srv:
        MONGO_LAST_ERROR = msg_srv
        log.error("[Mongo] %s", msg_srv)
        return

    try:
        mongo_client = MongoClient(
            MONGODB_URI,
            tls=True,
            tlsCAFile=certifi.where(),
            serverSelectionTimeoutMS=20000,
            connectTimeoutMS=20000,
            retryWrites=True,
            appname="zeno-chatbot",
        )
        mongo_client.admin.command("ping")

        # Determine DB (prefer DB embedded in URI)
        try:
            default_db = mongo_client.get_default_database()
        except Exception:
            default_db = None

        if default_db is not None:
            db_name = default_db.name
        else:
            db_name = MONGO_DB_NAME or "chatbot"

        mongo_db = mongo_client.get_database(db_name)
        
        # Leads collection
        col_name = MONGO_COLLECTION or "leads"
        mongo_col = mongo_db.get_collection(col_name)
        
        # Applications collection (for CV uploads)
        applications_col_name = MONGO_APPLICATIONS_COLLECTION or "applications"
        mongo_applications_col = mongo_db.get_collection(applications_col_name)

        try:
            # Create indexes for leads
            mongo_col.create_index("created_at")
            mongo_col.create_index("email")
            mongo_col.create_index("type")
            
            # Create indexes for applications
            mongo_applications_col.create_index("created_at")
            mongo_applications_col.create_index("email")
            mongo_applications_col.create_index("cv_filename")
        except Exception as e:
            log.warning("[Mongo] index creation warning: %s", str(e))

        MONGO_READY = True
        MONGO_LAST_ERROR = ""
        log.info(f"[Mongo] Connected: db={db_name}, leads_col={col_name}, applications_col={applications_col_name}, uri={mask_uri(MONGODB_URI)}")
    except pymongo_errors.PyMongoError as e:
        MONGO_READY = False
        MONGO_LAST_ERROR = f"PyMongoError: {str(e)}"
        log.error("[Mongo] init error: %s", MONGO_LAST_ERROR)
    except Exception as e:
        MONGO_READY = False
        MONGO_LAST_ERROR = str(e)
        log.error("[Mongo] init error: %s", MONGO_LAST_ERROR)

connect_mongo()

# -----------------------------------------------------------------------------
# Data API fallback (HTTPS 443)
# -----------------------------------------------------------------------------
def data_api_insert_one(collection: str, document: dict):
    if not DATA_API_ENABLED:
        return False, None, "Data API not configured"
    try:
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
        r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=20)
        if 200 <= r.status_code < 300:
            body = r.json()
            inserted_id = body.get("insertedId") or body.get("documentId")
            return True, inserted_id, ""
        return False, None, f"{r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, None, str(e)

def save_doc_resilient(collection: str, doc: dict):
    """
    Try Mongo driver first; if not ready or fails, try Data API.
    Returns: dict(ok, backend, id, error, driver_error?)
    """
    driver_error = ""
    
    # Determine which collection object to use
    target_col = None
    if collection == (MONGO_APPLICATIONS_COLLECTION or "applications"):
        target_col = mongo_applications_col
    elif collection == (MONGO_COLLECTION or "leads"):
        target_col = mongo_col
    
    can_try_driver = MONGO_READY and (mongo_db is not None) and (target_col is not None)
    
    if can_try_driver:
        try:
            res = target_col.insert_one(doc)
            return {"ok": True, "backend": "mongo", "id": str(res.inserted_id), "error": ""}
        except Exception as e:
            driver_error = str(e)
            log.error("[Mongo] insert error: %s", driver_error)

    ok, inserted_id, err = data_api_insert_one(collection, doc)
    if ok:
        return {"ok": True, "backend": "data_api", "id": inserted_id, "error": ""}
    
    return {
        "ok": False,
        "backend": "none",
        "id": None,
        "error": err or MONGO_LAST_ERROR or driver_error,
        "driver_error": driver_error
    }

# -----------------------------------------------------------------------------
# Optional Groq client
# -----------------------------------------------------------------------------
client_groq = None
if GROQ_API_KEY and Groq:
    try:
        client_groq = Groq(api_key=GROQ_API_KEY)
    except Exception as e:
        log.warning("[Groq] init error: %s", str(e))
        client_groq = None

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

        # Read bytes and enforce size
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
        user_data["created_at"] = datetime.datetime.utcnow().isoformat()

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

        # Save record to APPLICATIONS collection (not leads)
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
            "db_backend": store["backend"],
            "db_id": store["id"],
            "db_error": (store["error"][:180] if store["error"] else ""),
            "mongo_id": store["id"] if store["backend"] == "mongo" else None,
            "mongo_saved": store["backend"] == "mongo" and store["ok"],
            "mongo_error": (MONGO_LAST_ERROR[:180] if not MONGO_READY else ""),
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
        if GROQ_API_KEY and Groq:
            try:
                client_groq = Groq(api_key=GROQ_API_KEY)
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

        # Save to LEADS collection (service/product inquiries)
        store = save_doc_resilient(MONGO_COLLECTION or "leads", data)

        # Email summary
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
            "message": "User data processed",
            "db_saved": store["ok"],
            "db_backend": store["backend"],
            "db_id": store["id"],
            "db_error": (store["error"][:180] if store["error"] else ""),
            "mongo_id": store["id"] if store["backend"] == "mongo" else None,
            "mongo_saved": store["backend"] == "mongo" and store["ok"],
            "mongo_error": (MONGO_LAST_ERROR[:180] if not MONGO_READY else ""),
            "email_sent": email_ok,
            "email_error": (email_err[:180] if email_err else "")
        })
    except Exception as e:
        log.exception("save_user_data error")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "status": "healthy",
        "mongo_ready": MONGO_READY,
        "mongo_error": MONGO_LAST_ERROR,
        "data_api_enabled": DATA_API_ENABLED
    })

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

# ---- DEBUG ----
@app.route("/debug")
def debug_index():
    return (
        "<h3>Debug</h3>"
        "<ul>"
        "<li><a href='/health'>/health</a></li>"
        "<li><a href='/debug/mongo_info'>/debug/mongo_info</a></li>"
        "<li><a href='/debug/test_insert'>/debug/test_insert</a></li>"
        "<li><a href='/debug/ssl'>/debug/ssl</a></li>"
        "<li><a href='/debug/routes'>/debug/routes</a></li>"
        "</ul>"
    )

@app.route("/debug/routes")
def debug_routes():
    routes = []
    for rule in app.url_map.iter_rules():
        methods = ",".join(sorted(m for m in rule.methods if m not in ("HEAD", "OPTIONS")))
        routes.append({"rule": str(rule), "methods": methods, "endpoint": rule.endpoint})
    return jsonify({"ok": True, "routes": routes})

@app.route("/debug/mongo_info")
def debug_mongo_info():
    try:
        names = mongo_db.list_collection_names() if (MONGO_READY and (mongo_db is not None)) else []
        return jsonify({
            "ok": True,
            "db": (mongo_db.name if (MONGO_READY and (mongo_db is not None)) else None),
            "collections": names,
            "has_mongo_col": (mongo_col is not None),
            "has_applications_col": (mongo_applications_col is not None),
            "mongo_ready": MONGO_READY,
            "mongo_error": MONGO_LAST_ERROR,
            "uri_masked": mask_uri(MONGODB_URI),
            "openssl": ssl.OPENSSL_VERSION,
            "certifi_ca": certifi.where(),
            "data_api_enabled": DATA_API_ENABLED,
            "data_api_url": DATA_API_URL if DATA_API_ENABLED else None
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/debug/test_insert")
def debug_test_insert():
    try:
        doc = {"_debug": True, "source": "test_insert", "ts": datetime.datetime.utcnow().isoformat()}
        store = save_doc_resilient(MONGO_COLLECTION or "leads", doc)
        if store["ok"]:
            return jsonify({"ok": True, "backend": store["backend"], "inserted_id": store["id"]})
        return jsonify({"ok": False, "backend": store["backend"], "error": store["error"]}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/debug/ssl")
def debug_ssl():
    import sys, platform
    return jsonify({
        "python": sys.version,
        "platform": platform.platform(),
        "openssl": ssl.OPENSSL_VERSION,
        "certifi_ca": certifi.where(),
    })

@app.route("/debug/try_driver_insert")
def debug_try_driver_insert():
    try:
        if not (MONGO_READY and (mongo_db is not None) and (mongo_col is not None)):
            return jsonify({"ok": False, "error": f"Driver not ready: {MONGO_LAST_ERROR}"}), 500
        doc = {"_debug": True, "source": "try_driver_insert", "ts": datetime.datetime.utcnow().isoformat()}
        res = mongo_col.insert_one(doc)
        return jsonify({"ok": True, "backend": "mongo", "inserted_id": str(res.inserted_id)})
    except Exception as e:
        return jsonify({"ok": False, "backend": "mongo", "error": str(e)}), 500
# ---- /DEBUG ----

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
