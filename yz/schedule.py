"""Автоматическое время публикации: slots = 12:00, 18:00 в channels.ini.

Ролик без «Публикация:» в info.txt получает ближайший свободный слот. Занятость дня считается
по роликам канала (опубликованные и отложенные на эту дату в Studio) и по меткам uploaded.txt.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

from .info import uploaded_info

MONTHS = {"янв": 1, "фев": 2, "мар": 3, "апр": 4, "мая": 5, "май": 5, "июн": 6, "июл": 7, "авг": 8,
          "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
          "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9,
          "oct": 10, "nov": 11, "dec": 12}
LEAD = timedelta(minutes=45)  # не ставить слот раньше чем через 45 минут


def parse_studio_date(s: str) -> date | None:
    iso = re.match(r"\s*(\d{4})-(\d{2})-(\d{2})", s)
    if iso:
        return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
    m = re.search(r"(\d{1,2})\s+([а-яa-z]+)\.?\s+(\d{4})", s.lower())
    if not m:
        # «Nov 15, 2026», TikTok: «Nov 15, 3:00 PM» (текущий год) и «Jun 24, 2025, 10:14 AM»
        m2 = re.search(r"([a-z]{3})[a-z]*\.?\s+(\d{1,2})(?:,\s*(\d{4}))?", s.lower())
        if not m2:
            return None
        mon, d, y = m2.groups()
        y = y or str(date.today().year)
    else:
        d, mon, y = m.groups()
    month = MONTHS.get(mon[:3])
    return date(int(y), month, int(d)) if month else None


def parse_slots(spec: str) -> list[tuple[int, int]]:
    out = []
    for part in re.split(r"[,\s]+", spec.strip()):
        if part:
            h, m = part.split(":")
            out.append((int(h), int(m)))
    return sorted(out)


def assign_slots(jobs, spec: str, remote: list[dict], source: Path, now: datetime | None = None,
                 marker: str = "uploaded.txt", reserved: set | None = None):
    """reserved — общий набор уже выданных в этом запуске слотов (несколько папок → один аккаунт)."""
    slots = parse_slots(spec)
    if not slots:
        return
    now = now or datetime.now()
    busy: Counter[date] = Counter()
    for r in remote:
        if r.get("status") in {"Опубликовано", "Отложенная публикация", "Published", "Scheduled"}:
            d = parse_studio_date(r.get("date", ""))
            if d and d >= now.date():
                busy[d] += 1
    if source.exists():
        seen_ids = {r["id"] for r in remote}
        for folder in source.iterdir():
            u = uploaded_info(folder, marker) if folder.is_dir() else None
            if u and u.get("publish_at") and u.get("video_id") not in seen_ids:
                busy[datetime.fromisoformat(u["publish_at"]).date()] += 1

    # busy[d] — сколько роликов на канале уже стоит на этот день (точное время Studio в списке не показывает,
    # считаем, что они заняли первые слоты дня); taken — слоты, выданные в этом запуске
    taken: set[datetime] = reserved if reserved is not None else set()
    taken |= {j.publish_at for j in jobs if j.publish_at}
    day = now.date()
    for job in jobs:
        if job.publish_at:
            continue
        while True:
            cands = [datetime.combine(day, datetime.min.time()).replace(hour=h, minute=m) for h, m in slots[busy[day]:]]
            free = [t for t in cands if t not in taken and t > now + LEAD]
            if free:
                job.publish_at = free[0]
                taken.add(free[0])
                break
            day += timedelta(days=1)
