from __future__ import annotations

import re
from collections.abc import Iterable

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
DAY_THAI = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์"]
TIMES = [
    "08:00",
    "08:30",
    "09:00",
    "09:30",
    "10:00",
    "10:30",
    "11:00",
    "11:30",
    "13:00",
    "13:30",
    "14:00",
    "14:30",
    "15:00",
    "15:30",
    "16:00",
    "16:30",
    "17:00",
    "17:30",
]


def normalize_course_id(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def split_items(value: object) -> list[str]:
    if value is None:
        return []
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return []
    return [part.strip() for part in re.split(r"[/,;]+", text) if part.strip()]


def parse_capacity(value: object, default: int = 0) -> int:
    if isinstance(value, (int, float)) and value == value:
        return max(int(value), 0)
    numbers = re.findall(r"\d+(?:\.\d+)?", str(value or ""))
    return int(sum(float(item) for item in numbers)) if numbers else default


def parse_session_hours(value: object) -> list[int]:
    """Return session lengths in half-hour slots."""
    if isinstance(value, (int, float)) and value == value:
        hours = [float(value)]
    else:
        hours = [
            float(item)
            for item in re.findall(r"\d+(?:\.\d+)?", str(value or ""))
        ]
    if not hours:
        return [3, 3]
    return [max(1, round(hour * 2)) for hour in hours]


def _parse_day_token(token: str) -> list[int]:
    token = token.strip()
    aliases = {
        "M": [0],
        "MON": [0],
        "TU": [1],
        "TUE": [1],
        "T": [1],
        "W": [2],
        "WED": [2],
        "TH": [3],
        "THU": [3],
        "F": [4],
        "FRI": [4],
        "TT": [1, 3],
    }
    if token.upper() in aliases:
        return aliases[token.upper()]
    matches = re.findall(r"Tu|Th|M|W|F", token, flags=re.IGNORECASE)
    result: list[int] = []
    for match in matches:
        result.extend(aliases.get(match.upper(), []))
    return list(dict.fromkeys(result))


def _slot_index(value: str, *, is_end: bool = False) -> int | None:
    match = re.match(r"(\d{1,2})[.:](\d{2})", value.strip())
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    canonical = f"{hour:02d}:{minute:02d}"
    if canonical == "12:00" and is_end:
        return 8
    if canonical == "18:00" and is_end:
        return 18
    try:
        return TIMES.index(canonical)
    except ValueError:
        return None


def parse_fixed_time(value: object) -> list[tuple[int, int, int]]:
    """Parse strings such as 'MW 09.00-10.30; F 13.00-16.00'."""
    text = str(value or "").strip()
    if not text or text.lower() == "nan":
        return []
    blocks: list[tuple[int, int, int]] = []
    for part in text.split(";"):
        match = re.search(
            r"([A-Za-z]+)\s+(\d{1,2}[.:]\d{2})\s*-\s*(\d{1,2}[.:]\d{2})",
            part.strip(),
        )
        if not match:
            continue
        days = _parse_day_token(match.group(1))
        start = _slot_index(match.group(2))
        end = _slot_index(match.group(3), is_end=True)
        if start is None or end is None or end <= start:
            continue
        for day in days:
            blocks.append((day, start, end))
    return blocks


def occupied_slots(blocks: Iterable[tuple[int, int, int]]) -> set[tuple[int, int]]:
    return {
        (day, slot)
        for day, start, end in blocks
        for slot in range(start, end)
    }


def end_time(start: int, duration: int) -> str:
    total_minutes = (8 * 60 if start < 8 else 13 * 60) + (
        start if start < 8 else start - 8
    ) * 30 + duration * 30
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def time_label(start: int, duration: int) -> str:
    return f"{TIMES[start]}-{end_time(start, duration)}"
