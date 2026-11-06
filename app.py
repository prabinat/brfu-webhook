#!/usr/bin/env python3
"""Isolated Twilio WhatsApp/SMS webhook lab. Never forwards messages."""
import hashlib
import hmac
import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from xml.sax.saxutils import escape

PORT = int(os.getenv("PORT", "8080"))
BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
DB_PATH = os.getenv("DB_PATH", "/data/events.db")
MAX_BYTES = 8192
PATHS = {"/api/webhooks/twilio/whatsapp", "/api/match-day-ops/rsvp/webhook"}
LOCK = threading.Lock()
SID_RE = re.compile(r"^(SM|MM)[a-fA-F0-9]{32}$")
FIXTURE_RE = re.compile(r"^(YES|NO|MAYBE)(?:\s+FX#([0-9]{1,6}))?$", re.I)

def init_db():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    with sqlite3.connect(DB_PATH) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS events (
          message_sid TEXT PRIMARY KEY, received_utc TEXT NOT NULL,
          sender_masked TEXT NOT NULL, recipient_masked TEXT NOT NULL,
          command TEXT NOT NULL, fixture TEXT, status TEXT NOT NULL)""")

def mask_address(value):
    if not value:
        return "(empty)"
    number = re.sub(r"\D", "", value)
    channel = "whatsapp:" if value.lower().startswith("whatsapp:") else "sms:"
    return channel + "***" + number[-4:] if number else channel + "(invalid)"

def validate_twilio_signature(public_url, fields, signature, auth_token):
    """Twilio signed x-www-form-urlencoded parameters: URL + sorted key/value pairs."""
    if not public_url or not signature or not auth_token:
        return False
    payload = public_url + "".join(key + fields[key] for key in sorted(fields))
    digest = hmac.new(auth_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1).digest()
    import base64
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(signature, expected)

def process(fields):
    sid = fields.get("MessageSid", "")
    if not SID_RE.fullmatch(sid):
        return "invalid"
    match = FIXTURE_RE.fullmatch(fields.get("Body", "").strip())
    command = match.group(1).upper() if match else "UNKNOWN"
    fixture = match.group(2) if match else None
    row = (sid, datetime.now(timezone.utc).isoformat(), mask_address(fields.get("From", "")),
           mask_address(fields.get("To", "")), command, fixture,
           "recognized" if match else "unrecognized")
    with LOCK, sqlite3.connect(DB_PATH) as db:
        existing = db.execute("SELECT 1 FROM events WHERE message_sid = ?", (sid,)).fetchone()
        if existing:
            return "duplicate"
        db.execute("""INSERT INTO events
          (message_sid,received_utc,sender_masked,recipient_masked,command,fixture,status)
          VALUES (?,?,?,?,?,?,?)""", row)
    return "recorded"

class Handler(BaseHTTPRequestHandler):
    server_version = "BRFU-UAT-Webhooks"
    sys_version = ""

    def log_message(self, format, *args):
        # No URL query data, request headers, phone numbers or payloads are logged.
        print("request completed", flush=True)

    def reply(self, code, body, content_type="application/json"):
        raw = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def json(self, code, obj):
        self.reply(code, json.dumps(obj, separators=(",", ":")))

    def admin_ok(self):
        auth = self.headers.get("Authorization", "")
        if not ADMIN_TOKEN or not auth.startswith("Bearer "):
            return False
        return hmac.compare_digest(auth[7:], ADMIN_TOKEN)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/health":
            self.json(200, {"status":"ok", "service":"brfu-webhook-uat", "live_brfu_writes":False})
            return
        if path in PATHS:
            self.send_response(405)
            self.send_header("Allow", "POST")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path in ("/events", "/stats"):
            if not self.admin_ok():
                self.json(401, {"error":"unauthorized"})
                return
            with LOCK, sqlite3.connect(DB_PATH) as db:
                if path == "/events":
                    db.row_factory = sqlite3.Row
                    rows = db.execute("SELECT * FROM events ORDER BY received_utc DESC LIMIT 50").fetchall()
                    self.json(200, {"events":[dict(row) for row in rows]})
                else:
                    counts = db.execute("SELECT status,COUNT(*) FROM events GROUP BY status").fetchall()
                    total = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                    self.json(200, {"total":total, "by_status":dict(counts)})
            return
        self.json(404, {"detail":"Not Found"})

    def do_POST(self):
        path = urlsplit(self.path).path
        if path not in PATHS:
            self.json(404, {"detail":"Not Found"})
            return
        size_text = self.headers.get("Content-Length", "")
        try:
            size = int(size_text)
        except ValueError:
            self.json(411, {"error":"length required"})
            return
        if size <= 0 or size > MAX_BYTES:
            self.json(413, {"error":"invalid body size"})
            return
        content_type = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type != "application/x-www-form-urlencoded":
            self.json(415, {"error":"expected form-urlencoded"})
            return
        try:
            raw = self.rfile.read(size).decode("utf-8", errors="strict")
            parsed = parse_qs(raw, keep_blank_values=True, strict_parsing=True, max_num_fields=30)
            if any(len(values) != 1 for values in parsed.values()):
                raise ValueError("duplicate form key")
            fields = {key: values[0] for key, values in parsed.items()}
        except (UnicodeError, ValueError):
            self.json(400, {"error":"invalid form"})
            return
        # Fixed origin URL; never trust Host / X-Forwarded-* headers for HMAC validation.
        url = BASE_URL + path
        signature = self.headers.get("X-Twilio-Signature", "")
        if not validate_twilio_signature(url, fields, signature, AUTH_TOKEN):
            self.json(403, {"error":"invalid Twilio signature"})
            return
        result = process(fields)
        if result == "invalid":
            self.json(400, {"error":"invalid MessageSid"})
            return
        # Empty TwiML means ACK without generating an outbound message/cost.
        self.reply(200, '<?xml version="1.0" encoding="UTF-8"?><Response></Response>', "text/xml")

if __name__ == "__main__":
    if not BASE_URL.startswith("https://") or not AUTH_TOKEN or len(ADMIN_TOKEN) < 24:
        raise SystemExit("Set HTTPS PUBLIC_BASE_URL, TWILIO_AUTH_TOKEN and ADMIN_TOKEN (24+ chars)")
    if urlsplit(BASE_URL).path not in ("", "/") or urlsplit(BASE_URL).query:
        raise SystemExit("PUBLIC_BASE_URL must be a hostname origin only")
    init_db()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("BRFU UAT receiver listening (no outbound sending enabled)", flush=True)
    server.serve_forever()
