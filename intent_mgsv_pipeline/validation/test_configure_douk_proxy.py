from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.configure_douk_proxy import update_proxy, validate_proxy


class ConfigureDoukProxyTests(unittest.TestCase):
    def test_enable_status_and_disable_preserve_other_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / "settings.json"
            settings.write_text(
                json.dumps(
                    {
                        "proxy": "",
                        "root": "/data/download",
                        "music": True,
                    }
                ),
                encoding="utf-8",
            )

            enabled = update_proxy(
                settings,
                proxy="socks5://127.0.0.1:10808",
                apply=True,
            )
            self.assertTrue(enabled["enabled"])
            self.assertTrue(enabled["changed"])
            saved = json.loads(settings.read_text(encoding="utf-8"))
            self.assertEqual(saved["root"], "/data/download")
            self.assertTrue(saved["music"])

            status = update_proxy(settings, proxy=None, apply=False)
            self.assertEqual(status["proxy"], "socks5://127.0.0.1:10808")

            disabled = update_proxy(settings, proxy=None, apply=True)
            self.assertFalse(disabled["enabled"])
            self.assertEqual(
                json.loads(settings.read_text(encoding="utf-8"))["proxy"],
                "",
            )

    def test_rejects_invalid_proxy(self) -> None:
        with self.assertRaises(ValueError):
            validate_proxy("127.0.0.1:10808")
        with self.assertRaises(ValueError):
            validate_proxy("file://127.0.0.1:10808")


if __name__ == "__main__":
    unittest.main()
