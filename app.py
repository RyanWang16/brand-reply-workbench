#!/usr/bin/env python3
"""Local AI reply generator for authorized, manually supplied post text."""

from __future__ import annotations

import json
import errno
import io
import os
import sys
import threading
import webbrowser
import zipfile
from xml.sax.saxutils import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
PRODUCTS_FILE = ROOT / "config" / "products.json"
MAX_POST_TEXT = 5_000


def load_local_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key and key.replace("_", "").isalnum():
            os.environ.setdefault(key, value)


load_local_env()
sys.path.insert(0, str(ROOT / "scripts"))
from generate_drafts import request_model  # noqa: E402


def read_products() -> dict:
    return json.loads(PRODUCTS_FILE.read_text(encoding="utf-8"))


def write_products(config: dict) -> None:
    PRODUCTS_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def send_json(handler: BaseHTTPRequestHandler, payload: dict, status: int = 200) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)


class Handler(BaseHTTPRequestHandler):
    server_version = "BrandReplyStudio/1.0"

    def log_message(self, fmt: str, *args) -> None:
        if self.path.startswith("/api/"):
            print(f"{self.command} {urlparse(self.path).path}")
        else:
            super().log_message(fmt, *args)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 1_000_000:
            raise ValueError("请求内容为空或过大")
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("请求格式无效") from exc
        if not isinstance(body, dict):
            raise ValueError("请求格式无效")
        return body

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            config = read_products()
            return send_json(self, {
                "products": config.get("products", {}),
                "disclosure_note": config.get("disclosure_note", ""),
                "ai_configured": all(os.getenv(key) for key in ("AI_API_BASE", "AI_API_KEY", "AI_MODEL")),
            })
        return self.serve_static(parsed.path)

    def do_POST(self) -> None:
        try:
            body = self.read_json()
            path = urlparse(self.path).path
            if path == "/api/generate":
                return self.generate(body)
            if path == "/api/products":
                return self.update_products(body)
            if path == "/api/export":
                return self.export_handoff(body)
            return send_json(self, {"error": "找不到此接口"}, 404)
        except ValueError as exc:
            return send_json(self, {"error": str(exc)}, 400)
        except Exception as exc:
            print(f"Request failed: {type(exc).__name__}", file=sys.stderr)
            return send_json(self, {"error": "处理失败，请检查设置后重试"}, 500)

    def generate(self, body: dict) -> None:
        source_url = str(body.get("url", "")).strip()
        parsed_url = urlparse(source_url)
        host = (parsed_url.hostname or "").lower()
        domains = ("xiaohongshu.com", "xhslink.com")
        if parsed_url.scheme != "https" or not any(host == domain or host.endswith("." + domain) for domain in domains):
            raise ValueError("请输入有效的小红书 https 链接")
        if parsed_url.username or parsed_url.password:
            raise ValueError("链接格式无效")

        title = str(body.get("post_title", "")).strip()
        post_text = str(body.get("post_text", "")).strip()
        product_id = str(body.get("product_id", "")).strip()
        if not title or len(title) > 200:
            raise ValueError("请填写帖子标题，最多 200 个字符")
        if not post_text or len(post_text) > MAX_POST_TEXT:
            raise ValueError(f"请粘贴帖子正文，最多 {MAX_POST_TEXT} 个字符")

        config = read_products()
        product = config.get("products", {}).get(product_id)
        if not product:
            raise ValueError("请先选择一个产品")
        if not product.get("approved_points"):
            raise ValueError("这个产品还没有填写已审核的卖点，请先到‘产品卖点’页面添加")
        api_base, api_key, model = (os.getenv(key, "") for key in ("AI_API_BASE", "AI_API_KEY", "AI_MODEL"))
        if not all((api_base, api_key, model)):
            raise ValueError("AI 服务还没有配置，请联系管理员完成一次设置")

        # The source URL is intentionally not sent to the model; only the pasted text and approved facts are used.
        result = request_model(
            {"post_title": title, "post_text": post_text},
            product,
            api_base,
            api_key,
            model,
        )
        result["disclosure_note"] = config.get("disclosure_note", "") if result["decision"] == "draft" else ""
        return send_json(self, result)

    def update_products(self, body: dict) -> None:
        config = read_products()
        product_id = str(body.get("product_id", ""))
        product = config.get("products", {}).get(product_id)
        if not product:
            raise ValueError("找不到这个产品")
        raw_points = body.get("approved_points", [])
        if not isinstance(raw_points, list) or len(raw_points) > 20:
            raise ValueError("卖点最多填写 20 条")
        points = []
        for raw in raw_points:
            point = str(raw).strip()
            if point and point not in points:
                if len(point) > 200:
                    raise ValueError("每条卖点最多 200 个字符")
                points.append(point)
        product["approved_points"] = points
        write_products(config)
        return send_json(self, {"product_id": product_id, "approved_points": points})

    def export_handoff(self, body: dict) -> None:
        records = body.get("records")
        if not isinstance(records, list) or not records or len(records) > 500:
            raise ValueError("交付清单需包含 1 至 500 条内容")
        rows = []
        domains = ("xiaohongshu.com", "xhslink.com")
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("交付清单格式无效")
            url = str(record.get("url", "")).strip()
            parsed_url = urlparse(url)
            host = (parsed_url.hostname or "").lower()
            if parsed_url.scheme != "https" or not any(host == domain or host.endswith("." + domain) for domain in domains):
                raise ValueError("交付清单中有无效的小红书链接")
            title = str(record.get("post_title", "")).strip()
            product = str(record.get("product", "")).strip()
            reply = str(record.get("reply", "")).strip()
            if not reply or len(reply) > 2_000:
                raise ValueError("每条回复需填写，且不超过 2000 个字符")
            rows.append([url, title, product, reply, "品牌官方/项目运营账号"])

        data = make_handoff_workbook(rows)
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.send_header("Content-Disposition", "attachment; filename=brand-reply-handoff.xlsx; filename*=UTF-8''%E5%93%81%E7%89%8C%E5%9B%9E%E5%A4%8D%E4%BA%A4%E4%BB%98%E6%B8%85%E5%8D%95.xlsx")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def serve_static(self, raw_path: str) -> None:
        relative = unquote(raw_path).lstrip("/") or "index.html"
        target = (WEB / relative).resolve()
        if target != WEB.resolve() and WEB.resolve() not in target.parents:
            return send_json(self, {"error": "禁止访问此路径"}, 403)
        if not target.is_file():
            return send_json(self, {"error": "页面不存在"}, 404)
        mime = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime.get(target.suffix, "application/octet-stream"))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def make_handoff_workbook(records: list[list[str]]) -> bytes:
    """Create a small Excel workbook using only Python's standard library."""
    headers = ["帖子链接", "帖子标题", "产品", "品牌回复内容", "回复身份"]
    all_rows = [headers, *records]

    def column_name(index: int) -> str:
        name = ""
        while index:
            index, remainder = divmod(index - 1, 26)
            name = chr(65 + remainder) + name
        return name

    sheet_rows = []
    for row_number, values in enumerate(all_rows, start=1):
        cells = []
        for column_number, value in enumerate(values, start=1):
            address = f"{column_name(column_number)}{row_number}"
            style = ' s="1"' if row_number == 1 else ""
            safe_value = escape(str(value), {'"': "&quot;", "'": "&apos;"})
            cells.append(f'<c r="{address}" t="inlineStr"{style}><is><t xml:space="preserve">{safe_value}</t></is></c>')
        sheet_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        '<cols><col min="1" max="1" width="42" customWidth="1"/><col min="2" max="3" width="24" customWidth="1"/><col min="4" max="4" width="64" customWidth="1"/><col min="5" max="5" width="28" customWidth="1"/></cols>'
        f'<sheetData>{"".join(sheet_rows)}</sheetData><autoFilter ref="A1:E{len(all_rows)}"/>'
        '</worksheet>'
    )
    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="回复交付清单" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
        "xl/styles.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="等线"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="等线"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF6558C9"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>',
        "xl/worksheets/sheet1.xml": sheet_xml,
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as workbook:
        for filename, content in files.items():
            workbook.writestr(filename, content.encode("utf-8"))
    return output.getvalue()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="启动品牌回复生成器")
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    args = parser.parse_args()
    requested_port = int(os.getenv("PORT", "8765"))
    server = None
    for port in range(requested_port, requested_port + 20):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError as exc:
            if exc.errno not in {errno.EADDRINUSE, 48, 98}:
                raise
    if server is None:
        raise OSError("没有可用的本地端口，请关闭已打开的生成器后重试")
    port = server.server_address[1]
    print(f"生成器已启动：http://127.0.0.1:{port}")
    if args.open:
        threading.Timer(0.8, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    server.serve_forever()
