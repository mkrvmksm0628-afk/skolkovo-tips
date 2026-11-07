"""Проверки задания. Запуск: python 10_tests.py.

Нужен HMAC_SECRET учебного набора. Часы подставляются только в тестах.
Две expectedFailure показывают противоречия исходных T3-1/T3-2, а не успех.
"""
import csv
import hashlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('tips', ROOT / '04_прототип.py')
tips = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tips)
NOW = tips.parse_time('2025-11-01T18:00:00Z')


def request(qr='qr_002', amount='637.00', pct='5', key='test-key', action='pay'):
    row = tips.load_qr(qr)
    return dict(action=action, qr_id=qr, receipt_id=row['receipt_id'],
                base_amount=amount, tip_pct=pct, idempotency_key=key)


class PaymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / 'data'
        self.data.mkdir()
        for name in ('merchants.csv', 'employees.csv', 'qr_links.csv'):
            shutil.copy2(ROOT / name, self.data / name)
        self.out = Path(self.tmp.name) / 'out'
        self.app = tips.Platform(self.data, self.out, clock=lambda: NOW)

    def tearDown(self):
        self.app.close()
        self.tmp.cleanup()

    def rows(self):
        file = self.out / 'payments_out.csv'
        if not file.exists():
            return []
        with open(file, encoding='utf-8', newline='') as f:
            return list(csv.DictReader(f))

    def replace_row(self, filename, key, value, change):
        file = self.data / filename
        with open(file, encoding='utf-8', newline='') as f:
            reader = csv.DictReader(f)
            fields, rows = reader.fieldnames, list(reader)
        for row in rows:
            if row[key] == value:
                change(row)
        with open(file, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    @unittest.expectedFailure
    def test_T3_1_original_success_conflict(self):
        result = self.app.handle(request('qr_001'))
        self.assertEqual(result['amount_total'], 668.85)
        self.assertEqual(result['tip_amount'], 31.85)
        self.assertEqual(result['status'], 'SUCCESS')  # Фактически PENDING.

    @unittest.expectedFailure
    def test_T3_2_original_declined_conflict(self):
        result = self.app.handle(request('qr_003', '911.00', '15'))
        self.assertEqual(result['status'], 'DECLINED')  # Фактически SUCCESS.

    def test_T3_3_signature_no_payment(self):
        self.replace_row('qr_links.csv', 'qr_id', 'qr_002', lambda r: r.update(signature='0' * 16))
        result = self.app.handle(request())
        self.assertEqual((result['error_code'], result['http_status']), ('INVALID_QR', 422))
        self.assertEqual(self.rows(), [])

    def test_T3_3_expiry_no_payment(self):
        self.app.clock = lambda: tips.parse_time('2026-11-07T00:00:00Z')
        result = self.app.handle(request())
        self.assertEqual((result['error_code'], result['http_status']), ('QR_EXPIRED', 410))
        self.assertEqual(self.rows(), [])

    def test_T3_4_repeat_after_restart(self):
        first = self.app.handle(request(action='create'))
        self.assertEqual(first['http_status'], 201)
        self.app.close()
        self.app = tips.Platform(self.data, self.out, clock=lambda: NOW)
        second = self.app.handle(request(action='create'))
        self.assertEqual(second['http_status'], 200)
        self.assertEqual(second['payment_id'], first['payment_id'])
        self.assertEqual(len(self.rows()), 1)

    def test_T3_5_cancel_created(self):
        created = self.app.handle(request(action='create'))
        result = self.app.handle(dict(action='cancel', payment_id=created['payment_id']))
        self.assertEqual((result['status'], result['http_status']), ('CANCELED', 200))
        self.assertEqual(len(self.rows()), 1)

    def test_T3_5_cancel_pending_forbidden(self):
        created = self.app.handle(request('qr_001', action='create'))
        pending = self.app.handle(dict(action='confirm', payment_id=created['payment_id'], confirm_token=created['confirm_token']))
        self.assertEqual(pending['status'], 'PENDING')
        result = self.app.handle(dict(action='cancel', payment_id=created['payment_id']))
        self.assertEqual((result['error_code'], result['http_status']), ('CANCEL_NOT_ALLOWED', 409))
        self.assertEqual(self.rows()[0]['status'], 'PENDING')

    def test_success_on_actual_success_qr(self):
        result = self.app.handle(request('qr_002'))
        self.assertEqual((result['status'], result['tip_amount'], result['amount_total']), ('SUCCESS', 31.85, 668.85))

    def test_decline_on_actual_decline_qr(self):
        result = self.app.handle(request('qr_019', '911.00', '15'))
        self.assertEqual((result['status'], result['error_code'], result['http_status']), ('DECLINED', 'CARD_DECLINED', 402))

    def test_timeout_is_pending_and_repeated_once(self):
        first = self.app.handle(request('qr_001'))
        second = self.app.handle(request('qr_001'))
        self.assertEqual(first['error_code'], 'GATEWAY_TIMEOUT')
        self.assertEqual(first['status'], 'PENDING')
        self.assertEqual(first['payment_id'], second['payment_id'])
        self.assertEqual(second['http_status'], 200)
        self.assertEqual(len(self.rows()), 1)

    def test_qr_cannot_be_paid_twice(self):
        self.app.handle(request())
        result = self.app.handle(request(key='other'))
        self.assertEqual(result['error_code'], 'QR_USED')
        self.assertEqual(len(self.rows()), 1)

    def test_key_cannot_change_amount(self):
        self.app.handle(request())
        result = self.app.handle(request(amount='638.00'))
        self.assertEqual(result['error_code'], 'IDEMPOTENCY_CONFLICT')
        self.assertEqual(len(self.rows()), 1)

    def test_amount_and_percentage_limits(self):
        for base, pct, code in [('0', '5', 'AMOUNT_OUT_OF_RANGE'), ('1000000.01', '5', 'AMOUNT_OUT_OF_RANGE'),
                                ('1', '-1', 'TIP_OUT_OF_RANGE'), ('1', '50.01', 'TIP_OUT_OF_RANGE'),
                                ('NaN', '5', 'VALIDATION_ERROR')]:
            with self.subTest(base=base, pct=pct):
                result = self.app.handle(request(amount=base, pct=pct))
                self.assertEqual(result['error_code'], code)
        self.assertEqual(tips.calc_amounts('1000000', '50'), (Decimal('500000.00'), Decimal('1500000.00')))
        self.assertEqual(tips.calc_amounts('0.01', '50'), (Decimal('0.01'), Decimal('0.02')))
        self.assertEqual(self.rows(), [])

    def test_receipt_binding(self):
        req = request()
        req['receipt_id'] = 'r_9999'
        self.assertEqual(self.app.handle(req)['error_code'], 'RECEIPT_MISMATCH')
        self.assertEqual(self.rows(), [])

    def test_blocked_merchant(self):
        self.replace_row('merchants.csv', 'merchant_id', 'm_001', lambda r: r.update(status='BLOCKED'))
        self.assertEqual(self.app.handle(request())['error_code'], 'MERCHANT_BLOCKED')
        self.assertEqual(self.rows(), [])

    def test_ttl_boundary(self):
        qr = tips.load_qr('qr_002')
        exp = tips.parse_time(qr['exp'])
        self.assertTrue(tips.check_exp(qr, exp))
        self.assertFalse(tips.check_exp(qr, exp + timedelta(microseconds=1)))

    def test_confirm_token_and_audit(self):
        created = self.app.handle(request(action='create'))
        bad = self.app.handle(dict(action='confirm', payment_id=created['payment_id'], confirm_token='wrong'))
        self.assertEqual(bad['error_code'], 'FORBIDDEN')
        confirmed = self.app.handle(dict(action='confirm', payment_id=created['payment_id'], confirm_token=created['confirm_token']))
        self.assertEqual(confirmed['status'], 'SUCCESS')
        repeated = self.app.handle(dict(action='confirm', payment_id=created['payment_id'], confirm_token=created['confirm_token']))
        self.assertEqual(repeated['status'], 'SUCCESS')
        canceled = self.app.handle(dict(action='cancel', payment_id=created['payment_id']))
        self.assertEqual(canceled['error_code'], 'CANCEL_NOT_ALLOWED')
        events = [json.loads(s) for s in (self.out / 'audit_out.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(sum(e['action'] == 'payment_confirmed' for e in events), 1)
        self.assertTrue(all(len(e) == 10 for e in events))
        self.assertNotIn(created['confirm_token'], json.dumps(events))


def save_evidence(result, log):
    evidence = ROOT / 'evidence'
    evidence.mkdir(exist_ok=True)
    (evidence / 'tests.log').write_text(log, encoding='utf-8')
    details = []
    for qr, amount, pct, expected in [('qr_001', '637.00', '5', 'SUCCESS'), ('qr_003', '911.00', '15', 'DECLINED'),
                                      ('qr_002', '637.00', '5', 'SUCCESS'), ('qr_019', '911.00', '15', 'DECLINED')]:
        with tempfile.TemporaryDirectory() as tmp:
            app = tips.Platform(out_dir=tmp, clock=lambda: NOW)
            try:
                actual = app.handle(request(qr, amount, pct))
            finally:
                app.close()
        nonce = tips.load_qr(qr)['nonce']
        details.append(dict(qr_id=qr, h=int(hashlib.md5((qr + nonce).encode()).hexdigest(), 16) % 10,
                            expected=expected, actual=actual, matches=actual['status'] == expected))
    report = dict(tests=result.testsRun, passed=result.testsRun-len(result.expectedFailures)-len(result.failures)-len(result.errors),
                  source_conflicts=len(result.expectedFailures), failures=len(result.failures), errors=len(result.errors),
                  test_clock=NOW.isoformat(), examples=details)
    (evidence / 'test_results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PaymentTests)
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    log = stream.getvalue()
    print(log)
    save_evidence(result, log)
    # Ноль означает отсутствие непредвиденных ошибок, а не прохождение двух конфликтных примеров.
    raise SystemExit(0 if result.wasSuccessful() else 1)
