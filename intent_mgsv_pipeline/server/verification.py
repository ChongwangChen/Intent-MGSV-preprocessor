from __future__ import annotations

from typing import Any


SONG_VERIFIED_VALUES = frozenset(
    {
        "yes",
        "true",
        "1",
        "confirmed",
        "\u662f",
        "\u5df2\u786e\u8ba4",
    }
)


def is_song_verified(value: Any) -> bool:
    return str(value or "").strip().casefold() in SONG_VERIFIED_VALUES
