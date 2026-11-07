#!/usr/bin/env python3
"""Демонстрация: QR → проверки → расчёт → эмулятор → CSV.
Запуск: python 04_прототип.py request.json [--demo]
Повторы, отмена и аудит не реализованы: это демонстрационный скрипт.
"""
import csv
import hashlib
import hmac
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
OUT_CSV = BASE_DIR / 'payments_demo_out.csv'
OUT_FIELDS = ['payment_id', 'merchant_id', 'employee_id', 'amount', 'tip', 'status', 'timestamp']


def load_row(filename, field, value):
    with open(BASE_DIR / filename, encoding='utf-8-sig', newline='') as f:
        return next((row for row in csv.DictReader(f) if row[field] == value), None)


def load_qr(qr_id):
    return load_row('qr_links.csv', 'qr_id', qr_id)


def verify_signature(qr, secret):
    payload = '|'.join(qr[k] for k in ('merchant_id', 'employee_id', 'receipt_id', 'nonce', 'exp'))
    expected = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()[:16]
    return hmac.compare_digest(expected, qr['signature'])


def check_exp(qr, now):
    try:
        exp = datetime.fromisoformat(qr['exp'].strip().replace('Z', '+00:00'))
        return exp.tzinfo is not None and exp >= now
    except ValueError:
        return False


def calc_amounts(base, pct):
    try:
        if isinstance(base, bool) or isinstance(pct, bool):
            raise ValueError()
        base, pct = Decimal(str(base)), Decimal(str(pct))
        if not base.is_finite() or not pct.is_finite():
            raise ValueError()
    except (InvalidOperation, ValueError):
        raise ValueError('VALIDATION_ERROR') from None
    if not 0 < base <= 1000000:
        raise ValueError('AMOUNT_OUT_OF_RANGE')
    if not 0 <= pct <= 50:
        raise ValueError('TIP_OUT_OF_RANGE')
    if base != base.quantize(Decimal('0.01')):
        raise ValueError('VALIDATION_ERROR')
    tip = (base * pct / 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return tip, base + tip


def emulate_gateway(qr_id, nonce):
    h = int(hashlib.md5((qr_id + nonce).encode()).hexdigest(), 16) % 10
    if h <= 7:
        return 'SUCCESS', None
    if h == 8:
        return 'DECLINED', 'CARD_DECLINED'
    # Тайм-аут означает неизвестный результат, а не отказ.
    return 'PENDING', 'GATEWAY_TIMEOUT'


def write_payment(payment):
    new_file = not OUT_CSV.exists() or OUT_CSV.stat().st_size == 0
    with open(OUT_CSV, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=OUT_FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerow(payment)


def main():
    try:
        if len(sys.argv) not in (2, 3) or (len(sys.argv) == 3 and sys.argv[2] != '--demo'):
            raise ValueError('USAGE: python 04_прототип.py request.json [--demo]')
        with open(sys.argv[1], encoding='utf-8-sig') as f:
            req = json.load(f, parse_float=Decimal)
        if not isinstance(req, dict):
            raise ValueError('VALIDATION_ERROR')
        for field in ('qr_id', 'receipt_id'):
            if not isinstance(req.get(field), str) or not req[field].strip():
                raise ValueError('VALIDATION_ERROR')
        if 'base_amount' not in req or 'tip_pct' not in req:
            raise ValueError('VALIDATION_ERROR')
        if req.get('action', 'pay') != 'pay':
            raise ValueError('UNSUPPORTED_ACTION')
        secret = os.environ.get('HMAC_SECRET', '').encode()
        if not secret:
            raise ValueError('CONFIG_ERROR: задайте HMAC_SECRET')
        demo = len(sys.argv) == 3
        now = datetime(2025, 11, 1, 18, tzinfo=timezone.utc) if demo else datetime.now(timezone.utc)

        qr = load_qr(req['qr_id'])
        if not qr or not verify_signature(qr, secret):
            raise ValueError('INVALID_QR')
        if not check_exp(qr, now):
            raise ValueError('QR_EXPIRED')
        merchant = load_row('merchants.csv', 'merchant_id', qr['merchant_id'])
        employee = load_row('employees.csv', 'employee_id', qr['employee_id'])
        if not merchant or not employee or employee['merchant_id'] != qr['merchant_id']:
            raise ValueError('INVALID_QR')
        if merchant['status'] != 'ACTIVE':
            raise ValueError('MERCHANT_BLOCKED' if merchant['status'] == 'BLOCKED' else 'MERCHANT_NOT_ACTIVE')
        if req['receipt_id'] != qr['receipt_id']:
            raise ValueError('RECEIPT_MISMATCH')

        tip, total = calc_amounts(req['base_amount'], req['tip_pct'])
        status, error = emulate_gateway(req['qr_id'], qr['nonce'])
        payment = {
            'payment_id': 'p_demo_' + uuid.uuid4().hex,
            'merchant_id': qr['merchant_id'], 'employee_id': qr['employee_id'],
            'amount': f'{total - tip:.2f}', 'tip': f'{tip:.2f}',
            'status': status, 'timestamp': now.isoformat().replace('+00:00', 'Z'),
        }
        write_payment(payment)
        result = {
            'payment_id': payment['payment_id'], 'status': status,
            'amount_total': float(total), 'tip_amount': float(tip),
            'error_code': error, 'demo': demo,
            'notice': 'Учебная демонстрация: каждый запуск — новая попытка; защиты от повторов нет.',
        }
    except json.JSONDecodeError:
        result = {'status': 'ERROR', 'error_code': 'INVALID_JSON'}
    except ValueError as error:
        result = {'status': 'ERROR', 'error_code': str(error)}
    except (KeyError, TypeError, InvalidOperation):
        result = {'status': 'ERROR', 'error_code': 'INVALID_DATA'}
    except (OSError, UnicodeError):
        result = {'status': 'ERROR', 'error_code': 'FILE_ERROR'}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'SUCCESS' else 1


if __name__ == '__main__':
    sys.exit(main())
