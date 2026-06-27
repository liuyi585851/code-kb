from __future__ import annotations

from pathlib import Path

import yaml


def load_aliases(path: str | Path) -> dict[str, tuple[str, ...]]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not data:
        return {}
    aliases = data.get("aliases", data)
    if not isinstance(aliases, dict):
        raise ValueError("aliases yaml must be a mapping")
    return {str(key).lower(): tuple(str(item).lower() for item in value) for key, value in aliases.items()}


def alias_tokens(text: str, aliases: dict[str, tuple[str, ...]]) -> list[str]:
    lowered = text.lower()
    tokens: list[str] = []
    for canonical, values in aliases.items():
        phrases = (canonical, *values)
        if any(phrase and phrase in lowered for phrase in phrases):
            tokens.append(f"alias:{canonical}")
    return tokens

