"""
RealSlate OS — Local Intelligence Server
Run: py serve.py
Opens at: http://localhost:8000

Serves the dashboard and all intelligence JSON files.
CORS-enabled so the dashboard can fetch city data live.
Auto-watches for file changes and logs requests.
"""

import http.server
import socketserver
import json
import os
import sys
from datetime import datetime

PORT = 8000
DASHBOARD_DIR = r'D:\RealSlateOS'
INTEL_DIR = r'D:\RealSlateOS\data\intelligence'


class RealSlateHandler(http.server.SimpleHTTPRequestHandler):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DASHBOARD_DIR, **kwargs)

    def end_headers(self):
        # CORS — allow dashboard to fetch JSON from same origin
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        # Route: /api/cities — returns all_cities.json
        if self.path == '/api/cities':
            self._serve_json(os.path.join(INTEL_DIR, 'all_cities.json'))
            return

        # Route: /api/city/{name} — returns specific city JSON
        if self.path.startswith('/api/city/'):
            city = self.path.replace('/api/city/', '').strip('/')
            city_file = os.path.join(INTEL_DIR, f"{city}.json")
            self._serve_json(city_file)
            return

        # Route: /api/status — health check with stats
        if self.path == '/api/status':
            self._serve_status()
            return

        # Default: serve static files from DASHBOARD_DIR
        super().do_GET()

    def _serve_json(self, filepath):
        if not os.path.exists(filepath):
            self.send_response(404)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'Not found', 'path': filepath}).encode())
            return

        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        # Fix NaN values (Python JSON writes NaN which is invalid JSON spec)
        content = content.replace(': NaN', ': null').replace(':NaN', ':null')

        encoded = content.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', len(encoded))
        self.end_headers()
        self.wfile.write(encoded)

    def _serve_status(self):
        intel_files = [f for f in os.listdir(INTEL_DIR) if f.endswith('.json')] if os.path.exists(INTEL_DIR) else []
        all_cities_path = os.path.join(INTEL_DIR, 'all_cities.json')
        last_updated = None
        total_records = 0

        if os.path.exists(all_cities_path):
            mtime = os.path.getmtime(all_cities_path)
            last_updated = datetime.fromtimestamp(mtime).isoformat()
            try:
                with open(all_cities_path) as f:
                    data = json.load(f)
                    total_records = data.get('total_records', 0)
            except:
                pass

        status = {
            'status': 'ok',
            'server_time': datetime.now().isoformat(),
            'intel_files': len(intel_files),
            'last_updated': last_updated,
            'total_records': total_records,
            'cities_available': [f.replace('.json', '') for f in intel_files if f != 'all_cities.json']
        }

        encoded = json.dumps(status).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(encoded))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        try:
            if any(skip in str(args[0]) for skip in ['.png', '.ico', '.css', '.woff']):
                return
            ts = datetime.now().strftime('%H:%M:%S')
            print(f"  [{ts}] {args[0]}")
        except:
            pass


def main():
    print("\n" + "="*55)
    print("  REALSLATE OS — Intelligence Server")
    print("="*55)
    print(f"  Serving:   http://localhost:{PORT}")
    print(f"  Dashboard: http://localhost:{PORT}/dashboard/index.html")
    print(f"  API:       http://localhost:{PORT}/api/cities")
    print(f"  Status:    http://localhost:{PORT}/api/status")
    print("="*55)

    # Check data exists
    if not os.path.exists(INTEL_DIR):
        print(f"\n  WARNING: Intelligence dir not found: {INTEL_DIR}")
        print(f"  Run compute_intelligence.py first.\n")
    else:
        files = [f for f in os.listdir(INTEL_DIR) if f.endswith('.json')]
        print(f"\n  Found {len(files)} intelligence files ready to serve.")

    print(f"\n  Press Ctrl+C to stop.\n")

    with socketserver.TCPServer(('', PORT), RealSlateHandler) as httpd:
        httpd.allow_reuse_address = True
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n\n  Server stopped.")
            sys.exit(0)


if __name__ == '__main__':
    main()