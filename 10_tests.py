"""Проверки простого CLI: python 10_tests.py. Выходы — только во временной папке."""
import contextlib
import importlib.util
import io
import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from decimal import Decimal

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('simple', ROOT / '04_прототип.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class SimpleTests(unittest.TestCase):
    def run_request(self, request, demo=True, secret='demo-secret-k3'):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'request.json'
            path.write_text(json.dumps(request), encoding='utf-8')
            args = ['04_прототип.py', str(path)]
            output = io.StringIO()
            class Clock(datetime):
                @classmethod
                def now(cls, tz=None):
                    return datetime(2025, 11, 1, 18, tzinfo=timezone.utc) if demo else datetime.now(timezone.utc)
            with patch.object(app.sys, 'argv', args), patch.object(app, 'OUT_CSV', str(Path(folder) / 'out.csv')), patch.object(app, 'SECRET', secret.encode()), patch.object(app, 'datetime', Clock), contextlib.redirect_stdout(output):
                with self.assertRaises(SystemExit) as exit_info:
                    app.main()
                code = exit_info.exception.code
            return code, json.loads(output.getvalue()), (Path(folder) / 'out.csv').exists()

    def request(self, qr='qr_002'):
        return dict(qr_id=qr, receipt_id=app.load_qr(qr)['receipt_id'], base_amount='637.00', tip_pct='5')

    def test_success_and_timeout(self):
        for qr, status in [('qr_002', 'SUCCESS'), ('qr_001', 'TIMEOUT'), ('qr_019', 'DECLINED')]:
            with self.subTest(qr=qr):
                code, result, saved = self.run_request(self.request(qr))
                self.assertEqual(result['status'], status)
                self.assertEqual(result['amount_total'], 668.85)
                self.assertEqual(code, 0 if status == 'SUCCESS' else 1)
                self.assertTrue(saved)

    def test_expired(self):
        _, result, saved = self.run_request(self.request(), demo=False)
        self.assertEqual(result['error_code'], 'QR_EXPIRED')
        self.assertFalse(saved)

    def test_bad_signature(self):
        _, result, saved = self.run_request(self.request(), secret='wrong')
        self.assertEqual(result['error_code'], 'INVALID_QR')
        self.assertFalse(saved)

    def test_invalid_input(self):
        for req in [[], {}, dict(self.request(), base_amount='NaN'), dict(self.request(), base_amount='-1'), dict(self.request(), tip_pct='51')]:
            with self.subTest(req=req):
                _, result, saved = self.run_request(req)
                self.assertEqual(result['status'], 'ERROR')
                self.assertFalse(saved)

    def test_expiry_boundary(self):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2025, 11, 1, 18, tzinfo=timezone.utc)
        with patch.object(app, 'datetime', Clock):
            self.assertTrue(app.check_exp({'exp': '2025-11-01T18:00:00Z'}))
            self.assertFalse(app.check_exp({'exp': '2025-11-01T17:59:59Z'}))
            self.assertFalse(app.check_exp({'exp': 'invalid'}))

    def test_rounding(self):
        self.assertEqual(app.calc_amounts('0.15', '10'), (Decimal('0.02'), Decimal('0.17')))
        self.assertEqual(app.calc_amounts('1000000', '50'), (Decimal('500000.00'), Decimal('1500000.00')))


if __name__ == '__main__':
    unittest.main(verbosity=2)
