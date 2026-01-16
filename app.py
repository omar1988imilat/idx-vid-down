#!/usr/bin/env python3
import subprocess
import os
import re
import json

def save_to_gofile_history(filename, download_page, size, file_id=None, direct_link=None):
    """Save upload info to local history file"""
    try:
        import time
        history = []
        if os.path.exists(GOFILE_HISTORY_FILE):
            with open(GOFILE_HISTORY_FILE, 'r') as f:
                history = json.load(f)
        
        entry = {
            "name": filename,
            "link": download_page,
            "id": file_id or download_page.split('/')[-1],
            "direct_link": direct_link,
            "size": size,
            "createTime": int(time.time()),
            "type": "file"
        }
        # Avoid duplicates
        if not any(item.get("link") == download_page for item in history):
            history.insert(0, entry) # Newest first
            # Keep last 100 uploads
            history = history[:100]
            with open(GOFILE_HISTORY_FILE, 'w') as f:
                json.dump(history, f, indent=4)
    except Exception as e:
        print(f"Error saving to Gofile history: {e}")

import threading
import queue
import shutil
import sys
from urllib.parse import unquote, quote
from datetime import timedelta
import requests

def get_gofile_website_token():
    """Dynamically fetch the required X-Website-Token from Gofile's JS"""
    try:
        # Try config.js first as it's the primary location for appdata.wt
        r = requests.get("https://gofile.io/dist/js/config.js", timeout=10)
        m = re.search(r'appdata\.wt\s*=\s*["\']([^"\']+)["\']', r.text)
        if m: return m.group(1)
        
        # Fallback to home page if not found in config.js
        r = requests.get("https://gofile.io/", timeout=10)
        m = re.search(r'wt\s*[:=]\s*["\']([^"\']+)["\']', r.text)
        if m: return m.group(1)
    except:
        pass
    return "4fd6sg89d7s6" # Hardcoded fallback if detection fails

# --- ADD DENO TO PATH ---
# Deno installs to ~/.deno/bin by default. 
# We need to make sure Python and yt-dlp can see it.
deno_bin = os.path.expanduser("~/.deno/bin")
if os.path.exists(deno_bin) and deno_bin not in os.environ["PATH"]:
    print(f"Adding Deno to PATH: {deno_bin}")
    os.environ["PATH"] += os.pathsep + deno_bin

    
# --- YTDLP PATH CONFIGURATION ---
def find_ytdlp():
    # 1. Try to find 'yt-dlp' in the system PATH
    path = shutil.which("yt-dlp")
    if path:
        return path
    
    # 2. Look in the same directory as the python executable (e.g., .venv/bin/)
    python_dir = os.path.dirname(sys.executable)
    possible_path = os.path.join(python_dir, "yt-dlp")
    if os.path.exists(possible_path):
        return possible_path

    # 3. Last resort: default to "yt-dlp" command string
    return "yt-dlp"

YTDLP_PATH = find_ytdlp()
print(f"Using yt-dlp at: {YTDLP_PATH}")

# Get ffmpeg and ffprobe paths - priority: env variables > app bin > system PATH > common paths
# App-specific and common system paths for ffmpeg (Linux)
COMMON_PATHS = [
    "/app/flask/bin", "/app/bin", "/usr/bin", "/usr/local/bin", "/opt/bin"
]


def find_command(cmd_name):
    """Find command in env var, system PATH, or common paths"""
    # Check environment variable first
    env_path = os.environ.get(f"{cmd_name.upper()}_PATH", "").strip()
    if env_path and os.path.exists(env_path):
        return env_path

    # Check system PATH using shutil.which
    found = shutil.which(cmd_name)
    if found:
        return found

    # Check common system paths (useful for isolated Python environments)
    for base_path in COMMON_PATHS:
        full_path = os.path.join(base_path, cmd_name)
        if os.path.exists(full_path) and os.access(full_path, os.X_OK):
            return full_path

    # Try subprocess to find it via shell which
    try:
        result = subprocess.run(["sh", "-c", f"which {cmd_name}"],
                                capture_output=True,
                                text=True,
                                timeout=2)
        if result.returncode == 0:
            path = result.stdout.strip()
            if path and os.path.exists(path):
                return path
    except:
        pass

    # Fallback to just the command name (will fail at runtime with useful error)
    return cmd_name


FFMPEG_PATH = find_command("ffmpeg")
FFPROBE_PATH = find_command("ffprobe")


def ffmpeg_merge_with_progress(files, output_path):
    list_file = os.path.join(DOWNLOAD_FOLDER, "merge_list.txt")

    with open(list_file, "w", encoding="utf-8") as f:
        for file in files:
            # Fix for SyntaxError: f-string expression part cannot include a backslash
            escaped_file = file.replace("'", "'\\''")
            f.write(f"file '{escaped_file}'\n")

    def run(cmd):
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        if process.stdout:
            for line in process.stdout:
                if "out_time_ms" in line:
                    try:
                        out_ms = int(line.split("=")[1])
                        progress_queue.put({"stage": "Merging…"})
                    except:
                        pass
        ret_code = process.wait()
        if os.path.exists(list_file):
            os.remove(list_file)
        return ret_code

    # 1️⃣ TRY FAST COPY MERGE
    cmd_copy = [
        FFMPEG_PATH,
        "-f", "concat",
        "-safe", "0",
        "-i", list_file,
        "-c", "copy",
        output_path
    ]

    ret = run(cmd_copy)

    # 2️⃣ FALLBACK: RE-ENCODE (GUARANTEED)
    if ret != 0 or not os.path.exists(output_path):
        progress_queue.put({"stage": "Re-encoding for compatibility…"})

        cmd_reencode = [
            FFMPEG_PATH,
            "-f", "concat",
            "-safe", "0",
            "-i", list_file,
            "-map", "0:v:0",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-movflags", "+faststart",
            output_path
        ]

        subprocess.run(cmd_reencode)

    # cleanup
    progress_queue.put({"log": "DONE"})


# Check if paths exist and are executable
def check_ffmpeg_available():
    try:
        # Try to run ffmpeg
        result = subprocess.run([FFMPEG_PATH, "-version"],
                                capture_output=True,
                                timeout=5,
                                text=True)
        if result.returncode == 0:
            return True, "OK"
        return False, f"Exit code {result.returncode}"
    except subprocess.TimeoutExpired:
        return False, "Timeout"
    except FileNotFoundError:
        return False, "Command not found in PATH"
    except Exception as e:
        return False, str(e)[:100]


# Replit environment - dependencies installed via packager_tool
ffmpeg_available, ffmpeg_error = check_ffmpeg_available()
ffmpeg_status = "✅ Available" if ffmpeg_available else "❌ NOT FOUND"
print(f"FFmpeg: {ffmpeg_status}")
print(f"  FFMPEG_PATH: {FFMPEG_PATH}")
if not ffmpeg_available:
    print(f"    - error: {ffmpeg_error}")
print("✅ Python packages: flask, yt-dlp, requests (pre-installed)")

from flask import Flask, render_template_string, request, send_from_directory, flash, url_for, Response, redirect, session, jsonify
from werkzeug.utils import secure_filename
import requests
import yt_dlp

# create app first
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET",
                                "replit-video-downloader-secret-key")

# Global for current encoding process
current_process = None

# Jinja Filter for Date Formatting
from datetime import datetime
@app.template_filter('datetime')
def format_datetime(value):
    if not value: return ""
    try:
        return datetime.fromtimestamp(value).strftime('%Y-%m-%d %H:%M')
    except:
        return str(value)

# -----------------------------
# Simple one-password protection
# -----------------------------
# Priority 1: Environment variable 'APP_PASSWORD'
# Priority 2: Hardcoded default '1234'
APP_PASSWORD = os.environ.get("APP_PASSWORD", "1234")

if APP_PASSWORD == "1234":
    print("⚠️ WARNING: Using default password '1234'. Please set APP_PASSWORD environment variable for security.")

# Admin username (hardcoded)
ADMIN_USERNAME = "admin"

# Authentication decorator
from functools import wraps

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

