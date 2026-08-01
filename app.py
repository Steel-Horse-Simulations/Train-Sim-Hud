# TSW Hud [v1.0.3] [main] [main]
# Last modified: 2025-08-01

import os
import json
import requests
import socket
import time
from pathlib import Path
from flask import Flask, jsonify, send_from_directory, request

app = Flask(__name__)
APP_DIR = os.path.dirname(os.path.abspath(__file__))
PAGES_DIR = os.path.join(APP_DIR, 'pages')
CONFIG_FILE = os.path.join(APP_DIR, 'config.json')
DEFAULT_API_BASE = 'http://127.0.0.1:31270'

SESSION = requests.Session()
LOCO_CACHE = {}
LOCO_CACHE_TTL = 30
CACHED_API_KEY = None

def load_config():
    try:
        return json.load(open(CONFIG_FILE))
    except:
        return {}

def save_config(cfg):
    json.dump(cfg, open(CONFIG_FILE, 'w'), indent=2)

CONFIG = load_config()

def read_api_key(folder=None):
    """Find API key in config folder or common locations."""
    if folder is None:
        folder = CONFIG.get('config_folder', '')
    
    # Try configured folder first
    if folder:
        for name in ['CommAPIKey.txt', 'DTGCommKey.txt']:
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                try:
                    with open(path, 'r', encoding='utf-8-sig') as f:
                        key = f.read().strip()
                    if key:
                        return key, path
                except:
                    pass
    
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
                try:
                    with open(path, 'r', encoding='utf-8-sig') as f:
                        key = f.read().strip()
                    if key:
                        return key, path
                except:
                    pass
    
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
        r = SESSION.get(f'{base}/{path.lstrip("/")}', headers=headers, timeout=timeout)
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
    return send_from_directory(PAGES_DIR, 'index.html')

@app.route('/pages/<path:filename>')
def pages(filename):
    return send_from_directory(PAGES_DIR, filename)

@app.route('/api/version')
def get_version():
    try:
        with open(os.path.join(APP_DIR, 'version.json')) as f:
            version_data = json.load(f)
        return jsonify({'main_version': version_data.get('main_version', 'Unknown')})
    except:
        return jsonify({'main_version': 'Unknown'})

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
    key, key_path = read_api_key()
    return jsonify({
        'ok': True,
        'key_found': bool(key),
        'key_file_path': key_path or ''
    })

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
        r = SESSION.get(f'{base}/info', headers=headers, timeout=(1, 2))
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

@app.route('/api/network-info')
def network_info():
    """Get local network IP address (not VPN or loopback)."""
    ip = get_local_ip()
    return jsonify({'network_ip': ip})

@app.route('/api/proxy/get/<path:subpath>')
def proxy_get(subpath):
    """Proxy GET requests to TSW game API."""
    key, _ = read_api_key()
    if not key:
        return jsonify({'Result': 'Error', 'Message': 'No API key found'}), 400
    try:
        r = SESSION.get(
            f'http://127.0.0.1:31270/get/{subpath}',
            headers={'DTGCommKey': key},
            timeout=(2, 3)
        )
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({'Result': 'Error', 'Message': str(e)}), 503

@app.route('/api/proxy/list/<path:subpath>')
def proxy_list(subpath):
    """Proxy LIST requests to TSW game API."""
    key, _ = read_api_key()
    if not key:
        return jsonify({'Result': 'Error', 'Message': 'No API key found'}), 400
    try:
        r = SESSION.get(
            f'http://127.0.0.1:31270/list/{subpath}',
            headers={'DTGCommKey': key},
            timeout=(2, 3)
        )
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({'Result': 'Error', 'Message': str(e)}), 503

@app.route('/api/proxy/list')
def proxy_list_root():
    """Proxy LIST requests for root."""
    key, _ = read_api_key()
    if not key:
        return jsonify({'Result': 'Error', 'Message': 'No API key found'}), 400
    try:
        r = SESSION.get(
            'http://127.0.0.1:31270/list',
            headers={'DTGCommKey': key},
            timeout=(2, 3)
        )
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({'Result': 'Error', 'Message': str(e)}), 503

def get_loco_cached():
    """Fetch locomotive info with 30-second cache."""
    now = time.time()
    cached = LOCO_CACHE.get('data')
    if cached and cached['timestamp'] + LOCO_CACHE_TTL > now:
        return cached['value']
    
    loco_data = {
        "name": None,
        "raw_object_class": None,
        "max_speed_mph": None
    }
    
    key, _ = read_api_key()
    if not key:
        return loco_data
    
    try:
        # Get ObjectClass
        obj_res = SESSION.get(
            'http://127.0.0.1:31270/get/CurrentDrivableActor.ObjectClass',
            headers={'DTGCommKey': key},
            timeout=(2, 3)
        ).json()
        raw_class = obj_res.get('Values', {}).get('ReturnValue')
        loco_data['raw_object_class'] = raw_class
        
        # Get display name
        info_res = SESSION.get(
            'http://127.0.0.1:31270/get/CurrentFormation/0.Function.IS_GetVehicleInfo',
            headers={'DTGCommKey': key},
            timeout=(2, 3)
        ).json()
        display_name = info_res.get('Values', {}).get('VehicleInfoResult', {}).get('DisplayName')
        loco_data['name'] = display_name
        
        # Get max speed
        driver_res = SESSION.get(
            'http://127.0.0.1:31270/get/DriverAid.Data',
            headers={'DTGCommKey': key},
            timeout=(2, 3)
        ).json()
        max_speed_ms = driver_res.get('Values', {}).get('formationMaxSpeed')
        if max_speed_ms:
            loco_data['max_speed_mph'] = max_speed_ms * 2.23694
    except:
        pass
    
    LOCO_CACHE['data'] = {'timestamp': now, 'value': loco_data}
    return loco_data

@app.route('/api/loco')
def api_loco():
    """Fetch locomotive info with caching."""
    data = get_loco_cached()
    return jsonify(data)

@app.route('/api/pages')
def api_pages():
    return send_from_directory(PAGES_DIR, 'registry.json')

@app.route('/api/shutdown', methods=['POST'])
def shutdown():
    """Shutdown the app."""
    try:
        if 'api_instance' in globals():
            api_instance.exit()
    except:
        pass
    return jsonify({'ok': True})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
