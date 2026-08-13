from __future__ import annotations

import unittest

from scripts.patch_douk_proxy_routing import (
    DOWNLOADER_ORIGINAL,
    DOWNLOADER_PATCHED,
    INTERFACE_GET_ORIGINAL,
    INTERFACE_GET_PATCHED,
    INTERFACE_POST_ORIGINAL,
    INTERFACE_POST_PATCHED,
    PARAMETER_CLIENT_ORIGINAL,
    PARAMETER_CLIENT_PATCHED,
    PARAMETER_CLOSE_ORIGINAL,
    PARAMETER_CLOSE_PATCHED,
    TERMINAL_ORIGINAL,
    TERMINAL_PATCHED,
    patch_downloader_source,
    patch_interface_source,
    patch_parameter_source,
    patch_terminal_source,
)


class PatchDoukProxyRoutingTests(unittest.TestCase):
    def test_terminal_patch_is_idempotent(self) -> None:
        source = f"before\n{TERMINAL_ORIGINAL}after\n"
        updated, status = patch_terminal_source(source)
        self.assertEqual(status, "patched")
        self.assertIn(TERMINAL_PATCHED, updated)
        self.assertNotIn(TERMINAL_ORIGINAL, updated)

        repeated, status = patch_terminal_source(updated)
        self.assertEqual(status, "already_patched")
        self.assertEqual(repeated, updated)

    def test_parameter_patch_adds_direct_client_to_both_lifecycles(self) -> None:
        source = (
            PARAMETER_CLIENT_ORIGINAL
            + "\nother\n"
            + PARAMETER_CLIENT_ORIGINAL
            + "\n"
            + PARAMETER_CLOSE_ORIGINAL
        )
        updated, status = patch_parameter_source(source)
        self.assertEqual(status, "patched")
        self.assertEqual(updated.count(PARAMETER_CLIENT_PATCHED), 2)
        self.assertIn(PARAMETER_CLOSE_PATCHED, updated)

        repeated, status = patch_parameter_source(updated)
        self.assertEqual(status, "already_patched")
        self.assertEqual(repeated, updated)

    def test_downloader_uses_direct_download_client(self) -> None:
        updated, status = patch_downloader_source(DOWNLOADER_ORIGINAL)
        self.assertEqual(status, "patched")
        self.assertEqual(updated, DOWNLOADER_PATCHED)

    def test_interface_reuses_async_proxy_client(self) -> None:
        source = INTERFACE_GET_ORIGINAL + "\n" + INTERFACE_POST_ORIGINAL
        updated, status = patch_interface_source(source)
        self.assertEqual(status, "patched")
        self.assertIn(INTERFACE_GET_PATCHED, updated)
        self.assertIn(INTERFACE_POST_PATCHED, updated)
        self.assertNotIn(INTERFACE_GET_ORIGINAL, updated)
        self.assertNotIn(INTERFACE_POST_ORIGINAL, updated)

        repeated, status = patch_interface_source(updated)
        self.assertEqual(status, "already_patched")
        self.assertEqual(repeated, updated)

    def test_patch_rejects_unknown_upstream_source(self) -> None:
        for patcher in (
            patch_terminal_source,
            patch_parameter_source,
            patch_downloader_source,
            patch_interface_source,
        ):
            with self.subTest(patcher=patcher.__name__):
                with self.assertRaises(RuntimeError):
                    patcher("changed upstream")


if __name__ == "__main__":
    unittest.main()
