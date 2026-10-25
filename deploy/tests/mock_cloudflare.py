"""A small stand-in for the Cloudflare API: zones, tunnels and DNS records.

Used by test_tunnel_script.py, and run on its own by sandbox-e2e.sh:
    python3 mock_cloudflare.py PORT_FILE
writes the port it listens on to PORT_FILE and serves until killed.
GET /state returns everything it holds, for checks.
"""

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

TOKEN = "test-token-0123456789abcdef0123456789abcdef"
ZONE_ID = "zone123"
ACCOUNT_ID = "acct456"


class MockCloudflare(BaseHTTPRequestHandler):
    tunnels: list[dict] = []
    records: list[dict] = []
    config: dict | None = None
    calls: list[tuple[str, str, dict | None]] = []

    def log_message(self, *args):
        pass

    def _reply(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self, method):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        self.calls.append((method, self.path, body))
        return body

    def _ok(self, result):
        self._reply({"success": True, "errors": [], "result": result})

    def _authorized(self):
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self._reply({"success": False, "errors": [{"code": 10000}]}, 403)
            return False
        return True

    def do_GET(self):
        self._body("GET")
        if self.path == "/state":
            return self._ok(
                {"tunnels": self.tunnels, "records": self.records, "config": self.config}
            )
        if not self._authorized():
            return
        p = self.path
        if p.startswith("/client/v4/zones?name="):
            return self._ok([{"id": ZONE_ID, "account": {"id": ACCOUNT_ID}}])
        if p.startswith(f"/client/v4/accounts/{ACCOUNT_ID}/cfd_tunnel?"):
            name = re.search(r"name=([^&]+)", p).group(1)
            return self._ok([t for t in self.tunnels if t["name"] == name])
        m = re.match(rf"/client/v4/accounts/{ACCOUNT_ID}/cfd_tunnel/([^/]+)/token$", p)
        if m:
            return self._ok(f"tunnel-token-for-{m.group(1)}")
        m = re.match(rf"/client/v4/accounts/{ACCOUNT_ID}/cfd_tunnel/([^/?]+)$", p)
        if m:
            t = next(t for t in self.tunnels if t["id"] == m.group(1))
            return self._ok({**t, "status": "healthy", "connections": [{}, {}, {}, {}]})
        if p.startswith(f"/client/v4/zones/{ZONE_ID}/dns_records?"):
            return self._ok(self.records)
        self._reply({"success": False}, 404)

    def do_POST(self):
        body = self._body("POST")
        if not self._authorized():
            return
        if self.path == f"/client/v4/accounts/{ACCOUNT_ID}/cfd_tunnel":
            tunnel = {"id": f"tun{len(self.tunnels) + 1}", "name": body["name"]}
            self.tunnels.append(tunnel)
            return self._ok(tunnel)
        if self.path == f"/client/v4/zones/{ZONE_ID}/dns_records":
            record = {"id": f"rec{len(self.records) + 1}", **body}
            self.records.append(record)
            return self._ok(record)
        self._reply({"success": False}, 404)

    def do_PUT(self):
        body = self._body("PUT")
        if not self._authorized():
            return
        if self.path.endswith("/configurations"):
            MockCloudflare.config = body
            return self._ok(body)
        m = re.match(rf"/client/v4/zones/{ZONE_ID}/dns_records/(.+)$", self.path)
        if m:
            record = next(r for r in self.records if r["id"] == m.group(1))
            record.update(body)
            return self._ok(record)
        self._reply({"success": False}, 404)

    def do_DELETE(self):
        self._body("DELETE")
        if not self._authorized():
            return
        m = re.match(rf"/client/v4/zones/{ZONE_ID}/dns_records/(.+)$", self.path)
        if m:
            MockCloudflare.records = [r for r in self.records if r["id"] != m.group(1)]
            return self._ok({"id": m.group(1)})
        m = re.match(rf"/client/v4/accounts/{ACCOUNT_ID}/cfd_tunnel/(.+)$", self.path)
        if m:
            MockCloudflare.tunnels = [t for t in self.tunnels if t["id"] != m.group(1)]
            return self._ok({"id": m.group(1)})
        self._reply({"success": False}, 404)


def main() -> None:
    server = HTTPServer(("127.0.0.1", 0), MockCloudflare)
    with open(sys.argv[1], "w") as f:
        f.write(str(server.server_port))
    server.serve_forever()


if __name__ == "__main__":
    main()
