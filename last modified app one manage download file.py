#!/usr/bin/env python3
import subprocess
import sys
import os
import re
import json
import threading
import queue
import time
import shutil
from urllib.parse import quote, unquote

# Determine the absolute path to the yt-dlp executable within the venv
YT_DLP_PATH = os.path.join(os.path.dirname(sys.executable), 'yt-dlp')
if not os.path.exists(YT_DLP_PATH):
    # If not found in the venv bin, fall back to checking the system's PATH
    YT_DLP_PATH = "yt-dlp"

# Replit environment - dependencies installed via packager_tool
print("✅ ffmpeg:", "Available" if shutil.which("ffmpeg") else "NOT FOUND")
print(f"✅ yt-dlp executable:", "Available" if shutil.which(YT_DLP_PATH) else "NOT FOUND")
print("✅ Python packages: flask, yt-dlp, requests (pre-installed)")

from flask import Flask, render_template_string, request, send_from_directory, flash, url_for, Response, redirect, session
from werkzeug.utils import secure_filename
import requests
import yt_dlp
import os

# create app first
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET",
                                "replit-video-downloader-secret-key")

# Global for current encoding process
current_process = None

# -----------------------------
# Simple one-password protection
# -----------------------------
APP_PASSWORD = os.environ.get("APP_PASSWORD", "1234")  # default if not set


@app.before_request
def require_password():
    # Skip health route for UptimeRobot
    if request.path == "/health":
        return
    auth = request.authorization
    if not auth or auth.username != "admin" or auth.password != APP_PASSWORD:
        return Response("Authentication required", 401,
                        {"WWW-Authenticate": 'Basic realm="Login Required"'})


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
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

COOKIES_FILE = os.path.join(os.getcwd(), "youtube_cookies.txt")
PIXELDRAIN_API_KEY = os.environ.get(
    "PIXELDRAIN_API_KEY", "f28b77a0-9270-4a0f-b08d-c78c2cccad53")  # Replace with your key if you have one
