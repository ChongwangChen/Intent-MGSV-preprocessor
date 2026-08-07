from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sync_douyin_cookie.py"
SPEC = importlib.util.spec_from_file_location("sync_douyin_cookie", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SyncDouyinCookieTests(unittest.TestCase):
    def test_parses_sid_guard_expiry(self) -> None:
        issued = 1_800_000_000
        lifetime = 3600
        cookie = {
            "sid_guard": quote(f"token|{issued}|{lifetime}|tail", safe="")
        }
        self.assertEqual(
            MODULE.sid_guard_expiry(cookie),
            datetime.fromtimestamp(issued + lifetime, tz=timezone.utc),
        )

    def test_apply_preserves_unrelated_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            future_issued = int(datetime.now(timezone.utc).timestamp())
            cookie = {
                "sid_guard": quote(
                    f"token|{future_issued}|5184000|tail",
                    safe="",
                ),
                "odin_tt": "odin",
                "passport_csrf_token": "csrf",
                "sessionid": "session",
                "sessionid_ss": "session-ss",
                "ttwid": "ttwid",
            }
            payload_path = root / "cookie.json"
            payload_path.write_text(
                json.dumps(
                    {
                        "cookie": cookie,
                        "browser_info": {
                            "browser_version": "150.0.0.0",
                            "engine_version": "150.0.0.0",
                        },
                    }
                ),
                encoding="utf-8",
            )
            settings_path = root / "settings.json"
            settings_path.write_text(
                "\ufeff"
                + json.dumps(
                    {
                        "root": "/data/download",
                        "music": True,
                        "cookie": {"old": "value"},
                        "browser_info": {"browser_version": "139.0.0.0"},
                    }
                ),
                encoding="utf-8",
            )

            summary = MODULE.apply_cookie_file(payload_path, settings_path)
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertFalse(summary["sid_guard_expired"])
            self.assertEqual(settings["root"], "/data/download")
            self.assertTrue(settings["music"])
            self.assertEqual(settings["cookie"]["sessionid"], "session")
            self.assertEqual(
                settings["browser_info"]["browser_version"],
                "150.0.0.0",
            )


if __name__ == "__main__":
    unittest.main()
