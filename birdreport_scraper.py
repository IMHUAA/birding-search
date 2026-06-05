#!/usr/bin/env python3
"""
中国观鸟记录中心 - 鸟种记录查询工具
=======================================
根据时间范围和地点，从 www.birdreport.cn 抓取鸟种记录并汇总。

用法:
  python3 birdreport_scraper.py --province 浙江省 --city 杭州市 --district 西湖区 \
      --pointname 杭州植物园 --start 2026-05-19 --end 2026-05-21

  python3 birdreport_scraper.py --province 浙江省 --start 2026-05-01 --end 2026-05-20 --top 20

依赖: pip3 install pycryptodome requests

免责声明: 仅供学习交流使用，请勿用于商业目的或高频请求。
"""

import argparse
import base64
import hashlib
import json
import time
import uuid
import sys
from urllib.parse import urlencode, unquote
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5, AES
from Crypto.Util.Padding import unpad

# ============================================================
# 常量
# ============================================================

API_BASE = "https://api.birdreport.cn"
WEB_BASE = "https://www.birdreport.cn"
TAXON_ENDPOINT = "front/record/activity/taxon"
REPORT_ENDPOINT = "front/record/activity/search"
TAXON_PAGE = "/home/search/taxon.html"
REPORT_PAGE = "/home/search/report.html"

RSA_MODULUS = int(
    "00afc576bdf04d6e5979c1cd7912db21d47e704cea7dcf0b02dd7c8a31d374fdd63"
    "a01be3b680ea890e71ded4ce7e91ecdcbb62a8c30469f96ce9bd185ad801d5db1ed5"
    "6e66f85a5ebac56408e910c41e126431fab6e7ab2249e7981fc7b2ae70804908bcee"
    "4f7dff5b8a283a032a0091d5a949e93fd7f07bcd821ea5863497129", 16
)
RSA_EXPONENT = 65537

# 经过 getMapping() 变换后的 AES 密钥和 IV（从浏览器 BIRDREPORT_APIJS 提取）
AES_KEY_STR = "C8EB5514AF5ADDB94B2207B08C66601C"  # 32 bytes → AES-256
AES_IV_STR = "55DD79C6F04E1A67"                     # 16 bytes → AES-CBC IV

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


# ============================================================
# 加密/解密模块
# ============================================================

class BirdReportCrypto:
    """处理观鸟记录中心的请求加密和响应解密。"""

    def __init__(self):
        pubkey = RSA.construct((RSA_MODULUS, RSA_EXPONENT))
        self.rsa_cipher = PKCS1_v1_5.new(pubkey)
        self.aes_key = AES_KEY_STR.encode("utf-8")
        self.aes_iv = AES_IV_STR.encode("utf-8")

    def encrypt_request(self, data_str: str) -> str:
        """RSA分块加密 → hex拼接 → base64 (模拟 JSEncrypt.encryptLong)"""
        data_bytes = data_str.encode("utf-8")
        max_len = 117  # 1024-bit RSA: 128 - 11 (PKCS1v1.5 padding)
        encrypted_parts = []
        for i in range(0, len(data_bytes), max_len):
            chunk = data_bytes[i:i + max_len]
            encrypted_chunk = self.rsa_cipher.encrypt(chunk)
            encrypted_parts.append(encrypted_chunk.hex())
        encrypted_hex = "".join(encrypted_parts)
        return base64.b64encode(bytes.fromhex(encrypted_hex)).decode()

    def decrypt_response(self, b64_data: str) -> dict:
        """AES-CBC 解密 API 返回的 data 字段。"""
        ciphertext = base64.b64decode(b64_data)
        cipher = AES.new(self.aes_key, AES.MODE_CBC, self.aes_iv)
        plaintext = unpad(cipher.decrypt(ciphertext), AES.block_size)
        return json.loads(plaintext.decode("utf-8"))

    @staticmethod
    def make_sign(data_str: str, req_id: str, ts: str) -> str:
        """MD5(排序JSON + requestId + timestamp)"""
        return hashlib.md5((data_str + req_id + ts).encode()).hexdigest()

    @staticmethod
    def make_request_id() -> str:
        return str(uuid.uuid4()).replace("-", "")[:32]

    @staticmethod
    def make_timestamp() -> str:
        return str(int(time.time() * 1000))


# ============================================================
# 查询参数构建
# ============================================================

