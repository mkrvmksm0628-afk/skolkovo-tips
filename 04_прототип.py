#!/usr/bin/env python3
"""
CLI-прототип платформы чаевых — кейс 3.

TODO:
    1. load_qr() — читать qr_links.csv.
    2. verify_signature() — HMAC-SHA256.
    3. check_exp() — TTL.
    4. emulate_gateway() — 4 сценария (0..7 SUCCESS, 8 DECLINED, 9 TIMEOUT).
    5. calc_amounts() — tip + total.
    6. write_payment() — append в payments_out.csv.
"""
import csv
import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

SECRET = b"demo-secret-k3"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
QR_CSV = os.path.join(BASE_DIR, "qr_links.csv")
OUT_CSV = os.path.join(BASE_DIR, "payments_out.csv")

OUT_FIELDS = ["payment_id", "merchant_id", "employee_id", "amount", "tip", "status", "timestamp"]


def load_qr(qr_id):
    with open(QR_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["qr_id"] == qr_id:
                return row
    return None


def verify_signature(qr):
    payload = f"{qr['merchant_id']}|{qr['employee_id']}|{qr['receipt_id']}|{qr['nonce']}|{qr['exp']}"
    expected = hmac.new(SECRET, payload.encode(), hashlib.sha256).hexdigest()[:16]
    return hmac.compare_digest(expected, qr["signature"])


def check_exp(qr):
    """QR действителен, пока exp >= текущего времени UTC."""
    try:
        exp = datetime.fromisoformat(qr["exp"].strip().replace("Z", "+00:00"))
        return exp.tzinfo is not None and exp >= datetime.now(timezone.utc)
    except ValueError:
        return False


def calc_amounts(base, pct):
    try:
        if isinstance(base, bool) or isinstance(pct, bool):
            raise ValueError("VALIDATION_ERROR")
        base, pct = Decimal(str(base)), Decimal(str(pct))
        if not base.is_finite() or not pct.is_finite():
            raise ValueError("VALIDATION_ERROR")
    except InvalidOperation:
        raise ValueError("VALIDATION_ERROR") from None
    if not 0 < base <= 1000000:
        raise ValueError("AMOUNT_OUT_OF_RANGE")
    if not 0 <= pct <= 50:
        raise ValueError("TIP_OUT_OF_RANGE")
    if base != base.quantize(Decimal("0.01")):
        raise ValueError("VALIDATION_ERROR")
    tip = (base * pct / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return tip, base + tip


def emulate_gateway(qr_id, nonce):
    """TODO: 0..7 SUCCESS, 8 DECLINED, 9 TIMEOUT."""
    h = int(hashlib.md5((qr_id + nonce).encode()).hexdigest(), 16) % 10
    if h <= 7:
        return "SUCCESS", None
    if h == 8:
        return "DECLINED", "CARD_DECLINED"
    return "TIMEOUT", "GATEWAY_TIMEOUT"


def write_payment(payment):
    new_file = not os.path.exists(OUT_CSV) or os.path.getsize(OUT_CSV) == 0
    with open(OUT_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OUT_FIELDS)
        if new_file:
            w.writeheader()
        w.writerow(payment)


def main():
    if len(sys.argv) < 2:
        print("Использование: python 04_прототип.py qr.json")
        sys.exit(1)

    try:
        with open(sys.argv[1], encoding="utf-8-sig") as f:
            req = json.load(f, parse_float=Decimal)
        if not isinstance(req, dict) or not isinstance(req.get("qr_id"), str) or not req["qr_id"].strip():
            raise ValueError()
        qr_id = req["qr_id"]
        base = req.get("base_amount", 1500)
        pct = req.get("tip_pct", 10)
    except (ValueError, OSError, UnicodeError):
        print(json.dumps({"status": "ERROR", "error_code": "INVALID_REQUEST"}, ensure_ascii=False))
        sys.exit(2)

    qr = load_qr(qr_id)
    if not qr:
        print(json.dumps({"status": "ERROR", "error_code": "INVALID_QR"}, ensure_ascii=False))
        sys.exit(2)

    if not verify_signature(qr):
        print(json.dumps({"status": "ERROR", "error_code": "INVALID_QR"}, ensure_ascii=False))
        sys.exit(2)

    if not check_exp(qr):
        print(json.dumps({"status": "ERROR", "error_code": "QR_EXPIRED"}, ensure_ascii=False))
        sys.exit(2)

    try:
        tip, total = calc_amounts(base, pct)
        base = total - tip
    except (ValueError, InvalidOperation) as error:
        print(json.dumps({"status": "ERROR", "error_code": str(error) if isinstance(error, ValueError) else "VALIDATION_ERROR"}, ensure_ascii=False))
        sys.exit(2)
    status, error = emulate_gateway(qr_id, qr["nonce"])

    payment = {
        "payment_id": f"p_demo_{qr_id[-3:]}",
        "merchant_id": qr["merchant_id"],
        "employee_id": qr["employee_id"],
        "amount": f"{base:.2f}",
        "tip": f"{tip:.2f}",
        "status": status,
        "timestamp": "2025-11-01T18:00:00Z",
    }
    write_payment(payment)

    print(json.dumps({
        "payment_id": payment["payment_id"],
        "status": status,
        "amount_total": float(total),
        "tip_amount": float(tip),
        "error_code": error,
    }, ensure_ascii=False, indent=2))

    sys.exit(0 if status == "SUCCESS" else 1)


if __name__ == "__main__":
    try:
        main()
    except (OSError, UnicodeError, csv.Error, KeyError):
        print(json.dumps({"status": "ERROR", "error_code": "FILE_OR_DATA_ERROR"}, ensure_ascii=False))
        sys.exit(2)