print(f"📂 Downloads folder: {os.path.abspath(DOWNLOAD_FOLDER)}")
print(
    f"🍪 Cookies file: {'Exists' if os.path.exists(COOKIES_FILE) else 'Not found'}"
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
    <title>Video Downloader</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; background-color: #f4f4f9; color: #333; margin: 0; padding: 20px; }
        .container { max-width: 800px; margin: auto; background: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        h1, h2, h3 { color: #444; }
        hr { border: 0; border-top: 1px solid #ddd; margin: 20px 0; }
        input[type="text"], input[type="number"], select { width: 100%; padding: 8px; margin: 5px 0 15px; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; }
        input[type="file"] { margin-bottom: 15px; }
        button { background-color: #007bff; color: white; padding: 10px 15px; border: none; border-radius: 4px; cursor: pointer; font-size: 16px; margin-right: 5px; }
        button:hover { background-color: #0056b3; }
        button.delete { background-color: #dc3545; }
        button.delete:hover { background-color: #c82333; }
        button.upload { background-color: #17a2b8; }
        button.upload:hover { background-color: #138496; }
        button.encode { background-color: #28a745; }
        button.encode:hover { background-color: #218838; }
        button.rename { background-color: #ffc107; color: #212529; }
        button.rename:hover { background-color: #e0a800; }
        a { color: #007bff; text-decoration: none; }
        a:hover { text-decoration: underline; }
        pre { background-color: #eee; padding: 10px; border-radius: 4px; white-space: pre-wrap; word-wrap: break-word; }
        .flash-msg { padding: 10px; border-radius: 4px; margin-bottom: 15px; }
        .flash-success { background-color: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
        .flash-error { background-color: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
        .flash-info { background-color: #d1ecf1; color: #0c5460; border: 1px solid #bee5eb; }
        .progress-container { display: none; margin-top: 20px; }
        .progress-bar { width: 100%; background-color: #e9ecef; border-radius: 4px; }
        .progress-bar-inner { width: 0%; height: 24px; background-color: #28a745; text-align: center; line-height: 24px; color: white; border-radius: 4px; transition: width 0.4s ease; }
        #progress-log { margin-top: 10px; font-family: monospace; font-size: 12px; max-height: 200px; overflow-y: auto; background: #333; color: #fff; padding: 10px; border-radius: 4px; }
        .notification { position: fixed; top: 20px; right: 20px; padding: 15px 20px; border-radius: 8px; color: white; font-weight: bold; z-index: 10000; animation: slideIn 0.3s ease-out; }
        .notification.success { background-color: #28a745; }
        .notification.error { background-color: #dc3545; }
        .notification.info { background-color: #17a2b8; }
        @keyframes slideIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
    </style>
</head>
<body>
<div class="container">
    <h1>Video Downloader & Uploader</h1>
    <p>Powered by yt-dlp, FFmpeg & Pixeldrain(1)</p>
    {% with messages = get_flashed_messages(with_categories=true) %}
        {% if messages %}
            {% for category, message in messages %}
                <div class="flash-msg flash-{{ category }}">{{ message|safe }}</div>
            {% endfor %}
        {% endif %}
    {% endwith %}

    <div id="progress-container" class="progress-container">
        <h3 id="progress-stage">Starting...</h3>
        <div class="progress-bar">
            <div id="progress-bar-inner" class="progress-bar-inner">0%</div>
        </div>
        <pre id="progress-log"></pre>
        <button id="stop-button">Stop Encoding</button>
    </div>

    <h2>Advanced Download</h2>
    <form method="POST" action="{{ url_for('index') }}" id="download-form" onsubmit="return validateForm()">
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
            </select><br>
            <div id="encoding-options" style="display: {% if codec != 'none' %}block{% else %}none{% endif %};">
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
                <input type="number" name="crf" id="crf" value="{{ crf|default(28 if codec == 'h265' else 24) }}" min="0" max="63" step="1" placeholder="e.g., 28 for H.265, 24 for AV1"><br>

                <label>Audio Bitrate (kb/s):</label><br>
                <input type="number" name="audio_bitrate" id="audio_bitrate" value="{{ audio_bitrate|default('96') }}" min="32" max="512" step="8" placeholder="e.g., 64, 96, 128"><br>

                <label>Frame Rate (optional):</label><br>
                <select name="fps">
                    <option value="">Original</option>
                    <option value="24">24 fps</option>
                    <option value="30">30 fps</option>
                    <option value="60">60 fps</option>
                </select><br>

                <label><input type="checkbox" name="force_stereo" value="true"> Force Stereo (2-channel) Audio</label><br>

                <label>Adaptive Quantization Mode (AV1 only):</label><br>
                <select name="aq_mode" id="aq_mode">
                    <option value="0">Disabled</option>
                    <option value="1" selected>PSNR-based</option>
                    <option value="2">Variance-based</option>
                </select><br>

                <label>Variance Boost (AV1 only, 0–3):</label><br>
                <input type="number" name="variance_boost" id="variance_boost" value="1" min="0" max="3" step="1" placeholder="e.g., 1"><br>

                <label>Tiles (AV1 only, e.g., 2x2 for faster encoding):</label><br>
                <select name="tiles" id="tiles">
                    <option value="">None</option>
                    <option value="2x2" selected>2x2 (Recommended for 720p)</option>
                    <option value="4x4">4x4</option>
                </select><br>

                <label><input type="checkbox" name="enable_vmaf" value="true"> Compute VMAF Quality Score (slower)</label><br>
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
                    presetSelect.innerHTML = '';
                    if (codec === 'av1') {
                        for (let p = 0; p <= 13; p++) {
                            let label = p.toString();
                            if (p === 0) label += ' (slowest)';
                            else if (p === 13) label += ' (fastest)';
                            else if (p > 7) label += ' (fast)';
                            else label += ' (medium)';
                            const option = document.createElement('option');
                            option.value = p;
                            option.text = label;
                            if (p === 4) option.selected = true;
                            presetSelect.appendChild(option);
                        }
                        crfInput.value = crfInput.value || '24';
                        crfInput.placeholder = 'e.g., 24 for AV1';
                        aqModeSelect.disabled = false;
                        varianceBoostInput.disabled = false;
                        tilesSelect.disabled = false;
                    } else if (codec === 'h265') {
                        const presets = ['ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow', 'slower', 'veryslow', 'placebo'];
                        presets.forEach(p => {
                            const option = document.createElement('option');
                            option.value = p;
                            option.text = p;
                            if (p === 'faster') option.selected = true;
                            presetSelect.appendChild(option);
                        });
                        crfInput.value = crfInput.value || '28';
                        crfInput.placeholder = 'e.g., 28 for H.265';
                        aqModeSelect.disabled = true;
                        varianceBoostInput.disabled = true;
                        tilesSelect.disabled = true;
                    } else {
                        aqModeSelect.disabled = true;
                        varianceBoostInput.disabled = true;
                        tilesSelect.disabled = true;
                    }
                    const encodingOptions = document.getElementById('encoding-options');
                    encodingOptions.style.display = codec !== 'none' ? 'block' : 'none';

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
                    if (codec !== 'none') {
                        if (!presetSelect.value) {
                            alert('Please select a preset.');
                            return false;
                        }
                        if (passModeSelect.value === '2-pass' && (!bitrateInput.value || parseInt(bitrateInput.value) < 100)) {
                            alert('Please specify a valid video bitrate (minimum 100) for 2-pass encoding.');
                            return false;
                        }
                        if (codec === 'av1') {
                            const varianceBoost = parseInt(varianceBoostInput.value);
                            if (isNaN(varianceBoost) || varianceBoost < 0 || varianceBoost > 3) {
                                alert('Variance Boost must be between 0 and 3.');
                                return false;
                            }
                        }
                    }
                    return true;
                }

                codecSelect.addEventListener('change', updatePresetOptions);
                passModeSelect.addEventListener('change', function() {
                    if (codecSelect.value !== 'none') {
                        if (this.value === '2-pass') {
                            bitrateInput.setAttribute('required', 'required');
                        } else {
                            bitrateInput.removeAttribute('required');
                        }
                    }
                });

                document.addEventListener('DOMContentLoaded', updatePresetOptions);
            </script>
            <br>
            <label><input type="checkbox" name="upload_pixeldrain" value="true"> Upload to Pixeldrain after completion</label><br><br>
            <button type="submit" name="action" value="download">Download & Convert</button>
            <h3>Available Formats (Raw):</h3>
            <pre>{{ formats }}</pre>
        {% endif %}
    </form>

    <hr>

    <h2>Direct URL Download</h2>
    <form method="POST" action="{{ url_for('index') }}">
        <label>URL (Video, Playlist, or any direct file):</label><br>
        <input type="text" name="direct_url" size="80" required><br>
        <label><input type="checkbox" name="upload_pixeldrain_direct" value="true"> Upload to Pixeldrain after download</label><br>
        <button type="submit" name="action" value="direct_download">Download to Server</button>
        <button type="submit" name="action" value="direct_upload_pixeldrain" class="upload">Upload to Pixeldrain</button>
    </form>

    <hr>

    <h2>Upload File to Pixeldrain</h2>
    <form method="POST" action="{{ url_for('upload_direct') }}" enctype="multipart/form-data">
        <label>Select a file from your computer:</label><br>
        <input type="file" name="file" required><br>
        <button type="submit" class="upload">Upload Directly</button>
    </form>

    <hr>

    <h2>Manual Format Merge</h2>
    <p>Fetch formats from a URL, then manually provide the Video and Audio IDs to merge into an MKV file.</p>
    <form method="POST" action="{{ url_for('index') }}">
        <label>Page URL:</label><br>
        <input type="text" name="manual_url" size="80" value="{{ manual_url }}" required><br>
        <button type="submit" name="action" value="manual_fetch">Fetch Formats</button><br><br>

        {% if manual_formats_raw %}
            <input type="hidden" name="manual_url" value="{{ manual_url }}">
            <h3>Available Formats (Raw):</h3>
            <pre>{{ manual_formats_raw }}</pre>

            <label>Video ID:</label><br>
            <input type="text" name="manual_video_id" required placeholder="Enter the ID of the video stream"><br>

            <label>Audio ID (optional):</label><br>
            <input type="text" name="manual_audio_id" placeholder="Enter ID of audio stream (leave blank for video-only)"><br>

            <label>Filename (will be saved as .mkv):</label><br>
            <input type="text" name="manual_filename" value="{{ manual_filename }}" required><br><br>

            <button type="submit" name="action" value="manual_merge">Merge & Download</button>
        {% endif %}
    </form>

    <hr>
    <p><a href="{{ url_for('list_files') }}">📂 Manage Downloaded Files</a></p>
</div>

<script>
    function showNotification(message, type = 'info') {
        const notification = document.createElement('div');
        notification.className = `notification ${type}`;
        notification.textContent = message;
        document.body.appendChild(notification);

        setTimeout(() => {
            notification.style.opacity = '0';
            setTimeout(() => {
                document.body.removeChild(notification);
            }, 300);
        }, 3000);
    }

    document.addEventListener("DOMContentLoaded", function() {
        {% if download_started %}
            const progressContainer = document.getElementById('progress-container');
            const stage = document.getElementById('progress-stage');
            const progressBar = document.getElementById('progress-bar-inner');
            const log = document.getElementById('progress-log');

            progressContainer.style.display = 'block';

            const eventSource = new EventSource("{{ url_for('progress_stream') }}");
            let finalUrl = null; // Variable to store the final URL from the upload

            eventSource.onmessage = function(event) {
                try {
                    const data = JSON.parse(event.data);

                    // Store the final URL if the server sends it
                    if (data.final_url) {
                        finalUrl = data.final_url;
                    }

                    if (data.log && data.log === 'DONE') {
                        eventSource.close();
                        stage.textContent = '✅ Completed!';
                        progressBar.style.backgroundColor = '#28a745';
                        log.innerHTML += "\\n\\nOperation finished. Redirecting...";

                        let redirectTarget = "{{ url_for('list_files') }}";
                        // If we have a final URL, redirect to a special endpoint to set the session flash message
                        if (finalUrl) {
                            redirectTarget = "{{ url_for('operation_complete') }}?url=" + encodeURIComponent(finalUrl);
                        }

                        setTimeout(() => { window.location.href = redirectTarget; }, 2000);
                        return;
                    }

                    if (data.error) {
                        eventSource.close();
                        stage.textContent = '❌ Error!';
                        progressBar.style.backgroundColor = '#dc3545';
                        log.innerHTML += `\\n\\nERROR: ${data.error}`;
                        showNotification('Operation failed: ' + data.error, 'error');
                        return;
                    }

                    if (data.stage) {
                        stage.textContent = data.stage;
                    }
                    if (data.percent) {
                        progressBar.style.width = data.percent + '%';
                        progressBar.textContent = data.percent.toFixed(1) + '%';
                    }
                    if (data.log) {
                        log.innerHTML += data.log + '\\n';
                        log.scrollTop = log.scrollHeight;
                    }
                } catch (e) {
                    console.error('Error parsing SSE data:', e);
                }
            };

            eventSource.onerror = function(err) {
                stage.textContent = 'Connection error. Please refresh.';
                eventSource.close();
                console.error('SSE error:', err);
            };

            document.getElementById('stop-button').addEventListener('click', function() {
                fetch('/stop_encode', {method: 'POST'}).then(response => {
                    if (response.ok) {
                        stage.textContent = 'Encoding stopped.';
                        progressBar.style.backgroundColor = '#dc3545';
                        eventSource.close();
                    }
                });
            });
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
    <title>Encode Video</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; background-color: #f4f4f9; color: #333; margin: 0; padding: 20px; }
        .container { max-width: 800px; margin: auto; background: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        h1, h2, h3 { color: #444; }
        hr { border: 0; border-top: 1px solid #ddd; margin: 20px 0; }
        input[type="text"], input[type="number"], select { width: 100%; padding: 8px; margin: 5px 0 15px; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; }
        button { background-color: #007bff; color: white; padding: 10px 15px; border: none; border-radius: 4px; cursor: pointer; font-size: 16px; }
        button:hover { background-color: #0056b3; }
        a { color: #007bff; text-decoration: none; }
        a:hover { text-decoration: underline; }
        .flash-msg { padding: 10px; border-radius: 4px; margin-bottom: 15px; }
        .flash-success { background-color: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
        .flash-error { background-color: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
        .progress-container { display: none; margin-top: 20px; }
        .progress-bar { width: 100%; background-color: #e9ecef; border-radius: 4px; }
        .progress-bar-inner { width: 0%; height: 24px; background-color: #28a745; text-align: center; line-height: 24px; color: white; border-radius: 4px; transition: width 0.4s ease; }
        #progress-log { margin-top: 10px; font-family: monospace; font-size: 12px; max-height: 200px; overflow-y: auto; background: #333; color: #fff; padding: 10px; border-radius: 4px; }
    </style>
</head>
<body>
<div class="container">
    <h1>Encode Video: {{ filepath }}</h1>
    {% with messages = get_flashed_messages(with_categories=true) %}
        {% if messages %}
            {% for category, message in messages %}
                <div class="flash-msg flash-{{ category }}">{{ message|safe }}</div>
            {% endfor %}
        {% endif %}
    {% endwith %}

    <div id="progress-container" class="progress-container">
        <h3 id="progress-stage">Starting...</h3>
        <div class="progress-bar">
            <div id="progress-bar-inner" class="progress-bar-inner">0%</div>
        </div>
        <pre id="progress-log"></pre>
        <button id="stop-button">Stop Encoding</button>
    </div>

    <form method="POST" onsubmit="return validateEncodeForm()">
        <label>Output Filename (relative to downloads folder):</label><br>
        <input type="text" name="output_filename" value="{{ suggested_output }}" required><br>

        <label>Codec:</label><br>
        <select name="codec" id="codec" required>
            <option value="none" {% if codec == "none" %}selected{% endif %}>No Encoding (Copy)</option>
            <option value="h265" {% if codec == "h265" %}selected{% endif %}>Encode to H.265 (x265)</option>
            <option value="av1" {% if codec == "av1" %}selected{% endif %}>Encode to AV1 (SVT-AV1)</option>
        </select><br>

        <div id="encoding-options" style="display: {% if codec != 'none' %}block{% else %}none{% endif %};">
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
            <input type="number" name="crf" id="crf" value="{{ crf|default(28 if codec == 'h265' else 24) }}" min="0" max="63" step="1" placeholder="e.g., 28 for H.265, 24 for AV1"><br>

            <label>Audio Bitrate (kb/s):</label><br>
            <input type="number" name="audio_bitrate" id="audio_bitrate" value="{{ audio_bitrate|default('96') }}" min="32" max="512" step="8" placeholder="e.g., 64, 96, 128"><br>

            <label>Frame Rate (optional):</label><br>
            <select name="fps">
                <option value="">Original</option>
                <option value="24">24 fps</option>
                <option value="30">30 fps</option>
                <option value="60">60 fps</option>
            </select><br>

            <label><input type="checkbox" name="force_stereo" value="true"> Force Stereo (2-channel) Audio</label><br>

            <label>Adaptive Quantization Mode (AV1 only):</label><br>
            <select name="aq_mode" id="aq_mode">
                <option value="0">Disabled</option>
                <option value="1" selected>PSNR-based</option>
                <option value="2">Variance-based</option>
            </select><br>

            <label>Variance Boost (AV1 only, 0–3):</label><br>
            <input type="number" name="variance_boost" id="variance_boost" value="1" min="0" max="3" step="1" placeholder="e.g., 1"><br>

            <label>Tiles (AV1 only, e.g., 2x2 for faster encoding):</label><br>
            <select name="tiles" id="tiles">
                <option value="">None</option>
                <option value="2x2" selected>2x2 (Recommended for 720p)</option>
                <option value="4x4">4x4</option>
            </select><br>

            <label><input type="checkbox" name="enable_vmaf" value="true"> Compute VMAF Quality Score (slower)</label><br>
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
                presetSelect.innerHTML = '';
                if (codec === 'av1') {
                    for (let p = 0; p <= 13; p++) {
                        let label = p.toString();
                        if (p === 0) label += ' (slowest)';
                        else if (p === 13) label += ' (fastest)';
                        else if (p > 7) label += ' (fast)';
                        else label += ' (medium)';
                        const option = document.createElement('option');
                        option.value = p;
                        option.text = label;
                        if (p === 4) option.selected = true;
                        presetSelect.appendChild(option);
                    }
                    crfInput.value = crfInput.value || '24';
                    crfInput.placeholder = 'e.g., 24 for AV1';
                    aqModeSelect.disabled = false;
                    varianceBoostInput.disabled = false;
                    tilesSelect.disabled = false;
                } else if (codec === 'h265') {
                    const presets = ['ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow', 'slower', 'veryslow', 'placebo'];
                    presets.forEach(p => {
                        const option = document.createElement('option');
                        option.value = p;
                        option.text = p;
                        if (p === 'faster') option.selected = true;
                        presetSelect.appendChild(option);
                    });
                    crfInput.value = crfInput.value || '28';
                    crfInput.placeholder = 'e.g., 28 for H.265';
                    aqModeSelect.disabled = true;
                    varianceBoostInput.disabled = true;
                    tilesSelect.disabled = true;
                } else {
                    aqModeSelect.disabled = true;
                    varianceBoostInput.disabled = true;
                    tilesSelect.disabled = true;
                }
                const encodingOptions = document.getElementById('encoding-options');
                encodingOptions.style.display = codec !== 'none' ? 'block' : 'none';

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

            function validateEncodeForm() {
                const codec = codecSelect.value;
                if (codec !== 'none') {
                    if (!presetSelect.value) {
                        alert('Please select a preset.');
                        return false;
                    }
                    if (passModeSelect.value === '2-pass' && (!bitrateInput.value || parseInt(bitrateInput.value) < 100)) {
                        alert('Please specify a valid video bitrate (minimum 100) for 2-pass encoding.');
                        return false;
                    }
                    if (codec === 'av1') {
                        const varianceBoost = parseInt(varianceBoostInput.value);
                        if (isNaN(varianceBoost) || varianceBoost < 0 || varianceBoost > 3) {
                            alert('Variance Boost must be between 0 and 3.');
                            return false;
                        }
                    }
                }
                return true;
            }

            codecSelect.addEventListener('change', updatePresetOptions);
            passModeSelect.addEventListener('change', function() {
                if (codecSelect.value !== 'none') {
                    if (this.value === '2-pass') {
                        bitrateInput.setAttribute('required', 'required');
                    } else {
                        bitrateInput.removeAttribute('required');
                    }
                }
            });

            document.addEventListener('DOMContentLoaded', updatePresetOptions);
        </script>

        <br>
        <label><input type="checkbox" name="upload_pixeldrain" value="true"> Upload to Pixeldrain after completion</label><br><br>
        <button type="submit">Start Encoding</button>
        <a href="{{ url_for('list_files') }}">Back to Files</a>
    </form>
</div>

<script>
    document.addEventListener("DOMContentLoaded", function() {
        {% if download_started %}
            const progressContainer = document.getElementById('progress-container');
            const stage = document.getElementById('progress-stage');
            const progressBar = document.getElementById('progress-bar-inner');
            const log = document.getElementById('progress-log');

            progressContainer.style.display = 'block';

            const eventSource = new EventSource("{{ url_for('progress_stream') }}");
            let finalUrl = null; // Variable to store the final URL

            eventSource.onmessage = function(event) {
                try {
                    const data = JSON.parse(event.data);

                    if (data.final_url) {
                        finalUrl = data.final_url;
                    }

                    if (data.log && data.log === 'DONE') {
                        eventSource.close();
                        stage.textContent = '✅ Completed!';
                        progressBar.style.backgroundColor = '#28a745';
                        log.innerHTML += "\\n\\nOperation finished. Redirecting...";

                        let redirectTarget = "{{ url_for('list_files') }}";
                        if (finalUrl) {
                            redirectTarget = "{{ url_for('operation_complete') }}?url=" + encodeURIComponent(finalUrl);
                        } 

                        setTimeout(() => { window.location.href = redirectTarget; }, 2000);
                        return;
                    }

                    if (data.error) {
                        eventSource.close();
                        stage.textContent = '❌ Error!';
                        progressBar.style.backgroundColor = '#dc3545';
                        log.innerHTML += `\\n\\nERROR: ${data.error}`;
                        return;
                    }

                    if (data.stage) {
                        stage.textContent = data.stage;
                    }
                    if (data.percent) {
                        progressBar.style.width = data.percent + '%';
                        progressBar.textContent = data.percent.toFixed(1) + '%';
                    }
                    if (data.log) {
                        log.innerHTML += data.log + '\\n';
                        log.scrollTop = log.scrollHeight;
                    }
                } catch (e) {
                    console.error('Error parsing SSE data:', e);
                }
            };

            eventSource.onerror = function(err) {
                stage.textContent = 'Connection error. Please refresh.';
                eventSource.close();
                console.error('SSE error:', err);
            };

            document.getElementById('stop-button').addEventListener('click', function() {
                fetch('/stop_encode', {method: 'POST'}).then(response => {
                    if (response.ok) {
                        stage.textContent = 'Encoding stopped.';
                        progressBar.style.backgroundColor = '#dc3545';
                        eventSource.close();
                    }
                });
            });
        {% endif %}
    });
</script>
</body>
</html>
"""

TRIM_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Trim Video</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; background-color: #f4f4f9; color: #333; margin: 0; padding: 20px; }
        .container { max-width: 800px; margin: auto; background: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        h1, h2, h3 { color: #444; }
        input[type="text"] { width: 100%; padding: 8px; margin: 5px 0 15px; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; }
        button { background-color: #007bff; color: white; padding: 10px 15px; border: none; border-radius: 4px; cursor: pointer; font-size: 16px; }
        button:hover { background-color: #0056b3; }
        a { color: #007bff; text-decoration: none; }
        a:hover { text-decoration: underline; }
        .flash-msg { padding: 10px; border-radius: 4px; margin-bottom: 15px; }
        .flash-success { background-color: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
        .flash-error { background-color: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
        .double_range_slider_box { position: relative; width: 100%; height: 100px; display: flex; justify-content: center; align-items: center; }
        .double_range_slider { width: 90%; height: 10px; position: relative; background-color: #dddddd; border-radius: 20px; }
        .range_track { height: 100%; position: absolute; border-radius: 20px; background-color: #95d564; }
        .minvalue { position: absolute; padding: 6px 15px; background: #0e5f59; border-radius: 1rem; color: white; bottom: 0; transform: translate(0, -100%); left: 0; font-size: 1rem; transition: left 0.3s cubic-bezier(0.165, 0.84, 0.44, 1); will-change: left, transform; }
        .maxvalue { position: absolute; padding: 6px 15px; background: #0e5f59; border-radius: 1rem; color: white; top: 0; transform: translate(0, 100%); right: 0; font-size: 1rem; transition: right 0.3s cubic-bezier(0.165, 0.84, 0.44, 1); will-change: right, transform; }
        input[type="range"] { position: absolute; width: 100%; height: 5px; background: none; pointer-events: none; -webkit-appearance: none; -moz-appearance: none; top: 50%; transform: translateY(-50%); }
        input[type="range"]::-webkit-slider-thumb { height: 25px; width: 25px; border-radius: 50%; border: 3px solid #cbffa3; background-color: #95d564; pointer-events: auto; -webkit-appearance: none; cursor: pointer; margin-bottom: 1px; }
        input[type="range"]::-moz-range-thumb { height: 18px; width: 18px; border-radius: 50%; border: 3px solid #cbffa3; background-color: #95d564; pointer-events: auto; -moz-appearance: none; cursor: pointer; margin-top: 30%; }
    </style>
</head>
<body>
<div class="container">
    <h1>Trim Video: {{ filepath }}</h1>
    {% with messages = get_flashed_messages(with_categories=true) %}
        {% if messages %}
            {% for category, message in messages %}
                <div class="flash-msg flash-{{ category }}">{{ message|safe }}</div>
            {% endfor %}
        {% endif %}
    {% endwith %}

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
        <button type="submit">Trim Video (No Re-encoding)</button>
        <a href="{{ url_for('list_files') }}">Back to Files</a>
    </form>
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

    const minRangeFill = () => {
        range.style.left = (rangeInput[0].value / rangeInput[0].max) * 100 + "%";
    };
    const maxRangeFill = () => {
        range.style.right = 100 - (rangeInput[1].value / rangeInput[1].max) * 100 + "%";
    };
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

    const setMinValueOutput = () => {
        minRange = parseInt(rangeInput[0].value);
        minval.innerHTML = formatTime(rangeInput[0].value);
    };
    const setMaxValueOutput = () => {
        maxRange = parseInt(rangeInput[1].value);
        maxval.innerHTML = formatTime(rangeInput[1].value);
    };

    setMinValueOutput();
    setMaxValueOutput();
    minRangeFill();
    maxRangeFill();
    MinValueBubbleStyle();
    MaxValueBubbleStyle();

    rangeInput.forEach((input) => {
        input.addEventListener("input", (e) => {
            setMinValueOutput();
            setMaxValueOutput();

            minRangeFill();
            maxRangeFill();

            MinValueBubbleStyle();
            MaxValueBubbleStyle();

            if (maxRange - minRange < minRangeValueGap) {
                if (e.target.className === "min") {
                    rangeInput[0].value = maxRange - minRangeValueGap;
                    setMinValueOutput();
                    minRangeFill();
                    MinValueBubbleStyle();
                    e.target.style.zIndex = "2";
                } else {
                    rangeInput[1].value = minRange + minRangeValueGap;
                    e.target.style.zIndex = "2";
                    setMaxValueOutput();
                    maxRangeFill();
                    MaxValueBubbleStyle();
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
    <title>Processing...</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; background-color: #f4f4f9; color: #333; margin: 0; padding: 20px; }
        .container { max-width: 800px; margin: auto; background: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        h1, h2, h3 { color: #444; }
        pre { background-color: #eee; padding: 10px; border-radius: 4px; white-space: pre-wrap; word-wrap: break-word; }
        .progress-container { display: block; margin-top: 20px; }
        .progress-bar { width: 100%; background-color: #e9ecef; border-radius: 4px; }
        .progress-bar-inner { width: 0%; height: 24px; background-color: #17a2b8; text-align: center; line-height: 24px; color: white; border-radius: 4px; transition: width 0.4s ease; }
        #progress-log { margin-top: 10px; font-family: monospace; font-size: 12px; max-height: 200px; overflow-y: auto; background: #333; color: #fff; padding: 10px; border-radius: 4px; }
    </style>
</head>
<body>
<div class="container">
    <h1>{{ operation_title }}</h1>
    <p>Please wait while the operation completes. You will be redirected automatically.</p>
    <div id="progress-container" class="progress-container">
        <h3 id="progress-stage">Starting...</h3>
        <div class="progress-bar">
            <div id="progress-bar-inner" class="progress-bar-inner" style="background-color: #17a2b8;">0%</div>
        </div>
        <pre id="progress-log"></pre>
    </div>
</div>

<script>
    document.addEventListener("DOMContentLoaded", function() {
        {% if download_started %}
            const stage = document.getElementById('progress-stage');
            const progressBar = document.getElementById('progress-bar-inner');
            const log = document.getElementById('progress-log');

            const eventSource = new EventSource("{{ url_for('progress_stream') }}");
            let finalUrl = null; // To store the final URL from the upload

            eventSource.onmessage = function(event) {
                try {
                    const data = JSON.parse(event.data);

                    if (data.final_url) {
                        finalUrl = data.final_url;
                    }

                    if (data.log && data.log === 'DONE') {
                        eventSource.close();
                        stage.textContent = '✅ Completed!';
                        progressBar.style.backgroundColor = '#28a745';
                        log.innerHTML += "\\n\\nOperation finished. Redirecting...";

                        let redirectTarget = "{{ url_for('list_files') }}";
                        if (finalUrl) {
                            redirectTarget = "{{ url_for('operation_complete') }}?url=" + encodeURIComponent(finalUrl);
                        }

                        setTimeout(() => { window.location.href = redirectTarget; }, 2000);
                        return;
                    }

                    if (data.error) {
                        eventSource.close();
                        stage.textContent = '❌ Error!';
                        progressBar.style.backgroundColor = '#dc3545';
                        log.innerHTML += `\\n\\nERROR: ${data.error}`;
                        return;
                    }

                    if (data.stage) {
                        stage.textContent = data.stage;
                    }
                    if (data.percent) {
                        progressBar.style.width = data.percent + '%';
                        progressBar.textContent = data.percent.toFixed(1) + '%';
                    }
                    if (data.log) {
                        log.innerHTML += data.log + '\\n';
                        log.scrollTop = log.scrollHeight;
                    }
                } catch (e) {
                    console.error('Error parsing SSE data:', e);
                }
            };

            eventSource.onerror = function(err) {
                stage.textContent = 'Connection error. Please refresh.';
                eventSource.close();
                console.error('SSE error:', err);
            };
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
    if size_bytes is None or size_bytes == 0:
        return "0 B"
    power = 1024
    n = 0
    power_labels = {0: '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size_bytes >= power and n < len(power_labels) - 1:
        size_bytes /= power
        n += 1
    return f"{size_bytes:.1f} {power_labels[n]}iB"


def get_safe_filename(name):
    """Sanitizes a string to be a valid filename component, allowing slashes for paths."""
    parts = name.split('/')
    safe_parts = [re.sub(r'[\\*?:"<>|]', "_", part) for part in parts]
    safe_parts = [re.sub(r'\s+', ' ', part).strip() for part in safe_parts]
    return '/'.join(safe_parts)


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


def get_media_info(file_path):
    """Fetches media information using ffprobe."""
    try:
        command = [
            "ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams",
            "-show_format", file_path
        ]
        result = subprocess.check_output(command, stderr=subprocess.STDOUT)
        data = json.loads(result)

        info = {}

        video_stream = next((s for s in data.get('streams', [])
                             if s.get('codec_type') == 'video'), None)
        audio_stream = next((s for s in data.get('streams', [])
                             if s.get('codec_type') == 'audio'), None)

        if video_stream:
            info['video_codec'] = video_stream.get('codec_name', 'N/A')
            fr_str = video_stream.get('avg_frame_rate', '0/1')
            if '/' in fr_str and fr_str != '0/1':
                num, den = map(int, fr_str.split('/'))
                info['video_fps'] = f"{num / den:.2f}" if den else '0.00'
            else:
                info['video_fps'] = 'N/A'

            v_br = video_stream.get('bit_rate')
            if not v_br and 'format' in data:
                v_br = data['format'].get('bit_rate')
            info[
                'video_bitrate'] = f"{int(v_br) // 1000} kbps" if v_br else 'N/A'

        if audio_stream:
            info['audio_codec'] = audio_stream.get('codec_name', 'N/A')
            a_br = audio_stream.get('bit_rate')
            info[
                'audio_bitrate'] = f"{int(a_br) // 1000} kbps" if a_br else 'N/A'

        return info
    except (subprocess.CalledProcessError, FileNotFoundError,
            json.JSONDecodeError, KeyError) as e:
        print(f"Error fetching media info for {file_path}: {e}")
        return {"error": "Could not retrieve media information."}


def get_media_duration(file_path):
    if not is_media_file(file_path): return 0
    try:
        cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
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
            "ffprobe", "-v", "error", "-select_streams", "a:0",
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
        cmd = [
            "ffmpeg", "-y", "-i", input_path, "-ss", start_time, "-to",
            end_time, "-c", "copy", output_path
        ]
        subprocess.run(cmd,
                       check=True,
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        return True
    except subprocess.CalledProcessError as e:
        raise Exception(f"FFmpeg error: {e.returncode}")
    except Exception as e:
        raise Exception(f"Trim failed: {str(e)}")


def fetch_formats(url):
    try:
        ydl_opts = {'quiet': True}
        if os.path.exists(COOKIES_FILE):
            ydl_opts['cookiefile'] = COOKIES_FILE
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
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
        ydl_opts = {'quiet': True}
        if os.path.exists(COOKIES_FILE): ydl_opts['cookiefile'] = COOKIES_FILE
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        title = info.get('title', 'download').strip()
        # First, sanitize the title by removing invalid characters.
        safe_title = re.sub(r'[\\/*?:"<>|]', '_', title)
        # Then, use the sanitized title in the f-string.
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


def encode_file(input_path,
                output_filename,
                codec,
                preset,
                pass_mode,
                bitrate,
                crf,
                audio_bitrate,
                fps,
                force_stereo,
                aq_mode,
                variance_boost,
                tiles,
                enable_vmaf,
                q,
                upload_pixeldrain=False):
    global current_process
    safe_output = get_safe_filename(output_filename)
    output_path = os.path.join(DOWNLOAD_FOLDER, safe_output)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    if os.path.exists(output_path): os.remove(output_path)

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
            ffmpeg_cmd = ["ffmpeg", "-y", "-i", input_path]
            video_codec = "libx265" if codec == "h265" else "libsvtav1"

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
                crf_val = int(crf) if crf else (28 if codec == 'h265' else 24)
                ffmpeg_cmd.extend([
                    "-c:v", video_codec, "-preset", preset, "-crf",
                    str(crf_val)
                ])

            if fps: ffmpeg_cmd.extend(["-r", fps])
            audio_bitrate_val = int(audio_bitrate) if audio_bitrate else 96
            audio_channels = 2 if force_stereo else get_audio_channels(
                input_path)
            ffmpeg_cmd.extend([
                "-ac",
                str(audio_channels), "-c:a", "libopus", "-b:a",
                f"{audio_bitrate_val}k"
            ])

            # ================================================================= #
            # START: CORRECTED CODE BLOCK FOR AV1 PARAMETERS                    #
            # ================================================================= #
            if codec == 'av1':
                # SVT-AV1 parameters must be passed as a single colon-separated string.
                # The parameter for variance boost is 'variance-boost-strength'.
                svt_params = [
                    f"aq-mode={aq_mode}",
                    f"variance-boost-strength={variance_boost}"
                ]
                if tiles and 'x' in tiles:
                    # Convert user-friendly '2x2' tile format to what SVT-AV1 expects
                    try:
                        rows_str, cols_str = tiles.split('x')
                        rows = int(rows_str)
                        cols = int(cols_str)
                        # Calculate the log base 2, which corresponds to tile-rows/tile-columns
                        tile_rows = rows.bit_length() - 1 if rows > 0 else 0
                        tile_columns = cols.bit_length() - 1 if cols > 0 else 0
                        svt_params.append(f"tile-rows={tile_rows}")
                        svt_params.append(f"tile-columns={tile_columns}")
                    except ValueError:
                        # Log a warning if parsing fails, but don't stop the encode
                        q.put({
                            "log":
                            f"Warning: Could not parse tiles setting '{tiles}'. Ignoring."
                        })

                ffmpeg_cmd.extend(["-svtav1-params", ":".join(svt_params)])
            # =============================================================== #
            # END: CORRECTED CODE BLOCK                                       #
            # =============================================================== #

            if enable_vmaf:
                ffmpeg_cmd.extend(["-lavfi", "libvmaf", "-f", "null"])

            ffmpeg_cmd.append(output_path)

            current_process = subprocess.Popen(ffmpeg_cmd,
                                               stdout=subprocess.PIPE,
                                               stderr=subprocess.STDOUT,
                                               universal_newlines=True,
                                               encoding='utf-8',
                                               errors='ignore')
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

    except Exception as e:
        q.put({"error": str(e)})
    finally:
        current_process = None
        q.put({"log": "DONE"})


def download_file_directly(url, q, upload_pixeldrain_direct=False):
    try:
        while not q.empty():
            q.get()
        q.put({"stage": "Starting direct download...", "percent": 0})
        with requests.get(url,
                          stream=True,
                          allow_redirects=True,
                          headers={'User-Agent': 'Mozilla/5.0'}) as r:
            r.raise_for_status()
            filename = "direct_download"
            cd_header = r.headers.get('content-disposition')
            if cd_header:
                match = re.search(r"filename\*=([^']*)''([^;]*)",
                                  cd_header) or re.search(
                                      r'filename="?([^"]+)"?', cd_header)
                if match: filename = unquote(match.group(1))
            if filename == "direct_download":
                filename_from_url = url.split('/')[-1].split('?')[0]
                if filename_from_url: filename = unquote(filename_from_url)

            safe_name = get_safe_filename(filename)
            final_path = os.path.join(DOWNLOAD_FOLDER, safe_name)
            total_size = int(r.headers.get('content-length', 0))
            downloaded_size = 0
            os.makedirs(os.path.dirname(final_path), exist_ok=True)
            with open(final_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded_size += len(chunk)
                    if total_size > 0:
                        q.put({
                            "stage": "Downloading...",
                            "percent": (downloaded_size / total_size) * 100
                        })
        q.put({"stage": "✅ Download complete!", "percent": 100})
        if upload_pixeldrain_direct:
            upload_to_pixeldrain(final_path, safe_name, q)
    except Exception as e:
        q.put({"error": f"Direct download failed: {str(e)}"})
    finally:
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
                         force_stereo,
                         q,
                         is_muxed,
                         upload_pixeldrain=False,
                         aq_mode="1",
                         variance_boost="1",
                         tiles="2x2",
                         enable_vmaf=False):
    global current_process
    safe_name = get_safe_filename(filename)
    base_name, _ = os.path.splitext(safe_name)
    final_path = os.path.join(DOWNLOAD_FOLDER, safe_name)
    tmp_path_template = os.path.join(DOWNLOAD_FOLDER, base_name + ".part")

    try:
        while not q.empty():
            q.get()
        q.put({"stage": "Initializing download...", "percent": 0})
        yt_formats = f"{video_id}+{audio_id}" if audio_id else (
            video_id if is_muxed else f"{video_id}+bestaudio")
        yt_dlp_cmd = [
            YT_DLP_PATH, "-f", yt_formats, "-o", tmp_path_template,
            "--merge-output-format", "mkv", url
        ]
        if os.path.exists(COOKIES_FILE):
            yt_dlp_cmd.extend(["--cookies", COOKIES_FILE])
        run_command_with_progress(yt_dlp_cmd, "Downloading with yt-dlp...", q)
        q.put({"stage": "Download Complete", "percent": 100})

        found_files = [
            f for f in os.listdir(DOWNLOAD_FOLDER)
            if f.startswith(os.path.basename(tmp_path_template))
        ]
        if not found_files:
            raise FileNotFoundError("yt-dlp did not create the expected file.")
        actual_tmp_path = os.path.join(DOWNLOAD_FOLDER, found_files[0])

        if codec == "none":
            if os.path.exists(final_path): os.remove(final_path)
            os.rename(actual_tmp_path, final_path)
            q.put({"stage": "✅ Done!", "log": "File saved without encoding."})
        else:
            final_path = os.path.join(DOWNLOAD_FOLDER, base_name + ".mkv")
            # Collect all encode_file arguments from the current function's scope
            encode_options = {
                'input_path': actual_tmp_path,
                'output_filename': os.path.basename(final_path),
                'codec': codec,
                'preset': preset,
                'pass_mode': pass_mode,
                'bitrate': bitrate,
                'crf': crf,
                'audio_bitrate': audio_bitrate,
                'fps': fps,
                'force_stereo': force_stereo,
                'aq_mode': aq_mode,
                'variance_boost': variance_boost,
                'tiles': tiles,
                'enable_vmaf': enable_vmaf
            }
            encode_file(
                **encode_options, q=q,
                upload_pixeldrain=False)  # Upload is handled after this call

        # After any potential encoding, check for uploads
        if upload_pixeldrain:
            # Check which path is the final one
            path_to_upload = final_path if os.path.exists(
                final_path) else actual_tmp_path
            upload_to_pixeldrain(path_to_upload,
                                 os.path.basename(path_to_upload), q)
        else:
            q.put({"log": "DONE"
                   })  # Make sure to signal DONE if no upload is happening

    except Exception as e:
        q.put({"error": str(e)})
    finally:
        if 'actual_tmp_path' in locals() and os.path.exists(actual_tmp_path):
            try:
                os.remove(actual_tmp_path)
            except OSError:
                pass
        # The encode_file or upload_to_pixeldrain functions now handle sending the final "DONE"
        # q.put({"log": "DONE"}) # This can be redundant now


def manual_merge_worker(url, video_id, audio_id, filename, q):
    """Worker to download and merge streams using manually provided IDs."""
    safe_name = get_safe_filename(filename)
    base_name, _ = os.path.splitext(safe_name)
    final_path = os.path.join(DOWNLOAD_FOLDER, base_name + ".mkv")

    try:
        while not q.empty():
            q.get()
        q.put({"stage": "Initializing manual download...", "percent": 0})

        video_id_clean = video_id.strip()
        audio_id_clean = audio_id.strip() if audio_id else ""

        if audio_id_clean:
            format_selector = f"{video_id_clean}+{audio_id_clean}"
        else:
            format_selector = video_id_clean

        yt_dlp_cmd = [
            YT_DLP_PATH, "-f", format_selector, "-o", final_path,
            "--merge-output-format", "mkv", url
        ]
        if os.path.exists(COOKIES_FILE):
            yt_dlp_cmd.extend(["--cookies", COOKIES_FILE])

        run_command_with_progress(yt_dlp_cmd,
                                  "Downloading & Merging with yt-dlp...", q)

        q.put({"stage": "✅ Download Complete!", "percent": 100})
    except Exception as e:
        q.put({"error": str(e)})
    finally:
        q.put({"log": "DONE"})


# -----------------------------
# Flask Routes
# -----------------------------
@app.route("/")
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
                                  manual_filename="")


@app.route("/", methods=["POST"])
def index_post():
    action = request.form.get("action")
    form_data = {
        "url": request.form.get("url", "").strip(),
        "download_started": False,
        "manual_url": request.form.get("manual_url", "").strip(),
        "manual_formats_raw": None,
        "manual_filename": ""
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

    task_thread = None
    if action in [
            "download", "direct_download", "direct_upload_pixeldrain",
            "manual_merge"
    ]:
        form_data["download_started"] = True
        if action == "download":
            form_data.update({
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
            # This is complex and might not be available at this stage, so let's simplify
            # Re-fetch minimal format info to determine if it's muxed. A bit inefficient but safer.
            is_muxed = False
            try:
                _, vformats, _ = fetch_formats(request.form.get("url"))
                selected_video_format = next(
                    (f for f in vformats
                     if f['id'] == request.form.get("video_id")), None)
                if selected_video_format:
                    is_muxed = selected_video_format.get('is_muxed', False)
            except Exception:
                pass  # Default to false if fetch fails

            task_thread = threading.Thread(
                target=download_and_convert,
                args=(request.form.get("url"), request.form.get("video_id"),
                      request.form.get("audio_id"),
                      request.form.get("filename"), request.form.get("codec"),
                      request.form.get("preset"),
                      request.form.get("pass_mode"),
                      request.form.get("bitrate"), request.form.get("crf"),
                      request.form.get("audio_bitrate"),
                      request.form.get("fps"),
                      request.form.get("force_stereo") == "true",
                      progress_queue, is_muxed,
                      request.form.get("upload_pixeldrain") == "true",
                      request.form.get("aq_mode", "1"),
                      request.form.get("variance_boost",
                                       "1"), request.form.get("tiles", "2x2"),
                      request.form.get("enable_vmaf") == "true"))
        elif action == "direct_download":
            task_thread = threading.Thread(
                target=download_file_directly,
                args=(request.form.get("direct_url"), progress_queue,
                      request.form.get("upload_pixeldrain_direct") == "true"))
        elif action == "direct_upload_pixeldrain":
            task_thread = threading.Thread(
                target=upload_file_directly_to_pixeldrain,
                args=(request.form.get("direct_url"), progress_queue))
        elif action == "manual_merge":
            task_thread = threading.Thread(
                target=manual_merge_worker,
                args=(request.form.get("manual_url"),
                      request.form.get("manual_video_id"),
                      request.form.get("manual_audio_id"),
                      request.form.get("manual_filename"), progress_queue))

        if task_thread:
            task_thread.daemon = True
            task_thread.start()

    return render_template_string(TEMPLATE, **form_data)


@app.route("/progress")
def progress_stream():

    def generate():
        while True:
            try:
                # get() will wait until an item is available
                msg = progress_queue.get()
                yield f"data: {json.dumps(msg)}\n\n"
            except GeneratorExit:
                # Client disconnected
                break
            except Exception as e:
                print(f"Error in SSE generator: {e}")
                break

    # Create the response object
    response = Response(generate(), mimetype="text/event-stream")
    
    # Add headers to instruct proxies not to buffer or modify the response
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    response.headers['Content-Encoding'] = 'identity' # Added this line

    return response


@app.route("/upload_direct", methods=["POST"])
def upload_direct():
    if 'file' in request.files and request.files['file'].filename:
        file = request.files['file']
        filename = secure_filename(file.filename)
        file.save(os.path.join(DOWNLOAD_FOLDER, filename))

        thread = threading.Thread(target=upload_to_pixeldrain,
                                  args=(os.path.join(DOWNLOAD_FOLDER,
                                                     filename), filename,
                                        progress_queue))
        thread.daemon = True
        thread.start()

        return render_template_string(FILE_OPERATION_TEMPLATE,
                                      operation_title=f"Uploading: {filename}",
                                      download_started=True)

    flash("No file selected", "error")
    return redirect(url_for('index'))


@app.route("/upload_local", methods=["POST"])
def upload_local():
    if 'file' in request.files and request.files['file'].filename:
        file = request.files['file']
        filename = secure_filename(file.filename)
        file_path = os.path.join(DOWNLOAD_FOLDER, filename)
        if os.path.exists(file_path):
            flash(f"A file named '{filename}' already exists.", "error")
        else:
            file.save(file_path)
            session['last_local_upload'] = filename
        return redirect(url_for('list_files'))
    flash("No file selected for uploading.", "error")
    return redirect(url_for('list_files'))


@app.route("/files")
def list_files():
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

    all_items = []
    # Using os.walk to get all files and directories recursively
    for root, dirs, files in os.walk(DOWNLOAD_FOLDER, topdown=True):
        # Sort directories and files alphabetically
        dirs.sort(key=str.lower)
        files.sort(key=str.lower)

        # Add directories to the list
        for name in dirs:
            full_path = os.path.join(root, name)
            relative_path = os.path.relpath(full_path, DOWNLOAD_FOLDER)
            all_items.append({
                'display_path':
                relative_path.replace(os.sep, ' / ') + '/',
                'path':
                relative_path,
                'size':
                '-',
                'is_media':
                False,
                'is_folder':
                True,
                'mtime':
                os.path.getmtime(full_path)
            })

        # Add files to the list
        for name in files:
            full_path = os.path.join(root, name)
            relative_path = os.path.relpath(full_path, DOWNLOAD_FOLDER)
            all_items.append({
                'display_path':
                relative_path.replace(os.sep, ' / '),
                'path':
                relative_path,
                'size':
                get_file_size(full_path),
                'is_media':
                is_media_file(full_path),
                'is_folder':
                False,
                'mtime':
                os.path.getmtime(full_path)
            })

    # Sort the final list by path to group items logically
    all_items.sort(key=lambda x: x['mtime'], reverse=True)

    return render_template_string("""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Downloaded Files</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; background-color: #f4f4f9; color: #333; margin: 0; padding: 20px; }
        .container { max-width: 1000px; margin: auto; background: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        h1, h3 { color: #444; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #ddd; word-break: break-all; }
        th { background-color: #f2f2f2; }
        tr:hover { background-color: #f9f9f9; }
        td.cell-path { font-family: monospace; }
        td.cell-path b { color: #0056b3; }
        a { color: #007bff; text-decoration: none; margin-right: 5px; }
        a:hover { text-decoration: underline; }
        button { background-color: #007bff; color: white; padding: 5px 10px; border: none; border-radius: 4px; cursor: pointer; margin-right: 5px; font-size: 14px; }
        button:hover { background-color: #0056b3; }
        button.delete { background-color: #dc3545; }
        button.delete:hover { background-color: #c82333; }
        button.upload { background-color: #17a2b8; }
        button.upload:hover { background-color: #138496; }
        button.encode { background-color: #28a745; }
        button.encode:hover { background-color: #218838; }
        button.rename { background-color: #ffc107; color: #212529; }
        button.rename:hover { background-color: #e0a800; }
        button.info { background-color: #0dcaf0; color: #000; }
        button.info:hover { background-color: #0cb9d7; }
        button:disabled { background-color: #6c757d; cursor: not-allowed; }
        .actions { white-space: nowrap; }
        .modal { display: none; position: fixed; z-index: 1000; left: 0; top: 0; width: 100%; height: 100%; background-color: rgba(0,0,0,0.5); }
        .modal-content { background-color: #fff; margin: 15% auto; padding: 20px; border-radius: 8px; width: 500px; max-width: 90%; }
        .modal-content input { width: 100%; padding: 8px; margin: 10px 0; box-sizing: border-box; }
        .modal-content pre { background-color: #eee; font-family: monospace; padding: 10px; border-radius: 4px; }
        .flash-msg { padding: 10px; border-radius: 4px; margin-bottom: 15px; }
        .flash-success { background-color: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
        .flash-error { background-color: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
        a.trim { background-color: #6f42c1; color: white; padding: 5px 10px; border-radius: 4px; }
        a.trim:hover { background-color: #5a32a3; }
    </style>
</head>
<body>
<div class="container">
    <h1>Downloaded Files</h1>
    <p><a href="{{ url_for('index') }}">← Back to Downloader</a></p>

    {% with messages = get_flashed_messages(with_categories=true) %}
        {% for category, message in messages %}
            <div class="flash-msg flash-{{ category }}">{{ message|safe }}</div>
        {% endfor %}
    {% endwith %}

    <div style="border: 1px solid #ddd; padding: 20px; border-radius: 8px; margin-top: 20px; margin-bottom: 20px;">
        <h3>Upload New File to This List(1) (( <a href="https://u.pcloud.link/publink/show?code=kZMQ3n5ZSnXgcgvdcOufntHqQNgVVyxCSPVX" target="_blank">pCloud Link</a>  <a href="https://www.dropbox.com/scl/fo/pbj4suf64bx3dy6823q4m/AH7sEbQozX07lWHDHE_8sgo?rlkey=52gqevdguucjpj6o0tyxzqlih&st=y3gaq1n7&dl=0" target="_blank">Dropbox Link</a> </h3>
        <form method="POST" action="{{ url_for('upload_local') }}" enctype="multipart/form-data">
            <input type="file" name="file" required>
            <button type="submit" style="margin-top: 10px;">Upload File</button>
        </form>
    </div>

    {% if items %}
        <table>
            <thead><tr><th>Path</th><th>Size</th><th>Actions</th></tr></thead>
            <tbody>
                {% for item in items %}
                <tr>
                    <td class="cell-path">
                        {% if item.is_folder %}
                            <b>📁 {{ item.display_path }}</b>
                        {% else %}
                            📄 {{ item.display_path }}
                        {% endif %}
                    </td>
                    <td>{{ item.size }}</td>
                    <td class="actions">
                        {% if not item.is_folder %}
                            <a href="{{ url_for('download_file', filepath=item.path) }}">Download</a>
                        {% endif %}
                        <button onclick="showRenameModal('{{ item.path }}')" class="rename">Rename</button>
                        {% if not item.is_folder %}
                            <form method="POST" action="{{ url_for('upload_to_pixeldrain_file') }}" style="display:inline;">
                                <input type="hidden" name="filepath" value="{{ item.path }}">
                                <button type="submit" class="upload">Upload to Pixeldrain</button>
                            </form>
                            {% if item.is_media %}
                                <a href="{{ url_for('encode_page', filepath=item.path) }}" class="encode">Encode</a>
                                <a href="{{ url_for('trim_page', filepath=item.path) }}" class="trim">Trim</a>
                                <button type="button" onclick="showInfoModal('{{ item.path }}')" class="info">Info</button>
                            {% endif %}
                        {% endif %}
                        <form method="POST" action="{{ url_for('delete_file', filepath=item.path) }}" style="display:inline;">
                            <button type="submit" class="delete" onclick="return confirm('Are you sure you want to delete \'{{ item.display_path }}\'? This cannot be undone.')">Delete</button>
                        </form>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    {% else %}
        <p>No files downloaded yet.</p>
    {% endif %}
</div>

<div id="renameModal" class="modal"><div class="modal-content">
    <h3>Rename File or Folder</h3>
    <p>Current path: <strong id="currentName"></strong></p>
    <label>New path (relative to downloads folder):</label>
    <input type="text" id="newName" placeholder="Enter new path">
    <button onclick="confirmRename()">Rename</button>
    <button onclick="closeRenameModal()">Cancel</button>
</div></div>

<div id="infoModal" class="modal">
    <div class="modal-content">
        <h3>Media Information</h3>
        <p><strong>File:</strong> <span id="infoFilename"></span></p>
        <pre id="infoContent"></pre>
        <button type="button" onclick="closeInfoModal()">Close</button>
    </div>
</div>

<script>
    let currentFile = '';
    function showRenameModal(filepath) {
        currentFile = filepath;
        document.getElementById('currentName').textContent = filepath;
        document.getElementById('newName').value = filepath;
        document.getElementById('renameModal').style.display = 'block';
        document.getElementById('newName').focus();
    }
    function closeRenameModal() { document.getElementById('renameModal').style.display = 'none'; }
    function confirmRename() {
        const newName = document.getElementById('newName').value.trim();
        if (newName && newName !== currentFile) {
            const form = document.createElement('form');
            form.method = 'POST'; form.action = '{{ url_for("rename_file") }}';
            const oldInput = document.createElement('input'); oldInput.type = 'hidden'; oldInput.name = 'old_name'; oldInput.value = currentFile;
            const newInput = document.createElement('input'); newInput.type = 'hidden'; newInput.name = 'new_name'; newInput.value = newName;
            form.append(oldInput, newInput);
            document.body.appendChild(form); form.submit();
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
        fetch(`/info/${filepath}`)
            .then(response => { if (!response.ok) { throw new Error('Network response was not ok'); } return response.json(); })
            .then(data => {
                if (data.error) {
                    content.textContent = `Error: ${data.error}`;
                    return;
                }
                let infoText = '';
                infoText += `Video Codec:    ${data.video_codec || 'N/A'}\n`;
                infoText += `Frame Rate:     ${data.video_fps || 'N/A'} fps\n`;
                infoText += `Video Bitrate:  ${data.video_bitrate || 'N/A'}\n\n`;
                infoText += `Audio Codec:    ${data.audio_codec || 'N/A'}\n`;
                infoText += `Audio Bitrate: ${data.audio_bitrate || 'N/A'}`;
                content.textContent = infoText;
            })
            .catch(error => {
                content.textContent = 'Failed to fetch media information.';
                console.error('Error:', error);
            });
    }
    function closeInfoModal() { document.getElementById('infoModal').style.display = 'none'; }

    window.onclick = (event) => {
        if (event.target == document.getElementById('renameModal')) closeRenameModal();
        if (event.target == document.getElementById('infoModal')) closeInfoModal();
    };
</script>
</body></html>
    """,
                                  items=all_items)


@app.route("/operation_complete")
def operation_complete():
    url = request.args.get('url')
    if url:
        session['last_upload_url'] = url
    return redirect(url_for('list_files'))


@app.route("/download/<path:filepath>")
def download_file(filepath):
    # Security: send_from_directory is safe against path traversal.
    return send_from_directory(DOWNLOAD_FOLDER, filepath, as_attachment=True)


@app.route("/info/<path:filepath>")
def get_info(filepath):
    """API endpoint to get media info for a file."""
    from flask import jsonify
    full_path = os.path.join(DOWNLOAD_FOLDER, filepath)
    if not os.path.abspath(full_path).startswith(
            os.path.abspath(DOWNLOAD_FOLDER)):
        return jsonify({"error": "Invalid file path"}), 400
    if not os.path.exists(full_path):
        return jsonify({"error": "File not found"}), 404

    info = get_media_info(full_path)
    if "error" in info:
        return jsonify(info), 500

    return jsonify(info)


@app.route("/delete/<path:filepath>", methods=["POST"])
def delete_file(filepath):
    # Construct the full path and perform security checks
    full_path = os.path.join(DOWNLOAD_FOLDER, filepath)
    if not os.path.abspath(full_path).startswith(
            os.path.abspath(DOWNLOAD_FOLDER)):
        flash("Invalid path specified.", "error")
        return redirect(url_for('list_files'))

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

    return redirect(url_for('list_files'))


@app.route("/rename", methods=["POST"])
def rename_file():
    old_rel_path = request.form.get("old_name")
    new_rel_path = request.form.get("new_name")

    if not all([old_rel_path, new_rel_path]):
        flash("Both old and new paths are required.", "error")
        return redirect(url_for('list_files'))

    # Sanitize the new path
    new_rel_path = get_safe_filename(new_rel_path.strip('/'))

    old_path = os.path.join(DOWNLOAD_FOLDER, old_rel_path)
    new_path = os.path.join(DOWNLOAD_FOLDER, new_rel_path)

    # Security checks to prevent path traversal
    if not os.path.abspath(old_path).startswith(os.path.abspath(DOWNLOAD_FOLDER)) or \
       not os.path.abspath(new_path).startswith(os.path.abspath(DOWNLOAD_FOLDER)):
        flash("Invalid path specified.", "error")
        return redirect(url_for('list_files'))

    if not os.path.exists(old_path):
        flash(f"Item not found: {old_rel_path}", "error")
    elif os.path.exists(new_path):
        flash(f"An item named '{new_rel_path}' already exists.", "error")
    else:
        try:
            # Ensure the parent directory for the new path exists
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            os.rename(old_path, new_path)
            session['last_renamed_file'] = {
                'old': old_rel_path,
                'new': new_rel_path
            }
        except Exception as e:
            flash(f"Error renaming item: {str(e)}", "error")

    return redirect(url_for('list_files'))


@app.route("/upload_to_pixeldrain", methods=["POST"])
def upload_to_pixeldrain_file():
    filepath = request.form.get("filepath")
    if not filepath or not os.path.exists(
            os.path.join(DOWNLOAD_FOLDER, filepath)):
        flash("File not found or path is missing.", "error")
        return redirect(url_for('list_files'))

    full_path = os.path.join(DOWNLOAD_FOLDER, filepath)
    filename = os.path.basename(filepath)

    thread = threading.Thread(target=upload_to_pixeldrain,
                              args=(full_path, filename, progress_queue))
    thread.daemon = True
    thread.start()

    return render_template_string(FILE_OPERATION_TEMPLATE,
                                  operation_title=f"Uploading: {filename}",
                                  download_started=True)


@app.route("/encode/<path:filepath>")
def encode_page(filepath):
    file_path = os.path.join(DOWNLOAD_FOLDER, filepath)
    if not os.path.exists(file_path):
        flash("File not found.", "error")
        return redirect(url_for('list_files'))
    if not is_media_file(file_path):
        flash("This file type cannot be encoded.", "error")
        return redirect(url_for('list_files'))

    base, _ = os.path.splitext(filepath)
    suggested_output = f"{base}_encoded.mkv"

    return render_template_string(ENCODE_TEMPLATE,
                                  filepath=filepath,
                                  suggested_output=suggested_output,
                                  download_started=False)


@app.route("/encode/<path:filepath>", methods=["POST"])
def encode_file_post(filepath):
    file_path = os.path.join(DOWNLOAD_FOLDER, filepath)
    if not os.path.exists(file_path):
        flash("File not found.", "error")
        return redirect(url_for('list_files'))

    thread = threading.Thread(
        target=encode_file,
        args=(
            file_path,
            request.form.get("output_filename"),
            request.form.get("codec"),
            request.form.get("preset"),
            request.form.get("pass_mode"),
            request.form.get("bitrate"),
            request.form.get("crf"),
            request.form.get("audio_bitrate"),
            request.form.get("fps"),
            request.form.get("force_stereo") == "true",
            request.form.get("aq_mode", "1"),  # Default to PSNR-based
            request.form.get("variance_boost", "1"),  # Default to 1
            request.form.get("tiles", "2x2"),  # Default to 2x2
            request.form.get("enable_vmaf") == "true",
            progress_queue,
            request.form.get("upload_pixeldrain") == "true"))
    thread.daemon = True
    thread.start()

    return render_template_string(
        ENCODE_TEMPLATE,
        filepath=filepath,
        suggested_output=request.form.get("output_filename"),
        codec=request.form.get("codec"),
        pass_mode=request.form.get("pass_mode"),
        bitrate=request.form.get("bitrate"),
        crf=request.form.get("crf"),
        audio_bitrate=request.form.get("audio_bitrate"),
        download_started=True)


@app.route("/trim/<path:filepath>")
def trim_page(filepath):
    full_path = os.path.join(DOWNLOAD_FOLDER, filepath)
    if not os.path.exists(full_path) or not is_media_file(full_path):
        flash("File not found or not a media file.", "error")
        return redirect(url_for('list_files'))
    duration = int(get_media_duration(full_path))
    suggested_output = f"{os.path.splitext(filepath)[0]}_trimmed.mkv"
    return render_template_string(TRIM_TEMPLATE,
                                  filepath=filepath,
                                  suggested_output=suggested_output,
                                  duration=duration)


@app.route("/trim/<path:filepath>", methods=["POST"])
def trim_file_post(filepath):
    full_input = os.path.join(DOWNLOAD_FOLDER, filepath)
    if not os.path.exists(full_input):
        flash("File not found.", "error")
        return redirect(url_for('list_files'))

    output_filename = request.form.get("output_filename")
    safe_output = get_safe_filename(output_filename)
    output_path = os.path.join(DOWNLOAD_FOLDER, safe_output)
    start_time = request.form.get("start_seconds")
    end_time = request.form.get("end_seconds")

    if not start_time or not end_time or int(start_time) >= int(end_time):
        flash("Invalid start and end times. Start must be less than end.",
              "error")
        return redirect(url_for('trim_page', filepath=filepath))

    if os.path.exists(output_path):
        flash(
            f"Output file '{safe_output}' already exists. Please choose a different name.",
            "error")
        return redirect(url_for('trim_page', filepath=filepath))

    try:
        trim_video(full_input, output_path, start_time, end_time)
        session['last_trimmed_file'] = {'old': filepath, 'new': safe_output}
    except Exception as e:
        flash(f"❌ Error trimming video: {str(e)}", "error")

    return redirect(url_for('list_files'))


@app.route("/stop_encode", methods=["POST"])
def stop_encode():
    global current_process
    if current_process and current_process.poll() is None:
        current_process.terminate()
        flash("Encoding stopped. Partial file saved.", "info")
    else:
        flash("No active encoding to stop.", "error")
    return redirect(request.referrer or url_for('list_files'))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True)