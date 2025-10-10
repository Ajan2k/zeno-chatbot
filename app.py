from flask import Flask, render_template, request, jsonify
import os, json, uuid, datetime

app = Flask(__name__)
UPLOAD_FOLDER = 'uploads/cvs'
USER_DATA_FOLDER = 'uploads/users'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(USER_DATA_FOLDER, exist_ok=True)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload_cv', methods=['POST'])
def upload_cv():
    if 'file' not in request.files:
        return jsonify({"ok": False, "error": "No file uploaded"})
    file = request.files['file']
    if not file.filename.endswith('.pdf'):
        return jsonify({"ok": False, "error": "PDF only"})
    if len(file.read()) > 5 * 1024 * 1024:
        return jsonify({"ok": False, "error": "Max 5MB"})
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
    user_id = uuid.uuid4().hex
    file_path = os.path.join(USER_DATA_FOLDER, f"user_{user_id}.json")
    data['timestamp'] = datetime.datetime.now().isoformat()
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    return jsonify({"ok": True, "message": "User data saved successfully."})

if __name__ == '__main__':
    app.run(debug=True)
