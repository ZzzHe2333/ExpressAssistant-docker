"""Headless localhost-facing HTTP + JSON-RPC gateway for Hermes Agent."""
import hmac
import json
import logging
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote
from .cainiao import CainiaoError, Client
from .store import Store

LOG = logging.getLogger("expressassistant")
DB_PATH = os.getenv("DB_PATH", "/data/packages.db")
COOKIE_FILE = os.getenv("COOKIE_FILE", "/data/taobao.cookie")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8766"))
API_KEY = os.getenv("API_KEY", "")
INTERVAL = int(os.getenv("SYNC_INTERVAL_SECONDS", "900"))
MAX_PAGES = max(1, min(10, int(os.getenv("MAX_ORDER_PAGES", "3"))))

def tools_definitions():
    obj = lambda p=None, r=None: {"type": "object", "properties": p or {}, "required": r or []}
    return [
        {"name": "list_packages", "description": "List locally synced deliveries", "inputSchema": obj()},
        {"name": "package_detail", "description": "Read cached delivery and logistics events",
         "inputSchema": obj({"mailNo": {"type": "string"}}, ["mailNo"])},
        {"name": "query_waybill", "description": "Query a waybill using the configured Taobao H5 session",
         "inputSchema": obj({"mailNo": {"type": "string"}}, ["mailNo"])},
        {"name": "sync_packages", "description": "Sync recent Taobao orders and package details",
         "inputSchema": obj()}
    ]

class Service:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()

    def client(self):
        return Client(self.store.get_cookie())

    def list_packages(self):
        return self.store.list_packages()

    def detail(self, mail_no):
        return self.store.get_package(mail_no)

    def sync(self):
        with self.lock:
            packages, skipped = self.client().sync(MAX_PAGES)
            self.store.upsert(packages)
            return {"ok": True, "updated": len(packages), "skipped": skipped,
                    "total": len(self.store.list_packages())}

    def query(self, mail_no):
        with self.lock:
            item = self.client().query_waybill(mail_no)
            self.store.upsert([item])
            return self.store.get_package(mail_no)

def handler_for(service, key):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ExpressAssistant-Docker"
        def log_message(self, fmt, *args):
            # Do not log URLs containing waybills, cookies, or sensitive request bodies.
            LOG.info("%s %s", self.client_address[0], self.command)

        def send_json(self, code, result):
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            supplied = self.headers.get("Authorization", "")
            if supplied.lower().startswith("bearer "):
                supplied = supplied[7:]
            else:
                supplied = self.headers.get("X-API-Key", "")
            if not hmac.compare_digest(supplied.encode(), key.encode()):
                self.send_json(401, {"error": "unauthorized"})
                return False
            return True

        def read_body(self):
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise ValueError("Invalid Content-Length")
            if n <= 0 or n > 65536:
                raise ValueError("JSON body required, up to 64 KB")
            try:
                body = json.loads(self.rfile.read(n).decode("utf-8"))
                if not isinstance(body, dict):
                    raise ValueError("Expected a JSON object")
                return body
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ValueError("Invalid JSON") from None

        def execute(self, fn):
            try:
                return fn()
            except (ValueError, KeyError) as exc:
                self.send_json(400, {"error": str(exc)})
            except CainiaoError as exc:
                self.send_json(502, {"error": str(exc)})
            except Exception:
                LOG.exception("Internal request failure")
                self.send_json(500, {"error": "internal server error"})
            return None

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/health":
                self.send_json(200, {"ok": True, "credentialConfigured": bool(service.store.get_cookie())})
                return
            if not self.authorized():
                return
            if path == "/api/packages":
                self.send_json(200, service.list_packages())
            elif path.startswith("/api/packages/"):
                mail_no = unquote(path[len("/api/packages/"):])
                item = service.detail(mail_no)
                self.send_json(200 if item else 404, item if item else {"error": "not found"})
            else:
                self.send_json(404, {"error": "not found"})

        def rpc(self, body):
            method = body.get("method")
            rpc_id = body.get("id")
            if method == "tools/list":
                res = {"tools": tools_definitions()}
            elif method == "tools/call":
                params = body.get("params") or {}
                arguments = params.get("arguments") or {}
                name = params.get("name")
                mail_no = arguments.get("mailNo", "")
                if name == "list_packages":
                    result = service.list_packages()
                elif name == "package_detail":
                    result = service.detail(mail_no)
                    if result is None:
                        raise ValueError("Package not found")
                elif name == "query_waybill":
                    result = service.query(mail_no)
                elif name == "sync_packages":
                    result = service.sync()
                else:
                    raise ValueError("Unknown tool")
                res = {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}
            else:
                return {"jsonrpc": "2.0", "id": rpc_id,
                        "error": {"code": -32601, "message": "Method not found"}}
            return {"jsonrpc": "2.0", "id": rpc_id, "result": res}

        def do_POST(self):
            if not self.authorized():
                return
            body = self.execute(self.read_body)
            if body is None:
                return
            path = urlsplit(self.path).path
            if path not in ("/api/credentials/taobao", "/api/sync", "/api/query", "/mcp"):
                self.send_json(404, {"error": "not found"})
                return
            def handle():
                if path == "/api/credentials/taobao":
                    service.store.set_cookie(body.get("cookie", ""))
                    return {"ok": True}
                if path == "/api/sync":
                    return service.sync()
                if path == "/api/query":
                    return service.query(body.get("mailNo", ""))
                if path == "/mcp":
                    return self.rpc(body)
                return None
            result = self.execute(handle)
            if result is not None:
                self.send_json(200 if result is not None else 404, result)
    return Handler

def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    if len(API_KEY) < 16 or API_KEY == "change-me":
        raise SystemExit("Set API_KEY to a random string at least 16 characters long")
    if INTERVAL and INTERVAL < 60:
        raise SystemExit("SYNC_INTERVAL_SECONDS must be 0 or >= 60")
    store = Store(DB_PATH, COOKIE_FILE)
    service = Service(store)
    if INTERVAL:
        def periodic():
            while True:
                if stop.wait(INTERVAL):
                    return
                if not store.get_cookie():
                    continue
                try:
                    result = service.sync()
                    LOG.info("Background sync updated=%s skipped=%s",
                             result["updated"], result["skipped"])
                except CainiaoError as exc:
                    LOG.warning("Background sync unavailable: %s", str(exc))
                except Exception:
                    LOG.exception("Background sync failed")
        stop = threading.Event()
        threading.Thread(target=periodic, daemon=True).start()
    srv = ThreadingHTTPServer((HOST, PORT), handler_for(service, API_KEY))
    LOG.info("ExpressAssistant headless listening on %s:%d", HOST, PORT)
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()

if __name__ == "__main__":
    main()
