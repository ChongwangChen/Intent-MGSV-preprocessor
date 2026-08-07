from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.patch_douk_proxy_routing import ORIGINAL, PATCHED, patch_file, patch_source


class PatchDoukProxyRoutingTests(unittest.TestCase):
    def test_patch_is_idempotent(self) -> None:
        source = f"before\n{ORIGINAL}after\n"
        updated, status = patch_source(source)
        self.assertEqual(status, "patched")
        self.assertIn(PATCHED, updated)
        self.assertNotIn(ORIGINAL, updated)

        repeated, status = patch_source(updated)
        self.assertEqual(status, "already_patched")
        self.assertEqual(repeated, updated)

    def test_patch_file_rejects_unknown_upstream_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "main_terminal.py"
            target.write_text("changed upstream", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                patch_file(target)


if __name__ == "__main__":
    unittest.main()
