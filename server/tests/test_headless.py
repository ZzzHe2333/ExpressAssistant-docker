import hashlib
import json
import os
import tempfile
import unittest
from server.cainiao import Client, CainiaoError, parse_order_list, parse_ssr, wap_sign
from server.store import Store
from server.app import Service, handler_for

class H5Tests(unittest.TestCase):
    def test_sign(self):
        expected = hashlib.md5(b"abc&123&12574478&{}").hexdigest()
        self.assertEqual(wap_sign("abc", "123", "{}"), expected)

    def test_order_jsonp(self):
        raw = {"ret": ["SUCCESS::OK"], "data": {"result": json.dumps({
            "mainOrders": [{"id": "123", "extra": {"tradeStatus": "WAIT_SELLER_SEND_GOODS"},
                            "statusInfo": {"text": "运输中"}},
                           {"id": "456", "extra": {"tradeStatus": "TRADE_CLOSED"}}]})}}
        self.assertEqual(parse_order_list("mtopjsonp1("+json.dumps(raw)+")")[0]["orderId"], "123")
        self.assertEqual(len(parse_order_list(json.dumps(raw))), 1)

    def test_reject_failed_login(self):
        with self.assertRaises(CainiaoError):
            parse_order_list('{"ret":["FAIL_SYS_TOKEN_EXOIRED::bad"],"data":{}}')

    def test_ssr(self):
        body = {"result": {"data": {"newLogistics": {"fields": {
            "logisticCompany": {"mailNo": "YD123", "name": "韵达"},
            "multiStage": [{"title": "运输中", "subtitle": "2026-10-13 12:00:00",
                            "labelDesc": {"richContent": [{"text": "到达泉州"}]}}]
        }}}}}
        html = "<script>window.__ICE_SUSPENSE_LOADER__']['undefined'] = " + json.dumps(body) + ";</script>"
        obj = parse_ssr(html, "ORDER1")
        self.assertEqual(obj["mailNo"], "YD123")
        self.assertEqual(obj["traces"][0]["desc"], "到达泉州")

    def test_token_and_request(self):
        seen = []
        def transport(url, headers):
            seen.append((url, headers))
            if "data=" not in url:
                return "{}", ["_m_h5_tk=tok_123; Path=/", "_m_h5_tk_enc=enc; Path=/"]
            return '{"ret":["SUCCESS::ok"],"data":{"result":[{"fullTraceDetail":[{"time":"now","desc":"arrived"}]}]}}', []
        c = Client("cookie2=abc", transport=transport)
        pkg = c.query_waybill("YD123456")
        self.assertEqual(pkg["traces"][0]["desc"], "arrived")
        self.assertIn("sign=", seen[1][0])
        self.assertIn("cookie2=abc", seen[1][1]["Cookie"])

class StoreTests(unittest.TestCase):
    def test_sqlite_and_secret(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(os.path.join(d, "packages.sqlite"), os.path.join(d, "taobao.cookie"))
            store.set_cookie("cookie2=secret; _m_h5_tk=abc_123")
            self.assertIn("cookie2=secret", store.get_cookie())
            self.assertEqual(os.stat(os.path.join(d, "taobao.cookie")).st_mode & 0o777, 0o600)
            item = {"mailNo": "123456789", "traces": [{"time": "now", "desc": "sent"}],
                    "stateName": "运输中", "latestText": "sent"}
            store.upsert([item])
            self.assertEqual(store.get_package("123456789")["traces"][0]["desc"], "sent")
            store.upsert([dict(item, stateName="已到达")])
            self.assertEqual(len(store.list_packages()), 1)
            self.assertEqual(store.get_package("123456789")["stateName"], "已到达")

if __name__ == "__main__":
    unittest.main()
