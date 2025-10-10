from flask import Flask, render_template, request, jsonify
import os, json, uuid, datetime
from werkzeug.utils import secure_filename

app = Flask(__name__)

# ---------------- Configuration ----------------
UPLOAD_FOLDER = 'uploads/cvs'
USER_DATA_FOLDER = 'uploads/users'
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
ALLOWED_EXTENSIONS = {'pdf'}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(USER_DATA_FOLDER, exist_ok=True)

# ---------------- Helpers ----------------
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def save_json(data):
    user_id = uuid.uuid4().hex
    file_path = os.path.join(USER_DATA_FOLDER, f"user_{user_id}.json")
    data['timestamp'] = datetime.datetime.now().isoformat()
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)
    return file_path

# ---------------- Routes ----------------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload_cv', methods=['POST'])
def upload_cv():
    if 'file' not in request.files:
        return jsonify({"ok": False, "error": "No file uploaded"})
    file = request.files['file']
    if file.filename == "":
        return jsonify({"ok": False, "error": "No file selected"})
    if not allowed_file(file.filename):
        return jsonify({"ok": False, "error": "Only PDF files are allowed"})
    
    # Validate file size
    file.seek(0, os.SEEK_END)
    file_length = file.tell()
    if file_length > MAX_FILE_SIZE:
        return jsonify({"ok": False, "error": "File size exceeds 5 MB"})
    file.seek(0)

    filename = f"{uuid.uuid4().hex}.pdf"
    file.save(os.path.join(UPLOAD_FOLDER, filename))
    return jsonify({"ok": True, "filename": filename})

@app.route('/summarize', methods=['POST'])
def summarize():
    data = request.get_json()
    summary = f"""
Name: {data.get('name')}
Phone: {data.get('phone')}
Email: {data.get('email')}
Path: {data.get('path')}
Requirement: {data.get('requirement_text')}
Platform: {data.get('platform')}
Budget: {data.get('budget')}
"""
    return jsonify({"ok": True, "summary": summary.strip()})

@app.route('/save_user_data', methods=['POST'])
def save_user_data():
    data = request.get_json()
    try:
        file_path = save_json(data)
        return jsonify({"ok": True, "message": "User data saved successfully", "file": file_path})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

# ---------------- Production Server ----------------
if __name__ == '__main__':
    # Development only
    app.run(debug=True, host='0.0.0.0', port=5000)
