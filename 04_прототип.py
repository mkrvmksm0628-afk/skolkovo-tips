#!/usr/bin/env python3
"""Учебная платформа чаевых. Запуск: python 04_прототип.py request.json.
Только стандартная библиотека. Входные CSV неизменны.
SQLite хранит состояния, ключи и аудит; CSV/JSONL — выгрузки для просмотра.
"""
import csv
import hashlib
import hmac
import json
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
OUT_FIELDS = ["payment_id", "merchant_id", "employee_id", "amount", "tip", "status", "timestamp"]


class PaymentError(Exception):
    def __init__(self, code, message, http=422, retryable=False):
        super().__init__(message)
        self.code, self.http, self.retryable = code, http, retryable


def load_qr(qr_id, data_dir=BASE_DIR):
    with open(Path(data_dir) / "qr_links.csv", encoding="utf-8-sig", newline="") as f:
        return next((r for r in csv.DictReader(f) if r["qr_id"] == qr_id), None)


def verify_signature(qr, secret):
    payload = "|".join(qr[k] for k in ("merchant_id", "employee_id", "receipt_id", "nonce", "exp"))
    expected = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    return hmac.compare_digest(expected, qr["signature"])


def parse_time(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Нужен часовой пояс")
    return result.astimezone(timezone.utc)


def check_exp(qr, now=None):
    return parse_time(qr["exp"]) >= (now or datetime.now(timezone.utc))


def number(value):
    try:
        if isinstance(value, bool):
            raise ValueError()
        value = Decimal(str(value))
        if not value.is_finite():
            raise ValueError()
        return value
    except (InvalidOperation, ValueError):
        raise PaymentError("VALIDATION_ERROR", "Ожидалось конечное число") from None


def calc_amounts(base, pct):
    base, pct = number(base), number(pct)
    if base <= 0 or base > 1000000:
        raise PaymentError("AMOUNT_OUT_OF_RANGE", "Сумма чека вне допустимого диапазона")
    if base != base.quantize(Decimal(".01")):
        raise PaymentError("VALIDATION_ERROR", "В сумме чека допускаются только копейки")
    if pct < 0 or pct > 50:
        raise PaymentError("TIP_OUT_OF_RANGE", "Чаевые должны быть от 0 до 50 процентов")
    tip = (base * pct / 100).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
    total = base + tip
    if total > 2000000:
        raise PaymentError("AMOUNT_OUT_OF_RANGE", "Итоговая сумма превышает лимит")
    return tip, total


def emulate_gateway(qr_id, nonce):
    # Формула исходного задания без подмены результата.
    h = int(hashlib.md5((qr_id + nonce).encode("utf-8")).hexdigest(), 16) % 10
    if h <= 7:
        return "SUCCESS", None
    if h == 8:
        return "DECLINED", "CARD_DECLINED"
    return "PENDING", "GATEWAY_TIMEOUT"


class Platform:
    def __init__(self, data_dir=BASE_DIR, out_dir=BASE_DIR, clock=None, secret=None):
        self.data_dir, self.out_dir = Path(data_dir), Path(out_dir)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.secret = secret if secret is not None else os.environ.get("HMAC_SECRET", "").encode()
        if not self.secret:
            raise PaymentError("CONFIG_ERROR", "Задайте HMAC_SECRET в окружении", 500)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.out_dir / "payments_state.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS payments (
                payment_id TEXT PRIMARY KEY, qr_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE, request TEXT NOT NULL,
                merchant_id TEXT NOT NULL, employee_id TEXT NOT NULL,
                amount TEXT NOT NULL, tip TEXT NOT NULL, status TEXT NOT NULL,
                timestamp TEXT NOT NULL, error_code TEXT);
            CREATE TABLE IF NOT EXISTS audit (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL);
        """)

    def close(self):
        self.db.close()

    def timestamp(self):
        return self.clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def read_csv(self, name):
        with open(self.data_dir / name, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))

    def token(self, payment_id):
        return hmac.new(self.secret, ("confirm|" + payment_id).encode(), hashlib.sha256).hexdigest()

    def audit(self, p, action, old=None, error=None):
        event = dict(payment_id=p["payment_id"], qr_id=p["qr_id"], action=action,
                     from_status=old, to_status=p["status"],
                     amount_total=float(Decimal(p["amount"]) + Decimal(p["tip"])),
                     actor="system" if action in ("payment_succeeded", "payment_declined", "gateway_timeout") else "client",
                     at=self.timestamp(), error_code=error)
        self.db.execute("INSERT INTO audit(event) VALUES (?)", (json.dumps(event, ensure_ascii=False),))

    def response(self, p, http=200):
        result = dict(payment_id=p["payment_id"], status=p["status"],
                      amount_total=float(Decimal(p["amount"]) + Decimal(p["tip"])),
                      tip_amount=float(Decimal(p["tip"])), error_code=p["error_code"], http_status=http)
        if p["status"] == "CREATED":
            result["confirm_token"] = self.token(p["payment_id"])
        return result

    def get_payment(self, payment_id):
        p = self.db.execute("SELECT * FROM payments WHERE payment_id=?", (payment_id,)).fetchone()
        if p is None:
            raise PaymentError("PAYMENT_NOT_FOUND", "Платёж не найден", 404)
        return dict(p)

    def validate_qr(self, req):
        qr = load_qr(req["qr_id"], self.data_dir)
        if not qr:
            raise PaymentError("INVALID_QR", "QR не найден")
        if not re.fullmatch("[0-9a-f]{6}", qr["nonce"]) or not verify_signature(qr, self.secret):
            raise PaymentError("INVALID_QR", "Подпись или структура QR неверна")
        try:
            valid = check_exp(qr, self.clock())
        except (ValueError, TypeError):
            raise PaymentError("INVALID_QR", "Неверный срок QR") from None
        if not valid:
            raise PaymentError("QR_EXPIRED", "QR истёк", 410, True)
        merchant = next((m for m in self.read_csv("merchants.csv") if m["merchant_id"] == qr["merchant_id"]), None)
        employee = next((e for e in self.read_csv("employees.csv") if e["employee_id"] == qr["employee_id"]), None)
        if not merchant or not employee or employee["merchant_id"] != qr["merchant_id"]:
            raise PaymentError("INVALID_QR", "Неверная связь ресторана и сотрудника")
        if merchant["status"] != "ACTIVE":
            code = "MERCHANT_BLOCKED" if merchant["status"] == "BLOCKED" else "MERCHANT_NOT_ACTIVE"
            raise PaymentError(code, "Ресторан не активен", 403)
        if req["receipt_id"] != qr["receipt_id"]:
            raise PaymentError("RECEIPT_MISMATCH", "QR принадлежит другому чеку")
        used = self.db.execute("SELECT 1 FROM payments WHERE qr_id=? AND status IN ('CREATED','PENDING','SUCCESS')",
                               (qr["qr_id"],)).fetchone()
        if used:
            raise PaymentError("QR_USED", "QR уже занят или оплачен", 409)
        return qr

    def create(self, req):
        for field in ("qr_id", "receipt_id", "idempotency_key"):
            if not isinstance(req.get(field), str) or not req[field].strip():
                raise PaymentError("VALIDATION_ERROR", "Не заполнено поле " + field)
        if "base_amount" not in req or "tip_pct" not in req:
            raise PaymentError("VALIDATION_ERROR", "Нужны base_amount и tip_pct")
        base, pct = number(req["base_amount"]), number(req["tip_pct"])
        fingerprint = json.dumps([req["qr_id"], req["receipt_id"], str(base.normalize()), str(pct.normalize())])
        existing = self.db.execute("SELECT * FROM payments WHERE idempotency_key=?", (req["idempotency_key"],)).fetchone()
        if existing:
            if existing["request"] != fingerprint:
                raise PaymentError("IDEMPOTENCY_CONFLICT", "Ключ уже связан с другим запросом", 409)
            return dict(existing), False
        qr = self.validate_qr(req)
        tip, _ = calc_amounts(base, pct)
        largest = self.db.execute("SELECT MAX(CAST(SUBSTR(payment_id,3) AS INTEGER)) FROM payments").fetchone()[0] or 100
        p = dict(payment_id=f"p_{largest + 1:04d}", qr_id=qr["qr_id"],
                 idempotency_key=req["idempotency_key"], request=fingerprint,
                 merchant_id=qr["merchant_id"], employee_id=qr["employee_id"],
                 amount=f"{base:.2f}", tip=f"{tip:.2f}", status="CREATED",
                 timestamp=self.timestamp(), error_code=None)
        columns, placeholders = ",".join(p), ",".join("?" for _ in p)
        self.db.execute(f"INSERT INTO payments({columns}) VALUES ({placeholders})", tuple(p.values()))
        self.audit(p, "payment_created")
        return p, True

    def confirm(self, p):
        if p["status"] != "CREATED":
            return p
        p["status"] = "PENDING"
        self.audit(p, "payment_confirmed", "CREATED")
        qr = load_qr(p["qr_id"], self.data_dir)
        p["status"], p["error_code"] = emulate_gateway(qr["qr_id"], qr["nonce"])
        self.db.execute("UPDATE payments SET status=?,error_code=? WHERE payment_id=?",
                        (p["status"], p["error_code"], p["payment_id"]))
        action = {"SUCCESS": "payment_succeeded", "DECLINED": "payment_declined", "PENDING": "gateway_timeout"}[p["status"]]
        self.audit(p, action, "PENDING", p["error_code"])
        return p

    def export(self):
        # Выгрузка восстанавливается из SQLite; повтор не добавляет вторую строку.
        # Блокировка не даёт параллельной выгрузке затереть более свежие строки.
        self.db.execute("BEGIN IMMEDIATE")
        target = self.out_dir / "payments_out.csv"
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=self.out_dir, delete=False) as f:
            temporary = f.name
            writer = csv.DictWriter(f, fieldnames=OUT_FIELDS)
            writer.writeheader()
            for row in self.db.execute("SELECT * FROM payments ORDER BY payment_id"):
                writer.writerow({k: row[k] for k in OUT_FIELDS})
        os.replace(temporary, target)
        target = self.out_dir / "audit_out.jsonl"
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.out_dir, delete=False) as f:
            temporary = f.name
            for row in self.db.execute("SELECT * FROM audit ORDER BY event_id"):
                event = dict(event_id=f"evt_{row['event_id']:04d}", **json.loads(row["event"]))
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        os.replace(temporary, target)

        self.db.commit()

    def reject(self, req, error):
        # Ошибка до создания тоже остаётся в аудите, без реквизитов и токенов.
        payment = None
        if isinstance(req.get("payment_id"), str):
            row = self.db.execute("SELECT * FROM payments WHERE payment_id=?", (req["payment_id"],)).fetchone()
            payment = dict(row) if row else None
        qr_id = payment["qr_id"] if payment else req.get("qr_id")
        qr = load_qr(qr_id, self.data_dir) if isinstance(qr_id, str) else None
        status = payment["status"] if payment else None
        event = dict(payment_id=payment["payment_id"] if payment else None,
                     qr_id=qr["qr_id"] if qr else None, action="request_rejected",
                     from_status=status, to_status=status,
                     amount_total=float(Decimal(payment["amount"]) + Decimal(payment["tip"])) if payment else None,
                     actor="client", at=self.timestamp(), error_code=error.code)
        self.db.execute("INSERT INTO audit(event) VALUES (?)", (json.dumps(event, ensure_ascii=False),))
        self.db.commit()
        self.export()

    def handle(self, req):
        if not isinstance(req, dict):
            return dict(status="ERROR", error_code="VALIDATION_ERROR", message="Нужен JSON-объект", http_status=422)
        try:
            self.db.execute("BEGIN IMMEDIATE")
            action = req.get("action", "pay")
            if action in ("pay", "create"):
                p, created = self.create(req)
                if action == "pay" and p["status"] == "CREATED":
                    p = self.confirm(p)
                http = 201 if action == "create" and created else 200
                if created and action == "pay":
                    http = {"SUCCESS": 200, "DECLINED": 402, "PENDING": 202}[p["status"]]
            elif action in ("confirm", "cancel", "status"):
                if not isinstance(req.get("payment_id"), str):
                    raise PaymentError("VALIDATION_ERROR", "Нужен payment_id")
                p, http = self.get_payment(req["payment_id"]), 200
                if action == "confirm":
                    supplied = req.get("confirm_token")
                    if not isinstance(supplied, str) or not hmac.compare_digest(self.token(p["payment_id"]), supplied):
                        raise PaymentError("FORBIDDEN", "Неверный токен подтверждения", 403)
                    p = self.confirm(p)
                    http = {"SUCCESS": 200, "DECLINED": 402, "PENDING": 202}.get(p["status"], 200)
                elif action == "cancel":
                    if p["status"] != "CREATED":
                        raise PaymentError("CANCEL_NOT_ALLOWED", "Отмена разрешена только до подтверждения", 409)
                    p["status"] = "CANCELED"
                    self.db.execute("UPDATE payments SET status=? WHERE payment_id=?", ("CANCELED", p["payment_id"]))
                    self.audit(p, "payment_canceled", "CREATED")
            else:
                raise PaymentError("VALIDATION_ERROR", "Неизвестное действие")
            self.db.commit()
            self.export()
            return self.response(p, http)
        except PaymentError as error:
            self.db.rollback()
            self.reject(req, error)
            return dict(status="ERROR", error_code=error.code, message=str(error),
                        http_status=error.http, retryable=error.retryable)
        except Exception:
            self.db.rollback()
            raise


def main():
    platform = None
    try:
        if len(sys.argv) != 2:
            raise PaymentError("VALIDATION_ERROR", "Использование: python 04_прототип.py request.json")
        with open(sys.argv[1], encoding="utf-8-sig") as f:
            req = json.load(f, parse_float=Decimal)
        platform = Platform()
        result = platform.handle(req)
    except PaymentError as error:
        result = dict(status="ERROR", error_code=error.code, message=str(error), http_status=error.http)
    except (OSError, ValueError, sqlite3.Error):
        result = dict(status="ERROR", error_code="INPUT_OR_STORAGE_ERROR",
                      message="Проверьте JSON, CSV и доступ к выходным файлам", http_status=500)
    finally:
        if platform is not None:
            platform.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in ("CREATED", "SUCCESS", "CANCELED") else 1


if __name__ == "__main__":
    sys.exit(main())