# Login route
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        
        if username == ADMIN_USERNAME and password == APP_PASSWORD:
            session['authenticated'] = True
            session.permanent = True  # Remember login
            next_page = request.args.get('next')
            return redirect(next_page or url_for('index'))
        else:
            flash("❌ Invalid username or password", "error")
    
    # Simple login page with popup styling
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Login - Video Downloader</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { 
                font-family: 'Segoe UI', 'Roboto', sans-serif; 
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 20px;
            }
            .login-box {
                background: white;
                padding: 40px;
                border-radius: 16px;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                width: 100%;
                max-width: 400px;
            }
            h1 { 
                font-size: 24px; 
                margin-bottom: 10px; 
                color: #1a1a1a;
                text-align: center;
            }
            p { 
                text-align: center; 
                color: #666; 
                margin-bottom: 30px; 
                font-size: 14px;
            }
            label { 
                display: block; 
                font-size: 13px; 
                font-weight: 600; 
                color: #3a3a3a; 
                margin-bottom: 6px; 
            }
            input[type="text"], input[type="password"] {
                width: 100%;
                padding: 12px;
                margin-bottom: 16px;
                border: 1px solid #d0d0d0;
                border-radius: 8px;
                font-size: 14px;
                font-family: inherit;
            }
            input:focus {
                outline: none;
                border-color: #667eea;
                box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
            }
            button {
                width: 100%;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 12px;
                border: none;
                border-radius: 8px;
                font-size: 14px;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.2s ease;
            }
            button:hover {
                transform: translateY(-2px);
                box-shadow: 0 6px 16px rgba(102, 126, 234, 0.3);
            }
            .flash-msg {
                padding: 12px;
                margin-bottom: 20px;
                border-radius: 8px;
                font-size: 13px;
                background: rgba(220, 53, 69, 0.1);
                color: #721c24;
                border-left: 4px solid #dc3545;
            }
        </style>
    </head>
    <body>
        <div class="login-box">
            <h1>🎬 Video Downloader</h1>
            <p>Please sign in to continue</p>
            
            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div class="flash-msg">{{ message }}</div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
            
            <form method="POST">
                <label>Username:</label>
                <input type="text" name="username" value="admin" required autofocus>
                
                <label>Password:</label>
                <input type="password" name="password" required>
                
                <button type="submit">Sign In</button>
            </form>
        </div>
    </body>
    </html>
    """)

# Logout route
@app.route("/logout")
def logout():
    session.clear()
    flash("✅ You have been logged out", "info")
    return redirect(url_for('login'))


# -----------------------------
# Health Check Route for UptimeRobot
# -----------------------------
@app.route("/health")
def health():
    return "OK", 200


# -----------------------------
# Suppress /health logs in Replit console
# -----------------------------
@app.after_request
def suppress_health_logging(response):
    if request.path == "/health":
        # disable logging for health checks
        import logging
        logging.getLogger('werkzeug').setLevel(logging.ERROR)
    return response


# -----------------------------
# Configuration
# -----------------------------
FLASK_PORT = 5000
DOWNLOAD_FOLDER = os.path.join(os.getcwd(), "downloads")
GOFILE_HISTORY_FILE = os.path.join(os.getcwd(), "gofile_history.json")
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

COOKIES_FILE = os.path.join(os.getcwd(), "youtube_cookies.txt")

# Pixeldrain API Configuration
PIXELDRAIN_API_KEY = os.environ.get(
    "PIXELDRAIN_API_KEY") or "42fe5077-0a1c-475e-b731-77b43a514e88"
PIXELDRAIN_API_KEY_ALT = os.environ.get(
    "PIXELDRAIN_API_KEY_ALT") or "8efc8d11-758c-4fbd-b4bd-8d27f4d55901"

# 4stream API Configuration
_api_key_raw = os.environ.get("UP4STREAM_API_KEY") or "2296uwbr9c715uy3qa8a"
if "key=" in _api_key_raw:
    UP4STREAM_API_KEY = _api_key_raw.split("key=")[-1]
else:
    UP4STREAM_API_KEY = _api_key_raw

_api_key_raw_alt = os.environ.get(
    "UP4STREAM_API_KEY_ALT") or "2295agj14qkpmobx282y"
if "key=" in _api_key_raw_alt:
    UP4STREAM_API_KEY_ALT = _api_key_raw_alt.split("key=")[-1]
else:
    UP4STREAM_API_KEY_ALT = _api_key_raw_alt

# Gofile API Configuration  
GOFILE_ACCOUNT_ID = os.environ.get("GOFILE_ACCOUNT_ID") or "a27d5ffc-1648-46d7-af7d-de1c2bb27dd2"
GOFILE_API_TOKEN = os.environ.get("GOFILE_API_TOKEN") or "cumWiIyGmxJXlf0BJUiUntcISrXUyrue"

print(f"📂 Downloads folder: {os.path.abspath(DOWNLOAD_FOLDER)}")
print(
    f"🍪 Cookies file: {'Exists' if os.path.exists(COOKIES_FILE) else 'Not found'}"
)
print(
    f"📤 4stream API: {'Configured' if UP4STREAM_API_KEY else 'Not configured'}"
)

# -----------------------------
# Flask & SSE Setup
# -----------------------------
progress_queue = queue.Queue()

# -----------------------------
# HTML Template (with CSS & JavaScript)
# -----------------------------
TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Video Downloader & Uploader</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', 'Roboto', '-apple-system', sans-serif; line-height: 1.6; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding: 15px; color: #1a1a1a; }
        .sticky-header { position: sticky; top: 0; z-index: 100; background: rgba(255, 255, 255, 0.95); backdrop-filter: blur(10px); border-bottom: 1px solid rgba(0,0,0,0.1); padding: 18px; margin: -15px -15px 20px -15px; display: flex; justify-content: space-between; align-items: center; gap: 15px; box-shadow: 0 8px 32px rgba(0,0,0,0.1); }
        .app-header { display: flex; align-items: center; gap: 12px; }
        .app-icon { width: 44px; height: 44px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 12px; display: flex; align-items: center; justify-content: center; font-size: 24px; color: white; box-shadow: 0 4px 12px rgba(102, 126, 234, 0.3); }
        .app-title h1 { font-size: 20px; font-weight: 700; margin: 0; color: #1a1a1a; }
        .app-title p { font-size: 11px; color: #666; margin: 2px 0 0 0; }
        .manage-btn { background: linear-gradient(135deg, #28a745 0%, #20c997 100%); color: white; padding: 10px 18px; border: none; border-radius: 8px; cursor: pointer; text-decoration: none; font-weight: 600; font-size: 12px; transition: all 0.3s ease; display: inline-flex; align-items: center; gap: 6px; box-shadow: 0 4px 12px rgba(40, 167, 69, 0.2); white-space: nowrap; }
        .manage-btn:hover { transform: translateY(-2px); box-shadow: 0 6px 16px rgba(40, 167, 69, 0.3); }
        .container { max-width: 1000px; margin: 0 auto; background: rgba(255, 255, 255, 0.97); backdrop-filter: blur(10px); border-radius: 16px; box-shadow: 0 20px 60px rgba(0,0,0,0.15); border: 1px solid rgba(255, 255, 255, 0.3); overflow: hidden; }
        .tabs { display: flex; gap: 8px; padding: 18px 20px; border-bottom: 1px solid rgba(0,0,0,0.08); background: linear-gradient(to right, rgba(240,240,255,0.5), rgba(255,240,240,0.5)); overflow-x: auto; }
        .tab-btn { padding: 10px 18px; border: 2px solid transparent; background: white; color: #666; border-radius: 8px; cursor: pointer; font-weight: 600; font-size: 13px; transition: all 0.3s ease; white-space: nowrap; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
        .tab-btn.active { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; box-shadow: 0 4px 12px rgba(102, 126, 234, 0.3); }
        .tab-btn:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
        .tab-content { display: none; padding: 25px; animation: fadeIn 0.3s ease; }
        .tab-content.active { display: block; }
        @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
        .card { background: white; border: 1px solid rgba(0,0,0,0.08); border-radius: 12px; padding: 20px; margin-bottom: 18px; box-shadow: 0 4px 12px rgba(0,0,0,0.08); transition: all 0.3s ease; }
        .card:hover { box-shadow: 0 8px 24px rgba(0,0,0,0.12); transform: translateY(-2px); }
        .card-header { display: flex; align-items: center; gap: 12px; margin-bottom: 15px; }
        .card-icon { width: 48px; height: 48px; border-radius: 10px; display: flex; align-items: center; justify-content: center; font-size: 24px; color: white; flex-shrink: 0; }
        .card-icon.blue { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }
        .card-icon.green { background: linear-gradient(135deg, #28a745 0%, #20c997 100%); }
        .card-icon.orange { background: linear-gradient(135deg, #ff6b35 0%, #f7931e 100%); }
        .card-icon.red { background: linear-gradient(135deg, #dc3545 0%, #c82333 100%); }
        .card-title { font-size: 16px; font-weight: 700; color: #1a1a1a; margin: 0; }
        .card-desc { font-size: 12px; color: #666; margin: 4px 0 0 0; }
        .helper-text { font-size: 11px; color: #0c5460; background: #d1ecf1; border-left: 3px solid #00bcd4; padding: 10px 12px; border-radius: 6px; margin-bottom: 12px; }
        h1 { font-size: 24px; font-weight: 700; color: #1a1a1a; margin-bottom: 10px; }
        h2 { font-size: 18px; font-weight: 600; color: #2c2c2c; margin-top: 20px; margin-bottom: 15px; padding-bottom: 10px; border-bottom: 1px solid #e0e0e0; }
        h3 { font-size: 15px; font-weight: 600; color: #3a3a3a; margin-bottom: 10px; }
        label { display: block; font-size: 13px; font-weight: 600; color: #3a3a3a; margin-bottom: 6px; }
        input[type="text"], input[type="number"], select { width: 100%; padding: 10px 12px; margin-bottom: 12px; border: 1px solid #d0d0d0; border-radius: 8px; font-size: 13px; transition: all 0.2s ease; font-family: inherit; background: white; }
        input[type="text"]:focus, input[type="number"]:focus, select:focus { outline: none; border-color: #667eea; box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1); background: #f9fafb; }
        input[type="file"] { margin-bottom: 12px; font-size: 12px; }
        button { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 10px 16px; border: none; border-radius: 8px; cursor: pointer; font-size: 12px; font-weight: 600; margin-right: 8px; margin-bottom: 8px; transition: all 0.2s ease; box-shadow: 0 4px 12px rgba(102, 126, 234, 0.2); }
        button:hover { transform: translateY(-2px); box-shadow: 0 6px 16px rgba(102, 126, 234, 0.3); }
        button:active { transform: translateY(0); }
        button.delete { background: linear-gradient(135deg, #dc3545 0%, #c82333 100%); box-shadow: 0 4px 12px rgba(220, 53, 69, 0.2); }
        button.delete:hover { box-shadow: 0 6px 16px rgba(220, 53, 69, 0.3); }
        button.upload { background: linear-gradient(135deg, #28a745 0%, #20c997 100%); box-shadow: 0 4px 12px rgba(40, 167, 69, 0.2); }
        button.upload:hover { box-shadow: 0 6px 16px rgba(40, 167, 69, 0.3); }
        button.encode { background: linear-gradient(135deg, #00bcd4 0%, #0097a7 100%); box-shadow: 0 4px 12px rgba(0, 188, 212, 0.2); }
        button.encode:hover { box-shadow: 0 6px 16px rgba(0, 188, 212, 0.3); }
        button.rename { background: linear-gradient(135deg, #ffc107 0%, #ff9800 100%); color: white; box-shadow: 0 4px 12px rgba(255, 193, 7, 0.2); }
        button.rename:hover { box-shadow: 0 6px 16px rgba(255, 193, 7, 0.3); }
        a { color: #667eea; text-decoration: none; font-weight: 600; transition: color 0.2s ease; }
        a:hover { color: #764ba2; }
        pre { background-color: #f4f4f4; padding: 12px; border-radius: 8px; white-space: pre-wrap; word-wrap: break-word; color: #222; font-size: 11px; overflow-x: auto; border: 1px solid #e0e0e0; }
        .flash-msg { padding: 14px 16px; border-radius: 8px; margin-bottom: 15px; font-weight: 600; border-left: 4px solid; font-size: 13px; }
        .flash-success { background: rgba(40, 167, 69, 0.1); color: #155724; border-left-color: #28a745; }
        .flash-error { background: rgba(220, 53, 69, 0.1); color: #721c24; border-left-color: #dc3545; }
        .flash-info { background: rgba(102, 126, 234, 0.1); color: #0c5460; border-left-color: #667eea; }
        .progress-container { display: none; margin-top: 20px; background: #f9f9f9; padding: 18px; border-radius: 12px; border: 1px solid rgba(0,0,0,0.08); }
        .progress-bar { width: 100%; height: 30px; background-color: #e0e0e0; border-radius: 10px; overflow: hidden; margin: 15px 0; box-shadow: inset 0 2px 4px rgba(0,0,0,0.06); display: flex; align-items: center; }
        .progress-bar-inner { height: 100%; background: linear-gradient(90deg, #667eea 0%, #764ba2 100%); text-align: center; line-height: 30px; color: white; transition: width 0.4s ease; border-radius: 10px; font-weight: 600; font-size: 12px; }
        #progress-log { margin-top: 10px; font-family: 'Courier New', monospace; font-size: 11px; max-height: 200px; overflow-y: auto; background: white; color: #222; padding: 12px; border-radius: 8px; border: 1px solid #e0e0e0; }
        .notification { position: fixed; top: 20px; right: 20px; padding: 14px 18px; border-radius: 8px; color: white; font-weight: 600; z-index: 10000; animation: slideIn 0.3s ease-out; box-shadow: 0 8px 24px rgba(0,0,0,0.2); font-size: 13px; }
        .notification.success { background: linear-gradient(135deg, #28a745 0%, #20c997 100%); }
        .notification.error { background: linear-gradient(135deg, #dc3545 0%, #c82333 100%); }
        .notification.info { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }
        @keyframes slideIn { from { transform: translateX(400px); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
        #global-progress-btn { position: fixed; bottom: 20px; right: 20px; z-index: 9998; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 12px 20px; border: none; border-radius: 8px; cursor: pointer; display: none; box-shadow: 0 8px 24px rgba(102, 126, 234, 0.3); font-weight: 600; transition: all 0.2s ease; font-size: 13px; }
        #global-progress-btn:hover { transform: translateY(-2px); box-shadow: 0 12px 32px rgba(102, 126, 234, 0.4); }
        .modal { display: none; position: fixed; z-index: 9999; left: 0; top: 0; width: 100%; height: 100%; background-color: rgba(0,0,0,0.4); backdrop-filter: blur(4px); overflow-y: auto; }
        .modal-content { background: rgba(255, 255, 255, 0.98); margin: 20px auto; padding: 28px; border-radius: 16px; width: 90%; max-width: 800px; box-shadow: 0 20px 60px rgba(0,0,0,0.2); border: 1px solid rgba(255, 255, 255, 0.3); backdrop-filter: blur(10px); }
        .modal-content .close { float: right; font-size: 28px; font-weight: bold; cursor: pointer; color: #999; transition: color 0.2s ease; }
        .modal-content .close:hover { color: #667eea; }
        hr { border: 0; border-top: 1px solid rgba(0,0,0,0.08); margin: 20px 0; }
        @media (max-width: 768px) {
            .sticky-header { flex-direction: column; gap: 12px; padding: 14px; }
            .app-header { width: 100%; }
            .manage-btn { width: 100%; justify-content: center; }
            .tabs { padding: 12px; }
            .tab-btn { padding: 8px 14px; font-size: 12px; }
            .tab-content { padding: 18px; }
            .card { padding: 16px; margin-bottom: 14px; }
            .card-icon { width: 40px; height: 40px; font-size: 20px; }
            .container { border-radius: 12px; }
            body { padding: 10px; }
            h1 { font-size: 20px; }
            h2 { font-size: 16px; margin-top: 15px; margin-bottom: 12px; }
            h3 { font-size: 14px; }
            label { font-size: 12px; margin-bottom: 5px; }
            input[type="text"], input[type="number"], select { padding: 8px 10px; margin-bottom: 10px; font-size: 12px; }
            button { padding: 8px 14px; font-size: 11px; margin-right: 6px; margin-bottom: 6px; }
            .modal-content { padding: 20px; margin: 15px auto; width: 95%; }
            #progress-log { max-height: 150px; font-size: 10px; }
            .notification { top: 10px; right: 10px; padding: 10px 14px; font-size: 11px; }
            #global-progress-btn { bottom: 10px; right: 10px; padding: 9px 14px; font-size: 11px; }
        }
        @media (max-width: 480px) {
            .sticky-header { gap: 10px; padding: 12px; }
            .app-title h1 { font-size: 16px; }
            .app-title p { font-size: 10px; }
            .tabs { padding: 10px; gap: 6px; }
            .tab-btn { padding: 7px 12px; font-size: 11px; }
            .tab-content { padding: 14px; }
            .card { padding: 14px; margin-bottom: 12px; }
            .card-icon { width: 36px; height: 36px; font-size: 18px; }
            .card-title { font-size: 14px; }
            body { padding: 8px; }
            h1 { font-size: 18px; margin-bottom: 8px; }
            h2 { font-size: 14px; margin-top: 12px; margin-bottom: 10px; }
            h3 { font-size: 12px; }
            label { font-size: 11px; margin-bottom: 4px; }
            input[type="text"], input[type="number"], select { padding: 7px 8px; margin-bottom: 8px; font-size: 11px; }
            button { padding: 7px 11px; font-size: 10px; margin-right: 4px; margin-bottom: 4px; }
            .modal-content { padding: 15px; margin: 10px auto; width: 98%; }
            pre { font-size: 9px; padding: 8px; }
            .notification { top: 8px; right: 8px; padding: 8px 12px; font-size: 10px; }
            #global-progress-btn { bottom: 8px; right: 8px; padding: 6px 11px; font-size: 9px; }
            .modal-content .close { font-size: 24px; }
        }
    </style>
    <script>
        function validateForm() { return true; }
        function validateEncodeForm() { return true; }
        function validateYtForm() {
            const codec = document.getElementById('yt_codec').value;
            if (codec.includes('h265') || codec.includes('av1')) {
                const preset = document.getElementById('yt_preset').value;
                if (!preset) { alert('Please select a preset.'); return false; }
                const passMode = document.getElementById('yt_pass_mode').value;
                if (passMode === '2-pass') {
                    const bitrate = document.getElementById('yt_bitrate').value;
                    if (!bitrate || parseInt(bitrate) < 100) { alert('Please specify a valid video bitrate (minimum 100) for 2-pass encoding.'); return false; }
                }
            }
            return true;
        }
        function switchTab(tabName, btnEl) {
            const tabs = document.querySelectorAll('.tab-content'); 
            const btns = document.querySelectorAll('.tab-btn'); 
            tabs.forEach(t => t.classList.remove('active')); 
            btns.forEach(b => b.classList.remove('active')); 
            document.getElementById('tab-' + tabName).classList.add('active'); 
            event.target.classList.add('active'); 
        }
    </script>
</head>
<body>
<!-- Global Progress Elements -->
<button id="global-progress-btn">📊 View Progress</button>
<div id="global-progress-modal" class="modal">
    <div class="modal-content">
        <span class="close" onclick="closeGlobalProgressModal()">&times;</span>
        <div id="global-progress-container" class="progress-container" style="display:block;">
            <h3 id="global-progress-stage">...</h3>
            <div class="progress-bar"><div id="global-progress-bar-inner" class="progress-bar-inner">0%</div></div>
            <pre id="global-progress-log"></pre>
            <button id="global-stop-button" class="delete" style="margin-top:10px;">⏹️ Stop Process</button>
        </div>
    </div>
</div>

<!-- Sticky Header -->
<div class="sticky-header">
    <div class="app-header">
        <div class="app-icon">🎬</div>
        <div class="app-title">
            <h1>Video Downloader & Uploader</h1>
            <p>Powered by yt-dlp, FFmpeg, Pixeldrain & 4stream</p>
        </div>
    </div>
    <a href="{{ url_for('list_files', current_path='') }}" class="manage-btn">📂 Manage Files</a>
</div>

<div class="container">
    <!-- Tab Buttons -->
    <div class="tabs">
        <button class="tab-btn {% if current_tab == 'advanced' %}active{% endif %}" onclick="switchTab('advanced', this)">⚙️ Advanced Download</button>
        <button class="tab-btn {% if current_tab == 'youtube' %}active{% endif %}" onclick="switchTab('youtube', this)">🎥 YouTube Download</button>
        <button class="tab-btn {% if current_tab == 'merge' %}active{% endif %}" onclick="switchTab('merge', this)">🔗 Format Merge</button>
        <button class="tab-btn {% if current_tab == 'direct' %}active{% endif %}" onclick="switchTab('direct', this)">📥 Direct Download</button>
        <button class="tab-btn {% if current_tab == 'upload' %}active{% endif %}" onclick="switchTab('upload', this)">☁️ Upload to Pixeldrain</button>
        <button class="tab-btn {% if current_tab == '4stream' %}active{% endif %}" onclick="switchTab('4stream', this)">🎬 Upload to 4stream</button>
        <button class="tab-btn {% if current_tab == 'links' %}active{% endif %}" onclick="switchTab('links', this)">🔗 External Links</button>
    </div>
    {% with messages = get_flashed_messages(with_categories=true) %}
        {% if messages %}
            {% for category, message in messages %}
                <div class="flash-msg flash-{{ category }}">{{ message|safe }}</div>
            {% endfor %}
        {% endif %}
    {% endwith %}

    <!-- Advanced Download Tab -->
    <div id="tab-advanced" class="tab-content {% if current_tab == 'advanced' %}active{% endif %}">
        <div class="card">
            <div class="card-header">
                <div class="card-icon blue">⚙️</div>
                <div>
                    <h2 class="card-title">Advanced Download</h2>
                    <p class="card-desc">Download and encode videos with full control over formats and codecs</p>
                </div>
            </div>
            <div class="helper-text">💡 Fetch available formats first, then select your preferred video and audio quality</div>

            <div id="progress-container" class="progress-container">
                <h3 id="progress-stage">Starting...</h3>
                <div class="progress-bar">
                    <div id="progress-bar-inner" class="progress-bar-inner">0%</div>
                </div>
                <pre id="progress-log"></pre>
                <button id="stop-button" class="delete">⏹️ Stop Encoding</button>
            </div>

            <form method="POST" action="{{ url_for('index') }}" id="download-form" onsubmit="return true;">
        <label>Video URL:</label><br>
        <input type="text" name="url" size="80" value="{{ url }}" required><br>
        <button type="submit" name="action" value="fetch">Fetch Formats</button><br><br>

        {% if formats %}
            <input type="hidden" name="url" value="{{ url }}">

            <label>Video Format:</label><br>
            <select name="video_id" required>
                {% for format in video_formats %}
                    <option value="{{ format.id }}" {% if format.is_muxed %}style="font-style: italic;"{% endif %}>{{ format.display }}{% if format.is_muxed %} (with audio){% endif %}</option>
                {% endfor %}
            </select><br>

            <label>Audio Format (optional):</label><br>
            <select name="audio_id">
                <option value="">Best Audio (default)</option>
                {% for format in audio_formats %}
                    <option value="{{ format.id }}">{{ format.display }}</option>
                {% endfor %}
            </select><br>

            <label>Filename:</label><br>
            <input type="text" name="filename" value="{{ original_name }}" required><br>
            <label>Codec:</label><br>
            <select name="codec" id="codec" required>
                <option value="none" {% if codec == "none" %}selected{% endif %}>No Encoding</option>
                <option value="h265" {% if codec == "h265" %}selected{% endif %}>Encode to H.265 (x265)</option>
                <option value="av1" {% if codec == "av1" %}selected{% endif %}>Encode to AV1 (SVT-AV1)</option>
                <option value="h265_copy_audio" {% if codec == "h265_copy_audio" %}selected{% endif %}>H.265 Video Only (Copy Audio)</option>
                <option value="av1_copy_audio" {% if codec == "av1_copy_audio" %}selected{% endif %}>AV1 Video Only (Copy Audio)</option>
                <option value="copy_video" {% if codec == "copy_video" %}selected{% endif %}>Copy Video (Encode Audio Only)</option>
            </select><br>
            <div id="encoding-options" style="display: {% if codec != 'none' %}block{% else %}none{% endif %};">
                <div id="video-encoding-options">
                    <label>Encoding Mode:</label><br>
                    <select name="pass_mode" id="pass_mode">
                        <option value="1-pass" {% if pass_mode == "1-pass" %}selected{% endif %}>1-pass (CRF)</option>
                        <option value="2-pass" {% if pass_mode == "2-pass" %}selected{% endif %}>2-pass (VBR)</option>
                    </select><br>
                    <label>Preset (slower = better quality/smaller file):</label><br>
                    <select name="preset" id="preset"></select><br>

                    <label>Video Bitrate (kb/s, optional):</label><br>
                    <input type="number" name="bitrate" id="bitrate" value="{{ bitrate }}" min="100" placeholder="e.g., 600 for AV1, 2000 for H.265"><br>

                    <label>CRF (0–63, lower = better quality):</label><br>
                    <input type="number" name="crf" id="crf" value="{{ crf|default(28 if codec == 'h265' else 45) }}" min="0" max="63" step="1" placeholder="e.g., 28 for H.265, 45 for AV1"><br>

                    <label>Frame Rate (optional):</label><br>
                    <select name="fps">
                        <option value="">Original</option>
                        <option value="24">24 fps</option>
                        <option value="30">30 fps</option>
                        <option value="60">60 fps</option>
                    </select><br>

                    <label>Resolution (Scale, optional):</label><br>
                    <select name="scale">
                        <option value="">Original</option>
                        <option value="1920:-2">1080p (1920px wide)</option>
                        <option value="1280:-2">720p (1280px wide)</option>
                        <option value="854:-2">480p (854px wide)</option>
                        <option value="640:-2">360p (640px wide)</option>
                    </select><br>

                    <label>Adaptive Quantization Mode (AV1 only):</label><br>
                    <select name="aq_mode" id="aq_mode">
                        <option value="0">Disabled</option>
                        <option value="1">PSNR-based</option>
                        <option value="2" selected>Variance-based</option>
                    </select><br>

                    <label>Variance Boost (AV1 only, 0–3):</label><br>
                    <input type="number" name="variance_boost" id="variance_boost" value="2" min="0" max="3" step="1" placeholder="e.g., 2"><br>

                    <label>Tiles (AV1 only, e.g., 2x2 for faster encoding):</label><br>
                    <select name="tiles" id="tiles">
                        <option value="">None</option>
                        <option value="2x2" selected>2x2 (Recommended for 720p)</option>
                        <option value="4x4">4x4</option>
                    </select><br>

                    <label><input type="checkbox" name="enable_vmaf" value="true"> Compute VMAF Quality Score (slower)</label><br>
                </div>

                <div id="audio-encoding-options">
                    <label>Audio Bitrate (kb/s):</label><br>
                    <input type="number" name="audio_bitrate" id="audio_bitrate" value="{{ audio_bitrate|default('32') }}" min="32" max="512" step="8" placeholder="e.g., 32, 64, 96, 128"><br>

                    <label><input type="checkbox" name="force_stereo" value="true"> Force Stereo (2-channel) Audio</label><br>
                </div>
            </div>
            <script>
                const codecSelect = document.getElementById('codec');
                const presetSelect = document.getElementById('preset');
                const crfInput = document.getElementById('crf');
                const passModeSelect = document.getElementById('pass_mode');
                const bitrateInput = document.getElementById('bitrate');
                const aqModeSelect = document.getElementById('aq_mode');
                const varianceBoostInput = document.getElementById('variance_boost');
                const tilesSelect = document.getElementById('tiles');

                function updatePresetOptions() {
                    const codec = codecSelect.value;
                    const encodingOptions = document.getElementById('encoding-options');
                    const videoEncodingOptions = document.getElementById('video-encoding-options');
                    const audioEncodingOptions = document.getElementById('audio-encoding-options');
                    encodingOptions.style.display = codec !== 'none' ? 'block' : 'none';
                    if (codec === 'copy_video') {
                        videoEncodingOptions.style.display = 'none';
                        audioEncodingOptions.style.display = 'block';
                    } else if (codec.endsWith('_copy_audio')) {
                        videoEncodingOptions.style.display = 'block';
                        audioEncodingOptions.style.display = 'none';
                    } else if (codec !== 'none') {
                        videoEncodingOptions.style.display = 'block';
                        audioEncodingOptions.style.display = 'block';
                    }

                    presetSelect.innerHTML = '';
                    if (codec === 'av1' || codec === 'av1_copy_audio') {
                        for (let p = 0; p <= 13; p++) {
                            let label = p.toString();
                            if (p === 0) label += ' (slowest)';
                            else if (p === 13) label += ' (fastest)';
                            else if (p > 7) label += ' (fast)';
                            else label += ' (medium)';
                            const option = document.createElement('option');
                            option.value = p; option.text = label;
                            if (p === 7) option.selected = true;
                            presetSelect.appendChild(option);
                        }
                        crfInput.value = crfInput.value || '45';
                        crfInput.placeholder = 'e.g., 45 for AV1';
                        aqModeSelect.disabled = false; varianceBoostInput.disabled = false; tilesSelect.disabled = false;
                    } else if (codec === 'h265' || codec === 'h265_copy_audio') {
                        const presets = ['ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow', 'slower', 'veryslow', 'placebo'];
                        presets.forEach(p => {
                            const option = document.createElement('option');
                            option.value = p; option.text = p;
                            if (p === 'faster') option.selected = true;
                            presetSelect.appendChild(option);
                        });
                        crfInput.value = crfInput.value || '28';
                        crfInput.placeholder = 'e.g., 28 for H.265';
                        aqModeSelect.disabled = true; varianceBoostInput.disabled = true; tilesSelect.disabled = true;
                    } else {
                        aqModeSelect.disabled = true; varianceBoostInput.disabled = true; tilesSelect.disabled = true;
                    }

                    if (codec === 'none') {
                        bitrateInput.removeAttribute('required');
                        bitrateInput.removeAttribute('min');
                        bitrateInput.value = '';
                    } else {
                        bitrateInput.setAttribute('min', '100');
                        if (passModeSelect.value === '2-pass') {
                            bitrateInput.setAttribute('required', 'required');
                        } else {
                            bitrateInput.removeAttribute('required');
                        }
                    }
                }
                function validateForm() {
                    const codec = codecSelect.value;
                    if (codec.includes('h265') || codec.includes('av1')) {
                        if (!presetSelect.value) { alert('Please select a preset.'); return false; }
                        if (passModeSelect.value === '2-pass' && (!bitrateInput.value || parseInt(bitrateInput.value) < 100)) { alert('Please specify a valid video bitrate (minimum 100) for 2-pass encoding.'); return false; }
                        if (codec.includes('av1')) {
                            const varianceBoost = parseInt(varianceBoostInput.value);
                            if (isNaN(varianceBoost) || varianceBoost < 0 || varianceBoost > 3) { alert('Variance Boost must be between 0 and 3.'); return false; }
                        }
                    }
                    return true;
                }
                codecSelect.addEventListener('change', updatePresetOptions);
                passModeSelect.addEventListener('change', function() {
                    if (codecSelect.value !== 'none') {
                        if (this.value === '2-pass') bitrateInput.setAttribute('required', 'required');
                        else bitrateInput.removeAttribute('required');
                    }
                });
                document.addEventListener('DOMContentLoaded', updatePresetOptions);
            </script>
            <br>
            <label><input type="checkbox" name="upload_pixeldrain" value="true"> Upload to Pixeldrain after completion</label><br>
            <label><input type="checkbox" name="upload_gofile" value="true"> Upload to Gofile after completion</label><br><br>
                <button type="submit" name="action" value="download" class="encode">▶️ Download & Convert</button>
                <h3 style="margin-top: 20px;">📋 Available Formats:</h3>
                <pre>{{ formats }}</pre>
            {% endif %}
            </form>
            <script>
                let advEventSource = null;
                const downloadForm = document.getElementById('download-form');

                downloadForm.addEventListener('submit', function(e) {
                    const action = e.submitter?.value;
                    if (action === 'download') {
                        e.preventDefault();
                        const formData = new FormData(downloadForm);
                        formData.set('action', 'download');

                        const container = document.getElementById('progress-container');
                        const log = document.getElementById('progress-log');
                        container.style.display = 'block';
                        log.innerHTML = '';

                        fetch('/', { method: 'POST', body: formData }).then(r => r.text());
                        setTimeout(() => startAdvancedProgressListener(), 300);
                    }
                });

                function startAdvancedProgressListener() {
                    if (advEventSource) advEventSource.close();
                    advEventSource = new EventSource("/progress");
                    const stage = document.getElementById('progress-stage');
                    const progressBar = document.getElementById('progress-bar-inner');
                    const log = document.getElementById('progress-log');
                    const container = document.getElementById('progress-container');

                    container.style.display = 'block';

                    advEventSource.onmessage = function(event) {
                        try {
                            const data = JSON.parse(event.data);
                            if (data.log === 'DONE') {
                                stage.textContent = '✅ Completed!';
                                progressBar.style.backgroundColor = '#28a745';
                                progressBar.style.width = '100%';
                                progressBar.textContent = '100%';
                                log.innerHTML += "\n\n✅ Operation finished.";
                                advEventSource.close();
                                return;
                            }
                            if (data.error) {
                                stage.textContent = '❌ Error!';
                                progressBar.style.backgroundColor = '#dc3545';
                                log.innerHTML += `\n\n❌ ERROR: ${data.error}`;
                                advEventSource.close();
                                return;
                            }
                            if (data.stage) stage.textContent = data.stage;
                            if (data.percent) {
                                const percent = Math.min(100, data.percent);
                                progressBar.style.width = percent + '%';
                                progressBar.textContent = percent.toFixed(1) + '%';
                            }
                            if (data.log) {
                                log.innerHTML += data.log + '\n';
                                log.scrollTop = log.scrollHeight;
                            }
                        } catch (e) { console.error('Progress error:', e); }
                    };
                    advEventSource.onerror = function() { if (advEventSource) advEventSource.close(); };
                }

                document.getElementById('stop-button')?.addEventListener('click', function() {
                    if (advEventSource) advEventSource.close();
                    fetch('/stop_process', { method: 'POST' });
                });
            </script>
        </div>
    </div>

    <!-- YouTube Download Tab -->
    <div id="tab-youtube" class="tab-content {% if current_tab == 'youtube' %}active{% endif %}">
        <div class="card">
            <div class="card-header">
                <div class="card-icon blue">🎥</div>
                <div>
                    <h2 class="card-title">YouTube Download</h2>
                    <p class="card-desc">Download YouTube videos using authentication cookies for restricted content</p>
                </div>
            </div>
            <div class="helper-text">💡 Download YouTube videos with full format selection and encoding options</div>

            <div id="yt-progress-container" class="progress-container">
                <h3 id="yt-progress-stage">Starting...</h3>
                <div class="progress-bar">
                    <div id="yt-progress-bar-inner" class="progress-bar-inner">0%</div>
                </div>
                <pre id="yt-progress-log"></pre>
                <button id="yt-stop-button" class="delete">⏹️ Stop Download</button>
            </div>

            <form method="POST" action="{{ url_for('youtube_download') }}" id="youtube-form">
        <label>YouTube URL:</label><br>
        <input type="text" name="yt_url" size="80" value="{{ yt_url }}" required placeholder="https://www.youtube.com/watch?v=..."><br>
        <button type="submit" name="action" value="yt_fetch">🔍 Fetch Formats</button><br><br>

        {% if yt_formats %}
            <input type="hidden" name="yt_url" value="{{ yt_url }}">

            <label>Video Format:</label><br>
            <select name="yt_video_id" required>
                {% for format in yt_video_formats %}
                    <option value="{{ format.id }}" {% if format.is_muxed %}style="font-style: italic;"{% endif %}>{{ format.display }}{% if format.is_muxed %} (with audio){% endif %}</option>
                {% endfor %}
            </select><br>

            <label>Audio Format (optional):</label><br>
            <select name="yt_audio_id">
                <option value="">Best Audio (default)</option>
                {% for format in yt_audio_formats %}
                    <option value="{{ format.id }}">{{ format.display }}</option>
                {% endfor %}
            </select><br>

            <label>Filename:</label><br>
            <input type="text" name="yt_filename" value="{{ yt_original_name }}" required><br>
            <label>Codec:</label><br>
            <select name="yt_codec" id="yt_codec" required>
                <option value="none" {% if yt_codec == "none" %}selected{% endif %}>No Encoding</option>
                <option value="h265" {% if yt_codec == "h265" %}selected{% endif %}>Encode to H.265 (x265)</option>
                <option value="av1" {% if yt_codec == "av1" %}selected{% endif %}>Encode to AV1 (SVT-AV1)</option>
                <option value="h265_copy_audio" {% if yt_codec == "h265_copy_audio" %}selected{% endif %}>H.265 Video Only (Copy Audio)</option>
                <option value="av1_copy_audio" {% if yt_codec == "av1_copy_audio" %}selected{% endif %}>AV1 Video Only (Copy Audio)</option>
                <option value="copy_video" {% if yt_codec == "copy_video" %}selected{% endif %}>Copy Video (Encode Audio Only)</option>
            </select><br>
            <div id="yt-encoding-options" style="display: {% if yt_codec != 'none' %}block{% else %}none{% endif %};">
                <div id="yt-video-encoding-options" style="display:none;">
                    <label>Encoding Mode:</label><br>
                    <select name="yt_pass_mode" id="yt_pass_mode">
                        <option value="1-pass" {% if yt_pass_mode == "1-pass" %}selected{% endif %}>1-pass (CRF)</option>
                        <option value="2-pass" {% if yt_pass_mode == "2-pass" %}selected{% endif %}>2-pass (VBR)</option>
                    </select><br>
                    <label>Preset (slower = better quality/smaller file):</label><br>
                    <select name="yt_preset" id="yt_preset"></select><br>

                    <label>Video Bitrate (kb/s, optional):</label><br>
                    <input type="number" name="yt_bitrate" id="yt_bitrate" value="{{ yt_bitrate }}" min="100" placeholder="e.g., 600 for AV1, 2000 for H.265"><br>

                    <label>CRF (0–63, lower = better quality):</label><br>
                    <input type="number" name="yt_crf" id="yt_crf" value="{{ yt_crf|default(28 if yt_codec == 'h265' else 45) }}" min="0" max="63" step="1" placeholder="e.g., 28 for H.265, 45 for AV1"><br>

                    <label>Frame Rate (optional):</label><br>
                    <select name="yt_fps">
                        <option value="">Original</option>
                        <option value="24">24 fps</option>
                        <option value="30">30 fps</option>
                        <option value="60">60 fps</option>
                    </select><br>

                    <label>Resolution (Scale, optional):</label><br>
                    <select name="yt_scale">
                        <option value="">Original</option>
                        <option value="1920:-2">1080p (1920px wide)</option>
                        <option value="1280:-2">720p (1280px wide)</option>
                        <option value="854:-2">480p (854px wide)</option>
                        <option value="640:-2">360p (640px wide)</option>
                    </select><br>

                    <label>Adaptive Quantization Mode (AV1 only):</label><br>
                    <select name="yt_aq_mode" id="yt_aq_mode">
                        <option value="0">Disabled</option>
                        <option value="1">PSNR-based</option>
                        <option value="2" selected>Variance-based</option>
                    </select><br>

                    <label>Variance Boost (AV1 only, 0–3):</label><br>
                    <input type="number" name="yt_variance_boost" id="yt_variance_boost" value="2" min="0" max="3" step="1" placeholder="e.g., 2"><br>

                    <label>Tiles (AV1 only, e.g., 2x2 for faster encoding):</label><br>
                    <select name="yt_tiles" id="yt_tiles">
                        <option value="">None</option>
                        <option value="2x2" selected>2x2 (Recommended for 720p)</option>
                        <option value="4x4">4x4</option>
                    </select><br>

                    <label><input type="checkbox" name="yt_enable_vmaf" value="true"> Compute VMAF Quality Score (slower)</label><br>
                </div>

                <div id="yt-audio-encoding-options">
                    <label>Audio Bitrate (kb/s):</label><br>
                    <input type="number" name="yt_audio_bitrate" id="yt_audio_bitrate" value="{{ yt_audio_bitrate|default('32') }}" min="32" max="512" step="8" placeholder="e.g., 32, 64, 96, 128"><br>

                    <label><input type="checkbox" name="yt_force_stereo" value="true"> Force Stereo (2-channel) Audio</label><br>
                </div>
            </div>
            <script>
                const ytCodecSelect = document.getElementById('yt_codec');
                const ytPresetSelect = document.getElementById('yt_preset');
                const ytCrfInput = document.getElementById('yt_crf');
                const ytPassModeSelect = document.getElementById('yt_pass_mode');
                const ytBitrateInput = document.getElementById('yt_bitrate');
                const ytAqModeSelect = document.getElementById('yt_aq_mode');
                const ytVarianceBoostInput = document.getElementById('yt_variance_boost');
                const ytTilesSelect = document.getElementById('yt_tiles');

                function updateYtPresetOptions() {
                    const codec = ytCodecSelect.value;
                    const encodingOptions = document.getElementById('yt-encoding-options');
                    const videoEncodingOptions = document.getElementById('yt-video-encoding-options');
                    const audioEncodingOptions = document.getElementById('yt-audio-encoding-options');
                    encodingOptions.style.display = codec !== 'none' ? 'block' : 'none';
                    if (codec === 'copy_video') {
                        videoEncodingOptions.style.display = 'none';
                        audioEncodingOptions.style.display = 'block';
                    } else if (codec.endsWith('_copy_audio')) {
                        videoEncodingOptions.style.display = 'block';
                        audioEncodingOptions.style.display = 'none';
                    } else if (codec !== 'none') {
                        videoEncodingOptions.style.display = 'block';
                        audioEncodingOptions.style.display = 'block';
                    }

                    ytPresetSelect.innerHTML = '';
                    if (codec === 'av1' || codec === 'av1_copy_audio') {
                        for (let p = 0; p <= 13; p++) {
                            let label = p.toString();
                            if (p === 0) label += ' (slowest)';
                            else if (p === 13) label += ' (fastest)';
                            else if (p > 7) label += ' (fast)';
                            else label += ' (medium)';
                            const option = document.createElement('option');
                            option.value = p; option.text = label;
                            if (p === 7) option.selected = true;
                            ytPresetSelect.appendChild(option);
                        }
                        ytCrfInput.value = ytCrfInput.value || '45';
                        ytCrfInput.placeholder = 'e.g., 45 for AV1';
                        ytAqModeSelect.disabled = false; ytVarianceBoostInput.disabled = false; ytTilesSelect.disabled = false;
                    } else if (codec === 'h265' || codec === 'h265_copy_audio') {
                        const presets = ['ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow', 'slower', 'veryslow', 'placebo'];
                        presets.forEach(p => {
                            const option = document.createElement('option');
                            option.value = p; option.text = p;
                            if (p === 'faster') option.selected = true;
                            ytPresetSelect.appendChild(option);
                        });
                        ytCrfInput.value = ytCrfInput.value || '28';
                        ytCrfInput.placeholder = 'e.g., 28 for H.265';
                        ytAqModeSelect.disabled = true; ytVarianceBoostInput.disabled = true; ytTilesSelect.disabled = true;
                    } else {
                        ytAqModeSelect.disabled = true; ytVarianceBoostInput.disabled = true; ytTilesSelect.disabled = true;
                    }

                    if (codec === 'none') {
                        ytBitrateInput.removeAttribute('required');
                        ytBitrateInput.removeAttribute('min');
                        ytBitrateInput.value = '';
                    } else {
                        ytBitrateInput.setAttribute('min', '100');
                        if (ytPassModeSelect.value === '2-pass') {
                            ytBitrateInput.setAttribute('required', 'required');
                        } else {
                            ytBitrateInput.removeAttribute('required');
                        }
                    }
                }
                function validateYtForm() {
                    const codec = ytCodecSelect.value;
                    if (codec.includes('h265') || codec.includes('av1')) {
                        if (!ytPresetSelect.value) { alert('Please select a preset.'); return false; }
                        if (ytPassModeSelect.value === '2-pass' && (!ytBitrateInput.value || parseInt(ytBitrateInput.value) < 100)) { alert('Please specify a valid video bitrate (minimum 100) for 2-pass encoding.'); return false; }
                        if (codec.includes('av1')) {
                            const varianceBoost = parseInt(ytVarianceBoostInput.value);
                            if (isNaN(varianceBoost) || varianceBoost < 0 || varianceBoost > 3) { alert('Variance Boost must be between 0 and 3.'); return false; }
                        }
                    }
                    return true;
                }
                ytCodecSelect.addEventListener('change', updateYtPresetOptions);
                ytPassModeSelect.addEventListener('change', function() {
                    if (ytCodecSelect.value !== 'none') {
                        if (this.value === '2-pass') ytBitrateInput.setAttribute('required', 'required');
                        else ytBitrateInput.removeAttribute('required');
                    }
                });
                document.addEventListener('DOMContentLoaded', updateYtPresetOptions);
            </script>
            <script>
                let ytEventSource = null;
                const youtubeForm = document.getElementById('youtube-form');

                youtubeForm.addEventListener('submit', function(e) {
                    const action = e.submitter?.value;
                    if (action === 'yt_download') {
                        e.preventDefault();
                        const formData = new FormData(youtubeForm);
                        formData.set('action', 'yt_download');

                        const container = document.getElementById('yt-progress-container');
                        const log = document.getElementById('yt-progress-log');
                        container.style.display = 'block';
                        log.innerHTML = '';

                        fetch('/youtube', { method: 'POST', body: formData }).then(r => r.text());
                        setTimeout(() => startYoutubeProgressListener(), 300);
                    }
                });

                function startYoutubeProgressListener() {
                    if (ytEventSource) {
                        ytEventSource.close();
                    }
                    ytEventSource = new EventSource("/progress");
                    const stage = document.getElementById('yt-progress-stage');
                    const progressBar = document.getElementById('yt-progress-bar-inner');
                    const log = document.getElementById('yt-progress-log');
                    const container = document.getElementById('yt-progress-container');

                    container.style.display = 'block';

                    ytEventSource.onmessage = function(event) {
                        try {
                            const data = JSON.parse(event.data);

                            if (data.log && data.log === 'DONE') {
                                stage.textContent = '✅ Completed!';
                                progressBar.style.backgroundColor = '#28a745';
                                progressBar.style.width = '100%';
                                progressBar.textContent = '100%';
                                log.innerHTML += "\n\n✅ Operation finished.";
                                ytEventSource.close();
                                ytEventSource = null;
                                return;
                            }

                            if (data.error) {
                                stage.textContent = '❌ Error!';
                                progressBar.style.backgroundColor = '#dc3545';
                                log.innerHTML += `\n\n❌ ERROR: ${data.error}`;
                                ytEventSource.close();
                                ytEventSource = null;
                                return;
                            }

                            if (data.stage) stage.textContent = data.stage;
                            if (data.percent) {
                                const percent = Math.min(100, data.percent);
                                progressBar.style.width = percent + '%';
                                progressBar.textContent = percent.toFixed(1) + '%';
                            }
                            if (data.log) {
                                log.innerHTML += data.log + '\n';
                                log.scrollTop = log.scrollHeight;
                            }
                        } catch (e) {
                            console.error('Error parsing progress data:', e);
                        }
                    };

                    ytEventSource.onerror = function(err) {
                        if (ytEventSource) ytEventSource.close();
                        ytEventSource = null;
                        console.error('YouTube progress stream error:', err);
                    };
                }

                const ytStopButton = document.getElementById('yt-stop-button');
                ytStopButton.addEventListener('click', function() {
                    if (ytEventSource) {
                        ytEventSource.close();
                        ytEventSource = null;
                    }
                    fetch('/stop_process', { method: 'POST' });
                });
            </script>
            <br>
            <label><input type="checkbox" name="yt_upload_pixeldrain" value="true"> Upload to Pixeldrain after completion</label><br>
            <label><input type="checkbox" name="yt_upload_gofile" value="true"> Upload to Gofile after completion</label><br><br>
                <button type="submit" name="action" value="yt_download" class="encode">▶️ Download & Convert</button>
                <h3 style="margin-top: 20px;">📋 Available Formats:</h3>
                <pre>{{ yt_formats }}</pre>
            {% endif %}
            </form>
        </div>
    </div>

    <!-- Direct Download Tab -->
    <div id="tab-direct" class="tab-content {% if current_tab == 'direct' %}active{% endif %}">
        <div class="card">
            <div class="card-header">
                <div class="card-icon green">📥</div>
                <div>
                    <h2 class="card-title">Direct URL Download</h2>
                    <p class="card-desc">Download any video or direct file without format selection</p>
                </div>
            </div>
            <div class="helper-text">💡 Paste any URL (videos, playlists, direct files). Optional: Use username & password for authenticated downloads</div>

            <form method="POST" action="{{ url_for('index') }}">
                <label>URL (Video, Playlist, or any direct file):</label><br>
                <input type="text" name="direct_url" size="80" required><br>

                <label>Username (optional, leave empty if not needed):</label><br>
                <input type="text" name="direct_username" placeholder="e.g., admin" size="80"><br>

                <label>Password (optional, leave empty if not needed):</label><br>
                <input type="text" name="direct_password" placeholder="e.g., 1234" size="80"><br>

                <label><input type="checkbox" name="upload_pixeldrain_direct" value="true"> ☁️ Upload to Pixeldrain after download</label><br>
                <label><input type="checkbox" name="upload_gofile_direct" value="true"> 📤 Upload to Gofile after download</label><br>
                <button type="submit" name="action" value="direct_download">📥 Download to Server</button>
                <button type="submit" name="action" value="direct_upload_pixeldrain" class="upload">☁️ Upload to Pixeldrain</button>
            </form>
        </div>
    </div>

    <!-- Upload Tab -->
    <div id="tab-upload" class="tab-content {% if current_tab == 'upload' %}active{% endif %}">
        <div class="card">
            <div class="card-header">
                <div class="card-icon orange">☁️</div>
                <div>
                    <h2 class="card-title">Upload File to Pixeldrain</h2>
                    <p class="card-desc">Upload any file from your computer directly</p>
                </div>
            </div>
            <div class="helper-text">💡 Select a file and upload. Pixeldrain provides fast, anonymous file hosting</div>

            <form method="POST" action="{{ url_for('upload_direct') }}" enctype="multipart/form-data">
                <label>Select a file from your computer:</label><br>
                <input type="file" name="file" required><br>
                <button type="submit" class="upload">☁️ Upload Directly</button>
            </form>
        </div>
    </div>

    <!-- Format Merge Tab -->
    <div id="tab-merge" class="tab-content {% if current_tab == 'merge' %}active{% endif %}">
        <div class="card">
            <div class="card-header">
                <div class="card-icon blue">🔗</div>
                <div>
                    <h2 class="card-title">Manual Format Merge</h2>
                    <p class="card-desc">Fetch formats from a URL, then manually merge video and audio</p>
                </div>
            </div>
            <div class="helper-text">💡 Fetch formats first, then specify Video/Audio IDs to merge into an MKV file</div>

            <div id="merge-progress-container" class="progress-container">
                <h3 id="merge-progress-stage">Starting...</h3>
                <div class="progress-bar">
                    <div id="merge-progress-bar-inner" class="progress-bar-inner">0%</div>
                </div>
                <pre id="merge-progress-log"></pre>
                <button id="merge-stop-button" class="delete">⏹️ Stop</button>
            </div>

            <form method="POST" action="{{ url_for('index') }}" id="merge-form" onsubmit="return true;">
                <label>Page URL:</label><br>
                <input type="text" name="manual_url" size="80" value="{{ manual_url }}" required><br>
                <button type="submit" name="action" value="manual_fetch">🔍 Fetch Formats</button><br><br>
                {% if manual_formats_raw %}
                    <input type="hidden" name="manual_url" value="{{ manual_url }}">
                    <h3 style="margin-top: 15px;">📋 Available Formats:</h3>
                    <pre>{{ manual_formats_raw }}</pre>
                    <label>Video ID:</label><br>
                    <input type="text" name="manual_video_id" required placeholder="Enter the ID of the video stream"><br>
                    <label>Audio ID (optional):</label><br>
                    <input type="text" name="manual_audio_id" placeholder="Enter ID of audio stream (leave blank for video-only)"><br>
                    <label>Filename (will be saved as .mkv):</label><br>
                    <input type="text" name="manual_filename" value="{{ manual_filename }}" required><br><br>
                    <label><input type="checkbox" name="upload_pixeldrain" value="true"> Upload to Pixeldrain after completion</label><br>
                    <label><input type="checkbox" name="upload_4stream" value="true"> 🎬 Upload to 4stream after completion</label><br>
                    <label><input type="checkbox" name="upload_gofile" value="true"> Upload to Gofile after completion</label><br><br>
                    <button type="submit" name="action" value="manual_merge" class="encode">🔗 Merge & Download</button>
                {% endif %}
            </form>
            <script>
                let mergeEventSource = null;
                const mergeForm = document.getElementById('merge-form');

                mergeForm.addEventListener('submit', function(e) {
                    if (e.submitter?.value === 'manual_merge') {
                        e.preventDefault();
                        const formData = new FormData(mergeForm);
                        formData.set('action', 'manual_merge');

                        const container = document.getElementById('merge-progress-container');
                        const log = document.getElementById('merge-progress-log');
                        container.style.display = 'block';
                        log.innerHTML = '';

                        fetch('/', { method: 'POST', body: formData }).then(r => r.text());
                        setTimeout(() => startMergeProgressListener(), 300);
                    }
                });

                function startMergeProgressListener() {
                    if (mergeEventSource) mergeEventSource.close();
                    mergeEventSource = new EventSource("/progress");
                    const stage = document.getElementById('merge-progress-stage');
                    const progressBar = document.getElementById('merge-progress-bar-inner');
                    const log = document.getElementById('merge-progress-log');
                    const container = document.getElementById('merge-progress-container');

                    container.style.display = 'block';
                    log.innerHTML = '';

                    mergeEventSource.onmessage = function(event) {
                        try {
                            const data = JSON.parse(event.data);
                            if (data.log === 'DONE') {
                                stage.textContent = '✅ Completed!';
                                progressBar.style.backgroundColor = '#28a745';
                                progressBar.style.width = '100%';
                                progressBar.textContent = '100%';
                                log.innerHTML += "\n\n✅ Operation finished.";
                                mergeEventSource.close();
                                return;
                            }
                            if (data.error) {
                                stage.textContent = '❌ Error!';
                                progressBar.style.backgroundColor = '#dc3545';
                                log.innerHTML += `\n\n❌ ERROR: ${data.error}`;
                                mergeEventSource.close();
                                return;
                            }
                            if (data.stage) stage.textContent = data.stage;
                            if (data.percent) {
                                const percent = Math.min(100, data.percent);
                                progressBar.style.width = percent + '%';
                                progressBar.textContent = percent.toFixed(1) + '%';
                            }
                            if (data.log) {
                                log.innerHTML += data.log + '\n';
                                log.scrollTop = log.scrollHeight;
                            }
                        } catch (e) { console.error('Merge progress error:', e); }
                    };
                    mergeEventSource.onerror = function() { if (mergeEventSource) mergeEventSource.close(); };
                }

                document.getElementById('merge-stop-button')?.addEventListener('click', function() {
                    if (mergeEventSource) mergeEventSource.close();
                    fetch('/stop_process', { method: 'POST' });
                });
            </script>
        </div>
    </div>

    <!-- 4stream Upload Tab -->
    <div id="tab-4stream" class="tab-content {% if current_tab == '4stream' %}active{% endif %}">
        <div class="card">
            <div class="card-header">
                <div class="card-icon orange">🎬</div>
                <div>
                    <h2 class="card-title">Upload to 4stream</h2>
                    <p class="card-desc">Upload files to your 4stream account via API</p>
                </div>
            </div>
            <div class="helper-text">💡 Select files from the downloads folder and upload them directly to your 4stream account</div>

            <form method="POST" action="{{ url_for('upload_direct_to_4stream') }}" enctype="multipart/form-data">
                <label>Select a file from your computer:</label><br>
                <input type="file" name="file" required><br>
                <button type="submit" class="upload">🎬 Upload to 4stream</button>
            </form>
        </div>
    </div>

    <!-- External Links Tab -->
    <div id="tab-links" class="tab-content {% if current_tab == 'links' %}active{% endif %}">
        <div class="card">
            <div class="card-header">
                <div class="card-icon red">🔗</div>
                <div>
                    <h2 class="card-title">External Links</h2>
                    <p class="card-desc">Quick access to cloud storage services</p>
                </div>
            </div>
            <div class="helper-text">💡 Open external storage services for easy file access and management</div>

            <div style="display: flex; gap: 10px; flex-wrap: wrap;">
                <a href="https://www.google.com" target="_blank" rel="noopener noreferrer"><button>🔍 Google</button></a>
                <a href="https://mail.google.com" target="_blank" rel="noopener noreferrer"><button>📧 Gmail</button></a>
                <a href="https://diploi.com" target="_blank" rel="noopener noreferrer"><button>📱 Diploi</button></a>
                <a href="https://replit.com" target="_blank" rel="noopener noreferrer"><button>🖥️ Server</button></a>
                <a href="https://u.pcloud.link/publink/show?code=kZMQ3n5ZSnXgcgvdcOufntHqQNgVVyxCSPVX" target="_blank" rel="noopener noreferrer"><button>☁️ pCloud</button></a>
                <a href="https://www.dropbox.com/scl/fo/pbj4suf64bx3dy6823q4m/AH7sEbQozX07lWHDHE_8sgo?rlkey=52gqevdguucjpj6o0tyxzqlih&st=y3gaq1n7&dl=0" target="_blank" rel="noopener noreferrer"><button>📦 Dropbox</button></a>
                <a href="https://www.1337x.to/" target="_blank" rel="noopener noreferrer"><button>🧲 Torrent</button></a>
                <a href="http://were-ref.gl.at.ply.gg:36828/" target="_blank" rel="noopener noreferrer"><button>🌐 Toro</button></a>
                <a href="https://studio.bilibili.tv/" target="_blank" rel="noopener noreferrer"><button>📺 Bilibili</button></a>
                <a href="http://as-strap.gl.at.ply.gg:36363/" target="_blank" rel="noopener noreferrer"><button>🎬 AS-ST</button></a>
                <a href="https://ext.to/" target="_blank" rel="noopener noreferrer"><button>🔗 EXT</button></a>
                <a href="https://pixeldrain.com/user/filemanager#files" target="_blank" rel="noopener noreferrer"><button>💾 Pixeldrain</button></a>
                <a href="https://up4stream.com/users/ogaoga" target="_blank" rel="noopener noreferrer"><button>🎬 4stream</button></a>
                <a href="https://a.asd.homes/home3/" target="_blank" rel="noopener noreferrer"><button>🏠 ASD</button></a>
            </div>
        </div>
    </div>
</div>

<script>
    function showNotification(message, type = 'info') {
        const notification = document.createElement('div');
        notification.className = `notification ${type}`;
        notification.textContent = message;
        document.body.appendChild(notification);
        setTimeout(() => {
            notification.style.opacity = '0';
            setTimeout(() => { document.body.removeChild(notification); }, 300);
        }, 3000);
    }

    const globalProgressBtn = document.getElementById('global-progress-btn');
    const globalProgressModal = document.getElementById('global-progress-modal');
    let globalEventSource = null;
    let logLines = [];

    function closeGlobalProgressModal() {
        globalProgressModal.style.display = 'none';
        if (globalEventSource) {
            globalEventSource.close();
            globalEventSource = null;
        }
    }

    globalProgressBtn.onclick = function() {
        globalProgressModal.style.display = 'block';
        if (globalEventSource) globalEventSource.close();

        const stage = document.getElementById('global-progress-stage');
        const progressBar = document.getElementById('global-progress-bar-inner');
        const log = document.getElementById('global-progress-log');

        logLines = []; // Reset log lines
        log.innerHTML = 'Connecting to progress stream...';

        globalEventSource = new EventSource("{{ url_for('progress_stream') }}");
        globalEventSource.onmessage = function(event) {
            handleSseEvent(event, stage, progressBar, log, globalEventSource, true);
        };
        globalEventSource.onerror = function(err) {
            stage.textContent = 'Connection error. Please refresh.';
            if (globalEventSource) globalEventSource.close();
        };
    }

    function handleSseEvent(event, stage, progressBar, log, eventSource, isGlobalModal) {
        try {
            const data = JSON.parse(event.data);
            if (data.final_url) { window.finalUrl = data.final_url; }

            if (data.log && data.log === 'DONE') {
                eventSource.close();
                stage.textContent = '✅ Completed!';
                progressBar.style.backgroundColor = '#28a745';
                log.innerHTML += "\\n\\nOperation finished. Redirecting...";
                globalProgressBtn.style.display = 'none';

                if (!isGlobalModal) {
                    let redirectTarget = "{{ url_for('list_files', current_path='') }}";
                    if (window.finalUrl) {
                        redirectTarget = "{{ url_for('operation_complete') }}?url=" + encodeURIComponent(window.finalUrl);
                    }
                    setTimeout(() => { window.location.href = redirectTarget; }, 2000);
                } else {
                    setTimeout(closeGlobalProgressModal, 3000);
                }
                return;
            }
            if (data.error) {
                eventSource.close();
                stage.textContent = '❌ Error!';
                progressBar.style.backgroundColor = '#dc3545';
                logLines.push(`\\nERROR: ${data.error}`);
                log.textContent = logLines.join('\\n');
                showNotification('Operation failed: ' + data.error, 'error');
                globalProgressBtn.style.display = 'none';
                return;
            }

            if (data.stage) stage.textContent = data.stage;
            if (data.percent) {
                progressBar.style.width = data.percent + '%';
                progressBar.textContent = data.percent.toFixed(1) + '%';
            }
            if (data.log) {
                logLines.push(data.log);
                const MAX_LOG_LINES = 150;
                if (logLines.length > MAX_LOG_LINES) {
                    logLines.shift(); // Remove the oldest line
                }
                log.textContent = logLines.join('\\n');
                log.scrollTop = log.scrollHeight;
            }
        } catch (e) {
            console.error('Error parsing SSE data:', e);
        }
    }

    document.addEventListener("DOMContentLoaded", function() {
        if ({{ session.get('task_active', 'false')|lower }}) {
            globalProgressBtn.style.display = 'block';
        }

        const globalStopBtn = document.getElementById('global-stop-button');
        if (globalStopBtn) {
            globalStopBtn.addEventListener('click', function() {
                fetch('/stop_encode', { method: 'POST' }).then(response => {
                    if (response.ok) {
                        document.getElementById('global-progress-stage').textContent = 'Process stop requested.';
                    }
                });
            });
        }

        {% if download_started %}
            var activeTab = "{{ current_tab }}";
            var cId = "progress-container", sId = "progress-stage", bId = "progress-bar-inner", lId = "progress-log", stId = "stop-button";

            if (activeTab === "youtube") {
                cId = "yt-progress-container"; sId = "yt-progress-stage"; bId = "yt-progress-bar-inner"; lId = "yt-progress-log"; stId = "yt-stop-button";
            } else if (activeTab === "merge") {
                cId = "merge-progress-container"; sId = "merge-progress-stage"; bId = "merge-progress-bar-inner"; lId = "merge-progress-log"; stId = "merge-stop-button";
            }

            const progressContainer = document.getElementById(cId);
            const stage = document.getElementById(sId);
            const progressBar = document.getElementById(bId);
            const log = document.getElementById(lId);
            let inlineLogLines = [];

            if(progressContainer) progressContainer.style.display = 'block';
            globalProgressBtn.style.display = 'block';

            const eventSource = new EventSource("{{ url_for('progress_stream') }}");
            window.finalUrl = null;

            eventSource.onmessage = function(event) {
                handleSseEvent(event, stage, progressBar, log, eventSource, false);
            };

            eventSource.onerror = function(err) {
                if(stage) stage.textContent = 'Connection error. Please refresh.';
                eventSource.close();
                console.error('SSE error:', err);
            };

            const stopBtn = document.getElementById(stId);
            if (stopBtn) {
                 stopBtn.addEventListener('click', function() {
                    fetch('/stop_encode', {method: 'POST'}).then(response => {
                        if (response.ok) {
                            if(stage) stage.textContent = 'Encoding stopped.';
                            if(progressBar) progressBar.style.backgroundColor = '#dc3545';
                            eventSource.close();
                        }
                    });
                });
            }
        {% endif %}
    });
</script>
</body>
</html>
"""

