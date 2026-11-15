"""Поиск уже залитых роликов: папка ↔ ролик на канале по названию и длительности.

Названия на YouTube часто правятся при заливке («Мы с краш растили робота-младенца 😲» →
«Мы с крашом растили робота младенца»), поэтому сравниваем основы слов, а длительность
служит вторым признаком.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

DUR_TOL = 1.6  # сек: Studio округляет до секунды


# общие слова тематики, которые есть почти в каждом названии и не помогают отличать ролики
STOP = {"майнкрафт", "майнкрафте", "майнкрафта", "minecraft", "minecraf", "shorts", "short",
        "роблокс", "roblox", "анимация", "twitch", "tiktok", "тикток"}


def _stems(title: str) -> set[str]:
    t = title.split("|")[0].lower().replace("ё", "е")
    t = re.sub(r"[^\w\s]", " ", t)
    # короткая основа, чтобы «мешок/мешки», «дракон/дракона» совпадали; общие слова тематики не считаем
    return {(w[:4] if len(w) > 5 else w[:3]) for w in t.split() if (len(w) > 1 or w.isdigit()) and w not in STOP}


def similarity(a: str, b: str) -> float:
    sa, sb = _stems(a), _stems(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


@dataclass
class Match:
    video: dict
    sim: float
    dur_ok: bool | None
    level: str  # "точно" | "возможно"


def classify(sim: float, dur_ok: bool | None) -> str | None:
    if dur_ok is False:
        return "возможно" if sim >= 0.75 else None
    if dur_ok:
        if sim >= 0.35:
            return "точно"
        return "возможно" if sim >= 0.05 else None
    # длительность неизвестна
    if sim >= 0.6:
        return "точно"
    return "возможно" if sim >= 0.4 else None


def match_folders(local: list[dict], remote: list[dict]) -> dict[str, Match]:
    """local: [{key, title, duration}], remote: [{id, title, duration, …}] → {key: Match}.
    Каждый ролик канала достаётся максимум одной папке (жадно, от лучшей пары)."""
    from datetime import timedelta

    from .schedule import parse_studio_date

    folder_date_re = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
    pairs = []
    for l in local:
        m = folder_date_re.search(Path(l["key"]).name) if l.get("key") else None
        l_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None
        for r in remote:
            r_date = parse_studio_date(r.get("date", "")) if r.get("date") else None
            if l_date and r_date and r_date < l_date - timedelta(days=2):
                continue  # пост старше самого ролика — это не он
            sim = similarity(l["title"], r["title"])
            if l.get("duration") and r.get("duration") is not None:
                dur_ok = abs(l["duration"] - r["duration"]) <= DUR_TOL
            else:
                dur_ok = None
            level = classify(sim, dur_ok)
            if level:
                score = sim + (0.5 if dur_ok else 0) + (0.3 if level == "точно" else 0)
                pairs.append((score, l["key"], r, sim, dur_ok, level))
    pairs.sort(key=lambda p: -p[0])
    used_l, used_r, out = set(), set(), {}
    for score, key, r, sim, dur_ok, level in pairs:
        if key in used_l or r["id"] in used_r:
            continue
        used_l.add(key)
        used_r.add(r["id"])
        out[key] = Match(r, sim, dur_ok, level)
    return out
