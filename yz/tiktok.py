"""TikTok Studio (tiktok.com/tiktokstudio) в браузере: список постов и заливка.

Прокси профиля с лимитом трафика → в браузере блокируем видео/аудио (save_traffic "light").
Картинки и шрифты не блокировать: без них приложение Studio падает.
Время в Studio — в часовом поясе браузера (страна прокси), поэтому слоты переводим.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta

from patchright.sync_api import Page
from patchright.sync_api import TimeoutError as PwTimeout

from .info import VideoJob

STUDIO = "https://www.tiktok.com/tiktokstudio"


class TikTokError(RuntimeError):
    pass


def _js(page: Page, script: str, arg=None):
    return page.evaluate(script, arg, isolated_context=False)


def _pause(page: Page, ms: int = 600):
    page.wait_for_timeout(ms)


def wait_text(page: Page, needle: str, timeout_s: int = 90):
    end = time.time() + timeout_s
    while time.time() < end:
        if _js(page, "(n) => !!document.body && document.body.innerText.includes(n)", needle):
            return
        _pause(page, 1500)
    raise TikTokError(f"Не дождался «{needle}» на странице TikTok")


def ensure_logged_in(page: Page):
    if "/login" in page.url:
        raise TikTokError("Профиль не залогинен в TikTok — войди руками (QR-код) и запусти снова")


def browser_utc_offset(page: Page) -> timedelta:
    """Смещение часового пояса браузера от UTC (Studio показывает и принимает время в нём)."""
    return timedelta(minutes=-_js(page, "() => new Date().getTimezoneOffset()"))


def to_browser_time(page: Page, local_dt: datetime) -> datetime:
    """Время по часам этого компьютера → время по часам браузера (страна прокси)."""
    local_off = datetime.now().astimezone().utcoffset()
    return local_dt - local_off + browser_utc_offset(page)


def _mmss(s: str) -> float | None:
    m = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{2})", s.strip())
    return None if not m else int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))


def _dialog_texts(page: Page) -> list[str]:
    return _js(page, """() => [...document.querySelectorAll('[role=dialog]')]
        .filter(d => d.getBoundingClientRect().height > 0).map(d => d.innerText.trim())""")


def _click_dialog_button(page: Page, label: str) -> bool:
    b = page.locator("[role=dialog] button").filter(has_text=re.compile(rf"^\s*{re.escape(label)}\s*$"))
    if b.count() and b.last.is_visible():
        b.last.click()
        _pause(page, 800)
        return True
    return False


MONTHS_EN = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
             "october", "november", "december"]


def set_schedule(page: Page, when_browser: datetime):
    """Отложенная публикация. when_browser — время по часам браузера (страна прокси).
    TikTok: не раньше чем через ~15 минут и не дальше ~10 дней; минуты кратны 5."""
    if when_browser.minute % 5:
        raise TikTokError("Минуты должны быть кратны 5")
    sc = page.locator("[data-e2e=schedule_container]")
    radio = sc.locator("input[value=schedule]")
    if not radio.is_checked():
        radio.click(force=True)
        _pause(page, 1200)
        # согласие владельца аккаунта получено (2026-11-14): хранить видео на сервере TikTok до публикации
        _click_dialog_button(page, "Allow")
        _pause(page, 1000)
    if not radio.is_checked():
        raise TikTokError("Не включилась отложенная публикация")
    time_in, date_in = sc.locator("input.TUXTextInputCore-input").nth(0), sc.locator("input.TUXTextInputCore-input").nth(1)

    # дата
    want_date = when_browser.strftime("%Y-%m-%d")
    if date_in.input_value() != want_date:
        cal = page.locator(".calendar-wrapper:visible").first
        for _ in range(3):  # поле работает как переключатель: если календарь был открыт, клик его закроет
            date_in.click()
            _pause(page, 800)
            if cal.count():
                break
        for _ in range(3):
            head = cal.inner_text().split("\n")[0].replace("\xa0", " ").lower()
            if MONTHS_EN[when_browser.month - 1] in head and str(when_browser.year) in head:
                break
            arrows = cal.locator("[class*=arrow]")
            if not arrows.count():
                raise TikTokError(f"Календарь на «{head}», не нашёл стрелку месяца")
            arrows.last.click()
            _pause(page, 600)
        day = cal.locator("span.day.valid").filter(has_text=re.compile(rf"^{when_browser.day}$"))
        if not day.count():
            raise TikTokError(f"День {want_date} недоступен для отложенной публикации (TikTok даёт до ~10 дней вперёд)")
        day.first.click()
        _pause(page, 800)
    if date_in.input_value() != want_date:
        raise TikTokError(f"Дата не встала: {date_in.input_value()} вместо {want_date}")

    # время
    want_time = when_browser.strftime("%H:%M")
    if time_in.input_value() != want_time:
        for _ in range(3):
            time_in.click()
            _pause(page, 800)
            if page.locator("span.tiktok-timepicker-option-text:visible").count():
                break
        for side, val in (("left", f"{when_browser.hour:02d}"), ("right", f"{when_browser.minute:02d}")):
            opt = page.locator(f"span.tiktok-timepicker-option-text.tiktok-timepicker-{side}:visible").filter(
                has_text=re.compile(rf"^{val}$"))
            if not opt.count():
                raise TikTokError(f"Нет значения {val} в выборе времени")
            opt.first.scroll_into_view_if_needed()
            opt.first.click()
            _pause(page, 500)
        page.keyboard.press("Escape")
        _pause(page, 500)
    if time_in.input_value() != want_time:
        raise TikTokError(f"Время не встало: {time_in.input_value()} вместо {want_time}")


def tiktok_caption(job: VideoJob, suffix: str = "") -> str:
    """Подпись TikTok: название без суффикса YouTube-канала (как в ваших постах)."""
    t = job.title
    if suffix and t.endswith(suffix):
        t = t[: -len(suffix)]
    return t.strip()


def upload(page: Page, job: VideoJob, caption: str, dry_run: bool = False, log=print) -> dict:
    """Залить ролик в TikTok Studio: сразу (publish_now / без даты) или отложенно (job.publish_at — местное время)."""
    page.set_default_timeout(90_000)
    page.set_default_navigation_timeout(120_000)
    if "/tiktokstudio/upload" not in page.url:
        page.goto(f"{STUDIO}/upload?from=creator_center&tab=video", wait_until="domcontentloaded")
    ensure_logged_in(page)
    page.locator("input[type=file]").first.wait_for(state="attached", timeout=120_000)
    if page.locator("[data-e2e=caption_container]").count():
        raise TikTokError("На странице загрузки уже есть незавершённый ролик — сначала разберись с ним вручную")

    log("  файл…")
    page.locator("input[type=file]").first.set_input_files(str(job.video))
    page.locator("[data-e2e=caption_container]").wait_for(timeout=180_000)
    _click_dialog_button(page, "Got it")

    log("  подпись…")
    box = page.locator("[data-e2e=caption_container] [contenteditable=true]").first
    box.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    page.keyboard.insert_text(caption)
    _pause(page, 800)
    page.keyboard.press("Escape")  # закрыть подсказки хештегов/упоминаний, если всплыли
    got = " ".join(box.inner_text().split())
    if got != " ".join(caption.split()):
        raise TikTokError(f"Подпись не совпала: «{got}»")

    # «AI-generated content»
    sm = page.get_by_text("Show more", exact=True)
    if sm.count() and sm.first.is_visible():
        sm.first.click()
        _pause(page, 800)
    want_ai = job.ai in {"да", "yes"}
    ai = page.locator("[data-e2e=aigc_container] input[role=switch]").first
    if ai.count() and ai.is_checked() != want_ai:
        ai.click(force=True)
        _pause(page, 800)
        _click_dialog_button(page, "Turn on") or _click_dialog_button(page, "Confirm")
    if ai.count() and ai.is_checked() != want_ai:
        raise TikTokError("Не удалось выставить переключатель «AI-generated content»")

    scheduled = bool(job.publish_at and not job.publish_now)
    if scheduled:
        when_b = to_browser_time(page, job.publish_at)
        log(f"  отложенно: {job.publish_at:%d.%m %H:%M} местного = {when_b:%d.%m %H:%M} по часам TikTok")
        set_schedule(page, when_b)
    else:
        now = page.locator("[data-e2e=schedule_container] input[value=post_now]")
        if not now.is_checked():
            now.click(force=True)

    log("  жду загрузки файла…")
    end = time.time() + 30 * 60
    while time.time() < end:
        st = page.locator("[data-e2e=upload_status_container]").inner_text()
        if "Uploaded" in st or "Загружено" in st:
            break
        if re.search(r"fail|error|ошибк", st, re.I):
            raise TikTokError(f"TikTok не принял файл: {st}")
        _pause(page, 2000)
    else:
        raise TikTokError("Файл не догрузился за 30 минут")

    if dry_run:
        return {"posted": False, "note": "тестовый режим: Post не нажат (черновик остался на странице)"}

    log("  публикую…")
    btn = page.locator("[data-e2e=post_video_button]")
    end = time.time() + 120
    while btn.get_attribute("aria-disabled") == "true" or btn.is_disabled():
        if time.time() > end:
            raise TikTokError("Кнопка Post так и не стала активной")
        _pause(page, 1500)
    btn.click()
    end = time.time() + 180
    while time.time() < end:
        _pause(page, 1500)
        # окно «проверка ещё идёт — опубликовать сейчас?»
        if (_click_dialog_button(page, "Post now") or _click_dialog_button(page, "Schedule")
                or _click_dialog_button(page, "Опубликовать сейчас")):
            continue
        if "/tiktokstudio/content" in page.url:
            return {"posted": True, "scheduled": scheduled}
        body = _js(page, "() => document.body.innerText")
        if re.search(r"has been (uploaded|posted|scheduled)|Video published|опубликовано", body, re.I):
            return {"posted": True, "scheduled": scheduled}
        for d in _dialog_texts(page):
            if re.search(r"fail|error|couldn|не удалось", d, re.I):
                raise TikTokError(f"TikTok: {d[:200]}")
    raise TikTokError("Не дождался подтверждения публикации")


def open_posts(page: Page):
    page.set_default_timeout(90_000)
    page.set_default_navigation_timeout(120_000)
    if "/tiktokstudio/content" not in page.url:
        page.goto(f"{STUDIO}/content", wait_until="domcontentloaded")
    ensure_logged_in(page)
    end = time.time() + 120
    while time.time() < end and not _js(page, "() => document.querySelectorAll('a[href*=\"/video/\"]').length"):
        if _js(page, "() => document.body.innerText.includes('Unexpected Application Error')"):
            page.reload(wait_until="domcontentloaded")
        _pause(page, 2000)


def list_posts(page: Page, max_rows: int = 60) -> list[dict]:
    """Посты из TikTok Studio → [{id, title, duration, date, privacy}] (новые сверху)."""
    open_posts(page)
    # список виртуальный и мог остаться прокрученным с прошлого раза — начинаем с самого верха
    _js(page, """() => { const a = document.querySelector('a[href*="/video/"]'); let sc = a;
        while (sc && !(sc.scrollHeight > sc.clientHeight + 50 && /auto|scroll/.test(getComputedStyle(sc).overflowY))) sc = sc.parentElement;
        (sc || document.scrollingElement).scrollTop = 0; window.scrollTo(0, 0); }""")
    _pause(page, 2500)
    seen: dict[str, dict] = {}
    stale = 0
    while len(seen) < max_rows and stale < 5:
        rows = _js(page, """() => [...document.querySelectorAll('[data-tt="components_RowLayout_FlexRow"]')].map(r => {
            const a = r.querySelector('a[href*="/video/"]');
            return a ? {href: a.getAttribute('href'), cells: r.innerText.split('\\n').map(s => s.trim()).filter(Boolean)} : null;
        }).filter(Boolean)""")
        before = len(seen)
        for r in rows:
            m = re.search(r"/video/(\d+)", r["href"])
            if not m or m.group(1) in seen:
                continue
            c = r["cells"]
            seen[m.group(1)] = {"id": m.group(1), "duration": _mmss(c[0]) if c else None,
                                "title": c[1] if len(c) > 1 else "", "date": c[2] if len(c) > 2 else "",
                                "privacy": c[3] if len(c) > 3 else "", "status": "Опубликовано"}
        stale = stale + 1 if len(seen) == before else 0
        # прокрутить контейнер таблицы
        _js(page, """() => { let a = [...document.querySelectorAll('a[href*="/video/"]')].pop(); let sc = a;
            while (sc && !(sc.scrollHeight > sc.clientHeight + 50 && /auto|scroll/.test(getComputedStyle(sc).overflowY))) sc = sc.parentElement;
            (sc || document.scrollingElement).scrollTop += 600; }""")  # мелкий шаг: виртуальный список не пропускает строки
        _pause(page, 1500)
    return list(seen.values())