ENCODE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Encode Video</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', 'Roboto', '-apple-system', sans-serif; line-height: 1.6; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: #1a1a1a; padding: 15px; min-height: 100vh; }
        .container { max-width: 900px; margin: 0 auto; background: rgba(255, 255, 255, 0.97); backdrop-filter: blur(10px); padding: 25px; border-radius: 16px; box-shadow: 0 20px 60px rgba(0,0,0,0.15); border: 1px solid rgba(255, 255, 255, 0.3); }
        .card { background: white; border: 1px solid rgba(0,0,0,0.08); border-radius: 12px; padding: 20px; margin-bottom: 18px; box-shadow: 0 4px 12px rgba(0,0,0,0.08); transition: all 0.3s ease; }
        .card:hover { box-shadow: 0 8px 24px rgba(0,0,0,0.12); transform: translateY(-2px); }
        .card-header { display: flex; align-items: center; gap: 12px; margin-bottom: 15px; }
        .card-icon { width: 48px; height: 48px; border-radius: 10px; display: flex; align-items: center; justify-content: center; font-size: 24px; color: white; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); flex-shrink: 0; }
        .card-title { font-size: 18px; font-weight: 700; color: #1a1a1a; margin: 0; }
        h1, h2, h3 { color: #2c2c2c; font-weight: 600; }
        h1 { font-size: 24px; margin-bottom: 10px; }
        h2 { font-size: 18px; margin-top: 18px; margin-bottom: 12px; }
        h3 { font-size: 15px; margin-bottom: 10px; }
        input[type="text"], input[type="number"], select { width: 100%; padding: 10px 12px; margin: 5px 0 12px; border: 1px solid #d0d0d0; border-radius: 8px; font-size: 13px; transition: all 0.2s ease; background: white; }
        input[type="text"]:focus, input[type="number"]:focus, select:focus { outline: none; border-color: #667eea; box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1); background: #f9fafb; }
        button { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 10px 16px; border: none; border-radius: 8px; cursor: pointer; font-size: 12px; font-weight: 600; transition: all 0.2s ease; margin-right: 8px; margin-bottom: 8px; box-shadow: 0 4px 12px rgba(102, 126, 234, 0.2); }
        button:hover { transform: translateY(-2px); box-shadow: 0 6px 16px rgba(102, 126, 234, 0.3); }
        button.delete { background: linear-gradient(135deg, #dc3545 0%, #c82333 100%); margin-top: 10px; box-shadow: 0 4px 12px rgba(220, 53, 69, 0.2); }
        button.delete:hover { box-shadow: 0 6px 16px rgba(220, 53, 69, 0.3); }
        a { color: #667eea; text-decoration: none; font-weight: 600; transition: color 0.2s ease; }
        a:hover { color: #764ba2; }
        .flash-msg { padding: 14px 16px; border-radius: 8px; margin-bottom: 15px; font-weight: 600; border-left: 4px solid; font-size: 13px; }
        .flash-success { background: rgba(40, 167, 69, 0.1); color: #155724; border-left-color: #28a745; }
        .flash-error { background: rgba(220, 53, 69, 0.1); color: #721c24; border-left-color: #dc3545; }
        .progress-container { display: none; margin-top: 20px; background: #f9f9f9; padding: 18px; border-radius: 12px; border: 1px solid rgba(0,0,0,0.08); }
        .progress-bar { width: 100%; height: 8px; background-color: #e0e0e0; border-radius: 10px; overflow: hidden; margin: 15px 0; box-shadow: inset 0 2px 4px rgba(0,0,0,0.06); }
        .progress-bar-inner { width: 0%; height: 100%; background: linear-gradient(90deg, #667eea 0%, #764ba2 100%); text-align: center; line-height: 24px; color: white; border-radius: 10px; transition: width 0.4s ease; }
        #progress-log { margin-top: 10px; font-family: 'Courier New', monospace; font-size: 11px; max-height: 200px; overflow-y: auto; background: white; color: #222; padding: 12px; border-radius: 8px; border: 1px solid #e0e0e0; }
        #global-progress-btn { position: fixed; bottom: 20px; right: 20px; z-index: 9998; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 12px 20px; display: none; color: white; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; transition: all 0.2s ease; font-size: 13px; box-shadow: 0 8px 24px rgba(102, 126, 234, 0.3); }
        #global-progress-btn:hover { transform: translateY(-2px); box-shadow: 0 12px 32px rgba(102, 126, 234, 0.4); }
        .modal { display: none; position: fixed; z-index: 9999; left: 0; top: 0; width: 100%; height: 100%; background-color: rgba(0,0,0,0.4); backdrop-filter: blur(4px); overflow-y: auto; }
        .modal-content { background: rgba(255, 255, 255, 0.98); margin: 20px auto; padding: 28px; border-radius: 16px; width: 90%; max-width: 800px; box-shadow: 0 20px 60px rgba(0,0,0,0.2); border: 1px solid rgba(255, 255, 255, 0.3); backdrop-filter: blur(10px); }
        .modal-content .close { float: right; font-size: 28px; font-weight: bold; cursor: pointer; color: #999; transition: color 0.2s ease; }
        .modal-content .close:hover { color: #667eea; }
        @media (max-width: 768px) {
            body { padding: 10px; }
            .container { padding: 18px; }
            h1 { font-size: 20px; }
            h2 { font-size: 16px; }
            h3 { font-size: 14px; }
            input[type="text"], input[type="number"], select { padding: 8px 10px; font-size: 12px; }
            button { padding: 8px 14px; font-size: 11px; }
            #progress-log { max-height: 150px; font-size: 10px; }
            #global-progress-btn { bottom: 10px; right: 10px; padding: 9px 14px; font-size: 11px; }
            .modal-content { padding: 20px; margin: 15px auto; }
        }
        @media (max-width: 480px) {
            body { padding: 8px; }
            .container { padding: 14px; }
            h1 { font-size: 18px; }
            h2 { font-size: 14px; }
            h3 { font-size: 12px; }
            input[type="text"], input[type="number"], select { padding: 7px 8px; font-size: 11px; }
            button { padding: 7px 11px; font-size: 10px; }
            .modal-content { padding: 15px; margin: 10px auto; }
            #global-progress-btn { bottom: 8px; right: 8px; padding: 6px 11px; font-size: 9px; }
        }
    </style>
    <script>
        function validateForm() { return true; }
        function validateEncodeForm() { return true; }
    </script>
</head>
<body>
<!-- Global Progress Elements -->
<button id="global-progress-btn">View Progress</button>
<div id="global-progress-modal" class="modal">
    <div class="modal-content">
        <span class="close" onclick="closeGlobalProgressModal()">&times;</span>
        <div id="global-progress-container" class="progress-container" style="display:block;">
            <h3 id="global-progress-stage">...</h3>
            <div class="progress-bar"><div id="global-progress-bar-inner" class="progress-bar-inner">0%</div></div>
            <pre id="global-progress-log"></pre>
            <button id="global-stop-button" class="delete" style="margin-top:10px;">Stop Process</button>
        </div>
    </div>
</div>

<div class="container">
    {% with messages = get_flashed_messages(with_categories=true) %}
        {% if messages %}
            {% for category, message in messages %}
                <div class="flash-msg flash-{{ category }}">{{ message|safe }}</div>
            {% endfor %}
        {% endif %}
    {% endwith %}

    <div class="card">
        <div class="card-header">
            <div class="card-icon">⚙️</div>
            <div>
                <h1 class="card-title">Encode Video</h1>
                <p style="font-size: 12px; color: #666; margin: 4px 0 0 0;">{{ filepath }}</p>
            </div>
        </div>

        <div id="progress-container" class="progress-container">
            <h3 id="progress-stage">Starting...</h3>
            <div class="progress-bar">
                <div id="progress-bar-inner" class="progress-bar-inner">0%</div>
            </div>
            <pre id="progress-log"></pre>
            <button id="stop-button" class="delete">⏹️ Stop Encoding</button>
        </div>

        <form method="POST" onsubmit="return validateEncodeForm()">
        <label>Output Filename (relative to downloads folder):</label><br>
        <input type="text" name="output_filename" value="{{ suggested_output }}" required><br>
        <label>Codec:</label><br>
        <select name="codec" id="codec" required>
            <option value="none" {% if codec == "none" %}selected{% endif %}>No Encoding (Copy)</option>
            <option value="h265" {% if codec == "h265" %}selected{% endif %}>Encode to H.265 (x265)</option>
            <option value="av1" {% if codec == "av1" %}selected{% endif %}>Encode to AV1 (SVT-AV1)</option>
            <option value="h265_copy_audio" {% if codec == "h265_copy_audio" %}selected{% endif %}>H.265 Video Only (Copy Audio)</option>
            <option value="av1_copy_audio" {% if codec == "av1_copy_audio" %}selected{% endif %}>AV1 Video Only (Copy Audio)</option>
            <option value="copy_video" {% if codec == "copy_video" %}selected{% endif %}>Copy Video (Encode Audio Only)</option>
        </select><br>

        <div id="encoding-options" style="display: {% if codec != 'none' %}block{% else %}none{% endif %};">
            <div id="video-encoding-options">
                <label>Encoding Mode:</label><br>
                <select name="pass_mode" id="pass_mode">
                    <option value="1-pass" {% if pass_mode == "1-pass" %}selected{% endif %}>1-pass (CRF)</option>
                    <option value="2-pass" {% if pass_mode == "2-pass" %}selected{% endif %}>2-pass (VBR)</option>
                </select><br>
                <label>Preset (slower = better quality/smaller file):</label><br>
                <select name="preset" id="preset"></select><br>
                <label>Video Bitrate (kb/s, optional):</label><br>
                <input type="number" name="bitrate" id="bitrate" value="{{ bitrate }}" min="100" placeholder="e.g., 600 for AV1, 2000 for H.265"><br>
                <label>CRF (0–63, lower = better quality):</label><br>
                <input type="number" name="crf" id="crf" value="{% if crf %}{{ crf }}{% elif codec == 'h265' or codec == 'h265_copy_audio' %}28{% else %}45{% endif %}" min="0" max="63" step="1" placeholder="e.g., 28 for H.265, 45 for AV1"><br>
                <label>Frame Rate (optional):</label><br>
                <select name="fps"><option value="">Original</option><option value="24">24 fps</option><option value="30">30 fps</option><option value="60">60 fps</option></select><br>
                <label>Resolution (Scale, optional):</label><br>
                <select name="scale"><option value="">Original</option><option value="1920:-2">1080p (1920px wide)</option><option value="1280:-2">720p (1280px wide)</option><option value="854:-2">480p (854px wide)</option><option value="640:-2">360p (640px wide)</option></select><br>
                <label>Adaptive Quantization Mode (AV1 only):</label><br>
                <select name="aq_mode" id="aq_mode"><option value="0">Disabled</option><option value="1">PSNR-based</option><option value="2" selected>Variance-based</option></select><br>
                <label>Variance Boost (AV1 only, 0–3):</label><br>
                <input type="number" name="variance_boost" id="variance_boost" value="2" min="0" max="3" step="1" placeholder="e.g., 2"><br>
                <label>Tiles (AV1 only, e.g., 2x2 for faster encoding):</label><br>
                <select name="tiles" id="tiles"><option value="">None</option><option value="2x2" selected>2x2 (Recommended for 720p)</option><option value="4x4">4x4</option></select><br>
                <label><input type="checkbox" name="enable_vmaf" value="true"> Compute VMAF Quality Score (slower)</label><br>
            </div>
            <div id="audio-encoding-options">
                <label>Audio Bitrate (kb/s):</label><br>
                <input type="number" name="audio_bitrate" id="audio_bitrate" value="{{ audio_bitrate|default('32') }}" min="32" max="512" step="8" placeholder="e.g., 32, 64, 96, 128"><br>
                <label><input type="checkbox" name="force_stereo" value="true" {% if force_stereo %}checked{% endif %}> Force Stereo (2-channel) Audio</label><br>
            </div>
        </div>

        <script>
            const codecSelect = document.getElementById('codec');
            const presetSelect = document.getElementById('preset');
            const crfInput = document.getElementById('crf');
            const passModeSelect = document.getElementById('pass_mode');
            const bitrateInput = document.getElementById('bitrate');
            const aqModeSelect = document.getElementById('aq_mode');
            const varianceBoostInput = document.getElementById('variance_boost');
            const tilesSelect = document.getElementById('tiles');

            function updatePresetOptions() {
                const codec = codecSelect.value;
                const encodingOptions = document.getElementById('encoding-options');
                const videoEncodingOptions = document.getElementById('video-encoding-options');
                const audioEncodingOptions = document.getElementById('audio-encoding-options');
                encodingOptions.style.display = codec !== 'none' ? 'block' : 'none';
                if (codec === 'copy_video') {
                    videoEncodingOptions.style.display = 'none'; audioEncodingOptions.style.display = 'block';
                } else if (codec.endsWith('_copy_audio')) {
                    videoEncodingOptions.style.display = 'block'; audioEncodingOptions.style.display = 'none';
                } else if (codec !== 'none') {
                    videoEncodingOptions.style.display = 'block'; audioEncodingOptions.style.display = 'block';
                }
                presetSelect.innerHTML = '';
                if (codec === 'av1' || codec === 'av1_copy_audio') {
                    for (let p = 0; p <= 13; p++) {
                        let label = p.toString();
                        if (p === 0) label += ' (slowest)'; else if (p === 13) label += ' (fastest)'; else if (p > 7) label += ' (fast)'; else label += ' (medium)';
                        const option = document.createElement('option');
                        option.value = p; option.text = label;
                        if (p === 7) option.selected = true;
                        presetSelect.appendChild(option);
                    }
                    crfInput.value = crfInput.value || '45'; crfInput.placeholder = 'e.g., 45 for AV1';
                    aqModeSelect.disabled = false; varianceBoostInput.disabled = false; tilesSelect.disabled = false;
                } else if (codec === 'h265' || codec === 'h265_copy_audio') {
                    const presets = ['ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow', 'slower', 'veryslow', 'placebo'];
                    presets.forEach(p => {
                        const option = document.createElement('option');
                        option.value = p; option.text = p;
                        if (p === 'faster') option.selected = true;
                        presetSelect.appendChild(option);
                    });
                    crfInput.value = crfInput.value || '28'; crfInput.placeholder = 'e.g., 28 for H.265';
                    aqModeSelect.disabled = true; varianceBoostInput.disabled = true; tilesSelect.disabled = true;
                } else {
                    aqModeSelect.disabled = true; varianceBoostInput.disabled = true; tilesSelect.disabled = true;
                }
                if (codec === 'none') {
                    bitrateInput.removeAttribute('required'); bitrateInput.removeAttribute('min'); bitrateInput.value = '';
                } else {
                    bitrateInput.setAttribute('min', '100');
                    if (passModeSelect.value === '2-pass') bitrateInput.setAttribute('required', 'required');
                    else bitrateInput.removeAttribute('required');
                }
            }
            function validateEncodeForm() {
                const codec = codecSelect.value;
                if (codec.includes('h265') || codec.includes('av1')) {
                    if (!presetSelect.value) { alert('Please select a preset.'); return false; }
                    if (passModeSelect.value === '2-pass' && (!bitrateInput.value || parseInt(bitrateInput.value) < 100)) { alert('Please specify a valid video bitrate for 2-pass encoding.'); return false; }
                    if (codec.includes('av1')) {
                        const varianceBoost = parseInt(varianceBoostInput.value);
                        if (isNaN(varianceBoost) || varianceBoost < 0 || varianceBoost > 3) { alert('Variance Boost must be between 0 and 3.'); return false; }
                    }
                }
                return true;
            }
            codecSelect.addEventListener('change', updatePresetOptions);
            passModeSelect.addEventListener('change', function() {
                if (codecSelect.value !== 'none') {
                    if (this.value === '2-pass') bitrateInput.setAttribute('required', 'required');
                    else bitrateInput.removeAttribute('required');
                }
            });
            document.addEventListener('DOMContentLoaded', updatePresetOptions);
        </script>
        <br>
        <label><input type="checkbox" name="upload_pixeldrain" value="true"> Upload to Pixeldrain after completion</label><br>
        <label><input type="checkbox" name="upload_4stream" value="true" {% if upload_4stream %}checked{% endif %}> Upload to 4stream after completion</label><br>
        <label><input type="checkbox" name="upload_gofile" value="true"> Upload to Gofile after completion</label><br><br>
        <button type="submit">▶️ Start Encoding</button>
        <a href="{{ url_for('list_files', current_path=current_path) }}">← Back to Files</a>
        </form>
    </div>
</div>

<script>
    const globalProgressBtn = document.getElementById('global-progress-btn');
    const globalProgressModal = document.getElementById('global-progress-modal');
    let globalEventSource = null;
    let logLines = [];

    function closeGlobalProgressModal() {
        globalProgressModal.style.display = 'none';
        if (globalEventSource) { globalEventSource.close(); globalEventSource = null; }
    }

    globalProgressBtn.onclick = function() {
        globalProgressModal.style.display = 'block';
        if (globalEventSource) globalEventSource.close();
        const stage = document.getElementById('global-progress-stage');
        const progressBar = document.getElementById('global-progress-bar-inner');
        const log = document.getElementById('global-progress-log');
        logLines = []; // Reset log
        log.innerHTML = 'Connecting to progress stream...';
        globalEventSource = new EventSource("{{ url_for('progress_stream') }}");
        globalEventSource.onmessage = function(event) {
            handleSseEvent(event, stage, progressBar, log, globalEventSource, true);
        };
        globalEventSource.onerror = function(err) {
            stage.textContent = 'Connection error. Please refresh.';
            if (globalEventSource) globalEventSource.close();
        };
    }

    function handleSseEvent(event, stage, progressBar, log, eventSource, isGlobalModal) {
        try {
            const data = JSON.parse(event.data);
            if (data.final_url) { window.finalUrl = data.final_url; }

            if (data.log && data.log === 'DONE') {
                eventSource.close();
                stage.textContent = '✅ Completed!';
                progressBar.style.backgroundColor = '#28a745';
                log.innerHTML += "\\n\\nOperation finished. Redirecting...";
                globalProgressBtn.style.display = 'none';

                if (!isGlobalModal) {
                    let redirectTarget = "{{ url_for('list_files', current_path=current_path) }}";
                    if (window.finalUrl) {
                        redirectTarget = "{{ url_for('operation_complete') }}?url=" + encodeURIComponent(window.finalUrl);
                    }
                    setTimeout(() => { window.location.href = redirectTarget; }, 2000);
                } else {
                     setTimeout(closeGlobalProgressModal, 3000);
                }
                return;
            }

            if (data.error) {
                eventSource.close();
                stage.textContent = '❌ Error!';
                progressBar.style.backgroundColor = '#dc3545';
                logLines.push(`\\nERROR: ${data.error}`);
                log.textContent = logLines.join('\\n');
                globalProgressBtn.style.display = 'none';
                return;
            }

            if (data.stage) stage.textContent = data.stage;
            if (data.percent) {
                progressBar.style.width = data.percent + '%';
                progressBar.textContent = data.percent.toFixed(1) + '%';
            }
            if (data.log) {
                logLines.push(data.log);
                const MAX_LOG_LINES = 150;
                if (logLines.length > MAX_LOG_LINES) { logLines.shift(); }
                log.textContent = logLines.join('\\n');
                log.scrollTop = log.scrollHeight;
            }
        } catch (e) { console.error('Error parsing SSE data:', e); }
    };

    document.addEventListener("DOMContentLoaded", function() {
        if ({{ session.get('task_active', 'false')|lower }}) {
            globalProgressBtn.style.display = 'block';
        }

        const globalStopBtn = document.getElementById('global-stop-button');
        if (globalStopBtn) {
            globalStopBtn.addEventListener('click', function() {
                fetch('/stop_encode', { method: 'POST' }).then(response => {
                    if (response.ok) {
                        document.getElementById('global-progress-stage').textContent = 'Process stop requested.';
                    }
                });
            });
        }

        {% if download_started %}
            const progressContainer = document.getElementById('progress-container');
            const stage = document.getElementById('progress-stage');
            const progressBar = document.getElementById('progress-bar-inner');
            const log = document.getElementById('progress-log');

            progressContainer.style.display = 'block';
            globalProgressBtn.style.display = 'block';

            const eventSource = new EventSource("{{ url_for('progress_stream') }}");
            window.finalUrl = null;

            eventSource.onmessage = function(event) {
                handleSseEvent(event, stage, progressBar, log, eventSource, false);
            };
            eventSource.onerror = function(err) {
                stage.textContent = 'Connection error. Please refresh.';
                eventSource.close();
            };
            const stopBtn = document.getElementById('stop-button');
            if (stopBtn) {
                stopBtn.addEventListener('click', function() {
                    fetch('/stop_encode', {method: 'POST'}).then(response => {
                        if (response.ok) {
                            stage.textContent = 'Encoding stopped.';
                            progressBar.style.backgroundColor = '#dc3545';
                            eventSource.close();
                        }
                    });
                });
            }
        {% endif %}
    });
</script>
</body>
</html>
"""


GOFILE_MANAGER_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Gofile Manager</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', 'Roboto', sans-serif; line-height: 1.6; background: #f0f2f5; color: #1a1a1a; padding: 15px; }
        .container { max-width: 1100px; margin: 0 auto; background: white; padding: 25px; border-radius: 16px; box-shadow: 0 10px 40px rgba(0,0,0,0.1); }
        h1 { font-size: 28px; color: #9b59b6; margin-bottom: 20px; text-align: center; }
        .back-link { display: inline-block; margin-bottom: 20px; color: #0066cc; text-decoration: none; font-weight: 600; }
        
        .premium-note { background: #fff8e1; border-left: 5px solid #ffc107; padding: 15px; border-radius: 8px; margin-bottom: 25px; font-size: 14px; }
        .premium-note a { color: #d32f2f; font-weight: bold; }

        .file-list { width: 100%; border-collapse: collapse; margin-top: 10px; }
        .file-list th, .file-list td { padding: 15px; text-align: left; border-bottom: 1px solid #eee; }
        .file-list th { background: #f8f9fa; color: #7f8c8d; text-transform: uppercase; font-size: 11px; letter-spacing: 1px; }
        .file-list tr:hover { background: #fcfaff; }
        
        .btn { padding: 8px 15px; border: none; border-radius: 6px; cursor: pointer; font-size: 12px; font-weight: 600; text-decoration: none; color: white; transition: all 0.2s; display: inline-block; margin-right: 5px; }
        .btn-blue { background: #3498db; }
        .btn-blue:hover { background: #2980b9; }
        .btn-green { background: #27ae60; }
        .btn-green:hover { background: #219150; }
        .btn-red { background: #e74c3c; }
        .btn-red:hover { background: #c0392b; }
        
        .status-msg { padding: 10px; border-radius: 6px; margin-bottom: 20px; text-align: center; }
        .status-success { background: #e8f5e9; color: #2e7d32; }
        .status-error { background: #ffebee; color: #c62828; }
        
        .source-tag { font-size: 10px; padding: 2px 6px; border-radius: 4px; background: #eee; margin-left: 5px; color: #666; vertical-align: middle; }
    </style>
</head>
<body>
<div class="container">
    <h1>📤 Gofile Upload Manager</h1>
    <a href="{{ url_for('list_files') }}" class="back-link">← Back to Local File Manager</a>
    
    <div class="premium-note">
        <strong>⚠️ Account Restriction:</strong> Gofile API restricts full account listings to <strong>Premium accounts only</strong>. 
        Below we show files uploaded using this app. To see all files, visit the <a href="https://gofile.io/login" target="_blank">Gofile Website</a>.
    </div>

    {% with messages = get_flashed_messages(with_categories=true) %}
        {% for category, message in messages %}
            <div class="status-msg status-{% if category == 'error' %}error{% else %}success{% endif %}">
                {{ message }}
            </div>
        {% endfor %}
    {% endwith %}

    <div style="background: #f8f9fa; border: 1px dashed #9b59b6; padding: 15px; border-radius: 12px; margin-bottom: 25px;">
        <h4 style="color: #9b59b6; margin-bottom: 10px;">🛠️ Manual Restore (For older files)</h4>
        <form method="POST" action="{{ url_for('gofile_add_to_local') }}" style="display: flex; gap: 10px;">
            <input type="text" name="direct_link" placeholder="Paste direct link (https://storeX.gofile.io/download/direct/...)" style="margin:0; flex-grow: 1;">
            <input type="hidden" name="filename" value="manual_restore.mkv">
            <button type="submit" class="btn btn-green" style="margin:0;">Restore Manually</button>
        </form>
        <p style="font-size: 11px; color: #666; margin-top: 8px;">
            If an old file fails to restore, open it in your browser, copy the real download link, and paste it here.
        </p>
    </div>

    <h3>📋 Recent Uploads (Local History)</h3>
    <table class="file-list">
        <thead>
            <tr>
                <th>Name</th>
                <th>Size</th>
                <th>Uploaded</th>
                <th>Actions</th>
            </tr>
        </thead>
        <tbody>
            {% if items %}
                {% for item in items %}
                <tr>
                    <td><strong>{{ item.name }}</strong> {% if item.is_remote %}<span class="source-tag">Account</span>{% else %}<span class="source-tag">App History</span>{% endif %}</td>
                    <td>{{ (item.size / 1024 / 1024)|round(2) }} MB</td>
                    <td>{{ item.createTime|datetime }}</td>
                    <td>
                        <a href="{{ item.link }}" target="_blank" class="btn btn-blue">Open</a>
                        {% if item.direct_link %}
                        <a href="{{ item.direct_link }}" target="_blank" class="btn btn-blue" style="background:#8e44ad;">Direct</a>
                        {% endif %}
                        <form method="POST" action="{{ url_for('gofile_add_to_local') }}" style="display:inline;">
                            <input type="hidden" name="fileId" value="{{ item.id }}">
                            <input type="hidden" name="filename" value="{{ item.name }}">
                            <input type="hidden" name="link" value="{{ item.link }}">
                            <input type="hidden" name="direct_link" value="{{ item.direct_link or '' }}">
                            <button type="submit" class="btn btn-green">Add to File List</button>
                        </form>
                        {% if item.is_remote %}
                        <form method="POST" action="{{ url_for('gofile_delete') }}" style="display:inline;" onsubmit="return confirm('Really delete this file from Gofile?');">
                            <input type="hidden" name="contentId" value="{{ item.id }}">
                            <button type="submit" class="btn btn-red">Delete</button>
                        </form>
                        {% endif %}
                    </td>
                </tr>
                {% endfor %}
            {% else %}
                <tr>
                    <td colspan="4" style="text-align: center; padding: 40px; color: #95a5a6;">No upload history found. Upload a file to see it here!</td>
                </tr>
            {% endif %}
        </tbody>
    </table>
</div>
</body>
</html>
"""

TRIM_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Trim Video</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', 'Roboto', '-apple-system', sans-serif; line-height: 1.6; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: #1a1a1a; padding: 15px; min-height: 100vh; }
        .container { max-width: 900px; margin: 0 auto; background: rgba(255, 255, 255, 0.97); backdrop-filter: blur(10px); padding: 25px; border-radius: 16px; box-shadow: 0 20px 60px rgba(0,0,0,0.15); border: 1px solid rgba(255, 255, 255, 0.3); }
        .card { background: white; border: 1px solid rgba(0,0,0,0.08); border-radius: 12px; padding: 20px; margin-bottom: 18px; box-shadow: 0 4px 12px rgba(0,0,0,0.08); transition: all 0.3s ease; }
        .card:hover { box-shadow: 0 8px 24px rgba(0,0,0,0.12); transform: translateY(-2px); }
        .card-header { display: flex; align-items: center; gap: 12px; margin-bottom: 15px; }
        .card-icon { width: 48px; height: 48px; border-radius: 10px; display: flex; align-items: center; justify-content: center; font-size: 24px; color: white; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); flex-shrink: 0; }
        .card-title { font-size: 18px; font-weight: 700; color: #1a1a1a; margin: 0; }
        h1, h2, h3 { color: #2c2c2c; font-weight: 600; }
        h1 { font-size: 24px; margin-bottom: 10px; }
        input[type="text"] { width: 100%; padding: 10px 12px; margin: 5px 0 12px; border: 1px solid #d0d0d0; border-radius: 8px; font-size: 13px; transition: all 0.2s ease; background: white; }
        input[type="text"]:focus { outline: none; border-color: #667eea; box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1); background: #f9fafb; }
        button { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 10px 16px; border: none; border-radius: 8px; cursor: pointer; font-size: 12px; font-weight: 600; transition: all 0.2s ease; margin-right: 8px; margin-bottom: 8px; box-shadow: 0 4px 12px rgba(102, 126, 234, 0.2); }
        button:hover { transform: translateY(-2px); box-shadow: 0 6px 16px rgba(102, 126, 234, 0.3); }
        a { color: #667eea; text-decoration: none; font-weight: 600; transition: color 0.2s ease; }
        a:hover { color: #764ba2; }
        .flash-msg { padding: 14px 16px; border-radius: 8px; margin-bottom: 15px; font-weight: 600; border-left: 4px solid; font-size: 13px; }
        .flash-success { background: rgba(40, 167, 69, 0.1); color: #155724; border-left-color: #28a745; }
        .flash-error { background: rgba(220, 53, 69, 0.1); color: #721c24; border-left-color: #dc3545; }
        .double_range_slider_box { position: relative; width: 100%; height: 80px; display: flex; justify-content: center; align-items: center; margin: 20px 0; }
        .double_range_slider { width: 90%; height: 5px; position: relative; background-color: #e0e0e0; border-radius: 3px; }
        .range_track { height: 100%; position: absolute; border-radius: 3px; background: linear-gradient(90deg, #667eea 0%, #764ba2 100%); }
        .minvalue { position: absolute; padding: 5px 10px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 4px; color: white; bottom: 0; transform: translate(0, -100%); left: 0; font-size: 11px; font-weight: 600; transition: left 0.3s cubic-bezier(0.165, 0.84, 0.44, 1); will-change: left, transform; }
        .maxvalue { position: absolute; padding: 5px 10px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 4px; color: white; top: 0; transform: translate(0, 100%); right: 0; font-size: 11px; font-weight: 600; transition: right 0.3s cubic-bezier(0.165, 0.84, 0.44, 1); will-change: right, transform; }
        input[type="range"] { position: absolute; width: 100%; height: 5px; background: none; pointer-events: none; -webkit-appearance: none; -moz-appearance: none; top: 50%; transform: translateY(-50%); }
        input[type="range"]::-webkit-slider-thumb { height: 18px; width: 18px; border-radius: 50%; border: 3px solid white; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); pointer-events: auto; -webkit-appearance: none; cursor: pointer; box-shadow: 0 2px 6px rgba(102, 126, 234, 0.3); }
        input[type="range"]::-moz-range-thumb { height: 14px; width: 14px; border-radius: 50%; border: 2px solid white; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); pointer-events: auto; -moz-appearance: none; cursor: pointer; box-shadow: 0 2px 6px rgba(102, 126, 234, 0.3); }
        @media (max-width: 768px) {
            body { padding: 10px; }
            .container { padding: 15px; }
            h1 { font-size: 20px; }
            input[type="text"] { padding: 8px 10px; font-size: 13px; }
            button { padding: 8px 14px; font-size: 11px; }
            .double_range_slider_box { height: 70px; margin: 15px 0; }
            .minvalue, .maxvalue { font-size: 10px; padding: 4px 8px; }
        }
        @media (max-width: 480px) {
            body { padding: 8px; }
            .container { padding: 12px; }
            h1 { font-size: 18px; }
            input[type="text"] { padding: 7px 8px; font-size: 12px; }
            button { padding: 7px 12px; font-size: 10px; }
            .double_range_slider_box { height: 60px; margin: 10px 0; }
            .minvalue, .maxvalue { font-size: 9px; padding: 3px 6px; }
        }
    </style>
</head>
<body>
<div class="container">
    {% with messages = get_flashed_messages(with_categories=true) %}
        {% if messages %}
            {% for category, message in messages %}
                <div class="flash-msg flash-{{ category }}">{{ message|safe }}</div>
            {% endfor %}
        {% endif %}
    {% endwith %}

    <div class="card">
        <div class="card-header">
            <div class="card-icon">✂️</div>
            <div>
                <h1 class="card-title">Trim Video</h1>
                <p style="font-size: 12px; color: #666; margin: 4px 0 0 0;">{{ filepath }}</p>
            </div>
        </div>

        <form method="POST">
        <label>Output Filename (relative to downloads folder):</label><br>
        <input type="text" name="output_filename" value="{{ suggested_output }}" required><br>

        <div class="double_range_slider_box">
            <div class="double_range_slider">
                <span class="range_track" id="range_track"></span>

                <input type="range" name="start_seconds" class="min" min="0" max="{{ duration }}" value="0" step="1" />
                <input type="range" name="end_seconds" class="max" min="0" max="{{ duration }}" value="{{ duration }}" step="1" />

                <div class="minvalue"></div>
                <div class="maxvalue"></div>
            </div>
        </div>

        <br>
        <button type="submit">✂️ Trim Video (No Re-encoding)</button>
        <a href="{{ url_for('list_files', current_path=current_path) }}">← Back to Files</a>
        </form>
    </div>
</div>

<script>
    function formatTime(seconds) {
        let h = Math.floor(seconds / 3600);
        let m = Math.floor((seconds % 3600) / 60);
        let s = Math.floor(seconds % 60);
        return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
    }

    let minRangeValueGap = 1;
    const range = document.getElementById("range_track");
    const minval = document.querySelector(".minvalue");
    const maxval = document.querySelector(".maxvalue");
    const rangeInput = document.querySelectorAll("input[type='range']");
    let minRange, maxRange, minPercentage, maxPercentage;
    const minRangeFill = () => { range.style.left = (rangeInput[0].value / rangeInput[0].max) * 100 + "%"; };
    const maxRangeFill = () => { range.style.right = 100 - (rangeInput[1].value / rangeInput[1].max) * 100 + "%"; };
    const MinValueBubbleStyle = () => {
        minPercentage = (minRange / rangeInput[0].max) * 100;
        minval.style.left = minPercentage + "%";
        minval.style.transform = `translate(-${minPercentage / 2}%, -100%)`;
    };
    const MaxValueBubbleStyle = () => {
        maxPercentage = 100 - (maxRange / rangeInput[1].max) * 100;
        maxval.style.right = maxPercentage + "%";
        maxval.style.transform = `translate(${maxPercentage / 2}%, 100%)`;
    };
    const setMinValueOutput = () => { minRange = parseInt(rangeInput[0].value); minval.innerHTML = formatTime(rangeInput[0].value); };
    const setMaxValueOutput = () => { maxRange = parseInt(rangeInput[1].value); maxval.innerHTML = formatTime(rangeInput[1].value); };

    setMinValueOutput(); setMaxValueOutput(); minRangeFill(); maxRangeFill(); MinValueBubbleStyle(); MaxValueBubbleStyle();

    rangeInput.forEach((input) => {
        input.addEventListener("input", (e) => {
            setMinValueOutput(); setMaxValueOutput(); minRangeFill(); maxRangeFill(); MinValueBubbleStyle(); MaxValueBubbleStyle();
            if (maxRange - minRange < minRangeValueGap) {
                if (e.target.className === "min") {
                    rangeInput[0].value = maxRange - minRangeValueGap;
                    setMinValueOutput(); minRangeFill(); MinValueBubbleStyle();
                    e.target.style.zIndex = "2";
                } else {
                    rangeInput[1].value = minRange + minRangeValueGap;
                    e.target.style.zIndex = "2";
                    setMaxValueOutput(); maxRangeFill(); MaxValueBubbleStyle();
                }
            }
        });
    });
</script>
</body>
</html>
"""

FILE_OPERATION_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Processing...</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', 'Roboto', sans-serif; line-height: 1.6; background: #f8f9fa; color: #1a1a1a; min-height: 100vh; padding: 15px; }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; flex-wrap: wrap; gap: 10px; }
        .header h1 { margin: 0; flex-grow: 1; font-size: 26px; font-weight: 700; color: #1a1a1a; }
        .header-button { background: #28a745; color: white; padding: 9px 16px; border: none; border-radius: 6px; cursor: pointer; text-decoration: none; font-weight: 600; transition: all 0.2s ease; display: inline-block; font-size: 12px; }
        .header-button:hover { background: #218838; transform: translateY(-1px); box-shadow: 0 2px 6px rgba(40, 167, 69, 0.2); }
        .container { max-width: 900px; margin: 0 auto; background: white; padding: 20px; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); border: 1px solid #e8e8e8; }
        h1 { font-size: 24px; font-weight: 700; color: #1a1a1a; margin-bottom: 10px; }
        h2 { font-size: 18px; font-weight: 600; color: #2c2c2c; margin-top: 20px; margin-bottom: 15px; padding-bottom: 10px; border-bottom: 1px solid #e0e0e0; }
        h3 { font-size: 15px; font-weight: 600; color: #3a3a3a; margin-bottom: 10px; }
        pre { background-color: #f4f4f4; padding: 12px; border-radius: 6px; white-space: pre-wrap; word-wrap: break-word; color: #222; font-size: 11px; overflow-x: auto; border: 1px solid #e0e0e0; font-family: 'Courier New', monospace; }
        .progress-container { display: block; margin-top: 20px; }
        .progress-info { background: #fff3cd; border-left: 4px solid #ffc107; padding: 12px; border-radius: 6px; margin-bottom: 15px; font-size: 12px; font-weight: bold; color: #333; line-height: 1.6; }
        .progress-bar { width: 100%; height: 6px; background-color: #e0e0e0; border-radius: 8px; overflow: hidden; margin: 15px 0; }
        .progress-bar-inner { width: 0%; height: 100%; background: #0066cc; text-align: center; line-height: 24px; color: white; transition: width 0.4s ease; border-radius: 8px; }
        #progress-log { margin-top: 10px; font-family: 'Courier New', monospace; font-size: 11px; max-height: 200px; overflow-y: auto; background: #f4f4f4; color: #222; padding: 10px; border-radius: 6px; border: 1px solid #e0e0e0; }
        #global-progress-btn { position: fixed; bottom: 15px; right: 15px; z-index: 9998; background: #0066cc; color: white; padding: 10px 18px; border: none; border-radius: 6px; cursor: pointer; display: none; font-weight: 600; transition: all 0.2s ease; font-size: 12px; }
        #global-progress-btn:hover { background: #0052a3; transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0, 102, 204, 0.3); }
        button { background: #0066cc; color: white; padding: 9px 16px; border: none; border-radius: 6px; cursor: pointer; font-size: 12px; font-weight: 600; margin-right: 6px; margin-bottom: 6px; transition: all 0.2s ease; }
        button:hover { background: #0052a3; transform: translateY(-1px); box-shadow: 0 2px 6px rgba(0, 102, 204, 0.2); }
        button.delete { background: #dc3545; margin-top: 15px; }
        button.delete:hover { background: #c82333; box-shadow: 0 2px 6px rgba(220, 53, 69, 0.2); }
        .modal { display: none; position: fixed; z-index: 9999; left: 0; top: 0; width: 100%; height: 100%; background-color: rgba(0,0,0,0.5); overflow-y: auto; }
        .modal-content { background-color: white; margin: 20px auto; padding: 25px; border-radius: 12px; width: 90%; max-width: 800px; box-shadow: 0 10px 40px rgba(0,0,0,0.2); border: 1px solid #e8e8e8; }
        .modal-content .close { float: right; font-size: 24px; font-weight: bold; cursor: pointer; color: #999; transition: color 0.2s ease; }
        .modal-content .close:hover { color: #333; }
        @media (max-width: 768px) {
            body { padding: 10px; }
            .header { margin-bottom: 15px; }
            .header h1 { font-size: 20px; }
            .header-button { padding: 8px 14px; font-size: 11px; }
            .container { padding: 15px; }
            h2 { font-size: 16px; }
            h3 { font-size: 14px; }
            button { padding: 8px 14px; font-size: 11px; }
            #progress-log { max-height: 150px; font-size: 10px; }
            #global-progress-btn { bottom: 10px; right: 10px; padding: 8px 14px; font-size: 11px; }
            .progress-info { font-size: 11px; padding: 10px; }
        }
        @media (max-width: 480px) {
            body { padding: 8px; }
            .header { margin-bottom: 10px; gap: 5px; }
            .header h1 { font-size: 18px; }
            .header-button { padding: 7px 12px; font-size: 10px; }
            .container { padding: 12px; }
            h2 { font-size: 14px; }
            h3 { font-size: 12px; }
            button { padding: 7px 12px; font-size: 10px; }
            #progress-log { max-height: 120px; font-size: 9px; }
            #global-progress-btn { bottom: 8px; right: 8px; padding: 6px 12px; font-size: 10px; }
            .progress-info { font-size: 10px; padding: 8px; }
        }
    </style>
    <script>
        function validateForm() { return true; }
        function validateEncodeForm() { return true; }
    </script>
</head>
<body>
<!-- Global Progress Elements -->
<button id="global-progress-btn">View Progress</button>
<div id="global-progress-modal" class="modal">
    <div class="modal-content">
        <span class="close" onclick="closeGlobalProgressModal()">&times;</span>
        <div id="global-progress-container" class="progress-container" style="display:block;">
            <h3 id="global-progress-stage">...</h3>
            <div class="progress-bar"><div id="global-progress-bar-inner" class="progress-bar-inner">0%</div></div>
            <pre id="global-progress-log"></pre>
            <button id="global-stop-button" class="delete">Stop Process</button>
        </div>
    </div>
</div>
<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
    <h1 style="margin: 0;">{{ operation_title }}</h1>
    <a href="{{ url_for('list_files', current_path='') }}" class="header-button">📂 Manage Downloaded Files</a>
</div>
<div class="container">
    <p>Please wait while the operation completes. You will be redirected automatically.</p>
    <div id="progress-container" class="progress-container">
        <div id="batch-info-section" style="display:none; background-color: #fff3cd; border: 2px solid #ffc107; padding: 20px; border-radius: 8px; margin-bottom: 20px;">
            <div id="batch-filename" style="font-size: 18px; font-weight: bold; margin-bottom: 10px;">📁 Filename</div>
            <div id="batch-stats" style="font-size: 14px;"><strong>✅ Passed:</strong> 0 | <strong>⏳ Encoding:</strong> 1 | <strong>⏱️ Waiting:</strong> 0</div>
        </div>
        <div id="progress-info" class="progress-info" style="display:none; margin-bottom: 20px;"></div>
        <h3 id="progress-stage">Starting...</h3>
        <div class="progress-bar" style="margin-top: 15px; margin-bottom: 15px;">
            <div id="progress-bar-inner" class="progress-bar-inner" style="background-color: #17a2b8;">0%</div>
        </div>
        <pre id="progress-log"></pre>
        <button id="batch-stop-button" class="delete" style="display:none;">Stop Batch Encoding</button>
    </div>
</div>

<script>
    const globalProgressBtn = document.getElementById('global-progress-btn');
    const globalProgressModal = document.getElementById('global-progress-modal');
    let globalEventSource = null;
    let logLines = [];

    function closeGlobalProgressModal() {
        globalProgressModal.style.display = 'none';
        if (globalEventSource) { globalEventSource.close(); globalEventSource = null; }
    }

    globalProgressBtn.onclick = function() {
        globalProgressModal.style.display = 'block';
        if (globalEventSource) globalEventSource.close();
        const stage = document.getElementById('global-progress-stage');
        const progressBar = document.getElementById('global-progress-bar-inner');
        const log = document.getElementById('global-progress-log');
        logLines = []; // Reset log
        log.innerHTML = 'Connecting to progress stream...';
        globalEventSource = new EventSource("{{ url_for('progress_stream') }}");
        globalEventSource.onmessage = function(event) {
            handleSseEvent(event, stage, progressBar, log, globalEventSource, true);
        };
        globalEventSource.onerror = function(err) {
            stage.textContent = 'Connection error. Please refresh.';
            if (globalEventSource) globalEventSource.close();
        };
    }

    function handleSseEvent(event, stage, progressBar, log, eventSource, isGlobalModal) {
        try {
            const data = JSON.parse(event.data);
            if (data.final_url) { window.finalUrl = data.final_url; }

            if (data.log && data.log === 'DONE') {
                eventSource.close();
                stage.textContent = '✅ Completed!';
                progressBar.style.backgroundColor = '#28a745';
                log.innerHTML += "\\n\\nOperation finished. Redirecting...";
                globalProgressBtn.style.display = 'none';

                if (!isGlobalModal) {
                    let redirectTarget = "{{ url_for('list_files', current_path=current_path) }}";
                    if (window.finalUrl) {
                        redirectTarget = "{{ url_for('operation_complete') }}?url=" + encodeURIComponent(window.finalUrl);
                    }
                    setTimeout(() => { window.location.href = redirectTarget; }, 2000);
                } else {
                     setTimeout(closeGlobalProgressModal, 3000);
                }
                return;
            }

            if (data.error) {
                eventSource.close();
                stage.textContent = '❌ Error!';
                progressBar.style.backgroundColor = '#dc3545';
                logLines.push(`\\nERROR: ${data.error}`);
                log.textContent = logLines.join('\\n');
                globalProgressBtn.style.display = 'none';
                return;
            }

            if (data.stage) stage.textContent = data.stage;
            if (data.percent) {
                progressBar.style.width = data.percent + '%';
                progressBar.textContent = data.percent.toFixed(1) + '%';
            }
            if (data.file_info) {
                const batchSection = document.getElementById('batch-info-section');
                if (batchSection) {
                    batchSection.style.display = 'block';
                    batchSection.innerHTML = data.file_info;
                }
            }
            if (data.batch_encode_status) {
                const batchStopBtn = document.getElementById('batch-stop-button');
                if (batchStopBtn) batchStopBtn.style.display = 'block';
            }
            if (data.log) {
                logLines.push(data.log);
                const MAX_LOG_LINES = 150;
                if (logLines.length > MAX_LOG_LINES) { logLines.shift(); }
                log.textContent = logLines.join('\\n');
                log.scrollTop = log.scrollHeight;
            }
        } catch (e) { console.error('Error parsing SSE data:', e); }
    };

    document.addEventListener("DOMContentLoaded", function() {
        if ({{ session.get('task_active', 'false')|lower }}) {
            globalProgressBtn.style.display = 'block';
        }

        const globalStopBtn = document.getElementById('global-stop-button');
        if (globalStopBtn) {
            globalStopBtn.addEventListener('click', function() {
                fetch('/stop_encode', { method: 'POST' }).then(response => {
                    if (response.ok) {
                        document.getElementById('global-progress-stage').textContent = 'Process stop requested.';
                    }
                });
            });
        }

        {% if download_started %}
            const stage = document.getElementById('progress-stage');
            const progressBar = document.getElementById('progress-bar-inner');
            const log = document.getElementById('progress-log');
            globalProgressBtn.style.display = 'block';
            const eventSource = new EventSource("{{ url_for('progress_stream') }}");
            window.finalUrl = null;
            eventSource.onmessage = function(event) {
                 handleSseEvent(event, stage, progressBar, log, eventSource, false);
            };
            eventSource.onerror = function(err) {
                stage.textContent = 'Connection error. Please refresh.';
                eventSource.close();
            };

            const batchStopBtn = document.getElementById('batch-stop-button');
            if (batchStopBtn) {
                batchStopBtn.addEventListener('click', function() {
                    fetch('/stop_encode', { method: 'POST' }).then(response => {
                        if (response.ok) {
                            stage.textContent = 'Batch encoding stop requested.';
                            progressBar.style.backgroundColor = '#dc3545';
                        }
                    });
                });
            }
        {% endif %}
    });
</script>
</body>
</html>
"""


# -----------------------------
# Helper Functions
# -----------------------------
def human_size(size_bytes):
    if size_bytes is None or not isinstance(size_bytes,
                                            (int, float)) or size_bytes == 0:
        return "0 B"
    power = 1024
    n = 0
    power_labels = {0: 'B', 1: 'KiB', 2: 'MiB', 3: 'GiB', 4: 'TiB'}
    while size_bytes >= power and n < len(power_labels) - 1:
        size_bytes /= power
        n += 1
    return f"{size_bytes:.2f} {power_labels[n]}"


def get_safe_filename(name):
    """Sanitizes a string to be a valid filename component, allowing slashes for paths."""
    parts = name.split('/')
    safe_parts = [re.sub(r'[\\*?:"<>|]', "_", part) for part in parts]
    safe_parts = [re.sub(r'\s+', ' ', part).strip() for part in safe_parts]
    return '/'.join(safe_parts)


def get_unique_filepath(target_path):
    """Checks if a file exists and returns a unique path by adding a number."""
    if not os.path.exists(target_path):
        return target_path
    directory, filename = os.path.split(target_path)
    base, ext = os.path.splitext(filename)
    counter = 1
    new_filepath = os.path.join(directory, f"{base}_{counter}{ext}")
    while os.path.exists(new_filepath):
        counter += 1
        new_filepath = os.path.join(directory, f"{base}_{counter}{ext}")
    return new_filepath


def get_file_size(file_path):
    try:
        return human_size(os.path.getsize(file_path))
    except FileNotFoundError:
        return "N/A"


def is_media_file(file_path):
    video_extensions = {
        '.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm', '.m4v',
        '.3gp', '.mpg', '.mpeg', '.ts', '.vob'
    }
    audio_extensions = {
        '.mp3', '.wav', '.flac', '.aac', '.ogg', '.wma', '.m4a', '.opus'
    }
    ext = os.path.splitext(os.path.basename(file_path))[1].lower()
    return ext in video_extensions or ext in audio_extensions


def get_folder_total_size(folder_path):
    """Calculate total size of all files in folder recursively."""
    total_size = 0
    try:
        for dirpath, dirnames, filenames in os.walk(folder_path):
            for filename in filenames:
                filepath = os.path.join(dirpath, filename)
                try:
                    total_size += os.path.getsize(filepath)
                except (OSError, FileNotFoundError):
                    pass
    except Exception:
        pass
    return total_size


def get_available_space(path):
    """Get available disk space for the given path in bytes."""
    try:
        stat = os.statvfs(path)
        available = stat.f_bavail * stat.f_frsize
        return available
    except Exception:
        return 0


def get_media_info(file_path):
    """Fetches detailed media information using ffprobe."""
    try:
        command = [
            FFPROBE_PATH, "-v", "quiet", "-print_format", "json",
            "-show_streams", "-show_format", file_path
        ]
        result = subprocess.check_output(command, stderr=subprocess.STDOUT)
        data = json.loads(result)
        info = {"file_size": get_file_size(file_path)}
        format_info = data.get('format', {})
        duration_sec = float(format_info.get('duration', 0))
        info['duration'] = str(timedelta(seconds=int(duration_sec)))
        video_stream = next((s for s in data.get('streams', [])
                             if s.get('codec_type') == 'video'), None)
        audio_stream = next((s for s in data.get('streams', [])
                             if s.get('codec_type') == 'audio'), None)
        if video_stream:
            info['video_codec'] = video_stream.get('codec_name', 'N/A')
            info[
                'resolution'] = f"{video_stream.get('width')}x{video_stream.get('height')}"
            fr_str = video_stream.get('avg_frame_rate', '0/1')
            if '/' in fr_str and fr_str != '0/1':
                num, den = map(int, fr_str.split('/'))
                info['video_fps'] = f"{num / den:.2f}" if den else '0.00'
            else:
                info['video_fps'] = 'N/A'
            v_br_str = video_stream.get('bit_rate')
            if v_br_str:
                v_br = int(v_br_str)
                info['video_bitrate'] = f"{v_br // 1000} kbps"
                if duration_sec > 0:
                    info['video_stream_size'] = human_size(
                        (v_br / 8) * duration_sec)
            else:
                v_br_str = format_info.get('bit_rate')
                if v_br_str:
                    info[
                        'video_bitrate'] = f"{int(v_br_str) // 1000} kbps (overall)"
        if audio_stream:
            info['audio_codec'] = audio_stream.get('codec_name', 'N/A')
            a_br_str = audio_stream.get('bit_rate')
            if a_br_str:
                a_br = int(a_br_str)
                info['audio_bitrate'] = f"{a_br // 1000} kbps"
                if duration_sec > 0:
                    info['audio_stream_size'] = human_size(
                        (a_br / 8) * duration_sec)
        return info
    except Exception as e:
        print(f"Error fetching media info for {file_path}: {e}")
        return {"error": "Could not retrieve media information."}


def get_media_duration(file_path):
    if not is_media_file(file_path): return 0
    try:
        cmd = [
            FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", file_path
        ]
        duration_str = subprocess.check_output(
            cmd, universal_newlines=True, stderr=subprocess.DEVNULL).strip()
        return float(duration_str) if duration_str else 0
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        return 0


def get_audio_channels(file_path):
    try:
        cmd = [
            FFPROBE_PATH, "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=channels", "-of",
            "default=noprint_wrappers=1:nokey=1", file_path
        ]
        channels_str = subprocess.check_output(
            cmd, universal_newlines=True, stderr=subprocess.DEVNULL).strip()
        return int(channels_str) if channels_str else 2
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        return 2


def trim_video(input_path, output_path, start_time, end_time):
    try:
        unique_output_path = get_unique_filepath(output_path)
        cmd = [
            FFMPEG_PATH, "-y", "-i", input_path, "-ss", start_time, "-to",
            end_time, "-c", "copy", unique_output_path
        ]
        subprocess.run(cmd,
                       check=True,
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        return True, os.path.basename(unique_output_path)
    except subprocess.CalledProcessError as e:
        raise Exception(f"FFmpeg error: {e.returncode}")
    except Exception as e:
        raise Exception(f"Trim failed: {str(e)}")


def fetch_formats(url):
    try:
        ydl_opts = {
            'quiet': True, 
            'force_ipv4': True, 
            'source_address': '0.0.0.0', 
            'socket_timeout': 60
        }
        if os.path.exists(COOKIES_FILE):
            ydl_opts['cookiefile'] = COOKIES_FILE  # type: ignore
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if info is None:
            return "", [], []
        formats = info.get('formats', [])
        video_formats, audio_formats, raw_lines = [], [], []
        for f in formats:
            if not f.get('format_id'): continue
            fid, ext = f['format_id'], f.get('ext', 'u')
            height, width = f.get('height'), f.get('width')
            vcodec, acodec = f.get('vcodec'), f.get('acodec')
            fps = f.get('fps')
            size_bytes = f.get('filesize') or f.get('filesize_approx')
            res = f"{width}x{height}" if height else "audio"
            fps_int = int(fps) if fps else 0
            size = human_size(size_bytes)
            raw_lines.append(
                f"{fid:>3} {ext:<7} {res:<9} {fps_int:>3}fps {size:<10} {vcodec or 'none':<12} {acodec or 'none'}"
            )
            is_video = vcodec and vcodec != 'none' and height
            is_audio = acodec and acodec != 'none'
            if is_audio and not is_video:
                abr = f.get('abr', 0)
                audio_formats.append({
                    'id': fid,
                    'display': f"{acodec.upper()} | {int(abr)}k | ({size})",
                    'br': abr or 0
                })
            elif is_video:
                br = f.get('tbr') or f.get('vbr') or 0
                video_formats.append({
                    'id': fid,
                    'display':
                    f"{height}p | {fps_int}fps | {vcodec.upper()} | {int(br)}k | ({size})",
                    'h': height,
                    'fps': fps_int,
                    'is_muxed': is_audio
                })
        video_formats.sort(key=lambda x: (x.get('h', 0), x.get('fps', 0)),
                           reverse=True)
        audio_formats.sort(key=lambda x: x.get('br', 0), reverse=True)
        return '\n'.join(raw_lines), video_formats, audio_formats
    except Exception as e:
        flash(f"❌ Error fetching formats: {str(e)}", "error")
        return "", [], []


def get_original_filename(url):
    try:
        ydl_opts = {
            'quiet': True,
            'extractor_args': {
                'youtube': ['player_client=android']
            },
            'socket_timeout': 60,
            'force_ipv4': True,
            'source_address': '0.0.0.0'
        }
        if os.path.exists(COOKIES_FILE): ydl_opts['cookiefile'] = COOKIES_FILE
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if info is None:
            return "download.mkv"
        title = info.get('title', 'download')
        if title is None:
            title = 'download'
        title = title.strip()
        safe_title = re.sub(r'[\\/*?:"<>|]', '_', title)
        return f"{safe_title}.mkv"
    except Exception:
        return "download.mkv"


def fetch_formats_no_cookies(url):
    """Fetch formats WITHOUT using cookies"""
    try:
        ydl_opts = {
            'quiet': True, 
            'force_ipv4': True,
            'source_address': '0.0.0.0',
            'socket_timeout': 60
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if info is None:
            return "", [], []
        formats = info.get('formats', [])
        video_formats, audio_formats, raw_lines = [], [], []
        for f in formats:
            if not f.get('format_id'): continue
            fid, ext = f['format_id'], f.get('ext', 'u')
            height, width = f.get('height'), f.get('width')
            vcodec, acodec = f.get('vcodec'), f.get('acodec')
            fps = f.get('fps')
            size_bytes = f.get('filesize') or f.get('filesize_approx')
            res = f"{width}x{height}" if height else "audio"
            fps_int = int(fps) if fps else 0
            size = human_size(size_bytes)
            raw_lines.append(
                f"{fid:>3} {ext:<7} {res:<9} {fps_int:>3}fps {size:<10} {vcodec or 'none':<12} {acodec or 'none'}"
            )
            is_video = vcodec and vcodec != 'none' and height
            is_audio = acodec and acodec != 'none'
            if is_audio and not is_video:
                abr = f.get('abr', 0)
                audio_formats.append({
                    'id': fid,
                    'display': f"{acodec.upper()} | {int(abr)}k | ({size})",
                    'br': abr or 0
                })
            elif is_video:
                br = f.get('tbr') or f.get('vbr') or 0
                video_formats.append({
                    'id': fid,
                    'display':
                    f"{height}p | {fps_int}fps | {vcodec.upper()} | {int(br)}k | ({size})",
                    'h': height,
                    'fps': fps_int,
                    'is_muxed': is_audio
                })
        video_formats.sort(key=lambda x: (x.get('h', 0), x.get('fps', 0)),
                           reverse=True)
        audio_formats.sort(key=lambda x: x.get('br', 0), reverse=True)
        return '\n'.join(raw_lines), video_formats, audio_formats
    except Exception as e:
        flash(f"❌ Error fetching formats: {str(e)}", "error")
        return "", [], []


def get_original_filename_no_cookies(url):
    """Get filename WITHOUT using cookies"""
    try:
        ydl_opts = {
            'quiet': True,
            'extractor_args': {
                'youtube': ['player_client=android']
            },
            'socket_timeout': 60,
            'force_ipv4': True,
            'source_address': '0.0.0.0'
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if info is None:
            return "download.mkv"
        title = info.get('title', 'download')
        if title is None:
            title = 'download'
        title = title.strip()
        safe_title = re.sub(r'[\\/*?:"<>|]', '_', title)
        return f"{safe_title}.mkv"
    except Exception:
        return "download.mkv"


def run_command_with_progress(command, stage, q):
    process = subprocess.Popen(command,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT,
                               universal_newlines=True,
                               encoding='utf-8',
                               errors='ignore')
    if process.stdout is None:
        raise Exception("Process stdout is None")
    for line in iter(process.stdout.readline, ''):
        q.put({"log": line.strip()})
        match = re.search(r'\[download\]\s+([0-9.]+)%', line)
        if match:
            q.put({"stage": stage, "percent": float(match.group(1))})
    if process.wait() != 0:
        raise subprocess.CalledProcessError(process.returncode, command)


def upload_to_pixeldrain(file_path, filename, q):
    try:
        q.put({
            "stage": f"Uploading '{filename}' to Pixeldrain...",
            "percent": 10
        })
        api_url = "https://pixeldrain.com/api/file"
        with open(file_path, 'rb') as f:
            files = {'file': (filename, f)}
            auth = ('', PIXELDRAIN_API_KEY) if PIXELDRAIN_API_KEY else None
            q.put({"stage": "Sending data...", "percent": 50})
            response = requests.post(api_url, files=files, auth=auth)
        response.raise_for_status()
        result = response.json()
        if result.get("success"):
            file_id = result.get("id")
            pixeldrain_url = f"https://pixeldrain.com/u/{file_id}"
            q.put({"stage": "✅ Pixeldrain Upload Complete!", "percent": 100})
            q.put({
                "log": f"Success! Link: {pixeldrain_url}",
                "final_url": pixeldrain_url
            })
        else:
            q.put({
                "error":
                f"Pixeldrain API error: {result.get('message', 'Unknown')}"
            })
    except Exception as e:
        q.put({"error": f"Pixeldrain upload failed: {str(e)}"})
    finally:
        q.put({"log": "DONE"})


def upload_to_4stream(file_path, filename, q):
    try:
        if not UP4STREAM_API_KEY:
            q.put({"error": "4stream API key not configured"})
            q.put({"log": "DONE"})
            return

        q.put({"stage": "Getting upload server...", "percent": 10})

        # Step 1: Get upload server
        server_response = requests.get(
            "https://up4stream.com/api/upload/server",
            params={"key": UP4STREAM_API_KEY},
            timeout=10)
        server_response.raise_for_status()
        server_data = server_response.json()

        if server_data.get("status") != 200 or not server_data.get("result"):
            q.put({
                "error":
                f"Failed to get upload server: {server_data.get('msg', 'Unknown error')}"
            })
            q.put({"log": "DONE"})
            return

        upload_server = server_data["result"]
        q.put({"stage": "Got upload server, uploading file...", "percent": 30})

        # Step 2: Upload file to the server
        file_size = os.path.getsize(file_path)

        with open(file_path, 'rb') as f:
            files = {
                'file': (filename, f),
                'key': (None, UP4STREAM_API_KEY),
            }

            # Track upload progress
            def upload_with_progress():
                uploaded = [0]

                def update_progress(monitor):
                    uploaded[0] = monitor.bytes_read
                    percent = min(99, int((uploaded[0] / file_size) * 60) + 30)
                    q.put({"stage": "Uploading file...", "percent": percent})

                # Simple progress tracking
                response = requests.post(upload_server,
                                         files=files,
                                         timeout=300)
                return response

            response = upload_with_progress()

        response.raise_for_status()
        upload_result = response.json()

        if upload_result.get("status") == 200 and upload_result.get("files"):
            uploaded_file = upload_result["files"][0]
            filecode = uploaded_file.get("filecode")
            file_url = f"https://up4stream.com/{filecode}.html"

            q.put({"stage": "✅ 4stream Upload Complete!", "percent": 100})
            q.put({
                "log": f"Success! File uploaded to 4stream: {filename}",
                "final_url": file_url
            })
        else:
            q.put({
                "error":
                f"Upload failed: {upload_result.get('msg', 'Unknown error')}"
            })
    except requests.exceptions.Timeout:
        q.put({
            "error":
            "Upload timeout - file may be too large or connection is slow"
        })
    except requests.exceptions.RequestException as e:
        q.put({"error": f"Upload request failed: {str(e)}"})
    except Exception as e:
        q.put({"error": f"4stream upload failed: {str(e)}"})
    finally:
        q.put({"log": "DONE"})


def upload_to_pixeldrain_alt(file_path, filename, q, api_key):
    try:
        q.put({
            "stage": f"Uploading '{filename}' to Pixeldrain 2...",
            "percent": 10
        })
        api_url = "https://pixeldrain.com/api/file"
        with open(file_path, 'rb') as f:
            files = {'file': (filename, f)}
            auth = ('', api_key) if api_key else None
            q.put({"stage": "Sending data...", "percent": 50})
            response = requests.post(api_url, files=files, auth=auth)
        response.raise_for_status()
        result = response.json()
        if result.get("success"):
            file_id = result.get("id")
            pixeldrain_url = f"https://pixeldrain.com/u/{file_id}"
            q.put({"stage": "✅ Pixeldrain 2 Upload Complete!", "percent": 100})
            q.put({
                "log": f"Success! Link: {pixeldrain_url}",
                "final_url": pixeldrain_url
            })
        else:
            q.put({
                "error":
                f"Pixeldrain 2 API error: {result.get('message', 'Unknown')}"
            })
    except Exception as e:
        q.put({"error": f"Pixeldrain 2 upload failed: {str(e)}"})
    finally:
        q.put({"log": "DONE"})


def upload_to_gofile(file_path, filename, q):
    """Upload file to Gofile.io"""
    try:
        if not GOFILE_API_TOKEN:
            q.put({"error": "Gofile API token not configured"})
            q.put({"log": "DONE"})
            return

        q.put({"stage": "Getting Gofile upload server...", "percent": 10})

        # Step 1: Get the list of upload servers
        server_response = requests.get(
            "https://api.gofile.io/servers",
            headers={"Authorization": f"Bearer {GOFILE_API_TOKEN}"},
            timeout=10
        )
        server_response.raise_for_status()
        server_data = server_response.json()

        if server_data.get("status") != "ok" or not server_data.get("data", {}).get("servers"):
            q.put({"error": f"Failed to get upload server: {server_data.get('status', 'No servers available')}"})
            q.put({"log": "DONE"})
            return

        # Pick the first available server
        upload_server = server_data["data"]["servers"][0]["name"]
        q.put({"stage": f"Got server '{upload_server}', uploading file...", "percent": 30})

        # Step 2: Upload file to the server
        file_size = os.path.getsize(file_path)
        upload_url = f"https://{upload_server}.gofile.io/uploadFile"

        with open(file_path, 'rb') as f:
            files = {'file': (filename, f)}
            headers = {
                "Authorization": f"Bearer {GOFILE_API_TOKEN}",
                "X-Website-Token": get_gofile_website_token()
            }

            # Track upload progress (simplified)
            uploaded = [0]
            
            def update_progress():
                # Simple progress simulation
                for i in range(30, 90, 10):
                    q.put({"stage": "Uploading to Gofile...", "percent": i})
            
            # Upload the file
            response = requests.post(
                upload_url,
                files=files,
                headers=headers,
                timeout=600  # 10 minutes for large files
            )

        response.raise_for_status()
        upload_result = response.json()
        q.put({"log": f"Gofile Raw Result: {json.dumps(upload_result)}"})

        if upload_result.get("status") == "ok":
            download_page = upload_result["data"]["downloadPage"]
            # Gofile API uses 'id' for the file UUID
            file_id_rec = upload_result["data"].get("id") or upload_result["data"].get("code")
            
            # Construct direct link candidates
            # Gofile 'download/web' format is often more reliable for free users
            direct_link = None
            
            # Use the ACTUAL storage server from result, not the upload server
            real_server = upload_server
            if upload_result["data"].get("servers"):
                real_server = upload_result["data"]["servers"][0]

            if real_server and file_id_rec:
                safe_url_name = quote(filename)
                direct_link = f"https://{real_server}.gofile.io/download/web/{file_id_rec}/{safe_url_name}"
                q.put({"log": f"Captured File ID: {file_id_rec}"})
                q.put({"log": f"Constructed Gofile link: {direct_link}"})
            
            save_to_gofile_history(filename, download_page, file_size, file_id_rec, direct_link)
            
            q.put({"stage": "✅ Gofile Upload Complete!", "percent": 100})
            q.put({
                "log": f"Success! File uploaded to Gofile: {filename}",
                "final_url": download_page
            })
        else:
            q.put({"error": f"Upload failed: {upload_result.get('status', 'Unknown error')}"})
            
    except requests.exceptions.Timeout:
        q.put({"error": "Upload timeout - file may be too large or connection is slow"})
    except requests.exceptions.RequestException as e:
        q.put({"error": f"Upload request failed: {str(e)}"})
    except Exception as e:
        q.put({"error": f"Gofile upload failed: {str(e)}"})
    finally:
        q.put({"log": "DONE"})


def upload_to_4stream_alt(file_path, filename, q, api_key):
    try:
        if not api_key:
            q.put({"error": "4stream 2 API key not configured"})
            q.put({"log": "DONE"})
            return

        q.put({"stage": "Getting upload server...", "percent": 10})

        # Step 1: Get upload server
        server_response = requests.get(
            "https://up4stream.com/api/upload/server",
            params={"key": api_key},
            timeout=10)
        server_response.raise_for_status()
        server_data = server_response.json()

        if server_data.get("status") != 200 or not server_data.get("result"):
            q.put({
                "error":
                f"Failed to get upload server: {server_data.get('msg', 'Unknown error')}"
            })
            q.put({"log": "DONE"})
            return

        upload_server = server_data["result"]
        q.put({"stage": "Got upload server, uploading file...", "percent": 30})

        # Step 2: Upload file to the server
        file_size = os.path.getsize(file_path)

        with open(file_path, 'rb') as f:
            files = {
                'file': (filename, f),
                'key': (None, api_key),
            }

            # Track upload progress
            def upload_with_progress():
                uploaded = [0]

                def update_progress(monitor):
                    uploaded[0] = monitor.bytes_read
                    percent = min(99, int((uploaded[0] / file_size) * 60) + 30)
                    q.put({"stage": "Uploading file...", "percent": percent})

                # Simple progress tracking
                response = requests.post(upload_server,
                                         files=files,
                                         timeout=300)
                return response

            response = upload_with_progress()

        response.raise_for_status()
        upload_result = response.json()

        if upload_result.get("status") == 200 and upload_result.get("files"):
            uploaded_file = upload_result["files"][0]
            filecode = uploaded_file.get("filecode")
            file_url = f"https://up4stream.com/{filecode}.html"

            q.put({"stage": "✅ 4stream 2 Upload Complete!", "percent": 100})
            q.put({
                "log": f"Success! File uploaded to 4stream 2: {filename}",
                "final_url": file_url
            })
        else:
            q.put({
                "error":
                f"Upload failed: {upload_result.get('msg', 'Unknown error')}"
            })
    except requests.exceptions.Timeout:
        q.put({
            "error":
            "Upload timeout - file may be too large or connection is slow"
        })
    except requests.exceptions.RequestException as e:
        q.put({"error": f"Upload request failed: {str(e)}"})
    except Exception as e:
        q.put({"error": f"4stream 2 upload failed: {str(e)}"})
    finally:
        q.put({"log": "DONE"})


def encode_file(input_path,
                output_filename,
                codec,
                preset,
                pass_mode,
                bitrate,
                crf,
                audio_bitrate,
                fps,
                scale,
                force_stereo,
                aq_mode,
                variance_boost,
                tiles,
                enable_vmaf,
                q,
                upload_pixeldrain=False,
                upload_4stream=False,
                upload_gofile=False):
    global current_process
    if scale:
        scale_map = {
            "1920:-2": "1080p",
            "1280:-2": "720p",
            "854:-2": "480p",
            "640:-2": "360p"
        }
        res_tag = scale_map.get(scale)
        if res_tag:
            base, ext = os.path.splitext(output_filename)
            output_filename = f"{base}_{res_tag}{ext}"
    safe_output = get_safe_filename(output_filename)
    output_path = os.path.join(DOWNLOAD_FOLDER, safe_output)
    output_path = get_unique_filepath(output_path)
    safe_output = os.path.basename(output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    try:
        while not q.empty():
            q.get()
        q.put({"stage": "Initializing encoding...", "percent": 0})
        if not is_media_file(input_path):
            q.put({"error": "File type cannot be encoded."})
            return
        duration = get_media_duration(input_path)
        if codec == "none":
            shutil.copy2(input_path, output_path)
            q.put({"stage": "✅ Copied!", "percent": 100})
        else:
            stage_msg = f"Encoding to {codec.upper()}..."
            q.put({"stage": stage_msg, "percent": 0})
            ffmpeg_cmd = [FFMPEG_PATH, "-y", "-i", input_path]
            vf_params = []
            if scale: vf_params.append(f"scale={scale}")
            base_codec = codec.replace('_copy_audio', '')
            video_codec = "libx265" if base_codec == "h265" else "libsvtav1" if base_codec == "av1" else None
            if codec == "copy_video":
                ffmpeg_cmd.extend(["-c:v", "copy"])
            else:
                if pass_mode == "2-pass":
                    bitrate_val = int(
                        bitrate) if bitrate and bitrate.strip() else 0
                    if bitrate_val < 100:
                        q.put({"error": "Bitrate required for 2-pass."})
                        return
                    video_opts = [
                        "-c:v", video_codec, "-preset", preset, "-b:v",
                        f"{bitrate_val}k"
                    ]
                    pass1_cmd = ffmpeg_cmd + video_opts + [
                        "-pass", "1", "-an", "-f", "null", "-"
                    ]
                    subprocess.run(pass1_cmd,
                                   check=True,
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
                    ffmpeg_cmd.extend(video_opts + ["-pass", "2"])
                else:
                    crf_val = int(crf) if crf else (
                        28 if base_codec == 'h265' else 24)
                    ffmpeg_cmd.extend([
                        "-c:v", video_codec, "-preset", preset, "-crf",
                        str(crf_val)
                    ])
                if fps: ffmpeg_cmd.extend(["-r", fps])
            if codec.endswith('_copy_audio'):
                ffmpeg_cmd.extend(["-c:a", "copy"])
            else:
                audio_bitrate_val = int(audio_bitrate) if audio_bitrate else 96
                audio_channels = 2 if force_stereo else get_audio_channels(
                    input_path)
                ffmpeg_cmd.extend([
                    "-ac",
                    str(audio_channels), "-c:a", "libopus", "-b:a",
                    f"{audio_bitrate_val}k"
                ])
            if base_codec == 'av1' and codec != 'copy_video':
                svt_params = [
                    f"aq-mode={aq_mode}",
                    f"variance-boost-strength={variance_boost}"
                ]
                if tiles and 'x' in tiles:
                    try:
                        rows_str, cols_str = tiles.split('x')
                        rows, cols = int(rows_str), int(cols_str)
                        tile_rows = rows.bit_length() - 1 if rows > 0 else 0
                        tile_columns = cols.bit_length() - 1 if cols > 0 else 0
                        svt_params.extend([
                            f"tile-rows={tile_rows}",
                            f"tile-columns={tile_columns}"
                        ])
                    except ValueError:
                        q.put({
                            "log":
                            f"Warning: Could not parse tiles '{tiles}'. Ignoring."
                        })
                ffmpeg_cmd.extend(["-svtav1-params", ":".join(svt_params)])
            if enable_vmaf: vf_params.append("libvmaf")
            if vf_params: ffmpeg_cmd.extend(["-vf", ",".join(vf_params)])
            ffmpeg_cmd.append(output_path)
            current_process = subprocess.Popen(ffmpeg_cmd,
                                               stdout=subprocess.PIPE,
                                               stderr=subprocess.STDOUT,
                                               universal_newlines=True,
                                               encoding='utf-8',
                                               errors='ignore')
            if current_process.stdout is None:
                raise Exception("Process stdout is None")
            for line in iter(current_process.stdout.readline, ''):
                q.put({"log": line.strip()})
                if duration > 0:
                    match = re.search(r'time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})',
                                      line)
                    if match:
                        h, m, s, ms = map(int, match.groups())
                        percent = min(
                            100,
                            ((h * 3600 + m * 60 + s + ms / 100) / duration) *
                            100)
                        q.put({"stage": stage_msg, "percent": percent})
                    if enable_vmaf:
                        vmaf_match = re.search(r'VMAF score: (\d+\.\d+)', line)
                        if vmaf_match:
                            q.put(
                                {"log": f"VMAF Score: {vmaf_match.group(1)}"})
            current_process.wait()
            if current_process.returncode != 0:
                q.put({"error": "Encoding process terminated."})
                current_process = None
                return
            current_process = None
            q.put({
                "stage": "✅ Done!",
                "percent": 100,
                "log": f"{codec.upper()} encoding complete."
            })
        if upload_pixeldrain:
            upload_to_pixeldrain(output_path, os.path.basename(safe_output), q)
        elif upload_4stream:
            upload_to_4stream(output_path, os.path.basename(safe_output), q)
        if upload_gofile:
            upload_to_gofile(output_path, os.path.basename(safe_output), q)
    except Exception as e:
        q.put({"error": str(e)})
    finally:
        current_process = None
        if not (upload_pixeldrain or upload_4stream or upload_gofile):
            q.put({"log": "DONE"})


def download_file_directly(url,
                           q,
                           upload_pixeldrain_direct=False,
                           upload_gofile_direct=False,
                           username="",
                           password="",
                           referer=None):
    try:
        while not q.empty():
            q.get()
        q.put({"stage": "Starting direct download...", "percent": 0})

        # Prepare auth tuple
        auth_tuple = None
        if username or password:
            auth_tuple = (username if username else "",
                          password if password else "")
            q.put({"log": f"Using authentication: username={username}"})

        # List of fallback credentials to try if 401 is received
        fallback_credentials = [("admin", "1234"), ("admin", "oga123456"),
                                ("admin", "Oga123456"),
                                ("admin", "Oga123456?!")]

        # Try download with provided credentials (or no auth)
        download_successful = False
        final_path = None
        safe_name = None

        try:
            # First try with provided credentials (or no auth)
            try:
                # Custom headers for Gofile compatibility
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Referer': referer if referer else 'https://gofile.io/',
                    'Origin': 'https://gofile.io'
                }
                
                # SPECIAL GOFILE BYPASS: Get tokens for direct downloads
                if "gofile.io" in url:
                    try:
                        q.put({"log": "Fetching Gofile security tokens..."})
                        wt = get_gofile_website_token()
                        headers['X-Website-Token'] = wt
                        
                        token_resp = requests.post("https://api.gofile.io/accounts", headers={
                            'User-Agent': headers['User-Agent'],
                            'Referer': 'https://gofile.io/',
                            'X-Website-Token': wt
                        }, timeout=5)
                        if token_resp.status_code == 200:
                            guest_token = token_resp.json().get("data", {}).get("token")
                            if guest_token:
                                headers["Authorization"] = f"Bearer {guest_token}"
                                q.put({"log": f"✅ Got Gofile guest token: {guest_token[:5]}..."})
                    except Exception as te:
                        q.put({"log": f"Note: Failed to get Gofile guest token: {te}"})

                # --- MANUAL REDIRECT LOOP (to preserve headers/auth) ---
                current_url = url
                response = None
                
                for jump in range(5):
                    q.put({"log": f"Connecting to {current_url.split('/')[2]}..."})
                    response = requests.get(current_url,
                                         stream=True,
                                         allow_redirects=False, # We handle redirects manually
                                         auth=auth_tuple,
                                         headers=headers,
                                         timeout=30)
                    
                    if response.status_code in [301, 302, 303, 307, 308]:
                        location = response.headers.get('Location')
                        if not location: break
                        
                        # Detect if we were redirected back to the Gofile landing page
                        if "gofile.io/d/" in location and "/download/" not in location:
                            q.put({"log": "⚠️ Block detected. Retrying with landing page referer..."})
                            # Try to extract the landing page code from the location if it's there
                            if "/d/" in location:
                                headers["Referer"] = location
                            
                        current_url = location
                        if not current_url.startswith('http'):
                            current_url = "https://gofile.io" + current_url
                        continue
                    else:
                        break
                
                if response:
                    response.raise_for_status()
                    # Final check: Did we end up on a landing page?
                    if "gofile.io/d/" in response.url and "/download/" not in response.url:
                         raise ValueError("Gofile redirected to landing page. Direct download blocked.")

                    # Download immediately while response is open
                    filename = "direct_download"
                    cd_header = response.headers.get('content-disposition')
                    if cd_header:
                        match = re.search(r"filename\*=([^']*)''([^;]*)",
                                          cd_header) or re.search(
                                              r'filename="?([^"]+)"?',
                                              cd_header)
                        if match: filename = unquote(match.group(1))
                    if filename == "direct_download":
                        filename_from_url = url.split('/')[-1].split('?')[0]
                        if filename_from_url:
                            filename = unquote(filename_from_url)
                    safe_name = get_safe_filename(filename)
                    final_path = os.path.join(DOWNLOAD_FOLDER, safe_name)
                    final_path = get_unique_filepath(final_path)
                    safe_name = os.path.basename(final_path)
                    total_size = int(response.headers.get('content-length', 0))
                    downloaded_size = 0
                    os.makedirs(os.path.dirname(final_path), exist_ok=True)
                    with open(final_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)
                            downloaded_size += len(chunk)
                            if total_size > 0:
                                q.put({
                                    "stage":
                                    "Downloading...",
                                    "percent":
                                    (downloaded_size / total_size) * 100
                                })
                    download_successful = True
            except requests.exceptions.HTTPError as e:
                # If we got 401 and no credentials were provided, retry with fallback credentials
                if e.response.status_code == 401 and not (username
                                                          or password):
                    for idx, (fallback_user, fallback_pass) in enumerate(
                            fallback_credentials, 1):
                        try:
                            q.put({
                                "log":
                                f"Access denied. Trying fallback credential {idx}/{len(fallback_credentials)}: {fallback_user}:{fallback_pass}..."
                            })
                            with requests.get(
                                    url,
                                    stream=True,
                                    allow_redirects=True,
                                    auth=(fallback_user, fallback_pass),
                                    headers={'User-Agent':
                                             'Mozilla/5.0'}) as response:
                                response.raise_for_status()
                                q.put({
                                    "log":
                                    f"✅ Authenticated successfully with {fallback_user}:{fallback_pass}"
                                })
                                # Download immediately while response is open
                                filename = "direct_download"
                                cd_header = response.headers.get(
                                    'content-disposition')
                                if cd_header:
                                    match = re.search(
                                        r"filename\*=([^']*)''([^;]*)",
                                        cd_header) or re.search(
                                            r'filename="?([^"]+)"?', cd_header)
                                    if match:
                                        filename = unquote(match.group(1))
                                if filename == "direct_download":
                                    filename_from_url = url.split(
                                        '/')[-1].split('?')[0]
                                    if filename_from_url:
                                        filename = unquote(filename_from_url)
                                safe_name = get_safe_filename(filename)
                                final_path = os.path.join(
                                    DOWNLOAD_FOLDER, safe_name)
                                final_path = get_unique_filepath(final_path)
                                safe_name = os.path.basename(final_path)
                                total_size = int(
                                    response.headers.get('content-length', 0))
                                downloaded_size = 0
                                os.makedirs(os.path.dirname(final_path),
                                            exist_ok=True)
                                with open(final_path, 'wb') as f:
                                    for chunk in response.iter_content(
                                            chunk_size=8192):
                                        f.write(chunk)
                                        downloaded_size += len(chunk)
                                        if total_size > 0:
                                            q.put({
                                                "stage":
                                                "Downloading...",
                                                "percent":
                                                (downloaded_size / total_size)
                                                * 100
                                            })
                                download_successful = True
                                break
                        except requests.exceptions.HTTPError as e2:
                            if e2.response.status_code == 401:
                                continue
                            else:
                                raise

                    if not download_successful:
                        raise
                else:
                    raise
        except Exception:
            raise

        q.put({"stage": "✅ Download complete!", "percent": 100})
        if upload_pixeldrain_direct and final_path and safe_name:
            upload_to_pixeldrain(final_path, safe_name, q)
        if upload_gofile_direct and final_path and safe_name:
            upload_to_gofile(final_path, safe_name, q)
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            q.put({
                "error":
                "Authentication failed (401). All credential options failed. Please check the URL and provide correct credentials."
            })
        else:
            q.put({"error": f"Direct download failed: {str(e)}"})
    except Exception as e:
        q.put({"error": f"Direct download failed: {str(e)}"})
    finally:
        if not (upload_pixeldrain_direct or upload_gofile_direct):
            q.put({"log": "DONE"})


def upload_file_directly_to_pixeldrain(url, q):
    try:
        while not q.empty():
            q.get()
        q.put({"stage": "Starting direct remote upload...", "percent": 0})
        with requests.get(url,
                          stream=True,
                          allow_redirects=True,
                          headers={'User-Agent': 'Mozilla/5.0'}) as r:
            r.raise_for_status()
            filename = "direct_upload"
            cd_header = r.headers.get('content-disposition')
            if cd_header:
                match = re.search(r"filename\*=([^']*)''([^;]*)",
                                  cd_header) or re.search(
                                      r'filename="?([^"]+)"?', cd_header)
                if match: filename = unquote(match.group(1))
            if filename == "direct_upload":
                filename_from_url = url.split('/')[-1].split('?')[0]
                if filename_from_url: filename = unquote(filename_from_url)
            q.put({"log": f"Identified filename: '{filename}'"})
            api_url = "https://pixeldrain.com/api/file"
            files = {
                'file': (filename, r.raw,
                         r.headers.get('content-type',
                                       'application/octet-stream'))
            }
            auth = ('', PIXELDRAIN_API_KEY) if PIXELDRAIN_API_KEY else None
            response = requests.post(api_url,
                                     files=files,
                                     auth=auth,
                                     stream=True)
            response.raise_for_status()
            result = response.json()
            if result.get("success"):
                pixeldrain_url = f"https://pixeldrain.com/u/{result.get('id')}"
                q.put({
                    "stage": "✅ Upload complete!",
                    "percent": 100,
                    "final_url": pixeldrain_url
                })
            else:
                q.put({
                    "error":
                    f"Pixeldrain API error: {result.get('message', 'Unknown')}"
                })
    except Exception as e:
        q.put({"error": f"Direct remote upload failed: {str(e)}"})
    finally:
        q.put({"log": "DONE"})


def download_and_convert(url,
                         video_id,
                         audio_id,
                         filename,
                         codec,
                         preset,
                         pass_mode,
                         bitrate,
                         crf,
                         audio_bitrate,
                         fps,
                         scale,
                         force_stereo,
                         q,
                         is_muxed,
                         upload_pixeldrain=False,
                         upload_gofile=False,
                         aq_mode="1",
                         variance_boost="1",
                         tiles="2x2",
                         enable_vmaf=False,
                         use_cookies=True):
    global current_process
    safe_name = get_safe_filename(filename)
    final_path_check = os.path.join(DOWNLOAD_FOLDER, safe_name)
    final_path_check = get_unique_filepath(final_path_check)
    safe_name = os.path.basename(final_path_check)
    base_name, _ = os.path.splitext(safe_name)
    final_path = os.path.join(DOWNLOAD_FOLDER, safe_name)
    tmp_path_template = os.path.join(DOWNLOAD_FOLDER, base_name + ".part")
    actual_tmp_path = None
    try:
        while not q.empty():
            q.get()
        q.put({"stage": "Initializing download...", "percent": 0})
        # Only merge audio if explicitly selected
        if audio_id:
            yt_formats = f"{video_id}+{audio_id}"
        else:
            yt_formats = video_id
        yt_dlp_cmd = [
            YTDLP_PATH, "--force-ipv4", "-f", yt_formats, "-o", tmp_path_template,
            "--merge-output-format", "mkv", url
        ]
        if use_cookies and os.path.exists(COOKIES_FILE):
            yt_dlp_cmd.extend(["--cookies", COOKIES_FILE])
        run_command_with_progress(yt_dlp_cmd, "Downloading with yt-dlp...", q)
        q.put({"stage": "Download Complete", "percent": 100})
        found_files = [
            f for f in os.listdir(DOWNLOAD_FOLDER)
            if f.startswith(os.path.basename(tmp_path_template))
        ]
        if not found_files:
            raise FileNotFoundError("yt-dlp did not create expected file.")
        actual_tmp_path = os.path.join(DOWNLOAD_FOLDER, found_files[0])
        final_path_mkv = None
        if codec == "none":
            os.rename(actual_tmp_path, final_path)
            q.put({"stage": "✅ Done!", "log": "File saved without encoding."})
        else:
            final_path_mkv = os.path.join(DOWNLOAD_FOLDER, base_name + ".mkv")
            final_path_mkv = get_unique_filepath(final_path_mkv)
            encode_options = {
                'input_path': actual_tmp_path,
                'output_filename': os.path.basename(final_path_mkv),
                'codec': codec,
                'preset': preset,
                'pass_mode': pass_mode,
                'bitrate': bitrate,
                'crf': crf,
                'audio_bitrate': audio_bitrate,
                'fps': fps,
                'scale': scale,
                'force_stereo': force_stereo,
                'aq_mode': aq_mode,
                'variance_boost': variance_boost,
                'tiles': tiles,
                'enable_vmaf': enable_vmaf
            }
            encode_file(**encode_options, q=q, upload_pixeldrain=False)
        final_file_to_upload = final_path if codec == "none" else (
            final_path_mkv or final_path)
        if upload_pixeldrain and os.path.exists(final_file_to_upload):
            upload_to_pixeldrain(final_file_to_upload,
                                 os.path.basename(final_file_to_upload), q)
        if upload_gofile and os.path.exists(final_file_to_upload):
            upload_to_gofile(final_file_to_upload,
                             os.path.basename(final_file_to_upload), q)
    except Exception as e:
        q.put({"error": str(e)})
    finally:
        if actual_tmp_path and os.path.exists(actual_tmp_path):
            try:
                os.remove(actual_tmp_path)
            except OSError:
                pass
        if not (upload_pixeldrain or upload_gofile):
            q.put({"log": "DONE"})


def manual_merge_worker(url,
                        video_id,
                        audio_id,
                        filename,
                        q,
                        upload_pixeldrain=False,
                        upload_4stream=False,
                        upload_gofile=False):
    safe_name = get_safe_filename(filename)
    base_name, _ = os.path.splitext(safe_name)
    final_path = os.path.join(DOWNLOAD_FOLDER, base_name + ".mkv")
    final_path = get_unique_filepath(final_path)
    try:
        while not q.empty():
            q.get()
        q.put({"stage": "Initializing manual download...", "percent": 0})
        video_id_clean = video_id.strip()
        audio_id_clean = audio_id.strip() if audio_id else ""
        format_selector = f"{video_id_clean}+{audio_id_clean}" if audio_id_clean else video_id_clean
        yt_dlp_cmd = [
            YTDLP_PATH, "--force-ipv4", "-f", format_selector, "-o", final_path,
            "--merge-output-format", "mkv", url
        ]
        if os.path.exists(COOKIES_FILE):
            yt_dlp_cmd.extend(["--cookies", COOKIES_FILE])
        run_command_with_progress(yt_dlp_cmd,
                                  "Downloading & Merging with yt-dlp...", q)
        q.put({"stage": "✅ Download Complete!", "percent": 100})
        if upload_pixeldrain and os.path.exists(final_path):
            upload_to_pixeldrain(final_path, os.path.basename(final_path), q)
        if upload_4stream and os.path.exists(final_path):
            upload_to_4stream(final_path, os.path.basename(final_path), q)
        if upload_gofile and os.path.exists(final_path):
            upload_to_gofile(final_path, os.path.basename(final_path), q)
    except Exception as e:
        q.put({"error": str(e)})
    finally:
        if not (upload_pixeldrain or upload_4stream or upload_gofile):
            q.put({"log": "DONE"})


# -----------------------------
# Flask Routes
# -----------------------------
def start_task(target, args):
    session['task_active'] = True
    thread = threading.Thread(target=target, args=args)
    thread.daemon = True
    thread.start()
    return thread


@app.route("/merge_files", methods=["POST"])
def merge_files_route():
    data = request.json or {}
    files = data.get("files", [])
    output = secure_filename(data.get("output", "merged.mp4"))

    if len(files) < 2:
        return jsonify({"error": "Select at least 2 files"}), 400

    valid_files = []
    for f in files:
        full = os.path.join(DOWNLOAD_FOLDER, f)
        if os.path.isfile(full):
            valid_files.append(full)
        else:
            app.logger.warning(f"Skipped invalid merge input: {full}")

    if len(valid_files) < 2:
        return jsonify({"error": "Invalid file selection"}), 400

    threading.Thread(
        target=ffmpeg_merge_with_progress,
        args=(valid_files, os.path.join(DOWNLOAD_FOLDER, output)),
        daemon=True
    ).start()

    return jsonify({"started": True})


@app.route("/")
@login_required
def index():
    if 'last_upload_url' in session:
        upload_url = session.pop('last_upload_url', None)
        flash(
            f"✅ Upload completed! <a href='{upload_url}' target='_blank'>View on Pixeldrain</a>",
            "success")
    return render_template_string(TEMPLATE,
                                  url="",
                                  formats=None,
                                  download_started=False,
                                  manual_url="",
                                  manual_formats_raw=None,
                                  manual_filename="",
                                  yt_url="",
                                  yt_formats=None,
                                  yt_download_started=False,
                                  current_tab="advanced")


@app.route("/", methods=["POST"])
@login_required
def index_post():
    action = request.form.get("action")
    form_data = {
        "url": request.form.get("url", "").strip(),
        "download_started": False,
        "manual_url": request.form.get("manual_url", "").strip(),
        "manual_formats_raw": None,
        "manual_filename": "",
        "yt_url": "",
        "yt_formats": None,
        "yt_download_started": False,
        "current_tab": "advanced"
    }
    if action == "fetch":
        formats_string, video_formats, audio_formats = fetch_formats(
            form_data["url"])
        if formats_string:
            form_data.update({
                "formats":
                formats_string,
                "video_formats":
                video_formats,
                "audio_formats":
                audio_formats,
                "original_name":
                get_original_filename(form_data["url"]),
                "codec":
                request.form.get("codec", "none"),
                "pass_mode":
                request.form.get("pass_mode", "1-pass"),
                "bitrate":
                request.form.get("bitrate", ""),
                "crf":
                request.form.get("crf", ""),
                "audio_bitrate":
                request.form.get("audio_bitrate", "96")
            })
            flash("✅ Formats fetched successfully!", "success")
        return render_template_string(TEMPLATE, **form_data)

    if action == "manual_fetch":
        form_data["current_tab"] = "merge"
        url = form_data["manual_url"]
        formats_raw, _, __ = fetch_formats(url)
        if formats_raw:
            form_data["manual_formats_raw"] = formats_raw
            try:
                form_data["manual_filename"] = get_original_filename(
                    url).replace('.mkv', '')
            except Exception:
                form_data["manual_filename"] = "video"
            flash("✅ Manual formats fetched successfully!", "success")
        return render_template_string(TEMPLATE, **form_data)

    if action in [
            "download", "direct_download", "direct_upload_pixeldrain",
            "manual_merge"
    ]:
        form_data["download_started"] = True
        if action == "manual_merge":
            form_data["current_tab"] = "merge"
        elif action in ["direct_download", "direct_upload_pixeldrain"]:
            form_data["current_tab"] = "direct"
        else:
            form_data["current_tab"] = "advanced"

        if action == "download":
            is_muxed = False
            try:
                _, vformats, _ = fetch_formats(request.form.get("url"))
                selected_video_format = next(
                    (f for f in vformats
                     if f['id'] == request.form.get("video_id")), None)
                if selected_video_format:
                    is_muxed = selected_video_format.get('is_muxed', False)
            except Exception:
                pass
            args = (request.form.get("url"), request.form.get("video_id"),
                    request.form.get("audio_id"), request.form.get("filename"),
                    request.form.get("codec"), request.form.get("preset"),
                    request.form.get("pass_mode"), request.form.get("bitrate"),
                    request.form.get("crf"), request.form.get("audio_bitrate"),
                    request.form.get("fps"), request.form.get("scale"),
                    request.form.get("force_stereo") == "true", progress_queue,
                    is_muxed, request.form.get("upload_pixeldrain") == "true",
                    request.form.get("upload_gofile") == "true",
                    request.form.get("aq_mode", "1"),
                    request.form.get("variance_boost",
                                     "1"), request.form.get("tiles", "2x2"),
                    request.form.get("enable_vmaf") == "true")
            start_task(download_and_convert, args)
        elif action == "direct_download":
            args = (request.form.get("direct_url"), progress_queue,
                    request.form.get("upload_pixeldrain_direct") == "true",
                    request.form.get("upload_gofile_direct") == "true",
                    request.form.get("direct_username", ""),
                    request.form.get("direct_password", ""))
            start_task(download_file_directly, args)
        elif action == "direct_upload_pixeldrain":
            args = (request.form.get("direct_url"), progress_queue)
            start_task(upload_file_directly_to_pixeldrain, args)
        elif action == "manual_merge":
            args = (request.form.get("manual_url"),
                    request.form.get("manual_video_id"),
                    request.form.get("manual_audio_id"),
                    request.form.get("manual_filename"), progress_queue,
                    request.form.get("upload_pixeldrain") == "true",
                    request.form.get("upload_4stream") == "true",
                    request.form.get("upload_gofile") == "true")
            start_task(manual_merge_worker, args)
        pass  # current_tab set earlier
    return render_template_string(TEMPLATE, **form_data)


# YouTube Download Routes (without cookies)
@app.route("/youtube", methods=["POST"])
def youtube_download():
    action = request.form.get("action")
    yt_url = request.form.get("yt_url", "").strip()
    form_data = {
        "url": "",
        "formats": None,
        "download_started": False,
        "manual_url": "",
        "manual_formats_raw": None,
        "manual_filename": "",
        "yt_url": yt_url,
        "yt_formats": None,
        "yt_download_started": False,
        "current_tab": "youtube"
    }

    if action == "yt_fetch":
        formats_string, video_formats, audio_formats = fetch_formats_no_cookies(
            yt_url)
        if formats_string:
            form_data.update({
                "yt_formats":
                formats_string,
                "yt_video_formats":
                video_formats,
                "yt_audio_formats":
                audio_formats,
                "yt_original_name":
                get_original_filename_no_cookies(yt_url),
                "yt_codec":
                request.form.get("yt_codec", "none"),
                "yt_pass_mode":
                request.form.get("yt_pass_mode", "1-pass"),
                "yt_bitrate":
                request.form.get("yt_bitrate", ""),
                "yt_crf":
                request.form.get("yt_crf", ""),
                "yt_audio_bitrate":
                request.form.get("yt_audio_bitrate", "96")
            })
            flash("✅ YouTube formats fetched successfully!", "success")
        return render_template_string(TEMPLATE, **form_data)

    if action == "yt_download":
        form_data["yt_download_started"] = True
        form_data["download_started"] = True
        form_data["current_tab"] = "youtube"
        is_muxed = False
        try:
            _, vformats, aformats = fetch_formats_no_cookies(
                request.form.get("yt_url"))
            selected_video_format = next(
                (f for f in vformats
                 if f['id'] == request.form.get("yt_video_id")), None)
            if selected_video_format:
                is_muxed = selected_video_format.get('is_muxed', False)
            form_data.update({
                "yt_formats":
                "\n".join([f"{v['id']}: {v['display']}" for v in vformats] +
                          [f"{a['id']}: {a['display']}" for a in aformats]),
                "yt_video_formats":
                vformats,
                "yt_audio_formats":
                aformats,
                "yt_video_id":
                request.form.get("yt_video_id"),
                "yt_audio_id":
                request.form.get("yt_audio_id"),
                "yt_filename":
                request.form.get("yt_filename"),
                "yt_codec":
                request.form.get("yt_codec", "none"),
                "yt_preset":
                request.form.get("yt_preset", "7"),
                "yt_pass_mode":
                request.form.get("yt_pass_mode", "1-pass"),
                "yt_bitrate":
                request.form.get("yt_bitrate", ""),
                "yt_crf":
                request.form.get("yt_crf", ""),
                "yt_audio_bitrate":
                request.form.get("yt_audio_bitrate", "96")
            })
        except Exception:
            pass
        args = (request.form.get("yt_url"), request.form.get("yt_video_id"),
                request.form.get("yt_audio_id"),
                request.form.get("yt_filename"), request.form.get("yt_codec"),
                request.form.get("yt_preset"),
                request.form.get("yt_pass_mode"),
                request.form.get("yt_bitrate"), request.form.get("yt_crf"),
                request.form.get("yt_audio_bitrate"),
                request.form.get("yt_fps"), request.form.get("yt_scale"),
                request.form.get("yt_force_stereo") == "true", progress_queue,
                is_muxed, request.form.get("yt_upload_pixeldrain") == "true",
                request.form.get("yt_upload_gofile") == "true",
                request.form.get("yt_aq_mode", "1"),
                request.form.get("yt_variance_boost",
                                 "1"), request.form.get("yt_tiles", "2x2"),
                request.form.get("yt_enable_vmaf") == "true", False)
        start_task(download_and_convert, args)
        return render_template_string(TEMPLATE, **form_data)

    return render_template_string(TEMPLATE, **form_data)


@app.route("/progress")
def progress_stream():

    def generate():
        while True:
            try:
                msg = progress_queue.get(timeout=30)
                yield f"data: {json.dumps(msg)}\n\n"
                if msg.get("log") == "DONE" or "error" in msg:
                    break
            except queue.Empty:
                break
            except GeneratorExit:
                break

    return Response(generate(), mimetype="text/event-stream")


@app.route("/upload_direct", methods=["POST"])
def upload_direct():
    if 'file' in request.files and request.files['file'].filename:
        file = request.files['file']
        file_filename = file.filename
        if not file_filename:
            flash("No file selected", "error")
            return redirect(url_for('index'))
        filename = get_safe_filename(file_filename)
        save_path = get_unique_filepath(os.path.join(DOWNLOAD_FOLDER,
                                                     filename))
        file.save(save_path)
        args = (save_path, os.path.basename(save_path), progress_queue)
        start_task(upload_to_pixeldrain, args)
        return render_template_string(FILE_OPERATION_TEMPLATE,
                                      operation_title=f"Uploading: {filename}",
                                      download_started=True,
                                      current_path='')
    flash("No file selected", "error")
    return redirect(url_for('index'))


@app.route("/upload_local", methods=["POST"])
def upload_local():
    current_path = request.form.get('current_path', '')
    dest_folder = os.path.join(DOWNLOAD_FOLDER, current_path)
    if not os.path.abspath(dest_folder).startswith(
            os.path.abspath(DOWNLOAD_FOLDER)):
        flash("Invalid upload directory specified.", "error")
        return redirect(url_for('list_files'))
    if 'file' in request.files and request.files['file'].filename:
        file = request.files['file']
        file_filename = file.filename
        if not file_filename:
            flash("No file selected", "error")
            return redirect(url_for('list_files', current_path=current_path))
        filename = get_safe_filename(file_filename)
        file_path = os.path.join(dest_folder, filename)
        unique_path = get_unique_filepath(file_path)
        file.save(unique_path)
        session['last_local_upload'] = os.path.relpath(unique_path,
                                                       DOWNLOAD_FOLDER)
        return redirect(url_for('list_files', current_path=current_path))
    flash("No file selected for uploading.", "error")
    return redirect(url_for('list_files', current_path=current_path))


@app.route("/upload_folder", methods=["POST"])
def upload_folder():
    current_path = request.form.get('current_path', '')
    dest_folder = os.path.join(DOWNLOAD_FOLDER, current_path)
    if not os.path.abspath(dest_folder).startswith(
            os.path.abspath(DOWNLOAD_FOLDER)):
        flash("Invalid upload directory.", "error")
        return redirect(url_for('list_files'))
    files = request.files.getlist("files[]")
    if not files or not files[0].filename:
        flash("No folder or files selected.", "error")
        return redirect(url_for('list_files', current_path=current_path))
    for file in files:
        if file.filename:
            relative_path = file.filename
            filename = get_safe_filename(os.path.basename(relative_path))
            full_rel_dir = os.path.dirname(relative_path)
            target_dir = os.path.join(dest_folder, full_rel_dir)
            if not os.path.abspath(target_dir).startswith(
                    os.path.abspath(DOWNLOAD_FOLDER)):
                continue
            os.makedirs(target_dir, exist_ok=True)
            file_path = os.path.join(target_dir, filename)
            unique_path = get_unique_filepath(file_path)
            file.save(unique_path)
    flash(f"Folder uploaded successfully to '{current_path or '/'}'.",
          "success")
    return redirect(url_for('list_files', current_path=current_path))


@app.route("/files/")
@app.route("/files/<path:current_path>")
@login_required
def list_files(current_path=""):
    feedback_messages = {
        'last_upload_url':
        "✅ Upload completed! <a href='{}' target='_blank'>View on Pixeldrain</a>",
        'last_deleted_file': "✅ Item deleted successfully: {}",
        'last_renamed_file': "✅ Item renamed: {old} → {new}",
        'last_local_upload': "✅ Successfully uploaded '{}' to the server.",
        'last_trimmed_file': "✅ Trimmed video: {old} → {new}"
    }
    for key, message_format in feedback_messages.items():
        if key in session:
            value = session.pop(key)
            if isinstance(value, dict):
                flash(message_format.format(**value), "success")
            else:
                flash(message_format.format(value), "success")

    browse_folder = os.path.join(DOWNLOAD_FOLDER, current_path)

    # 1. Security Check
    if not os.path.abspath(browse_folder).startswith(
            os.path.abspath(DOWNLOAD_FOLDER)):
        flash("Invalid path specified.", "error")
        return redirect(url_for('list_files', current_path=""))

    # 2. Check if path actually exists
    if not os.path.exists(browse_folder):
        flash("Folder not found.", "error")
        return redirect(url_for('list_files', current_path=""))

    # 3. Check if it is a file (Fixes your 500 Error)
    if os.path.isfile(browse_folder):
        # Redirect to parent directory if trying to browse a file
        return redirect(url_for('list_files', current_path=os.path.dirname(current_path)))

    all_items = []
    # 4. Safe scanning
    try:
        for entry in os.scandir(browse_folder):
            relative_path = os.path.relpath(entry.path, DOWNLOAD_FOLDER)
            item_info = {
                'name': entry.name,
                'path': relative_path,
                'is_folder': entry.is_dir(),
                'mtime': entry.stat().st_mtime
            }
            if entry.is_dir():
                item_info['size'], item_info['is_media'] = '-', False
            else:
                item_info['size'] = human_size(entry.stat().st_size)
                item_info['is_media'] = is_media_file(entry.path)
            all_items.append(item_info)
    except NotADirectoryError:
        return redirect(url_for('list_files', current_path=os.path.dirname(current_path)))

    # Get sort parameter from query string
    sort_by = request.args.get('sort', 'newest')
    if sort_by == 'name_asc':
        all_items.sort(key=lambda x: x['name'].lower())
    elif sort_by == 'name_desc':
        all_items.sort(key=lambda x: x['name'].lower(), reverse=True)
    else:  # 'newest' is default
        all_items.sort(key=lambda x: x['mtime'], reverse=True)

    breadcrumbs = []
    if current_path:
        parts = current_path.split(os.sep)
        for i in range(len(parts)):
            path_so_far = os.path.join(*parts[:i + 1])
            breadcrumbs.append({'name': parts[i], 'path': path_so_far})

    # Calculate storage stats
    total_files_size = get_folder_total_size(DOWNLOAD_FOLDER)
    available_space = get_available_space(DOWNLOAD_FOLDER)
    total_files_size_human = human_size(total_files_size)
    available_space_human = human_size(available_space)

    return render_template_string(
        """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Downloaded Files</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', 'Roboto', sans-serif; line-height: 1.6; background: #f8f9fa; color: #1a1a1a; min-height: 100vh; padding: 15px; }
        .container { max-width: 1200px; margin: 0 auto; background: white; padding: 20px; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); border: 1px solid #e8e8e8; }
        h1 { font-size: 26px; font-weight: 700; color: #1a1a1a; margin-bottom: 10px; }
        h3 { font-size: 15px; font-weight: 600; color: #3a3a3a; margin-bottom: 10px; }
        table { width: 100%; border-collapse: collapse; margin-top: 18px; font-size: 13px; }
        th, td { padding: 12px 10px; text-align: left; border-bottom: 1px solid #e0e0e0; }
        th { background-color: #f5f5f5; font-weight: 600; color: #2c2c2c; font-size: 12px; }
        tr:hover { background-color: #fafbff; }
        .file-name-cell { max-width: 400px; word-wrap: break-word; overflow-wrap: break-word; white-space: normal; }
        a { color: #0066cc; text-decoration: none; margin-right: 4px; font-weight: 600; transition: color 0.2s ease; font-size: 12px; }
        a:hover { color: #0052a3; }
        button { background-color: #0066cc; color: white; padding: 7px 12px; border: none; border-radius: 6px; cursor: pointer; margin-right: 4px; font-size: 11px; font-weight: 600; transition: all 0.2s ease; }
        button:hover { background-color: #0052a3; transform: translateY(-1px); box-shadow: 0 2px 6px rgba(0, 102, 204, 0.2); }
        button.delete { background-color: #dc3545; }
        button.delete:hover { background-color: #c82333; box-shadow: 0 2px 6px rgba(220, 53, 69, 0.2); }
        button.upload { background-color: #28a745; }
        button.upload:hover { background-color: #218838; box-shadow: 0 2px 6px rgba(40, 167, 69, 0.2); }
        button.encode { background-color: #007bff; }
        button.encode:hover { background-color: #0056b3; box-shadow: 0 2px 6px rgba(0, 123, 255, 0.2); }
        button.rename { background-color: #ffc107; color: #000; }
        button.rename:hover { background-color: #e0a800; box-shadow: 0 2px 6px rgba(255, 193, 7, 0.2); }
        button.info { background-color: #0dcaf0; color: #000; }
        button.info:hover { background-color: #0cb9d7; box-shadow: 0 2px 6px rgba(13, 202, 240, 0.2); }
        button:disabled { background-color: #6c757d; cursor: not-allowed; }
        .actions { font-size: 11px; display: flex; flex-wrap: wrap; gap: 4px; min-width: 250px; }
        .modal { display: none; position: fixed; z-index: 1000; left: 0; top: 0; width: 100%; height: 100%; background-color: rgba(0,0,0,0.5); overflow-y: auto; }
        .modal-content { background-color: white; margin: 20px auto; padding: 25px; border-radius: 12px; width: 90%; max-width: 700px; box-shadow: 0 10px 40px rgba(0,0,0,0.2); border: 1px solid #e8e8e8; }
        .modal-content h3 { margin-bottom: 18px; font-size: 15px; }
        .modal-content input { width: 100%; padding: 9px 11px; margin: 10px 0; border: 1px solid #d0d0d0; border-radius: 6px; font-size: 13px; transition: all 0.2s ease; }
        .modal-content input:focus { outline: none; border-color: #0066cc; box-shadow: 0 0 0 2px rgba(0, 102, 204, 0.1); }
        .modal-content pre { background-color: #f4f4f4; font-family: 'Courier New', monospace; padding: 10px; border-radius: 6px; border: 1px solid #e0e0e0; color: #222; font-size: 11px; }
        .modal-content button { margin-top: 12px; margin-right: 8px; padding: 8px 14px; font-size: 11px; }
        .flash-msg { padding: 12px; border-radius: 6px; margin-bottom: 15px; font-weight: 600; border-left: 4px solid; font-size: 13px; }
        .flash-success { background-color: #d4edda; color: #155724; border-left-color: #28a745; }
        .flash-error { background-color: #f8d7da; color: #721c24; border-left-color: #dc3545; }
        a.trim { background-color: #6f42c1; color: white; padding: 7px 12px; border-radius: 6px; text-decoration: none; font-weight: 600; font-size: 11px; transition: all 0.2s ease; display: inline-block; }
        a.trim:hover { background-color: #5a32a3; transform: translateY(-1px); box-shadow: 0 2px 6px rgba(111, 66, 193, 0.2); }
        .breadcrumbs { margin-bottom: 15px; font-size: 12px; padding: 10px; background: #f5f5f5; border-radius: 6px; }
        .breadcrumbs a { margin: 0 6px; font-size: 12px; }
        .upload-section { border: 1px solid #e0e0e0; padding: 15px; border-radius: 12px; margin-top: 20px; margin-bottom: 20px; background: #f9fafb; }
        .upload-section input[type="file"] { padding: 7px; margin-right: 8px; font-size: 12px; }
        .upload-section button { font-size: 12px; padding: 8px 14px; }
        #drag-drop-zone { border: 2px dashed #0066cc; border-radius: 8px; padding: 20px; text-align: center; cursor: pointer; margin-top: 15px; background-color: #f0f7ff; transition: all 0.3s ease; }
        #drag-drop-zone:hover { background-color: #e6f0ff; border-color: #0052a3; }
        #drag-drop-zone.drag-over { background-color: #0066cc; color: white; border-color: #0052a3; }
        #drag-drop-zone p { margin: 10px 0; color: #666; font-size: 13px; }
        #drag-drop-zone.drag-over p { color: white; }
        .file-row { cursor: grab; }
        .file-row:active { cursor: grabbing; }
        @media (max-width: 768px) {
            body { padding: 10px; }
            .container { padding: 15px; }
            h1 { font-size: 20px; }
            h3 { font-size: 13px; }
            table { font-size: 11px; }
            th, td { padding: 10px 8px; font-size: 11px; }
            button { padding: 6px 10px; font-size: 10px; margin-right: 3px; }
            a { font-size: 11px; }
            a.trim { padding: 6px 10px; font-size: 10px; }
            .actions { font-size: 10px; }
            .modal-content { padding: 20px; margin: 15px auto; }
            .breadcrumbs { font-size: 11px; padding: 8px; }
            .upload-section { padding: 12px; margin-top: 15px; margin-bottom: 15px; }
        }
        @media (max-width: 480px) {
            body { padding: 8px; }
            .container { padding: 12px; }
            h1 { font-size: 18px; }
            h3 { font-size: 12px; }
            table { display: block; width: 100%; border: none; }
            thead { display: none; }
            tbody { display: block; }
            tr { display: block; margin-bottom: 12px; border: 1px solid #ddd; border-radius: 8px; overflow: hidden; background: #fafbff; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
            td { display: block; width: 100%; padding: 8px 10px !important; border: none; font-size: 12px; text-align: left; word-break: break-word; }
            td[style*="width:30px"] { display: none; }
            td:before { content: attr(data-label); font-weight: 700; color: #0066cc; display: block; margin-bottom: 4px; font-size: 11px; text-transform: uppercase; }
            tr td:last-child { border-bottom: none; }
            button { padding: 6px 8px; font-size: 10px; margin: 3px 3px 3px 0; flex-shrink: 0; }
            a { font-size: 11px; margin-right: 3px; }
            a.trim { padding: 6px 8px; font-size: 10px; }
            .actions { font-size: 11px; flex-wrap: wrap; }
            .modal-content { padding: 15px; margin: 10px auto; }
            .breadcrumbs { font-size: 10px; padding: 6px; }
            .upload-section { padding: 10px; margin-top: 12px; margin-bottom: 12px; }
            .upload-section input[type="file"] { margin-right: 5px; font-size: 11px; }
        }
    </style>
</head>
<body>
<div class="container">
    <h1>Downloaded Files</h1>
    <p>
        <a href="{{ url_for('index') }}">← Back to Downloader</a> | 
        <a href="{{ url_for('gofile_manager') }}" style="color: #9b59b6; font-weight: bold;">📊 Gofile Manager</a>
    </p>

    {% with messages = get_flashed_messages(with_categories=true) %}
        {% for category, message in messages %}
            <div class="flash-msg flash-{{ category }}">{{ message|safe }}</div>
        {% endfor %}
    {% endwith %}

    <div class="breadcrumbs">
        <a href="{{ url_for('list_files', current_path='') }}">Home</a>
        {% for crumb in breadcrumbs %}
            / <a href="{{ url_for('list_files', current_path=crumb.path) }}">{{ crumb.name }}</a>
        {% endfor %}
        <div style="float: right; display: flex; gap: 10px; align-items: center;">
            <label for="sort-select" style="margin: 0; font-weight: 600; font-size: 12px;">Sort by:</label>
            <select id="sort-select" onchange="changeSortOrder(this.value)" style="padding: 6px 10px; border: 1px solid #d0d0d0; border-radius: 6px; font-size: 12px; cursor: pointer;">
                <option value="newest" {% if request.args.get('sort', 'newest') == 'newest' %}selected{% endif %}>📅 Newest First</option>
                <option value="name_asc" {% if request.args.get('sort') == 'name_asc' %}selected{% endif %}>🔤 A-Z</option>
                <option value="name_desc" {% if request.args.get('sort') == 'name_desc' %}selected{% endif %}>🔤 Z-A</option>
            </select>
        </div>
    </div>
    <div style="clear: both;"></div>

    <div style="margin: 15px 0; padding: 12px; background: #e3f2fd; border-left: 4px solid #2196F3; border-radius: 4px; font-size: 13px;">
        <strong>💾 Storage Info:</strong> Total Files: {{ total_files_size_human }} | Available Space: {{ available_space_human }}
    </div>

    <div class="upload-section">
        <h3>Upload to this Folder ({{ current_path or '/' }})</h3>
        <form method="POST" action="{{ url_for('upload_local') }}" enctype="multipart/form-data" style="display:inline-block; margin-right:20px;">
            <input type="hidden" name="current_path" value="{{ current_path }}">
            <input type="file" name="file" required>
            <button type="submit">Upload File</button>
        </form>
        <form method="POST" action="{{ url_for('upload_folder') }}" enctype="multipart/form-data" style="display:inline-block;">
            <input type="hidden" name="current_path" value="{{ current_path }}">
            <input type="file" name="files[]" webkitdirectory directory multiple required>
            <button type="submit">Upload Folder</button>
        </form>
        <div id="drag-drop-zone">
            <p>📁 Or drag and drop files here</p>
        </div>
    </div>

    <div id="batch-operations" style="display:none; margin-top:20px; padding:15px; background:#f0f0f0; border-radius:8px;">
        <strong>Selected: <span id="selected-count">0</span> file(s)</strong>
        <button onclick="batchDelete()" class="delete" style="margin-left:10px;">Delete Selected</button>
        <button onclick="showBatchMoveModal()" style="background-color: #6f42c1; color: white; margin-left:5px;">Move Selected</button>
        <button onclick="showBatchEncodeModal()" class="encode" style="margin-left:5px;">Encode Selected</button>
    </div>

    <div class="card" style="margin-top:20px;">
      <div class="card-header">
        <div class="card-icon blue">🔗</div>
        <div>
          <h2 class="card-title">Merge Files</h2>
          <p class="card-desc">Select files from the list above, then merge</p>
        </div>
      </div>

      <button type="button" class="encode" onclick="startMerge()" style="margin-top:10px;">🔗 Merge Selected</button>

      <div id="merge-progress-container" class="progress-container" style="display:none; margin-top:15px; background:#f9f9f9; padding:15px; border-radius:8px; border:1px solid #ddd;">
        <h3 id="merge-stage">Preparing…</h3>
        <div class="progress-bar" style="width:100%; height:20px; background:#e0e0e0; border-radius:10px; overflow:hidden;">
          <div id="merge-bar" class="progress-bar-inner" style="height:100%; width:0%; background:linear-gradient(90deg, #667eea 0%, #764ba2 100%); text-align:center; color:white; font-size:12px; line-height:20px; transition:width 0.4s ease;">0%</div>
        </div>
      </div>
    </div>
        <button onclick="batchUploadPixeldrain()" class="upload" style="margin-left:5px;">Upload to Pixeldrain</button>
        <button onclick="batchUploadPixeldrainAlt()" class="upload" style="background-color: #3498db; margin-left:5px;">Upload to Pixeldrain 2</button>
        <button onclick="batchUpload4stream()" style="background-color: #e74c3c; margin-left:5px;">Upload to 4stream</button>
        <button onclick="batchUpload4streamAlt()" style="background-color: #ff6b6b; margin-left:5px;">Upload to 4stream 2</button>
        <button onclick="batchUploadGofile()" style="background-color: #9b59b6; margin-left:5px;">Upload to Gofile</button>
        <button onclick="batchDownload()" style="margin-left:5px;">Download Selected (ZIP)</button>
        <button onclick="clearSelection()" style="margin-left:5px;">Clear Selection</button>
    </div>

    <table>
    <thead><tr><th style="width:30px;"><input type="checkbox" id="select-all" onchange="toggleSelectAll(this)"></th><th>Name</th><th>Size</th><th>Actions</th></tr></thead>
    <tbody>
        {% if current_path %}
            <tr><td style="width:30px;"></td><td><a href="{{ url_for('list_files', current_path=parent_dir) }}">⬆️ Parent Directory</a></td><td>-</td><td></td></tr>
        {% endif %}
        {% for item in items %}
        <tr draggable="true" class="file-row" data-filepath="{{ item.path }}" data-is-media="{{ item.is_media|lower }}">
            <td style="width:30px;">{% if not item.is_folder %}<input type="checkbox" class="file-checkbox" value="{{ item.path }}" onchange="updateSelection()">{% endif %}</td>
                <td class="file-name file-name-cell" data-filename="{{ item.path }}">
                {% if item.is_folder %}
                    <a href="{{ url_for('list_files', current_path=item.path) }}">📁 <strong>{{ item.name }}</strong></a>
                {% else %}
                    📄 {{ item.name }}
                {% endif %}
            </td>
            <td>{{ item.size }}</td>
            <td class="actions">
                {% if not item.is_folder %}<a href="{{ url_for('download_file', filepath=item.path) }}">Download</a>{% endif %}
                <button onclick="showRenameModal('{{ item.path }}', '{{ item.name }}')" class="rename">Rename</button>
                <button onclick="showMoveModal('{{ item.path }}', '{{ item.name }}')" style="background-color: #6f42c1; color: white; padding: 7px 12px; border: none; border-radius: 6px; cursor: pointer; font-size: 11px; font-weight: 600;">Move</button>
                {% if not item.is_folder %}
                    <form method="POST" action="{{ url_for('upload_to_pixeldrain_file') }}" style="display:inline;"><input type="hidden" name="filepath" value="{{ item.path }}"><button type="submit" class="upload">Upload</button></form>
                    <form method="POST" action="{{ url_for('upload_to_pixeldrain_file_alt') }}" style="display:inline;"><input type="hidden" name="filepath" value="{{ item.path }}"><button type="submit" class="upload" style="background-color: #3498db;">💾 Pixeldrain 2</button></form>
                    <form method="POST" action="{{ url_for('upload_to_4stream_file') }}" style="display:inline;"><input type="hidden" name="filepath" value="{{ item.path }}"><button type="submit" style="background-color: #e74c3c;">🎬 4stream</button></form>
                    <form method="POST" action="{{ url_for('upload_to_4stream_file_alt') }}" style="display:inline;"><input type="hidden" name="filepath" value="{{ item.path }}"><button type="submit" style="background-color: #ff6b6b;">🎬 4stream 2</button></form>
                    <form method="POST" action="{{ url_for('upload_to_gofile_file') }}" style="display:inline;"><input type="hidden" name="filepath" value="{{ item.path }}"><button type="submit" style="background-color: #9b59b6;">📤 Gofile</button></form>
                    {% if item.is_media %}
                        <a href="{{ url_for('encode_page', filepath=item.path) }}" class="encode">Encode</a>
                        <a href="{{ url_for('trim_page', filepath=item.path) }}" class="trim">Trim</a>
                        <button type="button" onclick="showInfoModal('{{ item.path }}')" class="info">Info</button>
                    {% endif %}
                {% endif %}
                <button type="button" class="delete" onclick="showDeleteModal('{{ item.path }}', '{{ item.name }}')">Delete</button>
            </td>
        </tr>
        {% endfor %}
        {% if not items %}<tr><td colspan="3">{% if current_path %}This folder is empty.{% else %}No files downloaded yet.{% endif %}</td></tr>{% endif %}
    </tbody>
    </table>
</div>

<div id="renameModal" class="modal"><div class="modal-content">
    <h3>Rename File or Folder</h3>
    <p>Current name: <strong id="currentName"></strong></p>
    <label>New name (within same folder):</label>
    <input type="text" id="newName" placeholder="Enter new name">
    <button onclick="confirmRename()">Rename</button><button onclick="closeRenameModal()">Cancel</button>
</div></div>
<div id="moveModal" class="modal"><div class="modal-content">
    <h3>Move File or Folder</h3>
    <p>Moving: <strong id="moveFileName"></strong></p>
    <label>Select destination folder:</label>
    <select id="moveDestFolder" style="width: 100%; padding: 8px; margin: 10px 0; border: 1px solid #d0d0d0; border-radius: 6px;">
        <option value="/">📁 Home</option>
    </select>
    <button onclick="confirmMove()">Move</button><button onclick="closeMoveModal()">Cancel</button>
</div></div>
<div id="batchMoveModal" class="modal"><div class="modal-content">
    <h3>Move Selected Files</h3>
    <p>Moving <strong id="batchMoveCount">0</strong> file(s)</p>
    <label>Select destination folder:</label>
    <select id="batchMoveDestFolder" style="width: 100%; padding: 8px; margin: 10px 0; border: 1px solid #d0d0d0; border-radius: 6px;">
        <option value="/">📁 Home</option>
    </select>
    <button onclick="confirmBatchMove()">Move</button><button onclick="closeBatchMoveModal()">Cancel</button>
</div></div>
<div id="infoModal" class="modal"><div class="modal-content">
    <h3>Media Information</h3>
    <p><strong>File:</strong> <span id="infoFilename"></span></p>
    <pre id="infoContent"></pre>
    <button type="button" onclick="closeInfoModal()">Close</button>
</div></div>
<div id="deleteModal" class="modal"><div class="modal-content">
    <h3>⚠️ Confirm Delete</h3>
    <p>Are you sure you want to delete <strong id="deleteFileName"></strong>?</p>
    <p style="color: #999; font-size: 12px;">This action cannot be undone.</p>
    <button onclick="confirmDelete()" class="delete" style="background-color: #dc3545;">Delete</button><button onclick="closeDeleteModal()">Cancel</button>
</div></div>

<div id="batchEncodeModal" class="modal"><div class="modal-content" style="width:700px; max-height:90vh; overflow-y:auto;">
    <h3>Batch Encode Settings - <span id="batchEncodeFileCount">0</span> file(s)</h3>
    <form id="batchEncodeForm">
        <label>Codec:</label><select id="batchCodec" name="codec" required onchange="updateBatchPresetOptions()">
            <option value="none">No Encoding (Copy)</option>
            <option value="h265">Encode to H.265 (x265)</option>
            <option value="av1" selected>Encode to AV1 (SVT-AV1)</option>
            <option value="h265_copy_audio">H.265 Video Only (Copy Audio)</option>
            <option value="av1_copy_audio">AV1 Video Only (Copy Audio)</option>
            <option value="copy_video">Copy Video (Encode Audio Only)</option>
        </select>

        <div id="batchEncodingOptions" style="display:block;">
            <div id="batchVideoEncodingOptions">
                <label>Encoding Mode:</label><select id="batchPassMode" name="pass_mode">
                    <option value="1-pass" selected>1-pass (CRF)</option>
                    <option value="2-pass">2-pass (VBR)</option>
                </select>

                <label>Preset (slower = better quality/smaller file):</label><select id="batchPreset" name="preset"></select>

                <label>Video Bitrate (kb/s, optional):</label><input type="number" id="batchBitrate" name="bitrate" min="100" placeholder="e.g., 600 for AV1, 2000 for H.265">

                <label>CRF (0–63, lower = better quality):</label><input type="number" id="batchCrf" name="crf" value="45" min="0" max="63" step="1" placeholder="e.g., 28 for H.265, 24 for AV1">

                <label>Frame Rate (optional):</label><select name="fps">
                    <option value="">Original</option><option value="24">24 fps</option><option value="30">30 fps</option><option value="60">60 fps</option>
                </select>

                <label>Resolution (Scale, optional):</label><select name="scale">
                    <option value="">Original</option><option value="1920:-2">1080p (1920px wide)</option><option value="1280:-2">720p (1280px wide)</option><option value="854:-2">480p (854px wide)</option><option value="640:-2">360p (640px wide)</option>
                </select>

                <label>Adaptive Quantization Mode (AV1 only):</label><select id="batchAqMode" name="aq_mode">
                    <option value="0">Disabled</option><option value="1">PSNR-based</option><option value="2" selected>Variance-based</option>
                </select>

                <label>Variance Boost (AV1 only, 0–3):</label><input type="number" id="batchVarianceBoost" name="variance_boost" value="2" min="0" max="3" step="1">

                <label>Tiles (AV1 only):</label><select id="batchTiles" name="tiles">
                    <option value="">None</option><option value="2x2" selected>2x2 (Recommended for 720p)</option><option value="4x4">4x4</option>
                </select>

                <label><input type="checkbox" id="batchEnableVmaf" name="enable_vmaf"> Compute VMAF Quality Score (slower)</label>
            </div>

            <div id="batchAudioEncodingOptions">
                <label>Audio Bitrate (kb/s):</label><input type="number" id="batchAudioBitrate" name="audio_bitrate" value="32" min="32" max="512" step="8" placeholder="e.g., 64, 96, 128">

                <label><input type="checkbox" name="force_stereo"> Force Stereo (2-channel) Audio</label>
            </div>
        </div>

        <label><input type="checkbox" id="batchUploadPixeldrain" name="upload_pixeldrain"> Upload to Pixeldrain after encoding</label>

        <div style="margin-top:20px;">
            <button type="button" onclick="startBatchEncode()" class="encode">Start Batch Encoding</button>
            <button type="button" onclick="closeBatchEncodeModal()" style="background:#ccc; color:#000;">Cancel</button>
        </div>
    </form>

    <script>
        function updateBatchPresetOptions() {
            const codec = document.getElementById('batchCodec').value;
            const presetSelect = document.getElementById('batchPreset');
            const crfInput = document.getElementById('batchCrf');
            const videoOpts = document.getElementById('batchVideoEncodingOptions');
            const audioOpts = document.getElementById('batchAudioEncodingOptions');
            const aqMode = document.getElementById('batchAqMode');
            const varianceBoost = document.getElementById('batchVarianceBoost');
            const tiles = document.getElementById('batchTiles');

            if (codec === 'copy_video') {
                videoOpts.style.display = 'none';
                audioOpts.style.display = 'block';
            } else if (codec.endsWith('_copy_audio')) {
                videoOpts.style.display = 'block';
                audioOpts.style.display = 'none';
            } else if (codec !== 'none') {
                videoOpts.style.display = 'block';
                audioOpts.style.display = 'block';
            } else {
                videoOpts.style.display = 'none';
                audioOpts.style.display = 'none';
            }

            presetSelect.innerHTML = '';
            if (codec === 'av1' || codec === 'av1_copy_audio') {
                for (let p = 0; p <= 13; p++) {
                    let label = p.toString();
                    if (p === 0) label += ' (slowest)'; else if (p === 13) label += ' (fastest)'; else if (p > 7) label += ' (fast)'; else label += ' (medium)';
                    const opt = document.createElement('option');
                    opt.value = p; opt.text = label;
                    if (p === 7) opt.selected = true;
                    presetSelect.appendChild(opt);
                }
                crfInput.value = crfInput.value || '24'; crfInput.placeholder = 'e.g., 24 for AV1';
                aqMode.disabled = false; varianceBoost.disabled = false; tiles.disabled = false;
            } else if (codec === 'h265' || codec === 'h265_copy_audio') {
                const presets = ['ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow', 'slower', 'veryslow', 'placebo'];
                presets.forEach(p => {
                    const opt = document.createElement('option');
                    opt.value = p; opt.text = p;
                    if (p === 'faster') opt.selected = true;
                    presetSelect.appendChild(opt);
                });
                crfInput.value = crfInput.value || '28'; crfInput.placeholder = 'e.g., 28 for H.265';
                aqMode.disabled = true; varianceBoost.disabled = true; tiles.disabled = true;
            } else {
                aqMode.disabled = true; varianceBoost.disabled = true; tiles.disabled = true;
            }
        }
        updateBatchPresetOptions();
    </script>
</div></div>

<script>
    function getSelectedFilesForMerge() {
      const selected = [];

      document.querySelectorAll(".file-row").forEach(row => {
        const cb = row.querySelector("input[type='checkbox']");
        if (cb && cb.checked) {
          const name = row.querySelector("[data-filename]")?.dataset.filename;
          if (name) selected.push(name);
        }
      });

      return selected;
    }

    function startMerge(e) {
      if (e) e.preventDefault();

      const selected = getSelectedFilesForMerge();
      console.log("Merge selected:", selected);

      if (selected.length < 2) {
        alert("Select at least 2 files");
        return;
      }

      const output = prompt("Output filename:", "merged.mkv");
      if (!output) return;

      fetch("/merge_files", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          files: selected,
          output: output
        })
      });

      document.getElementById("merge-progress-container").style.display = "block";
      listenMergeProgress();
    }

    function listenMergeProgress() {
      const es = new EventSource("/progress");
      const bar = document.getElementById("merge-bar");
      const stage = document.getElementById("merge-stage");

      es.onmessage = e => {
        const data = JSON.parse(e.data);
        if (data.percent) {
          bar.style.width = data.percent + "%";
          bar.textContent = data.percent.toFixed(1) + "%";
        }
        if (data.stage) stage.textContent = data.stage;
        if (data.log === "DONE") {
          stage.textContent = "✅ Merge Complete";
          es.close();
          setTimeout(() => location.reload(), 2000);
        }
      };
    }

    let currentFile = '';
    function showRenameModal(filepath, currentName) {
        currentFile = filepath;
        document.getElementById('currentName').textContent = currentName;
        document.getElementById('newName').value = currentName;
        document.getElementById('renameModal').style.display = 'block';
        document.getElementById('newName').focus();
    }
    function closeRenameModal() { document.getElementById('renameModal').style.display = 'none'; }
    function confirmRename() {
        const newName = document.getElementById('newName').value.trim();
        if (newName && newName !== currentFile.split('/').pop()) {
            const form = document.createElement('form'); form.method = 'POST'; form.action = '{{ url_for("rename_file") }}';
            const oldInput = document.createElement('input'); oldInput.type = 'hidden'; oldInput.name = 'old_name'; oldInput.value = currentFile;
            const newInput = document.createElement('input'); newInput.type = 'hidden'; newInput.name = 'new_name'; newInput.value = newName;
            form.append(oldInput, newInput); document.body.appendChild(form); form.submit();
        }
        closeRenameModal();
    }
    function showInfoModal(filepath) {
        const modal = document.getElementById('infoModal');
        const content = document.getElementById('infoContent');
        const filename = document.getElementById('infoFilename');
        filename.textContent = filepath.split('/').pop();
        content.textContent = 'Fetching info...';
        modal.style.display = 'block';
        const safePath = filepath.split('/').map(encodeURIComponent).join('/');
        fetch(`/info/${safePath}`)
            .then(response => { if (!response.ok) { throw new Error('Network response not ok'); } return response.json(); })
            .then(data => {
                if (data.error) { content.textContent = `Error: ${data.error}`; return; }
                let infoText = `File Size:      ${data.file_size || 'N/A'}\\n`
                             + `Duration:       ${data.duration || 'N/A'}\\n\\n`
                             + `Resolution:     ${data.resolution || 'N/A'}\\n`
                             + `Video Codec:    ${data.video_codec || 'N/A'}\\n`
                             + `Frame Rate:     ${data.video_fps || 'N/A'} fps\\n`
                             + `Video Bitrate:  ${data.video_bitrate || 'N/A'}\\n`
                             + `Stream Size:    ${data.video_stream_size || 'N/A'}\\n\\n`
                             + `Audio Codec:    ${data.audio_codec || 'N/A'}\\n`
                             + `Audio Bitrate:  ${data.audio_bitrate || 'N/A'}\\n`
                             + `Stream Size:    ${data.audio_stream_size || 'N/A'}`;
                content.textContent = infoText;
            })
            .catch(error => { content.textContent = 'Failed to fetch media information.'; });
    }
    function closeInfoModal() { document.getElementById('infoModal').style.display = 'none'; }

    let currentMoveFile = '';
    function showMoveModal(filepath, fileName) {
        currentMoveFile = filepath;
        document.getElementById('moveFileName').textContent = fileName;
        document.getElementById('moveModal').style.display = 'block';
        populateFolderSelect('moveDestFolder');
    }
    function closeMoveModal() { document.getElementById('moveModal').style.display = 'none'; }
    function confirmMove() {
        const destFolder = document.getElementById('moveDestFolder').value;
        const form = document.createElement('form');
        form.method = 'POST';
        form.action = '{{ url_for("move_file") }}';
        const filepathInput = document.createElement('input');
        filepathInput.type = 'hidden';
        filepathInput.name = 'filepath';
        filepathInput.value = currentMoveFile;
        const destInput = document.createElement('input');
        destInput.type = 'hidden';
        destInput.name = 'dest_folder';
        destInput.value = destFolder;
        form.append(filepathInput, destInput);
        document.body.appendChild(form);
        form.submit();
        closeMoveModal();
    }

    function showBatchMoveModal() {
        const files = getSelectedFiles();
        if (files.length === 0) { alert('No files selected'); return; }
        document.getElementById('batchMoveCount').textContent = files.length;
        document.getElementById('batchMoveModal').style.display = 'block';
        populateFolderSelect('batchMoveDestFolder');
        window.batchMoveFiles = files;
    }
    function closeBatchMoveModal() { document.getElementById('batchMoveModal').style.display = 'none'; }
    function confirmBatchMove() {
        const destFolder = document.getElementById('batchMoveDestFolder').value;
        const form = document.createElement('form');
        form.method = 'POST';
        form.action = '{{ url_for("batch_move") }}';
        window.batchMoveFiles.forEach(file => {
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'files[]';
            input.value = file;
            form.appendChild(input);
        });
        const destInput = document.createElement('input');
        destInput.type = 'hidden';
        destInput.name = 'dest_folder';
        destInput.value = destFolder;
        form.appendChild(destInput);
        document.body.appendChild(form);
        form.submit();
        closeBatchMoveModal();
    }

    function populateFolderSelect(selectId) {
        const select = document.getElementById(selectId);
        fetch('/get_folders')
            .then(response => response.json())
            .then(folders => {
                select.innerHTML = '<option value="/">📁 Home</option>';
                folders.forEach(folder => {
                    if (folder !== '/') {
                        const option = document.createElement('option');
                        option.value = folder;
                        option.textContent = '📁 ' + folder;
                        select.appendChild(option);
                    }
                });
            })
            .catch(error => console.error('Error fetching folders:', error));
    }

    let currentDeleteFile = '';
    function showDeleteModal(filepath, fileName) {
        currentDeleteFile = filepath;
        document.getElementById('deleteFileName').textContent = fileName;
        document.getElementById('deleteModal').style.display = 'block';
    }
    function closeDeleteModal() { document.getElementById('deleteModal').style.display = 'none'; }
    function confirmDelete() {
        const form = document.createElement('form');
        form.method = 'POST';
        form.action = '/delete/' + encodeURIComponent(currentDeleteFile);
        document.body.appendChild(form);
        form.submit();
        closeDeleteModal();
    }

    // Drag and Drop Upload
    function setupDragDrop() {
        const zone = document.getElementById('drag-drop-zone');
        const currentPath = '{{ current_path }}';

        ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
            zone.addEventListener(eventName, preventDefaults, false);
        });

        function preventDefaults(e) {
            e.preventDefault();
            e.stopPropagation();
        }

        ['dragenter', 'dragover'].forEach(eventName => {
            zone.addEventListener(eventName, () => zone.classList.add('drag-over'));
        });

        ['dragleave', 'drop'].forEach(eventName => {
            zone.addEventListener(eventName, () => zone.classList.remove('drag-over'));
        });

        zone.addEventListener('drop', (e) => {
            const dt = e.dataTransfer;
            const files = dt.files;
            if (files.length > 0) {
                // Upload files one by one
                const uploadPromises = [];
                for (let file of files) {
                    const fileForm = new FormData();
                    fileForm.append('current_path', currentPath);
                    fileForm.append('file', file);
                    uploadPromises.push(
                        fetch('{{ url_for("upload_local") }}', {
                            method: 'POST',
                            body: fileForm
                        }).catch(err => console.error('Upload error:', err))
                    );
                }
                // Reload only after ALL files are uploaded
                Promise.all(uploadPromises).then(() => {
                    window.location.reload();
                });
            }
        });
    }

    window.onclick = (event) => {
        if (event.target == document.getElementById('renameModal')) closeRenameModal();
        if (event.target == document.getElementById('infoModal')) closeInfoModal();
        if (event.target == document.getElementById('moveModal')) closeMoveModal();
        if (event.target == document.getElementById('batchMoveModal')) closeBatchMoveModal();
        if (event.target == document.getElementById('deleteModal')) closeDeleteModal();
    };

    // Initialize drag-drop on page load
    document.addEventListener('DOMContentLoaded', setupDragDrop);

    function toggleSelectAll(checkbox) {
        document.querySelectorAll('.file-checkbox').forEach(cb => cb.checked = checkbox.checked);
        updateSelection();
    }

    function updateSelection() {
        const checked = document.querySelectorAll('.file-checkbox:checked');
        const count = checked.length;
        document.getElementById('selected-count').textContent = count;
        document.getElementById('batch-operations').style.display = count > 0 ? 'block' : 'none';
    }

    function getSelectedFiles() {
        return Array.from(document.querySelectorAll('.file-checkbox:checked')).map(cb => cb.value);
    }

    function clearSelection() {
        document.querySelectorAll('.file-checkbox').forEach(cb => cb.checked = false);
        document.getElementById('select-all').checked = false;
        updateSelection();
    }

    function batchDelete() {
        const files = getSelectedFiles();
        if (files.length === 0) { alert('No files selected'); return; }
        if (!confirm(`Delete ${files.length} file(s)? This cannot be undone.`)) return;

        const form = document.createElement('form');
        form.method = 'POST';
        form.action = '{{ url_for("batch_delete") }}';
        files.forEach(file => {
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'files[]';
            input.value = file;
            form.appendChild(input);
        });
        document.body.appendChild(form);
        form.submit();
    }

    function batchDownload() {
        const files = getSelectedFiles();
        if (files.length === 0) { alert('No files selected'); return; }
        window.location.href = '{{ url_for("batch_download") }}?files=' + files.map(encodeURIComponent).join('&files=');
    }

    function showBatchEncodeModal() {
        const files = getSelectedFiles();
        if (files.length === 0) { alert('No files selected'); return; }
        const mediaFiles = files.filter(f => {
            const row = document.querySelector(`[data-filepath="${f}"]`);
            return row && row.dataset.isMedia === 'true';
        });
        if (mediaFiles.length === 0) { alert('No media files selected'); return; }
        document.getElementById('batchEncodeFileCount').textContent = mediaFiles.length;
        document.getElementById('batchEncodeModal').style.display = 'block';
        window.batchMediaFiles = mediaFiles;
    }

    function closeBatchEncodeModal() { 
        document.getElementById('batchEncodeModal').style.display = 'none'; 
    }

    function startBatchEncode() {
        if (!window.batchMediaFiles || window.batchMediaFiles.length === 0) { alert('No files to encode'); return; }
        const form = document.getElementById('batchEncodeForm');
        const formData = new FormData(form);

        const params = new URLSearchParams();
        window.batchMediaFiles.forEach(file => params.append('files[]', file));
        params.append('codec', formData.get('codec'));
        params.append('pass_mode', formData.get('pass_mode'));
        params.append('preset', formData.get('preset'));
        params.append('bitrate', formData.get('bitrate') || '');
        params.append('crf', formData.get('crf'));
        params.append('fps', formData.get('fps') || '');
        params.append('scale', formData.get('scale') || '');
        params.append('audio_bitrate', formData.get('audio_bitrate') || '');
        params.append('force_stereo', formData.get('force_stereo') ? 'true' : 'false');
        params.append('aq_mode', formData.get('aq_mode') || '1');
        params.append('variance_boost', formData.get('variance_boost') || '1');
        params.append('tiles', formData.get('tiles') || '2x2');
        params.append('enable_vmaf', formData.get('enable_vmaf') ? 'true' : 'false');
        params.append('upload_pixeldrain', formData.get('upload_pixeldrain') ? 'true' : 'false');

        closeBatchEncodeModal();
        window.location.href = '{{ url_for("batch_encode") }}?' + params.toString();
    }

    function batchUploadPixeldrain() {
        const files = getSelectedFiles();
        if (files.length === 0) { alert('No files selected'); return; }
        if (!confirm(`Upload ${files.length} file(s) to Pixeldrain?`)) return;

        const form = document.createElement('form');
        form.method = 'POST';
        form.action = '{{ url_for("batch_upload_pixeldrain") }}';
        files.forEach(file => {
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'files[]';
            input.value = file;
            form.appendChild(input);
        });
        document.body.appendChild(form);
        form.submit();
    }

    function batchUpload4stream() {
        const files = getSelectedFiles();
        if (files.length === 0) { alert('No files selected'); return; }
        if (!confirm(`Upload ${files.length} file(s) to 4stream?`)) return;

        const form = document.createElement('form');
        form.method = 'POST';
        form.action = '{{ url_for("batch_upload_4stream") }}';
        files.forEach(file => {
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'files[]';
            input.value = file;
            form.appendChild(input);
        });
        document.body.appendChild(form);
        form.submit();
    }

    function batchUploadPixeldrainAlt() {
        const files = getSelectedFiles();
        if (files.length === 0) { alert('No files selected'); return; }
        if (!confirm(`Upload ${files.length} file(s) to Pixeldrain 2?`)) return;

        const form = document.createElement('form');
        form.method = 'POST';
        form.action = '{{ url_for("batch_upload_pixeldrain_alt") }}';
        files.forEach(file => {
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'files[]';
            input.value = file;
            form.appendChild(input);
        });
        document.body.appendChild(form);
        form.submit();
    }

    function batchUpload4streamAlt() {
        const files = getSelectedFiles();
        if (files.length === 0) { alert('No files selected'); return; }
        if (!confirm(`Upload ${files.length} file(s) to 4stream 2?`)) return;

        const form = document.createElement('form');
        form.method = 'POST';
        form.action = '{{ url_for("batch_upload_4stream_alt") }}';
        files.forEach(file => {
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'files[]';
            input.value = file;
            form.appendChild(input);
        });
        document.body.appendChild(form);
        form.submit();
    }

    function batchUploadGofile() {
        const files = getSelectedFiles();
        if (files.length === 0) { alert('No files selected'); return; }
        if (!confirm(`Upload ${files.length} file(s) to Gofile?`)) return;

        const form = document.createElement('form');
        form.method = 'POST';
        form.action = '{{ url_for("batch_upload_gofile") }}';
        files.forEach(file => {
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'files[]';
            input.value = file;
            form.appendChild(input);
        });
        document.body.appendChild(form);
        form.submit();
    }

    let draggedRow = null;

    document.addEventListener("dragstart", e => {
      const row = e.target.closest(".file-row");
      if (!row) return;
      draggedRow = row;
      row.style.opacity = "0.5";
    });

    document.addEventListener("dragend", e => {
      if (draggedRow) draggedRow.style.opacity = "";
      draggedRow = null;
    });

    document.addEventListener("dragover", e => {
      e.preventDefault();
      const row = e.target.closest(".file-row");
      if (!row || row === draggedRow) return;

      const rect = row.getBoundingClientRect();
      const after = e.clientY > rect.top + rect.height / 2;

      row.parentNode.insertBefore(
        draggedRow,
        after ? row.nextSibling : row
      );
    });
</script>
</body></html>
    """,
        items=all_items,
        current_path=current_path,
        breadcrumbs=breadcrumbs,
        parent_dir=os.path.dirname(current_path),
        total_files_size_human=total_files_size_human,
        available_space_human=available_space_human)


@app.route("/operation_complete")
def operation_complete():
    url = request.args.get('url')
    if url: session['last_upload_url'] = url
    return redirect(url_for('list_files', current_path=''))


@app.route("/download/<path:filepath>")
def download_file(filepath):
    return send_from_directory(DOWNLOAD_FOLDER, filepath, as_attachment=True)


@app.route("/info/<path:filepath>")
def get_info(filepath):
    full_path = os.path.join(DOWNLOAD_FOLDER, filepath)
    if not os.path.abspath(full_path).startswith(
            os.path.abspath(DOWNLOAD_FOLDER)):
        return jsonify({"error": "Invalid file path"}), 400
    if not os.path.exists(full_path):
        return jsonify({"error": "File not found"}), 404
    info = get_media_info(full_path)
    if "error" in info: return jsonify(info), 500
    return jsonify(info)


@app.route("/delete/<path:filepath>", methods=["POST"])
def delete_file(filepath):
    full_path = os.path.join(DOWNLOAD_FOLDER, filepath)
    current_path = os.path.dirname(filepath)
    if not os.path.abspath(full_path).startswith(
            os.path.abspath(DOWNLOAD_FOLDER)):
        flash("Invalid path specified.", "error")
        return redirect(url_for('list_files', current_path=current_path))
    if os.path.exists(full_path):
        try:
            if os.path.isdir(full_path):
                shutil.rmtree(full_path)
                session['last_deleted_file'] = filepath + '/'
            else:
                os.remove(full_path)
                session['last_deleted_file'] = filepath
        except Exception as e:
            flash(f"Error deleting item: {str(e)}", "error")
    else:
        flash("Item not found.", "error")
    return redirect(url_for('list_files', current_path=current_path))


@app.route("/rename", methods=["POST"])
def rename_file():
    old_rel_path = request.form.get("old_name")
    new_name_only = request.form.get("new_name")
    if not old_rel_path or not new_name_only:
        flash("Both old and new paths are required.", "error")
        return redirect(url_for('list_files', current_path=""))
    current_dir = os.path.dirname(old_rel_path)
    safe_new_name = get_safe_filename(new_name_only.strip('/'))
    new_rel_path = os.path.join(current_dir, safe_new_name)
    old_path = os.path.join(DOWNLOAD_FOLDER, old_rel_path)
    new_path = os.path.join(DOWNLOAD_FOLDER, new_rel_path)
    if not os.path.abspath(old_path).startswith(os.path.abspath(DOWNLOAD_FOLDER)) or \
       not os.path.abspath(new_path).startswith(os.path.abspath(DOWNLOAD_FOLDER)):
        flash("Invalid path specified.", "error")
        return redirect(url_for('list_files', current_path=current_dir))
    if not os.path.exists(old_path):
        flash(f"Item not found: {old_rel_path}", "error")
    elif os.path.exists(new_path):
        flash(f"An item named '{new_rel_path}' already exists.", "error")
    else:
        try:
            os.rename(old_path, new_path)
            session['last_renamed_file'] = {
                'old': os.path.basename(old_rel_path),
                'new': safe_new_name
            }
        except Exception as e:
            flash(f"Error renaming item: {str(e)}", "error")
    return redirect(url_for('list_files', current_path=current_dir))


def get_all_folders(folder_path=None):
    """Recursively get all folders within download directory"""
    folders = []
    base_path = folder_path or DOWNLOAD_FOLDER
    try:
        for root, dirs, files in os.walk(base_path):
            for dir_name in dirs:
                full_path = os.path.join(root, dir_name)
                rel_path = os.path.relpath(full_path, DOWNLOAD_FOLDER)
                if not os.path.abspath(full_path).startswith(
                        os.path.abspath(DOWNLOAD_FOLDER)):
                    continue
                folders.append(rel_path)
    except Exception:
        pass
    return sorted(folders)


@app.route("/get_folders")
def get_folders():
    """API endpoint to get list of all folders"""
    folders = get_all_folders()
    return jsonify(folders)


@app.route("/move", methods=["POST"])
def move_file():
    filepath = request.form.get("filepath")
    dest_folder = request.form.get("dest_folder")
    if not filepath or dest_folder is None:
        flash("File path or destination is missing.", "error")
        return redirect(url_for('list_files', current_path=""))

    current_path = os.path.dirname(filepath)
    full_path = os.path.join(DOWNLOAD_FOLDER, filepath)
    filename = os.path.basename(filepath)
    dest_full_path = os.path.join(
        DOWNLOAD_FOLDER,
        dest_folder) if dest_folder != '/' else DOWNLOAD_FOLDER
    new_full_path = os.path.join(dest_full_path, filename)

    if not os.path.abspath(full_path).startswith(os.path.abspath(DOWNLOAD_FOLDER)) or \
       not os.path.abspath(dest_full_path).startswith(os.path.abspath(DOWNLOAD_FOLDER)) or \
       not os.path.abspath(new_full_path).startswith(os.path.abspath(DOWNLOAD_FOLDER)):
        flash("Invalid path specified.", "error")
        return redirect(url_for('list_files', current_path=current_path))

    if not os.path.exists(full_path):
        flash("File not found.", "error")
        return redirect(url_for('list_files', current_path=current_path))

    if not os.path.exists(dest_full_path):
        os.makedirs(dest_full_path, exist_ok=True)

    if os.path.exists(new_full_path):
        flash("Item already exists in destination folder.", "error")
        return redirect(url_for('list_files', current_path=current_path))

    try:
        shutil.move(full_path, new_full_path)
        flash(f"✅ Moved '{filename}' to '{dest_folder or 'Home'}'", "success")
    except Exception as e:
        flash(f"Error moving item: {str(e)}", "error")

    return redirect(url_for('list_files', current_path=current_path))


@app.route("/batch_move", methods=["POST"])
def batch_move():
    files = request.form.getlist("files[]")
    dest_folder = request.form.get("dest_folder")

    if not files or dest_folder is None:
        flash("No files selected or destination not specified.", "error")
        return redirect(url_for('list_files', current_path=""))

    dest_full_path = os.path.join(
        DOWNLOAD_FOLDER,
        dest_folder) if dest_folder != '/' else DOWNLOAD_FOLDER
    if not os.path.abspath(dest_full_path).startswith(
            os.path.abspath(DOWNLOAD_FOLDER)):
        flash("Invalid destination path.", "error")
        return redirect(url_for('list_files', current_path=""))

    if not os.path.exists(dest_full_path):
        os.makedirs(dest_full_path, exist_ok=True)

    moved_count = 0
    for filepath in files:
        full_path = os.path.join(DOWNLOAD_FOLDER, filepath)
        if not os.path.abspath(full_path).startswith(
                os.path.abspath(DOWNLOAD_FOLDER)):
            continue
        if not os.path.exists(full_path):
            continue

        filename = os.path.basename(filepath)
        new_full_path = os.path.join(dest_full_path, filename)

        if os.path.exists(new_full_path):
            continue

        try:
            shutil.move(full_path, new_full_path)
            moved_count += 1
        except Exception:
            pass

    flash(f"✅ Moved {moved_count} file(s) to '{dest_folder or 'Home'}'",
          "success")
    return redirect(url_for('list_files', current_path=""))


@app.route("/upload_to_pixeldrain", methods=["POST"])
def upload_to_pixeldrain_file():
    filepath = request.form.get("filepath")
    if not filepath:
        flash("File path is missing.", "error")
        return redirect(url_for('list_files', current_path=""))
    current_path = os.path.dirname(filepath)
    if not os.path.exists(os.path.join(DOWNLOAD_FOLDER, filepath)):
        flash("File not found.", "error")
        return redirect(url_for('list_files', current_path=current_path))
    full_path = os.path.join(DOWNLOAD_FOLDER, filepath)
    filename = os.path.basename(filepath)
    args = (full_path, filename, progress_queue)
    start_task(upload_to_pixeldrain, args)
    return render_template_string(FILE_OPERATION_TEMPLATE,
                                  operation_title=f"Uploading: {filename}",
                                  download_started=True,
                                  current_path=current_path)


@app.route("/encode/<path:filepath>")
def encode_page(filepath):
    file_path = os.path.join(DOWNLOAD_FOLDER, filepath)
    current_path = os.path.dirname(filepath)
    if not os.path.exists(file_path):
        flash("File not found.", "error")
        return redirect(url_for('list_files', current_path=current_path))
    if not is_media_file(file_path):
        flash("This file type cannot be encoded.", "error")
        return redirect(url_for('list_files', current_path=current_path))
    base, _ = os.path.splitext(filepath)
    suggested_output = f"{base}_encoded.mkv"
    return render_template_string(ENCODE_TEMPLATE,
                                  filepath=filepath,
                                  suggested_output=suggested_output,
                                  download_started=False,
                                  current_path=current_path,
                                  codec="av1",
                                  pass_mode="1-pass",
                                  crf="45",
                                  bitrate="",
                                  audio_bitrate="32",
                                  force_stereo=True,
                                  upload_4stream=True)


@app.route("/encode/<path:filepath>", methods=["POST"])
def encode_file_post(filepath):
    file_path = os.path.join(DOWNLOAD_FOLDER, filepath)
    current_path = os.path.dirname(filepath)
    if not os.path.exists(file_path):
        flash("File not found.", "error")
        return redirect(url_for('list_files', current_path=current_path))
    args = (file_path, request.form.get("output_filename"),
            request.form.get("codec"), request.form.get("preset"),
            request.form.get("pass_mode"), request.form.get("bitrate"),
            request.form.get("crf"), request.form.get("audio_bitrate"),
            request.form.get("fps"), request.form.get("scale"),
            request.form.get("force_stereo") == "true",
            request.form.get("aq_mode",
                             "1"), request.form.get("variance_boost", "1"),
            request.form.get("tiles",
                             "2x2"), request.form.get("enable_vmaf") == "true",
            progress_queue, request.form.get("upload_pixeldrain") == "true",
            request.form.get("upload_4stream") == "true",
            request.form.get("upload_gofile") == "true")
    start_task(encode_file, args)
    return render_template_string(
        ENCODE_TEMPLATE,
        filepath=filepath,
        suggested_output=request.form.get("output_filename"),
        download_started=True,
        current_path=current_path)


@app.route("/batch_delete", methods=["POST"])
def batch_delete():
    files = request.form.getlist("files[]")
    if not files:
        flash("No files selected.", "error")
        return redirect(url_for('list_files', current_path=""))

    deleted_count = 0
    for filepath in files:
        full_path = os.path.join(DOWNLOAD_FOLDER, filepath)
        if not os.path.abspath(full_path).startswith(
                os.path.abspath(DOWNLOAD_FOLDER)):
            continue
        try:
            if os.path.isdir(full_path):
                shutil.rmtree(full_path)
            else:
                os.remove(full_path)
            deleted_count += 1
        except Exception:
            pass

    flash(f"✅ Deleted {deleted_count} file(s)", "success")
    return redirect(url_for('list_files', current_path=""))


@app.route("/batch_download")
def batch_download():
    files = request.args.getlist("files")
    if not files:
        flash("No files selected.", "error")
        return redirect(url_for('list_files', current_path=""))

    import zipfile
    import io

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for filepath in files:
            full_path = os.path.join(DOWNLOAD_FOLDER, filepath)
            if not os.path.abspath(full_path).startswith(
                    os.path.abspath(DOWNLOAD_FOLDER)):
                continue
            if os.path.exists(full_path) and os.path.isfile(full_path):
                arcname = os.path.basename(filepath)
                zip_file.write(full_path, arcname=arcname)

    zip_buffer.seek(0)
    return send_from_directory(
        os.path.dirname(DOWNLOAD_FOLDER),
        os.path.basename(DOWNLOAD_FOLDER) + "_batch.zip",
        as_attachment=True) if False else Response(
            zip_buffer.getvalue(),
            mimetype='application/zip',
            headers={
                "Content-Disposition":
                "attachment; filename=downloaded_files.zip"
            })


@app.route("/batch_encode")
def batch_encode():
    files = request.args.getlist("files[]")
    if not files:
        flash("No files selected.", "error")
        return redirect(url_for('list_files', current_path=""))

    codec = request.args.get("codec", "h265")
    pass_mode = request.args.get("pass_mode", "1-pass")
    preset = request.args.get("preset", "faster")
    bitrate = request.args.get("bitrate", "")
    crf = request.args.get("crf", "28")
    fps = request.args.get("fps", "")
    scale = request.args.get("scale", "")
    audio_bitrate = request.args.get("audio_bitrate", "96")
    force_stereo = request.args.get("force_stereo") == "true"
    aq_mode = request.args.get("aq_mode", "1")
    variance_boost = request.args.get("variance_boost", "1")
    tiles = request.args.get("tiles", "2x2")
    enable_vmaf = request.args.get("enable_vmaf") == "true"
    upload_pixeldrain = request.args.get("upload_pixeldrain") == "true"

    def batch_encode_worker():
        total = len(files)
        completed = 0
        for idx, filepath in enumerate(files, 1):
            full_path = os.path.join(DOWNLOAD_FOLDER, filepath)
            if not os.path.abspath(full_path).startswith(
                    os.path.abspath(DOWNLOAD_FOLDER)):
                continue
            if not os.path.exists(full_path) or not is_media_file(full_path):
                continue

            base, ext = os.path.splitext(full_path)
            output_filename = f"{os.path.basename(base)}_encoded.mkv"
            filename = os.path.basename(filepath)
            remaining = total - idx

            file_info = f"<div style='font-size: 18px; font-weight: bold; margin-bottom: 10px;'>📁 {filename}</div><div style='font-size: 14px;'><strong>✅ Passed:</strong> {completed} | <strong>⏳ Encoding:</strong> 1 | <strong>⏱️ Waiting:</strong> {remaining}</div>"
            progress_queue.put({
                "file_info": file_info,
                "batch_encode_status": True
            })
            progress_queue.put({
                "log":
                f"[BATCH {idx}/{total}] {filename} - Passed: {completed}, Waiting: {remaining}"
            })
            progress_queue.put(
                {"stage": f"📁 Batch: {filename} (File {idx}/{total})"})

            args = (full_path, output_filename, codec, preset, pass_mode,
                    bitrate, crf, audio_bitrate, fps, scale, force_stereo,
                    aq_mode, variance_boost, tiles, enable_vmaf,
                    progress_queue, upload_pixeldrain)
            try:
                encode_file(*args)
                completed += 1
            except Exception as e:
                progress_queue.put(
                    {"error": f"Error encoding {filepath}: {str(e)}"})

    start_task(batch_encode_worker, ())
    return render_template_string(
        FILE_OPERATION_TEMPLATE,
        operation_title=f"Batch Encoding {len(files)} file(s)...",
        download_started=True,
        current_path="")


@app.route("/batch_upload_pixeldrain", methods=["POST"])
def batch_upload_pixeldrain():
    files = request.form.getlist("files[]")
    if not files:
        flash("No files selected.", "error")
        return redirect(url_for('list_files', current_path=""))

    def batch_upload_worker():
        for filepath in files:
            full_path = os.path.join(DOWNLOAD_FOLDER, filepath)
            if not os.path.abspath(full_path).startswith(
                    os.path.abspath(DOWNLOAD_FOLDER)):
                continue
            if not os.path.exists(full_path):
                continue

            filename = os.path.basename(filepath)
            progress_queue.put({"log": f"Uploading: {filename}"})

            try:
                upload_to_pixeldrain(full_path, filename, progress_queue)
            except Exception as e:
                progress_queue.put(
                    {"error": f"Error uploading {filename}: {str(e)}"})

    start_task(batch_upload_worker, ())
    return render_template_string(
        FILE_OPERATION_TEMPLATE,
        operation_title=
        f"Batch Uploading {len(files)} file(s) to Pixeldrain...",
        download_started=True,
        current_path="")


@app.route("/upload_to_4stream", methods=["POST"])
def upload_to_4stream_file():
    filepath = request.form.get("filepath")
    if not filepath:
        flash("File path is missing.", "error")
        return redirect(url_for('list_files', current_path=""))
    current_path = os.path.dirname(filepath)
    if not os.path.exists(os.path.join(DOWNLOAD_FOLDER, filepath)):
        flash("File not found.", "error")
        return redirect(url_for('list_files', current_path=current_path))
    full_path = os.path.join(DOWNLOAD_FOLDER, filepath)
    filename = os.path.basename(filepath)
    args = (full_path, filename, progress_queue)
    start_task(upload_to_4stream, args)
    return render_template_string(
        FILE_OPERATION_TEMPLATE,
        operation_title=f"Uploading to 4stream: {filename}",
        download_started=True,
        current_path=current_path)


@app.route("/batch_upload_4stream", methods=["POST"])
def batch_upload_4stream():
    files = request.form.getlist("files[]")
    if not files:
        flash("No files selected.", "error")
        return redirect(url_for('list_files', current_path=""))

    def batch_upload_worker():
        for filepath in files:
            full_path = os.path.join(DOWNLOAD_FOLDER, filepath)
            if not os.path.abspath(full_path).startswith(
                    os.path.abspath(DOWNLOAD_FOLDER)):
                continue
            if not os.path.exists(full_path):
                continue

            filename = os.path.basename(filepath)
            progress_queue.put({"log": f"Uploading to 4stream: {filename}"})

            try:
                upload_to_4stream(full_path, filename, progress_queue)
            except Exception as e:
                progress_queue.put(
                    {"error": f"Error uploading {filename}: {str(e)}"})

    start_task(batch_upload_worker, ())
    return render_template_string(
        FILE_OPERATION_TEMPLATE,
        operation_title=f"Batch Uploading {len(files)} file(s) to 4stream...",
        download_started=True,
        current_path="")


@app.route("/upload_to_pixeldrain_alt", methods=["POST"])
def upload_to_pixeldrain_file_alt():
    filepath = request.form.get("filepath")
    if not filepath:
        flash("File path is missing.", "error")
        return redirect(url_for('list_files', current_path=""))
    current_path = os.path.dirname(filepath)
    if not os.path.exists(os.path.join(DOWNLOAD_FOLDER, filepath)):
        flash("File not found.", "error")
        return redirect(url_for('list_files', current_path=current_path))
    full_path = os.path.join(DOWNLOAD_FOLDER, filepath)
    filename = os.path.basename(filepath)
    args = (full_path, filename, progress_queue, PIXELDRAIN_API_KEY_ALT)
    start_task(upload_to_pixeldrain_alt, args)
    return render_template_string(
        FILE_OPERATION_TEMPLATE,
        operation_title=f"Uploading to Pixeldrain 2: {filename}",
        download_started=True,
        current_path=current_path)


@app.route("/batch_upload_pixeldrain_alt", methods=["POST"])
def batch_upload_pixeldrain_alt():
    files = request.form.getlist("files[]")
    if not files:
        flash("No files selected.", "error")
        return redirect(url_for('list_files', current_path=""))

    def batch_upload_worker():
        for filepath in files:
            full_path = os.path.join(DOWNLOAD_FOLDER, filepath)
            if not os.path.abspath(full_path).startswith(
                    os.path.abspath(DOWNLOAD_FOLDER)):
                continue
            if not os.path.exists(full_path):
                continue

            filename = os.path.basename(filepath)
            progress_queue.put(
                {"log": f"Uploading to Pixeldrain 2: {filename}"})

            try:
                upload_to_pixeldrain_alt(full_path, filename, progress_queue,
                                         PIXELDRAIN_API_KEY_ALT)
            except Exception as e:
                progress_queue.put(
                    {"error": f"Error uploading {filename}: {str(e)}"})

    start_task(batch_upload_worker, ())
    return render_template_string(
        FILE_OPERATION_TEMPLATE,
        operation_title=
        f"Batch Uploading {len(files)} file(s) to Pixeldrain 2...",
        download_started=True,
        current_path="")


@app.route("/upload_to_4stream_alt", methods=["POST"])
def upload_to_4stream_file_alt():
    filepath = request.form.get("filepath")
    if not filepath:
        flash("File path is missing.", "error")
        return redirect(url_for('list_files', current_path=""))
    current_path = os.path.dirname(filepath)
    if not os.path.exists(os.path.join(DOWNLOAD_FOLDER, filepath)):
        flash("File not found.", "error")
        return redirect(url_for('list_files', current_path=current_path))
    full_path = os.path.join(DOWNLOAD_FOLDER, filepath)
    filename = os.path.basename(filepath)
    args = (full_path, filename, progress_queue, UP4STREAM_API_KEY_ALT)
    start_task(upload_to_4stream_alt, args)
    return render_template_string(
        FILE_OPERATION_TEMPLATE,
        operation_title=f"Uploading to 4stream 2: {filename}",
        download_started=True,
        current_path=current_path)


@app.route("/batch_upload_4stream_alt", methods=["POST"])
def batch_upload_4stream_alt():
    files = request.form.getlist("files[]")
    if not files:
        flash("No files selected.", "error")
        return redirect(url_for('list_files', current_path=""))

    def batch_upload_worker():
        for filepath in files:
            full_path = os.path.join(DOWNLOAD_FOLDER, filepath)
            if not os.path.abspath(full_path).startswith(
                    os.path.abspath(DOWNLOAD_FOLDER)):
                continue
            if not os.path.exists(full_path):
                continue

            filename = os.path.basename(filepath)
            progress_queue.put({"log": f"Uploading to 4stream 2: {filename}"})

            try:
                upload_to_4stream_alt(full_path, filename, progress_queue,
                                      UP4STREAM_API_KEY_ALT)
            except Exception as e:
                progress_queue.put(
                    {"error": f"Error uploading {filename}: {str(e)}"})

    start_task(batch_upload_worker, ())
    return render_template_string(
        FILE_OPERATION_TEMPLATE,
        operation_title=f"Batch Uploading {len(files)} file(s) to 4stream 2...",
        download_started=True,
        current_path="")




@app.route("/upload_to_gofile", methods=["POST"])
@login_required
def upload_to_gofile_file():
    filepath = request.form.get("filepath")
    if not filepath:
        flash("File path is missing.", "error")
        return redirect(url_for('list_files', current_path=""))
    current_path = os.path.dirname(filepath)
    if not os.path.exists(os.path.join(DOWNLOAD_FOLDER, filepath)):
        flash("File not found.", "error")
        return redirect(url_for('list_files', current_path=current_path))
    full_path = os.path.join(DOWNLOAD_FOLDER, filepath)
    filename = os.path.basename(filepath)
    args = (full_path, filename, progress_queue)
    start_task(upload_to_gofile, args)
    return render_template_string(
        FILE_OPERATION_TEMPLATE,
        operation_title=f"Uploading to Gofile: {filename}",
        download_started=True,
        current_path=current_path)


@app.route("/batch_upload_gofile", methods=["POST"])
@login_required
def batch_upload_gofile():
    files = request.form.getlist("files[]")
    if not files:
        flash("No files selected.", "error")
        return redirect(url_for('list_files', current_path=""))

    def batch_upload_worker():
        for filepath in files:
            full_path = os.path.join(DOWNLOAD_FOLDER, filepath)
            if os.path.exists(full_path):
                filename = os.path.basename(filepath)
                upload_to_gofile(full_path, filename, progress_queue)

    start_task(batch_upload_worker, ())
    return render_template_string(
        FILE_OPERATION_TEMPLATE,
        operation_title=f"Batch Uploading {len(files)} files to Gofile",
        download_started=True,
        current_path="")



@app.route("/gofile_manager")
@login_required
def gofile_manager():
    # Load local history first
    history = []
    if os.path.exists(GOFILE_HISTORY_FILE):
        try:
            with open(GOFILE_HISTORY_FILE, 'r') as f:
                history = json.load(f)
        except: pass

    if not GOFILE_API_TOKEN:
        flash("Gofile API Token not configured", "error")
        return render_template_string(GOFILE_MANAGER_TEMPLATE, items=history)

    try:
        # Fetch the dynamic Website Token
        wt = get_gofile_website_token()
        headers = {
            "Authorization": f"Bearer {GOFILE_API_TOKEN}",
            "X-Website-Token": wt
        }

        # Try to fetch remote files
        # 1. Get account ID from token
        id_resp = requests.get(
            "https://api.gofile.io/accounts/getid",
            headers=headers,
            timeout=5
        )
        if id_resp.status_code == 200:
            account_id = id_resp.json()["data"]["id"]
            acc_resp = requests.get(
                f"https://api.gofile.io/accounts/{account_id}",
                headers=headers,
                timeout=5
            )
            if acc_resp.status_code == 200:
                root_folder_id = acc_resp.json()["data"]["rootFolder"]
                cont_resp = requests.get(
                    f"https://api.gofile.io/contents/{root_folder_id}",
                    headers=headers,
                    timeout=5
                )
                if cont_resp.status_code == 200:
                    cont_data = cont_resp.json()
                    if cont_data.get("status") == "ok":
                        children = cont_data["data"].get("children", {})
                        remote_items = []
                        for cid, info in children.items():
                            if info.get("type") == "file":
                                info['is_remote'] = True
                                remote_items.append(info)
                        
                        # Merge history and remote items (using links as keys to avoid duplicates)
                        existing_links = {item.get("link") for item in remote_items}
                        for h_item in history:
                            if h_item.get("link") not in existing_links:
                                h_item['is_remote'] = False
                                remote_items.append(h_item)
                        
                        remote_items.sort(key=lambda x: x.get("createTime", 0), reverse=True)
                        return render_template_string(GOFILE_MANAGER_TEMPLATE, items=remote_items)

    except Exception as e:
        print(f"Gofile remote sync failed: {e}")
        
    # Fallback to just history
    for item in history: item['is_remote'] = False
    return render_template_string(GOFILE_MANAGER_TEMPLATE, items=history)

@app.route("/gofile_manager/delete", methods=["POST"])
@login_required
def gofile_delete():
    content_id = request.form.get("contentId")
    if not content_id:
        flash("Content ID missing", "error")
        return redirect(url_for('gofile_manager'))
        
    try:
        # Gofile delete requires contentId and contentIds (plural) sometimes, or just a list
        resp = requests.delete(
            "https://api.gofile.io/deleteContent",
            json={"contentsId": [content_id]},
            headers={
                "Authorization": f"Bearer {GOFILE_API_TOKEN}",
                "X-Website-Token": get_gofile_website_token()
            },
            timeout=10
        )
        resp.raise_for_status()
        res = resp.json()
        
        if res.get("status") == "ok":
            flash("File deleted from Gofile successfully.", "success")
        else:
            flash(f"Gofile Delete Error: {res.get('status')}", "error")
            
    except Exception as e:
        flash(f"Failed to delete from Gofile: {str(e)}", "error")
        
    return redirect(url_for('gofile_manager'))


@app.route("/gofile_manager/add_to_local", methods=["POST"])
@login_required
def gofile_add_to_local():
    file_id = request.form.get("fileId")
    filename = request.form.get("filename")
    if not file_id or not filename:
        flash("Missing file information", "error")
        return redirect(url_for('gofile_manager'))
        
    # We download the file from Gofile to our local downloads folder
    # We need the direct download link. If Gofile doesn't provide it easily, 
    # we might need to use a trick or their specific download flow.
    # Usually, a file's public link is gofile.io/d/CODE. 
    # But for API, we might need to get the specific server link.
    
    try:
        # Use direct link (from history) if available
        # This bypasses Gofile's restricted folder API
        download_url = request.form.get("direct_link")
        
        if not download_url or download_url == "":
            # Fallback to landing page link
            download_url = request.form.get("link")
        
        if not download_url:
            # Last resort: try to fetch from API (likely to fail for non-premium)
            resp = requests.get(
                f"https://api.gofile.io/contents/{file_id}",
                headers={"Authorization": f"Bearer {GOFILE_API_TOKEN}"},
                timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "ok":
                    download_url = data["data"].get("link")
        
        if not download_url:
            flash("Could not retrieve file download link. Try opening the Gofile link directly in your browser.", "error")
            return redirect(url_for('gofile_manager'))

        # Log for debugging
        print(f"DEBUG: Restoring Gofile from URL: {download_url}")

        # Check if it's a direct Gofile download link (bypasses yt-dlp 401 errors)
        if ".gofile.io/download/direct/" in download_url or ".gofile.io/download/web/" in download_url:
            # Use Direct Download (requests) for direct links
            # Pass the landing page link as Referer to bypass Gofile protection
            landing_page = request.form.get("link") or "https://gofile.io/"
            args = (download_url, progress_queue, False, False, "", "", landing_page)
            start_task(download_file_directly, args)
        else:
            # Use YouTube Downloader logic (yt-dlp) for landing pages
            args = (
                download_url,    # url
                "b",             # video_id
                None,            # audio_id
                filename,        # filename
                "none",          # codec
                "", "", "", "", "", "", "", 
                False,           # force_stereo
                progress_queue,  # q
                False,           # is_muxed
                False,           # upload_pixeldrain
                False            # upload_gofile
            )
            start_task(download_and_convert, args)
        
        return render_template_string(
            FILE_OPERATION_TEMPLATE,
            operation_title=f"Restoring from Gofile: {filename}",
            download_started=True,
            current_path="")

    except Exception as e:
        flash(f"Error starting download: {str(e)}", "error")
        return redirect(url_for('gofile_manager'))

@app.route("/trim/<path:filepath>")
def trim_page(filepath):
    full_path = os.path.join(DOWNLOAD_FOLDER, filepath)
    current_path = os.path.dirname(filepath)
    if not os.path.exists(full_path) or not is_media_file(full_path):
        flash("File not found or not a media file.", "error")
        return redirect(url_for('list_files', current_path=current_path))
    duration = int(get_media_duration(full_path))
    base, ext = os.path.splitext(os.path.basename(filepath))
    suggested_filename = f"{base}_trimmed{ext}"
    suggested_output = os.path.join(current_path, suggested_filename)
    return render_template_string(TRIM_TEMPLATE,
                                  filepath=filepath,
                                  suggested_output=suggested_output,
                                  duration=duration,
                                  current_path=current_path)


@app.route("/trim/<path:filepath>", methods=["POST"])
def trim_file_post(filepath):
    full_input = os.path.join(DOWNLOAD_FOLDER, filepath)
    current_path = os.path.dirname(filepath)
    if not os.path.exists(full_input):
        flash("File not found.", "error")
        return redirect(url_for('list_files', current_path=current_path))
    output_filename = request.form.get("output_filename")
    if not output_filename:
        flash("Output filename is required.", "error")
        return redirect(url_for('trim_page', filepath=filepath))
    safe_output = get_safe_filename(output_filename)
    output_path = os.path.join(DOWNLOAD_FOLDER, safe_output)
    start_time = request.form.get("start_seconds")
    end_time = request.form.get("end_seconds")
    if not start_time or not end_time or int(start_time) >= int(end_time):
        flash("Invalid start/end times.", "error")
        return redirect(url_for('trim_page', filepath=filepath))
    try:
        trimmed, final_name = trim_video(full_input, output_path, start_time,
                                         end_time)
        if trimmed:
            output_dir = os.path.dirname(output_filename)
            session['last_trimmed_file'] = {
                'old':
                filepath,
                'new':
                os.path.join(output_dir, final_name)
                if output_dir else final_name
            }
    except Exception as e:
        flash(f"❌ Error trimming video: {str(e)}", "error")
    output_dir = os.path.dirname(output_filename)
    return redirect(
        url_for('list_files',
                current_path=output_dir if output_dir else current_path))


@app.route("/upload_direct_to_4stream", methods=["POST"])
def upload_direct_to_4stream():
    if 'file' not in request.files:
        flash("No file part.", "error")
        return redirect(url_for('index'))
    file = request.files['file']
    if not file.filename or file.filename == '':
        flash("No selected file.", "error")
        return redirect(url_for('index'))
    safe_name = secure_filename(file.filename)
    if not safe_name:
        safe_name = "upload"
    if not safe_name:
        flash("Invalid filename.", "error")
        return redirect(url_for('index'))
    temp_path = os.path.join(DOWNLOAD_FOLDER, safe_name)
    file.save(temp_path)
    args = (temp_path, safe_name, progress_queue)
    start_task(upload_to_4stream, args)
    return render_template_string(
        FILE_OPERATION_TEMPLATE,
        operation_title=f"Uploading to 4stream: {safe_name}",
        download_started=True,
        current_path="")


@app.route("/stop_encode", methods=["POST"])
def stop_encode():
    global current_process
    if current_process and current_process.poll() is None:
        try:
            # Force kill the process (SIGKILL) - don't just terminate
            current_process.kill()
            current_process.wait(timeout=2)
        except Exception as e:
            print(f"Error killing process: {e}")
        finally:
            current_process = None
            session.pop('task_active', None)
            session.modified = True
        return jsonify({
            "status": "success",
            "message": "Encoding process stopped."
        }), 200
    else:
        return jsonify({
            "status": "error",
            "message": "No active encoding process to stop."
        }), 400

@app.route("/stop_process", methods=["POST"])
def stop_process():
    # Alias for stop_encode to avoid 404s
    return stop_encode()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True)
