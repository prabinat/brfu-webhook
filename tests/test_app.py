import base64
import hashlib
import hmac
import os
import tempfile
import unittest
from urllib.parse import urlencode

import app

class WebhookTests(unittest.TestCase):
    def test_signature(self):
        fields = {"MessageSid": "SM" + "a"*32, "From":"whatsapp:+14415550000",
                  "To":"whatsapp:+14155238886", "Body":"YES FX#25"}
        url = "https://test.example.com/api/webhooks/twilio/whatsapp"
        signed = url + "".join(k + fields[k] for k in sorted(fields))
        sig = base64.b64encode(hmac.new(b"test-token", signed.encode(), hashlib.sha1).digest()).decode()
        self.assertTrue(app.validate_twilio_signature(url, fields, sig, "test-token"))
        self.assertFalse(app.validate_twilio_signature(url, {**fields, "Body":"NO FX#25"}, sig, "test-token"))
        self.assertFalse(app.validate_twilio_signature(url, fields, sig, "wrong-token"))

    def test_masking(self):
        self.assertEqual(app.mask_address("whatsapp:+14415550199"), "whatsapp:***0199")

    def test_recognized_and_duplicates(self):
        old_path = app.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            try:
                app.DB_PATH = tmp + "/lab.db"
                app.init_db()
                fields = {"MessageSid":"SM" + "b"*32,"From":"whatsapp:+14415550199",
                          "To":"whatsapp:+14155238886","Body":"YES FX#25"}
                self.assertEqual(app.process(fields), "recorded")
                self.assertEqual(app.process(fields), "duplicate")
                self.assertEqual(app.process({**fields,"MessageSid":"SM"+"c"*32,"Body":"hello"}),"recorded")
            finally:
                app.DB_PATH = old_path

if __name__ == "__main__":
    unittest.main()
