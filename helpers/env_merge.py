"""Merge / replace .env style text (project variables and secrets).

Pure functions: text in, text out. They never log or return secret values
anywhere except in the returned text, which the caller writes to disk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from io import StringIO

from dotenv.parser import parse_stream

MODE_MERGE = "merge"
MODE_REPLACE = "replace"
MODES = (MODE_MERGE, MODE_REPLACE)

_TRAILING_NEWLINE = re.compile(r"\r?\n\Z")


@dataclass
class _Line:
    raw: str  # original text of the line (multi-line quoted values included), no trailing newline
    key: str | None  # normalized key for pairs, None for comments/blank/other


def _parse(text: str, *, upper_keys: bool) -> list[_Line]:
    lines: list[_Line] = []
    for binding in parse_stream(StringIO(text or "")):
        raw = _TRAILING_NEWLINE.sub("", binding.original.string)
        key = None
        if binding.key is not None and not binding.error:
            key = binding.key.upper() if upper_keys else binding.key
        lines.append(_Line(raw=raw, key=key))
    return lines


def _join(lines: list[_Line]) -> str:
    return "\n".join(line.raw for line in lines) + "\n" if lines else ""


def get_keys(text: str, *, upper_keys: bool = False) -> list[str]:
    """Keys of the pair lines, in order, without duplicates."""
    seen: dict[str, None] = {}
    for line in _parse(text, upper_keys=upper_keys):
        if line.key is not None:
            seen.setdefault(line.key)
    return list(seen)


def replace_env(source_text: str) -> str:
    """Replace mode: the target ends up identical to the source."""
    return _join(_parse(source_text, upper_keys=False))


def merge_env(source_text: str, target_text: str, *, upper_keys: bool = False) -> str:
    """Merge mode: source keys overwrite or are appended to the target.

    - A key present in both keeps the target position and takes the source line
      (value and inline comment). Later duplicates of that key in the target
      are dropped, since they would have overridden the new value.
    - Keys only in the source are appended at the end, in source order.
    - Keys only in the target, comment lines, blank lines and unparsable lines
      of the target are kept untouched, in order.
    - If the source repeats a key, its last occurrence wins (like dotenv).
    - `upper_keys=True` compares keys case-insensitively (secrets store keys
      in upper case).
    """
    source = {
        line.key: line
        for line in _parse(source_text, upper_keys=upper_keys)
        if line.key is not None
    }
    result: list[_Line] = []
    placed: set[str] = set()
    for line in _parse(target_text, upper_keys=upper_keys):
        if line.key is None or line.key not in source:
            result.append(line)
        elif line.key not in placed:
            placed.add(line.key)
            result.append(source[line.key])
        # else: duplicate of an already replaced key -> dropped
    for key, line in source.items():
        if key not in placed:
            result.append(line)
    return _join(result)


def apply_mode(mode: str, source_text: str, target_text: str, *, upper_keys: bool = False) -> str:
    if mode == MODE_MERGE:
        return merge_env(source_text, target_text, upper_keys=upper_keys)
    if mode == MODE_REPLACE:
        return replace_env(source_text)
    raise ValueError(f"mode must be one of {', '.join(MODES)}")
