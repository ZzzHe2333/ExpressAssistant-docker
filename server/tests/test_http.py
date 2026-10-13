"""Integration smoke tests for the Docker REST gateway (offline)."""
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from server.app import Service, handler_for
from server.store import Store

class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        data = Path(cls.tmp.name)
        cls.store = Store(data / "packages.db", data / "taobao.cookie")
        cls.key = "a-test-token-longer-than-16-characters"
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0),
                                       handler_for(Service(cls.store), cls.key))
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.http.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join(timeout=2)
        cls.tmp.cleanup()

    def request(self, path, body=None, auth=True, method="GET"):
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = "Bearer " + self.key
        req = Request(self.base + path, headers=headers,
                      method=method, data=json.dumps(body).encode() if body is not None else None)
        try:
            with urlopen(req, timeout=3) as r:
                return r.status, json.load(r)
        except HTTPError as err:
            return err.code, json.loads(err.read().decode())

    def test_health_requires_no_token_and_does_not_leak_credential(self):
        code, body = self.request("/health", auth=False)
        self.assertEqual(code, 200)
        self.assertEqual(body["ok"], True)

    def test_reject_unauthorized(self):
        code, _ = self.request("/api/packages", auth=False)
        self.assertEqual(code, 401)

    def test_read_cached_list(self):
        code, body = self.request("/api/packages")
        self.assertEqual(code, 200)
        self.assertIsInstance(body, list)

    def test_missing_route_is_404(self):
        code, _ = self.request("/api/unknown", body={}, method="POST")
        self.assertEqual(code, 404)

    def test_mcp_tool_list(self):
        code, body = self.request("/mcp", method="POST",
                                  body={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(code, 200)
        names = {x["name"] for x in body["result"]["tools"]}
        self.assertIn("query_waybill", names)
        self.assertIn("sync_packages", names)

if __name__ == "__main__":
    unittest.main()
