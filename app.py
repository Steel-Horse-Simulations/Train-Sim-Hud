# TSW Hud [v0.1.0] [main] [main]
# Last modified: 2025-01-31

import os
import json
import requests
import socket
from pathlib import Path
from flask import Flask, jsonify, send_from_directory, request

app = Flask(__name__)
APP_DIR = os.path.dirname(os.path.abspath(__file__))
PAGES_DIR = os.path.join(APP_DIR, 'pages')
CONFIG_FILE = os.path.join(APP_DIR, 'config.json')
DEFAULT_API_BASE = 'http://127.0.0.1:31270'

def load_config():
    try:
        return json.load(open(CONFIG_FILE))
    except:
        return {}

def save_config(cfg):
    json.dump(cfg, open(CONFIG_FILE, 'w'), indent=2)

CONFIG = load_config()

def read_api_key():
    """Find API key in config folder or common locations."""
    folder = CONFIG.get('config_folder', '')
    
    # Try configured folder first
    if folder:
        for name in ['CommAPIKey.txt', 'DTGCommKey.txt']:
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                key = open(path).read().strip()
                if key:
                    return key, path
    
    # Try common locations
    common_paths = [
        os.path.expandvars(r'%USERPROFILE%\Documents\My Games\TrainSimWorld6\Saved\Config'),
        os.path.expandvars(r'%USERPROFILE%\Documents\My Games\TrainSimWorld5\Saved\Config'),
        os.path.expandvars(r'%USERPROFILE%\Documents\My Games\TrainSimWorld4\Saved\Config'),
        os.path.expandvars(r'%USERPROFILE%\Documents\My Games\TrainSimWorld3\Saved\Config'),
        os.path.expandvars(r'%USERPROFILE%\Documents\My Games\TrainSimWorld2\Saved\Config'),
    ]
    
    for folder in common_paths:
        for name in ['CommAPIKey.txt', 'DTGCommKey.txt']:
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                key = open(path).read().strip()
                if key:
                    return key, path
    
    return None, None

def api_headers():
    """Get headers for TSW API with key."""
    key, _ = read_api_key()
    return {'DTGCommKey': key} if key else None

def api_get(path, timeout=(2, 3)):
    """Proxy GET request to TSW API."""
    headers = api_headers()
    if not headers:
        return {'error': 'no_key'}, 400
    
    base = CONFIG.get('api_base', DEFAULT_API_BASE).replace('localhost', '127.0.0.1')
    try:
        r = requests.get(f'{base}/{path.lstrip("/")}', headers=headers, timeout=timeout)
        return r.json(), r.status_code
    except Exception as e:
        return {'error': str(e)}, 503

def get_local_ip():
    """Get local machine IP for network display."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return '127.0.0.1'

@app.route('/')
def index():
    return send_from_directory(PAGES_DIR, 'settings.html')

@app.route('/pages/<path:filename>')
def pages(filename):
    return send_from_directory(PAGES_DIR, filename)

@app.route('/api/version')
def get_version():
    try:
        with open(os.path.join(APP_DIR, 'version.json')) as f:
            version_data = json.load(f)
        return jsonify({'version': version_data.get('version', 'Unknown')})
    except:
        return jsonify({'version': 'Unknown'})

@app.route('/api/config', methods=['GET'])
def get_config():
    key, key_path = read_api_key()
    return jsonify({
        'config_folder': CONFIG.get('config_folder', ''),
        'key_found': bool(key),
        'key_file_path': key_path or ''
    })

@app.route('/api/config', methods=['POST'])
def set_config():
    body = request.get_json(force=True) or {}
    if 'config_folder' in body:
        CONFIG['config_folder'] = body['config_folder']
    save_config(CONFIG)
    return jsonify({'ok': True})

@app.route('/api/autodetect', methods=['POST'])
def autodetect():
    key, key_path = read_api_key()
    if key:
        # Save the folder we found it in
        if key_path:
            folder = os.path.dirname(key_path)
            CONFIG['config_folder'] = folder
            save_config(CONFIG)
        return jsonify({'ok': True, 'key_file_path': key_path})
    return jsonify({'ok': False})

@app.route('/api/check-game')
def check_game():
    """Check if TSW game is running and API is accessible."""
    try:
        headers = api_headers()
        if not headers:
            return jsonify({'game_running': False})
        
        base = CONFIG.get('api_base', DEFAULT_API_BASE).replace('localhost', '127.0.0.1')
        r = requests.get(f'{base}/info', headers=headers, timeout=(1, 2))
        return jsonify({'game_running': r.status_code == 200})
    except:
        return jsonify({'game_running': False})

@app.route('/api/network')
def get_network():
    """Get local network address for remote connections."""
    ip = get_local_ip()
    port = os.environ.get('PORT', '5000')
    return jsonify({
        'ip': ip,
        'port': port
    })

@app.route('/api/proxy/get/<path:subpath>')
def proxy_get(subpath):
    """Proxy API GET requests."""
    body, status = api_get(f'get/{subpath}')
    return jsonify(body), status

@app.route('/api/proxy/list/<path:subpath>')
def proxy_list(subpath):
    """Proxy API LIST requests."""
    body, status = api_get(f'list/{subpath}')
    return jsonify(body), status

@app.route('/api/proxy/list')
def proxy_list_root():
    """Proxy API LIST requests for root."""
    body, status = api_get('list')
    return jsonify(body), status

@app.route('/api/pages')
def api_pages():
    return send_from_directory(PAGES_DIR, 'registry.json')

@app.route('/api/shutdown', methods=['POST'])
def shutdown():
    """Shutdown the app."""
    import sys
    sys.exit(0)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
