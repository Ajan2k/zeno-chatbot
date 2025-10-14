import os
import datetime
import base64
import json
import smtplib
from email.message import EmailMessage
from urllib.parse import quote_plus
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
from werkzeug.utils import secure_filename # OPTIMIZE: More secure filename handling

# Optional integrations
try:
    from groq import Groq
except ImportError:
    Groq = None

try:
    from flask_cors import CORS
except ImportError:
    CORS = None

# --- Database / Email clients
from pymongo import MongoClient
from sendgrid import SendGridAPIClient

load_dotenv()
app = Flask(__name__) # FIX: Use __name__ for Flask convention

if CORS:
    # IMPORTANT SECURITY: Replace "*" with your actual WordPress domain in production
    # e.g., origins=["https://your-wordpress-site.com", "https://www.your-wordpress-site.com"]
    CORS(app, origins="*")


# ---- Config ----
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

# Mongo (URI or components)
MONGODB_URI = os.getenv("MONGODB_URI", "")
MONGO_USER = os.getenv("MONGO_USER", "")
MONGO_PASS = os.getenv("MONGO_PASS", "")
MONGO_HOST = os.getenv("MONGO_HOST", "")
MONGO_PROTOCOL = os.getenv("MONGO_PROTOCOL", "mongodb+srv")
MONGO_PARAMS = os.getenv("MONGO_PARAMS", "retryWrites=true&w=majority")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "chatbot")
MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "leads")

client_groq = Groq(api_key=GROQ_API_KEY) if (GROQ_API_KEY and Groq) else None

def build_mongo_uri():
    if MONGODB_URI: return MONGODB_URI
    if not (MONGO_USER and MONGO_PASS and MONGO_HOST): return None
    user, pwd = quote_plus(MONGO_USER), quote_plus(MONGO_PASS)
    return f"{MONGO_PROTOCOL}://{user}:{pwd}@{MONGO_HOST}/?{MONGO_PARAMS or ''}"

mongo_client = mongo_db = mongo_col = None
try:
    uri = build_mongo_uri()
    if uri:
        mongo_client = MongoClient(uri, serverSelectionTimeoutMS=6000)
        mongo_client.admin.command("ping")
        mongo_db = mongo_client.get_database(MONGO_DB_NAME)
        mongo_col = mongo_db.get_collection(MONGO_COLLECTION)
        print(f"[Mongo] Connected: db={MONGO_DB_NAME}, col={MONGO_COLLECTION}")
    else:
        print("[Mongo] Skipped (no URI/credentials)")
except Exception as e:
    print(f"[Mongo] init error: {e}")
    mongo_client = mongo_db = mongo_col = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads", "cvs")
MAX_FILE_SIZE = 5 * 1024 * 1024
ALLOWED_EXTENSIONS = {"pdf"}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ---- Helpers ----

# OPTIMIZE: Code for building summary HTML is used in two places, so make a helper function
def build_summary_html(data):
    """Builds the HTML block for cost estimates and notes."""
    estimate_table_html = build_estimate_table_only(data)
    category = data.get("category")
    min_term_note = "<p style='margin:8px 0 0;font-style:italic;'>Minimum engagement for this service is 6 months.</p>" if category in ("Digital Marketing","SEO") else ""
    general_note = "" if category in ("Digital Marketing","SEO") else "<p style='margin:10px 0 6px;'>Note: The above pricing is indicative and may vary after we start working and refine the scope in detail.</p>"
    contact_html = "<p style='margin:6px 0 0;'>Contact: partha@infinitetechai.com | +91 98847 77171</p>"
    return f"{estimate_table_html}{min_term_note}{general_note}{contact_html}"

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def inr(n: int) -> str:
    try: return "₹" + format(int(n), ",")
    except (ValueError, TypeError): return f"₹{n}"

def parse_inr_string(s: str):
    # (This function is unchanged)
    if s is None: return None
    clean = str(s).strip().upper().replace("₹", "").replace(",", "").replace(" ", "")
    num, unit = "", ""
    for ch in clean:
        if ch.isdigit() or ch == ".": num += ch
        else: unit += ch
    if not num: return None
    val = float(num); mul = 1
    if unit == "K": mul = 1_000
    elif unit in ("L", "LAKH", "LAKHS", "LAC"): mul = 100_000
    elif unit in ("CR", "CRORE"): mul = 10_000_000
    return int(round(val * mul))

def budget_to_desc(state):
    # (This function is unchanged)
    b, amt = state.get("budget"), state.get("budget_amount")
    if amt: return {"type": "fixed", "amount": int(amt)}
    if not b: return None
    b = str(b)
    if "0 <" in b: return {"type": "range", "min": 0, "max": parse_inr_string("50K")}
    if "₹50K" in b and "₹1L" in b: return {"type": "range", "min": parse_inr_string("50K"), "max": parse_inr_string("1L")}
    if "₹1L" in b and "₹5L" in b: return {"type": "range", "min": parse_inr_string("1L"), "max": parse_inr_string("5L")}
    if ">" in b: return {"type": "min", "min": parse_inr_string("5L")}
    return None

