"""Taobao/Cainiao H5 client ported from api/TbOrders.kt and ui/CaiNiaoGoodsResolver.kt.

Not affiliated with Cainiao. Private H5 endpoints may change. Requires the
account owner's browser session cookie; never attempt to bypass login challenges.
"""
import hashlib
import json
import re
import time
from http.cookies import SimpleCookie
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

APP_KEY = "12574478"
UA = ("Mozilla/5.0 (Linux; Android 12; Mobile) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/105.0.0.0 Mobile Safari/537.36")
ORDER_API = "https://h5api.m.taobao.com/h5/mtop.taobao.order.queryboughtlistv2/1.0/"
TRACE_API = "https://acs.m.taobao.com/h5/mtop.taobao.logisticstracedetailservice.queryalltrace/1.0/"
SSR_API = "https://pages-g.m.taobao.com/wow/z/app/mtb/logisticsV2/h5-detail"

class CainiaoError(Exception):
    pass

def wap_sign(token, timestamp, data):
    """The H5 signature is *not* the iOS native x-sign signature."""
    source = f"{token}&{timestamp}&{APP_KEY}&{data}"
    return hashlib.md5(source.encode("utf-8")).hexdigest()

def parse_json_response(text):
    text = text.strip()
    if text.startswith("{"):
        return json.loads(text)
    match = re.match(r"^[\w.$]+\s*\(", text)
    if not match or not text.endswith(")"):
        raise CainiaoError("Unrecognized H5 JSON/JSONP response")
    return json.loads(text[match.end():-1])

def assert_success(obj):
    ret = obj.get("ret", [])
    if not isinstance(ret, list) or not any(str(v).startswith("SUCCESS") for v in ret):
        # Errors might contain account information; return only the server error code.
        codes = [str(v).split("::", 1)[0][:80] for v in ret] if isinstance(ret, list) else []
        raise CainiaoError("MTOP rejected request: " + ",".join(codes or ["unknown"]))
    return obj

def parse_order_list(text):
    payload = assert_success(parse_json_response(text))
    data = payload.get("data") or {}
    inner = data.get("result")
    if isinstance(inner, str):
        inner = json.loads(inner)
    if not isinstance(inner, dict):
        inner = data.get("data") or {}
    if not isinstance(inner, dict):
        raise CainiaoError("Order list response format changed")
    orders = []
    main = inner.get("mainOrders")
    if isinstance(main, list):
        for order in main:
            if not isinstance(order, dict):
                continue
            order_id = str(order.get("id") or "")
            trade = str((order.get("extra") or {}).get("tradeStatus") or "")
            state = str((order.get("statusInfo") or {}).get("text") or "")
            if order_id and "CLOSED" not in trade and "交易关闭" not in state and "待付款" not in state:
                orders.append({"orderId": order_id, "status": state, "tradeStatus": trade})
    else:
        for key, raw in inner.items():
            if key.startswith("Main_") and isinstance(raw, dict):
                fields = raw.get("fields") or {}
                orders.append({"orderId": str(fields.get("orderId") or key[5:]),
                               "status": str(fields.get("orderStatus") or ""), "tradeStatus": ""})
    return orders

def _stage_text(stage):
    desc = stage.get("labelDesc") or {}
    if isinstance(desc, dict):
        rich = desc.get("richContent")
        if isinstance(rich, list):
            return "".join(str(x.get("text", "")) for x in rich if isinstance(x, dict))
        return str(desc.get("text") or stage.get("text") or "")
    if isinstance(desc, str) and not desc.startswith("{"):
        return desc
    return str(stage.get("text") or "")

def parse_ssr(html, order_id):
    """Extract the serialized server-side logistics state from Taobao HTML."""
    marker = "__ICE_SUSPENSE_LOADER__"
    start = html.find(marker)
    if start < 0:
        return None
    # The upstream project uses __ICE_SUSPENSE_LOADER__']['undefined'] = {...}.
    # Read the first object after the assignment, with JSONDecoder handling nested braces.
    area = html[start:start + 2000000]
    match = re.search(r"=\s*(\{)", area)
    if not match:
        return None
    try:
        root, _ = json.JSONDecoder().raw_decode(area[match.start(1):])
        data = ((root.get("result") or {}).get("data") or {})
        logistics = ((data.get("newLogistics") or {}).get("fields") or
                     (data.get("logisticsDetailH5") or {}).get("fields") or {})
        company = logistics.get("logisticCompany") or {}
        mail_no = str(logistics.get("mailNo") or company.get("mailNo") or "")
        if not mail_no:
            return None
        points = []
        for stage in logistics.get("multiStage") or []:
            if not isinstance(stage, dict):
                continue
            desc = _stage_text(stage)
            if desc:
                points.append({"time": str(stage.get("subtitle") or stage.get("time") or ""),
                               "desc": desc})
        head = (logistics.get("multiStage") or [{}])[0]
        return {"mailNo": mail_no, "companyName": str(company.get("name") or ""),
                "stateName": str(head.get("title") or ""),
                "latestText": points[0]["desc"] if points else "",
                "latestTime": points[0]["time"] if points else "",
                "orderId": str(order_id), "source": "taobao", "traces": points}
    except (ValueError, AttributeError, TypeError, IndexError):
        return None