class SearchParams:
    """构建鸟种查询参数，生成搜索URL。"""

    def __init__(
        self,
        start_time: str = "",
        end_time: str = "",
        province: str = "",
        city: str = "",
        district: str = "",
        pointname: str = "",
        username: str = "",
        serial_id: str = "",
        ctime: str = "",
        version: str = "CH4",
        state: str = "",
        mode: str = "0",
        taxon_month: str = "",
        report_month: str = "",
        taxonid: str = "",
        outside_type: int = 0,
    ):
        self.params = {
            "taxonid": taxonid,
            "startTime": start_time,
            "endTime": end_time,
            "province": province,
            "city": city,
            "district": district,
            "pointname": pointname,
            "username": username,
            "serial_id": serial_id,
            "ctime": ctime,
            "version": version,
            "state": state,
            "mode": mode,
            "taxon_month": taxon_month,
            "report_month": report_month,
            "outside_type": outside_type,
        }

    def to_json(self) -> str:
        return json.dumps(self.params, separators=(",", ":"), ensure_ascii=False)

    def to_base64(self) -> str:
        return base64.b64encode(self.to_json().encode("utf-8")).decode()

    def to_url(self) -> str:
        return f"{WEB_BASE}{TAXON_PAGE}?search={self.to_base64()}"

    def to_report_url(self) -> str:
        return f"{WEB_BASE}{REPORT_PAGE}?search={self.to_base64()}"

    @classmethod
    def from_json(cls, json_str: str) -> "SearchParams":
        d = json.loads(json_str)
        return cls(
            start_time=d.get("startTime", ""),
            end_time=d.get("endTime", ""),
            province=d.get("province", ""),
            city=d.get("city", ""),
            district=d.get("district", ""),
            pointname=d.get("pointname", ""),
            username=d.get("username", ""),
            serial_id=d.get("serial_id", ""),
            ctime=d.get("ctime", ""),
            version=d.get("version", "CH4"),
            state=d.get("state", ""),
            mode=d.get("mode", "0"),
            taxon_month=d.get("taxon_month", ""),
            report_month=d.get("report_month", ""),
            taxonid=d.get("taxonid", ""),
            outside_type=d.get("outside_type", 0),
        )

    def get_api_data(self) -> dict:
        """返回用于API请求的参数（按字母序排序的JSON字符串）。"""
        return json.loads(self.to_json())


# ============================================================
# 主抓取类
# ============================================================

