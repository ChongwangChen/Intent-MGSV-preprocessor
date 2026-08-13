from __future__ import annotations

import argparse
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TERMINAL_TARGET = (
    PROJECT_ROOT
    / "DouK-Source"
    / "src"
    / "application"
    / "main_terminal.py"
)
DEFAULT_PARAMETER_TARGET = (
    PROJECT_ROOT / "DouK-Source" / "src" / "config" / "parameter.py"
)
DEFAULT_DOWNLOADER_TARGET = (
    PROJECT_ROOT / "DouK-Source" / "src" / "downloader" / "download.py"
)
DEFAULT_INTERFACE_TARGET = (
    PROJECT_ROOT / "DouK-Source" / "src" / "interface" / "template.py"
)

TERMINAL_ORIGINAL = """        detail_data = [
            await self.handle_detail_single(
                processor,
                cookie,
                proxy,
                i,
            )
            for i in ids
        ]
"""

TERMINAL_PATCHED = """        effective_proxy = proxy
        if effective_proxy is None:
            effective_proxy = (
                self.parameter.proxy_tiktok
                if tiktok
                else self.parameter.proxy
            )
        detail_data = [
            await self.handle_detail_single(
                processor,
                cookie,
                effective_proxy,
                i,
            )
            for i in ids
        ]
"""

PARAMETER_CLIENT_ORIGINAL = """        self.client = create_client(
            timeout=self.timeout,
            proxy=self.proxy,
        )
        self.client_tiktok = create_client(
"""

PARAMETER_CLIENT_PATCHED = """        self.client = create_client(
            timeout=self.timeout,
            proxy=self.proxy,
        )
        self.client_download = create_client(
            timeout=self.timeout,
            proxy=None,
        )
        self.client_tiktok = create_client(
"""

PARAMETER_CLOSE_ORIGINAL = """    async def close_client(self) -> None:
        await self.client.aclose()
        await self.client_tiktok.aclose()
"""

PARAMETER_CLOSE_PATCHED = """    async def close_client(self) -> None:
        await self.client.aclose()
        await self.client_download.aclose()
        await self.client_tiktok.aclose()
"""

DOWNLOADER_ORIGINAL = """        self.client: "AsyncClient" = params.client
"""

DOWNLOADER_PATCHED = """        self.client: "AsyncClient" = params.client_download
"""

INTERFACE_GET_ORIGINAL = """        response = get(
            f"{url}?{params}",
            headers=headers,
            proxy=self.proxy,
            follow_redirects=True,
            verify=False,
            timeout=self.timeout,
            **kwargs,
        )
"""

INTERFACE_GET_PATCHED = """        response = await self.client.get(
            f"{url}?{params}",
            headers=headers,
            **kwargs,
        )
"""

INTERFACE_POST_ORIGINAL = """        response = post(
            f"{url}?{params}",
            data=data,
            headers=headers,
            proxy=self.proxy,
            follow_redirects=True,
            verify=False,
            timeout=self.timeout,
            **kwargs,
        )
"""

INTERFACE_POST_PATCHED = """        response = await self.client.post(
            f"{url}?{params}",
            data=data,
            headers=headers,
            **kwargs,
        )
"""


def patch_terminal_source(source: str) -> tuple[str, str]:
    if TERMINAL_PATCHED in source:
        return source, "already_patched"
    if TERMINAL_ORIGINAL not in source:
        raise RuntimeError(
            "Expected DouK detail routing block was not found; "
            "the upstream source may have changed."
        )
    return source.replace(TERMINAL_ORIGINAL, TERMINAL_PATCHED, 1), "patched"


def patch_parameter_source(source: str) -> tuple[str, str]:
    clients_patched = PARAMETER_CLIENT_PATCHED in source
    close_patched = PARAMETER_CLOSE_PATCHED in source
    if clients_patched and close_patched:
        return source, "already_patched"
    if clients_patched != close_patched:
        raise RuntimeError("DouK Parameter contains a partial download-client patch")
    count = source.count(PARAMETER_CLIENT_ORIGINAL)
    if count != 2 or PARAMETER_CLOSE_ORIGINAL not in source:
        raise RuntimeError(
            "Expected DouK client lifecycle blocks were not found; "
            "the upstream source may have changed."
        )
    source = source.replace(
        PARAMETER_CLIENT_ORIGINAL,
        PARAMETER_CLIENT_PATCHED,
    )
    source = source.replace(
        PARAMETER_CLOSE_ORIGINAL,
        PARAMETER_CLOSE_PATCHED,
        1,
    )
    return source, "patched"


def patch_downloader_source(source: str) -> tuple[str, str]:
    if DOWNLOADER_PATCHED in source:
        return source, "already_patched"
    if DOWNLOADER_ORIGINAL not in source:
        raise RuntimeError(
            "Expected DouK downloader client assignment was not found; "
            "the upstream source may have changed."
        )
    return source.replace(DOWNLOADER_ORIGINAL, DOWNLOADER_PATCHED, 1), "patched"


def patch_interface_source(source: str) -> tuple[str, str]:
    get_patched = INTERFACE_GET_PATCHED in source
    post_patched = INTERFACE_POST_PATCHED in source
    if get_patched and post_patched:
        return source, "already_patched"
    if get_patched != post_patched:
        raise RuntimeError("DouK interface template contains a partial proxy-client patch")
    if INTERFACE_GET_ORIGINAL not in source or INTERFACE_POST_ORIGINAL not in source:
        raise RuntimeError(
            "Expected DouK proxy request blocks were not found; "
            "the upstream source may have changed."
        )
    source = source.replace(INTERFACE_GET_ORIGINAL, INTERFACE_GET_PATCHED, 1)
    source = source.replace(INTERFACE_POST_ORIGINAL, INTERFACE_POST_PATCHED, 1)
    return source, "patched"


def patch_file(path: Path, patcher) -> str:
    source = path.read_text(encoding="utf-8")
    updated, status = patcher(source)
    if status == "patched":
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(updated, encoding="utf-8")
        temp_path.replace(path)
    return status


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Route DouK metadata through its proxy and media downloads directly."
        )
    )
    parser.add_argument(
        "--terminal-target",
        default=str(DEFAULT_TERMINAL_TARGET),
    )
    parser.add_argument(
        "--parameter-target",
        default=str(DEFAULT_PARAMETER_TARGET),
    )
    parser.add_argument(
        "--downloader-target",
        default=str(DEFAULT_DOWNLOADER_TARGET),
    )
    parser.add_argument(
        "--interface-target",
        default=str(DEFAULT_INTERFACE_TARGET),
    )
    args = parser.parse_args()
    targets = (
        (Path(args.terminal_target), patch_terminal_source),
        (Path(args.parameter_target), patch_parameter_source),
        (Path(args.downloader_target), patch_downloader_source),
        (Path(args.interface_target), patch_interface_source),
    )
    for target, patcher in targets:
        status = patch_file(target, patcher)
        print(f"{status}: {target.resolve()}")


if __name__ == "__main__":
    main()
