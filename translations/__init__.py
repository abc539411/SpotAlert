from __future__ import annotations

import json
import os
from typing import Optional

_DIR = os.path.dirname(__file__)


def _load(filename: str) -> dict:
    with open(os.path.join(_DIR, filename), encoding="utf-8") as f:
        return json.load(f)


_STRINGS       = _load("strings.json")
_AIRLINES      = _load("airlines.json")
_AIRPORTS      = _load("airports.json")
_MANUFACTURERS = _load("manufacturers.json")

SUPPORTED_LANGUAGES = {"en": "English", "zh": "中文"}


def t(key: str, lang: str = "en", **kwargs) -> str:
    """Translate a UI string key. Falls back to English if key missing in lang."""
    text = _STRINGS.get(lang, _STRINGS["en"]).get(key) or _STRINGS["en"].get(key, key)
    return text.format(**kwargs) if kwargs else text


def tr_airline(name: str, lang: str) -> str:
    """Translate an airline name. Returns original if no translation found."""
    if lang == "en" or not name:
        return name
    return _AIRLINES.get(lang, {}).get(name, name)


def tr_airport(name: str, lang: str) -> str:
    """Translate an airport name. Returns original if no translation found."""
    if lang == "en" or not name:
        return name
    return _AIRPORTS.get(lang, {}).get(name, name)


def tr_aircraft(full_name: str, lang: str) -> str:
    """Translate only the manufacturer part of an aircraft name.

    e.g. 'Boeing 737-800' → '波音 737-800'  (model number unchanged)
    Falls back to original name if manufacturer not found.
    """
    if lang == "en" or not full_name:
        return full_name
    for mfr, translated in _MANUFACTURERS.get(lang, {}).items():
        if full_name.startswith(mfr):
            remainder = full_name[len(mfr):]
            return translated + remainder
    return full_name