class BirdReportScraper:
    """观鸟记录中心鸟种查询抓取器。"""

    def __init__(self):
        self.crypto = BirdReportCrypto()

    def _request(self, params: SearchParams, endpoint: str, referer: str,
                 page: int = 1, limit: int = 1500) -> tuple[list[dict], int]:
        """
        发送加密请求到指定API端点。
        返回 (解密后的数据列表, 总记录数)。
        """
        import requests

        api_params_copy = dict(params.params)
        api_params_copy.setdefault("page", page)
        api_params_copy.setdefault("limit", limit)

        encoded_items = []
        for key, value in api_params_copy.items():
            encoded_items.append((key, str(value)))
        form_data = urlencode(encoded_items)

        url_encoded_params = {}
        for pair in form_data.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                url_encoded_params[k] = v
            else:
                url_encoded_params[pair] = ""

        data_sorted = json.dumps(
            url_encoded_params, sort_keys=True, separators=(",", ":")
        )

        req_id = self.crypto.make_request_id()
        ts = self.crypto.make_timestamp()
        sign = self.crypto.make_sign(data_sorted, req_id, ts)
        encrypted_body = self.crypto.encrypt_request(data_sorted)

        headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": WEB_BASE,
            "Referer": referer,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "timestamp": ts,
            "requestId": req_id,
            "sign": sign,
        }

        url = f"{API_BASE}/{endpoint}"
        resp = requests.post(url, data=encrypted_body, headers=headers, timeout=30)

        if resp.status_code != 200:
            raise RuntimeError(f"API 返回 HTTP {resp.status_code}: {resp.text[:300]}")

        result = resp.json()
        if result.get("code") != 0 or result.get("data") is None:
            raise RuntimeError(f"API 业务错误: {json.dumps(result, ensure_ascii=False)[:500]}")

        data = self.crypto.decrypt_response(result["data"])
        total = result.get("count", len(data))
        return data, total

    def search(self, params: SearchParams) -> list[dict]:
        """按鸟种汇总查询（taxon 端点）。"""
        data, _ = self._request(params, TAXON_ENDPOINT, params.to_url())
        return data

    def search_reports(self, params: SearchParams) -> list[dict]:
        """按观测记录查询（report/search 端点），自动分页获取全部记录。"""
        params.params.pop("taxon_month", None)
        all_results = []
        page = 1
        limit = 20  # 报告端点限制，匹配前端 layui table 默认值
        while True:
            data, total = self._request(params, REPORT_ENDPOINT,
                                        params.to_report_url(), page=page, limit=limit)
            all_results.extend(data)
            if len(all_results) >= total:
                break
            page += 1
        return all_results

    def summarize(self, results: list[dict], top: int = 0) -> str:
        """
        汇总鸟种记录为可读表格。
        top=0 显示全部，top>0 只显示前N条（按记录次数降序）。
        """
        if not results:
            return "未找到鸟种记录。"

        # 按记录次数降序
        sorted_results = sorted(results, key=lambda x: x.get("recordcount", 0), reverse=True)

        if top > 0:
            sorted_results = sorted_results[:top]

        # 表格头
        header = f"{'鸟种编号':<10} {'中文名':<16} {'拉丁学名':<35} {'目':<10} {'科':<10} {'记录次数':>8}"
        sep = "-" * len(header)
        lines = [sep, header, sep]

        for item in sorted_results:
            tid = item.get("taxon_id", "")
            name = item.get("taxonname", "")
            latin = item.get("latinname", "")
            order = item.get("taxonordername", "")
            family = item.get("taxonfamilyname", "")
            count = item.get("recordcount", 0)
            outside = item.get("outside_type", 0)
            marker = " *" if outside == 1 else ""  # 标记外地鸟种
            lines.append(
                f"{tid:<10} {name + marker:<16} {latin:<35} {order:<10} {family:<10} {count:>8}"
            )

        lines.append(sep)
        lines.append(f"共 {len(results)} 种鸟 | 显示 {len(sorted_results)} 种")
        lines.append(f"搜索URL: {self._last_url}")

        return "\n".join(lines)

    def summarize_reports(self, results: list[dict]) -> str:
        """汇总观测记录为可读表格（地点、时间、观测者）。"""
        if not results:
            return "未找到观测记录。"

        header = f"{'编号':<16} {'日期':<12} {'时间':<18} {'观测点':<28} {'区县':<10} {'观测者':<14} {'种数':>5}"
        sep = "-" * len(header)
        lines = [sep, header, sep]

        for item in results:
            sid = item.get("serial_id", "")
            start = item.get("start_time", "")
            end = item.get("end_time", "")
            date = start[:10] if start else ""
            time_range = f"{start[11:16] if len(start) > 11 else ''}-{end[11:16] if len(end) > 11 else ''}"
            point = item.get("point_name", "")
            district = item.get("district_name", "")
            username = item.get("username", "")
            count = item.get("taxoncount", "")

            lines.append(
                f"{sid:<16} {date:<12} {time_range:<18} {point:<28} {district:<10} {username:<14} {str(count):>5}"
            )

        lines.append(sep)
        lines.append(f"共 {len(results)} 条观测记录")
        lines.append(f"搜索URL: {self._last_url}")

        return "\n".join(lines)

    def print_json(self, results: list[dict]):
        """直接原样输出JSON数组，方便管道处理。"""
        print(json.dumps(results, ensure_ascii=False, indent=2))


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="中国观鸟记录中心 - 鸟种记录查询",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --province 浙江省 --city 杭州市 --district 西湖区 \\
      --pointname 杭州植物园 --start 2026-05-19 --end 2026-05-21
  %(prog)s --province 浙江省 --start 2026-05-01 --end 2026-05-20 --top 20
  %(prog)s --province 浙江省 --city 杭州市 --json
  %(prog)s --report --taxonid 4572 --province 上海市 --start 2026-05-22 --end 2026-05-23
        """,
    )
    parser.add_argument("--report", action="store_true", help="报告模式：查询观测记录（地点+时间），而非鸟种汇总")
    parser.add_argument("--taxonid", default="", help="鸟种编号（报告模式下用于筛选特定鸟种）")
    parser.add_argument("--province", default="", help="省份，如: 浙江省")
    parser.add_argument("--city", default="", help="城市，如: 杭州市")
    parser.add_argument("--district", default="", help="区县，如: 西湖区")
    parser.add_argument("--pointname", default="", help="观测点名称（支持模糊匹配）")
    parser.add_argument("--username", default="", help="用户名（可选）")
    parser.add_argument("--start", default="", help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", default="", help="结束日期 YYYY-MM-DD")
    parser.add_argument("--month", default="", help="限定月份 MM")
    parser.add_argument("--state", default="", help="状态筛选")
    parser.add_argument("--mode", default="0", help="查询模式: 0=模糊 1=精确")
    parser.add_argument("--top", type=int, default=0, help="只显示前N种（按记录次数降序，仅鸟种模式）")
    parser.add_argument("--json", action="store_true", help="以JSON格式输出原始数据")
    parser.add_argument("--url-only", action="store_true", help="只输出搜索URL，不抓取")

    args = parser.parse_args()

    params = SearchParams(
        start_time=args.start,
        end_time=args.end,
        province=args.province,
        city=args.city,
        district=args.district,
        pointname=args.pointname,
        username=args.username,
        mode=args.mode,
        state=args.state,
        taxonid=args.taxonid,
        taxon_month=args.month if not args.report else "",
        report_month=args.month if args.report else "",
    )

    if args.url_only:
        if args.report:
            print(params.to_report_url())
        else:
            print(params.to_url())
        return

    scraper = BirdReportScraper()

    if args.report:
        scraper._last_url = params.to_report_url()
        print(f"查询参数: {params.to_json()}", file=sys.stderr)
        print(f"搜索URL: {params.to_report_url()}", file=sys.stderr)
        print(file=sys.stderr)
    else:
        scraper._last_url = params.to_url()
        print(f"查询参数: {params.to_json()}", file=sys.stderr)
        print(f"搜索URL: {params.to_url()}", file=sys.stderr)
        print(file=sys.stderr)

    try:
        if args.report:
            results = scraper.search_reports(params)
        else:
            results = scraper.search(params)
    except Exception as e:
        print(f"查询失败: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        scraper.print_json(results)
    else:
        if args.report:
            print(scraper.summarize_reports(results))
        else:
            print(scraper.summarize(results, top=args.top))


if __name__ == "__main__":
    main()