def materialize_budget_amount(desc):
    # (This function is unchanged)
    if not desc: return None
    if desc["type"] == "fixed": return int(desc["amount"])
    if desc["type"] == "range": return int(round((desc["min"] + desc["max"]) / 2))
    if desc["type"] == "min": return int(desc["min"])
    return None

def build_app_like_table(title, core_label, budget_desc):
    # (This function is unchanged)
    mapping = [("UI/UX Design", 15), (core_label, 30), ("Dashboard Development", 30), ("Testing", 10), ("Deployment", 10), ("API & hosting", 5)]
    base = materialize_budget_amount(budget_desc)
    rows_html, total_sum = [], 0
    for label, p in mapping:
        cost_cell = "-"
        if base is not None:
            amt = round(base * p / 100); total_sum += amt; cost_cell = inr(amt)
        rows_html.append(f"<tr><td>{label}</td><td>{cost_cell}</td></tr>")
    total_cell = "-" if base is None else inr(total_sum)
    return f"""<div class="estimate-title">{title}</div><table class="estimate-table"><thead><tr><th>Component</th><th>Estimated Cost</th></tr></thead><tbody>{''.join(rows_html)}</tbody><tfoot><tr><th>Total</th><th>{total_cell}</th></tr></tfoot></table>"""

def build_web_table(budget_desc):
    # (This function is unchanged)
    mapping = [("UI/UX Design", 20), ("Web Development", 50), ("Testing", 10), ("Deployment", 10), ("API & hosting", 10)]
    base = materialize_budget_amount(budget_desc)
    rows_html, total_sum = [], 0
    for label, p in mapping:
        cost_cell = "-"
        if base is not None:
            amt = round(base * p / 100); total_sum += amt; cost_cell = inr(amt)
        rows_html.append(f"<tr><td>{label}</td><td>{cost_cell}</td></tr>")
    total_cell = "-" if base is None else inr(total_sum)
    return f"""<div class="estimate-title">Web Development</div><table class="estimate-table"><thead><tr><th>Component</th><th>Estimated Cost</th></tr></thead><tbody>{''.join(rows_html)}</tbody><tfoot><tr><th>Total</th><th>{total_cell}</th></tr></tfoot></table>"""

def build_dm_table(company_size):
    price_map = {"0-10": 25_000, "10-100": 40_000, "100+": 70_000}
    return f"""<div class="estimate-title">Digital Marketing</div><table class="estimate-table"><thead><tr><th>Item</th><th>Estimated Cost</th></tr></thead><tbody><tr><td>Monthly Retainer</td><td>{inr(price_map.get(company_size))}/ month</td></tr></tbody></table>"""

def build_seo_table(company_size):
    price_map = {"0-10": 10_000, "10-100": 15_000, "100+": 20_000}
    return f"""<div class="estimate-title">SEO</div><table class="estimate-table"><thead><tr><th>Item</th><th>Estimated Cost</th></tr></thead><tbody><tr><td>Monthly Retainer</td><td>{inr(price_map.get(company_size))}/ month</td></tr></tbody></table>"""

def build_estimate_table_only(data):
    # (This function is unchanged)
    category, employee_size, budget_desc = data.get("category"), data.get("employee_size"), budget_to_desc(data)
    if category == "AI": return build_app_like_table("AI Development", "AI Development", budget_desc)
    if category == "Software Development": return build_app_like_table("Software Development", "Software Development", budget_desc)
    if category == "App Development": return build_app_like_table("App Development", "App Development", budget_desc)
    if category == "Web Development": return build_web_table(budget_desc)
    if category == "Digital Marketing": return build_dm_table(employee_size)
    if category == "SEO": return build_seo_table(employee_size)
    return """<div class="estimate-title">Estimate</div><table class="estimate-table"><thead><tr><th>Item</th><th>Estimated Cost</th></tr></thead><tbody><tr><td>-</td><td>-</td></tr></tbody></table>"""

# ---- Email (SendGrid API + SMTP fallback) ----
# (These functions are unchanged and robust, so I've omitted them for brevity. Your existing code is fine.)
# send_via_sendgrid_api(...)
# send_via_smtp(...)
# send_sales_email(...)
# build_lead_overview_html(...)