class Client:
    def __init__(self, cookie, transport=None):
        if not isinstance(cookie, str) or not cookie.strip():
            raise CainiaoError("Taobao login cookie is missing")
        self.cookies = {}
        for part in cookie.split(";"):
            if "=" in part:
                key, val = part.strip().split("=", 1)
                if key.strip():
                    self.cookies[key.strip()] = val.strip()
        self.transport = transport or self._http_get
        self._refreshed_hosts = set()

    @staticmethod
    def _http_get(url, headers):
        req = Request(url, headers=headers, method="GET")
        try:
            with urlopen(req, timeout=20) as response:
                return response.read().decode("utf-8", "replace"), response.headers.get_all("Set-Cookie", [])
        except HTTPError as exc:
            raise CainiaoError(f"Upstream HTTP {exc.code}") from None
        except URLError:
            raise CainiaoError("Upstream network error") from None

    def _cookie_header(self):
        return "; ".join(f"{key}={val}" for key, val in self.cookies.items())

    def _get(self, url, origin="https://page.cainiao.com"):
        headers = {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
                   "Accept-Language": "zh-CN,zh;q=0.9",
                   "Origin": origin, "Referer": origin + "/",
                   "Cookie": self._cookie_header()}
        body, set_cookies = self.transport(url, headers)
        for line in set_cookies or []:
            try:
                parsed = SimpleCookie()
                parsed.load(line)
                for key in ("_m_h5_tk", "_m_h5_tk_enc"):
                    if key in parsed:
                        self.cookies[key] = parsed[key].value
            except Exception:
                continue
        return body

    def _ensure_token(self, base, origin):
        if base not in self._refreshed_hosts:
            self._get(base + "?appKey=" + APP_KEY, origin)
            self._refreshed_hosts.add(base)
        raw = self.cookies.get("_m_h5_tk", "")
        token = raw.split("_", 1)[0]
        if not token:
            raise CainiaoError("Missing _m_h5_tk; log in to Taobao H5 first")
        return token

    def mtop(self, base, data, *, jsonp=False, origin="https://page.cainiao.com"):
        token = self._ensure_token(base, origin)
        timestamp = str(int(time.time() * 1000))
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        params = {"jsv": "2.3.18", "appKey": APP_KEY, "t": timestamp,
                  "sign": wap_sign(token, timestamp, raw),
                  "type": "jsonp" if jsonp else "originaljson"}
        if jsonp:
            params["dataType"] = "jsonp"
        params["data"] = raw
        url = base + "?" + urlencode(params)
        return assert_success(parse_json_response(self._get(url, origin)))

    def get_order_ids(self, max_pages=3):
        orders = {}
        for page in range(1, max_pages + 1):
            data = {"tabCode": "all", "page": page, "OrderType": "OrderList",
                    "templateConfigVersion": "0", "appName": "tborder",
                    "appVersion": "3.0",
                    "condition": '{"version":"1.0.0","appChannel":""}',
                    "ttid": "201200@taobao_h5_9.18.0",
                    "requestIdentity": "#t#ip#h5"}
            payload = self.mtop(ORDER_API, data, jsonp=True,
                                origin="https://h5.m.taobao.com")
            # parse_order_list needs the same success envelope.
            batch = parse_order_list(json.dumps(payload, ensure_ascii=False))
            for order in batch:
                orders[order["orderId"]] = order
            if len(batch) < 10:
                break
        return list(orders.values())

    def get_order_detail(self, order_id):
        url = SSR_API + "?x-ssr=true&bizOrderId=" + quote(str(order_id), safe="")
        return parse_ssr(self._get(url, "https://h5.m.taobao.com"), order_id)

    def sync(self, max_pages=3):
        packages = []
        skipped = 0
        for item in self.get_order_ids(max_pages=max_pages):
            try:
                detail = self.get_order_detail(item["orderId"])
                if detail:
                    packages.append(detail)
                else:
                    skipped += 1
            except CainiaoError:
                skipped += 1
        return packages, skipped

    def query_waybill(self, mail_no):
        if not isinstance(mail_no, str) or not re.fullmatch(r"[A-Za-z0-9-]{5,50}", mail_no):
            raise CainiaoError("Invalid waybill number")
        data = {"mailNo": mail_no, "appName": "GUOGUO", "actor": "RECEIVER",
                "isAccoutOut": True, "isShowConsignDetail": True,
                "ignoreInvalidNode": True, "isUnique": True, "isStandard": True,
                "isShowItem": True, "isShowTemporalityService": True,
                "isShowCommonService": True, "isStandardActionCode": True,
                "isOrderByAction": True, "isShowExpressMan": True,
                "isShowProgressbar": True, "isShowLastOneService": True,
                "isShowServiceProvider": True, "isShowDeliveryProgress": True}
        result = self.mtop(TRACE_API, data)
        values = (result.get("data") or {}).get("result") or []
        if not isinstance(values, list) or not values or not isinstance(values[0], dict):
            raise CainiaoError("No trace result; check account access and login status")
        obj = values[0]
        points = [{"time": str(x.get("time") or ""), "desc": str(x.get("desc") or "")}
                  for x in (obj.get("fullTraceDetail") or []) if isinstance(x, dict)]
        goods = [{"name": str(x.get("goodsName") or ""),
                  "imageUrl": str(x.get("itemPic") or x.get("allPicUrl") or ""),
                  "quantity": x.get("goodsQuantity")}
                 for x in (obj.get("packageItems") or []) if isinstance(x, dict)]
        return {"mailNo": mail_no, "companyName": "", "stateName": "",
                "latestText": points[-1]["desc"] if points else "",
                "latestTime": points[-1]["time"] if points else "",
                "orderId": "", "source": "cainiao", "traces": points,
                "goods": goods}
