from __future__ import annotations

import re
from typing import Any


GENRE_OPTIONS = (
    "Pop",
    "Electronic",
    "Hip-Hop",
    "R&B",
    "Rock",
    "Folk",
    "Classical",
    "Jazz",
    "Blues",
    "Soundtrack",
    "World",
    "Other",
)

_ALIASES = {
    "pop": "Pop",
    "mandopop": "Pop",
    "c-pop": "Pop",
    "k-pop": "Pop",
    "j-pop": "Pop",
    "electronic": "Electronic",
    "electronica": "Electronic",
    "dance": "Electronic",
    "edm": "Electronic",
    "house": "Electronic",
    "techno": "Electronic",
    "phonk": "Electronic",
    "hip hop": "Hip-Hop",
    "hip-hop": "Hip-Hop",
    "rap": "Hip-Hop",
    "r&b": "R&B",
    "rhythm and blues": "R&B",
    "rock": "Rock",
    "metal": "Rock",
    "folk": "Folk",
    "country": "Folk",
    "classical": "Classical",
    "jazz": "Jazz",
    "blues": "Blues",
    "soundtrack": "Soundtrack",
    "film score": "Soundtrack",
    "anime": "Soundtrack",
    "game": "Soundtrack",
    "world": "World",
}


def normalize_genre(value: Any) -> str:
    text = str(value or "").strip().casefold()
    if not text:
        return ""
    for key, normalized in _ALIASES.items():
        if key in text:
            return normalized
    return ""


def suggest_genre(
    title: Any,
    artist: Any,
    acr_genre: Any = "",
) -> tuple[str, str, float]:
    normalized = normalize_genre(acr_genre)
    if normalized:
        return normalized, "acrcloud", 0.82

    text = f"{title or ''} {artist or ''}".casefold()
    keyword_groups = (
        ("Electronic", 0.68, r"\b(dj|remix|mix|edm|phonk|house|techno|electro)\b|电子|电音|车载"),
        ("Hip-Hop", 0.68, r"\b(rap|hip[\s-]?hop|trap)\b|说唱"),
        ("Rock", 0.64, r"\b(rock|metal|punk)\b|摇滚"),
        ("R&B", 0.62, r"\br\s*&\s*b\b|rhythm and blues"),
        ("Classical", 0.64, r"\b(classical|piano|violin|orchestra)\b|古典|钢琴|小提琴"),
        ("Jazz", 0.64, r"\bjazz\b|爵士|萨克斯"),
        ("Soundtrack", 0.62, r"\b(ost|soundtrack|bgm|anime|game)\b|原声|影视|动漫|游戏"),
        ("Folk", 0.58, r"\b(folk|country)\b|民谣"),
    )
    for genre, confidence, pattern in keyword_groups:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return genre, "keyword", confidence
    return "Pop", "default_prior", 0.35