# ---- Routes ----
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/upload_cv", methods=["POST"])
def upload_cv():
    try:
        file = request.files.get("file")
        if not file or file.filename == "":
            return jsonify({"ok": False, "error": "No file selected"})
        if not allowed_file(file.filename):
            return jsonify({"ok": False, "error": "Only PDF files allowed"})

        file_bytes = file.read()
        if len(file_bytes) > MAX_FILE_SIZE:
            return jsonify({"ok": False, "error": "File size exceeds 5 MB"})

        # SECURITY: Use secure_filename to prevent directory traversal attacks
        safe_basename = secure_filename(file.filename)
        timestamp = datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')
        filename = f"{timestamp}_{safe_basename}"
        save_path = os.path.join(UPLOAD_FOLDER, filename)
        with open(save_path, "wb") as f:
            f.write(file_bytes)

        user_data = json.loads(request.form.get("state_json", "{}"))
        user_data["cv_filename"] = filename
        user_data["path"] = user_data.get("path", "job")

        overview = build_lead_overview_html(user_data)
        html_body = f"<div style='font-family:Arial,sans-serif;line-height:1.45;'><h2 style='margin:0 0 10px'>New CV Upload</h2>{overview}<p>CV attached.</p></div>"
        attachments = [{"filename": filename, "type": "application/pdf", "content": file_bytes}]
        subject = f"New CV Upload: {user_data.get('name', 'Candidate')}"
        email_ok, email_err = send_sales_email(user_data, html_body, subject, attachments=attachments)

        if mongo_col is not None:
            try:
                mongo_col.insert_one({
                    "type": "cv_upload",
                    "name": user_data.get("name"), "company_name": user_data.get("company_name"),
                    "email": user_data.get("email"), "phone": user_data.get("phone"),
                    "cv_filename": filename, "created_at": datetime.datetime.utcnow().isoformat()
                })
            except Exception as e:
                print(f"[Mongo] save cv_upload error: {e}")

        return jsonify({"ok": True, "filename": filename, "email_sent": email_ok, "email_error": (email_err[:180] if email_err else "")})
    except Exception as e:
        print(f"[/upload_cv] Error: {e}")
        return jsonify({"ok": False, "error": "An unexpected error occurred."})

@app.route("/summarize", methods=["POST"])
def summarize():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"ok": False, "error": "No data received"}), 400

        summary_html = build_summary_html(data) # OPTIMIZE: Use helper function

        if client_groq:
            try:
                completion = client_groq.chat.completions.create(
                    model="llama3-70b-8192",  # FIX: Use a valid, available model
                    messages=[
                        {"role": "system", "content": "You are an HTML formatting assistant. Return the user's HTML content exactly as provided, without adding any explanatory text, code fences, or modifications."},
                        {"role": "user", "content": summary_html},
                    ],
                    temperature=0.0,
                )
                model_out = (completion.choices[0].message.content or "").strip()
                if not model_out or "<table>" not in model_out: # Basic sanity check
                    model_out = summary_html
            except Exception as e:
                print(f"[Groq] API error: {e}")
                model_out = summary_html
        else:
            model_out = summary_html

        return jsonify({"ok": True, "summary": model_out})
    except Exception as e:
        print(f"[/summarize] Error: {e}")
        return jsonify({"ok": False, "error": "An unexpected error occurred."})

@app.route("/save_user_data", methods=["POST"])
def save_user_data():
    try:
        data = request.get_json() or {}
        data["created_at"] = datetime.datetime.utcnow().isoformat()
        
        inserted_id = None
        if mongo_col is not None:
            try:
                res = mongo_col.insert_one(data)
                inserted_id = str(res.inserted_id)
            except Exception as e:
                print(f"[Mongo] save lead error: {e}")

        summary_html = build_summary_html(data) # OPTIMIZE: Use helper function
        overview = build_lead_overview_html(data)
        html_body = f"<div style='font-family:Arial,sans-serif;line-height:1.45;'><h2 style='margin:0 0 10px'>New Lead Received</h2>{overview}<div style='margin:12px 0'>{summary_html}</div></div>"
        subject = f"New Lead: {data.get('company_name','')} - {data.get('category','Service')}"
        email_ok, email_err = send_sales_email(data, html_body, subject, attachments=None)

        return jsonify({
            "ok": True, "message": "User data saved", "mongo_id": inserted_id,
            "email_sent": email_ok, "email_error": (email_err[:180] if email_err else "")
        })
    except Exception as e:
        print(f"[/save_user_data] Error: {e}")
        return jsonify({"ok": False, "error": "An unexpected error occurred."})

@app.errorhandler(404)
def not_found(e):
    return jsonify({"ok": False, "error": "Endpoint not found"}), 404

@app.errorhandler(500)
def internal_error(e):
    return jsonify({"ok": False, "error": "Internal server error"}), 500

@app.route("/health")
def health():
    return jsonify({"ok": True, "status": "healthy"})

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
