"""Заливка одного Shorts через окно загрузки YouTube Studio.

Селекторы подобраны по живому DOM Studio (ноябрь 2026). Опора на id / name / test-id,
а не на текст кнопок — кроме пользовательских данных (название плейлиста, игры).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

from patchright.sync_api import Locator, Page
from patchright.sync_api import TimeoutError as PwTimeout

from .browser import ensure_logged_in, studio_url
from .info import VideoJob

DLG = "ytcp-uploads-dialog"
UPLOAD_TIMEOUT_S = 30 * 60


class UploadError(RuntimeError):
    pass


@dataclass
class Result:
    video_id: str
    url: str
    published: bool
    notes: list[str]


def _js(page: Page, script: str, arg=None):
    return page.evaluate(script, arg, isolated_context=False)


def _pause(page: Page, ms: int = 600):
    page.wait_for_timeout(ms)


def _check_blockers(page: Page):
    """Диалоги, после которых продолжать нельзя."""
    if page.locator("ytcp-auth-confirmation-dialog tp-yt-paper-dialog:not([aria-hidden=true])").count():
        raise UploadError("Google просит подтвердить, что это вы (auth-confirmation). Подтверди руками и запусти снова.")
    err = _js(page, f"""() => {{
        const d = document.querySelector('{DLG}');
        if (!d) return null;
        const e = d.querySelector('.error-area');
        return (e && e.offsetParent !== null && e.innerText.trim()) || null;
    }}""")
    if err:
        raise UploadError(f"Studio показал ошибку: {err}")


def _set_contenteditable(page: Page, box: Locator, text: str):
    box.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    if text:
        # insert_text не вызывает посимвольный автокомплит #хештегов / @упоминаний
        page.keyboard.insert_text(text)
    _pause(page, 400)


def _close_suggestion_popups(page: Page):
    # клик по заголовку шага закрывает всплывающие подсказки хештегов
    page.locator(f"{DLG} #step-badge-0").click()
    _pause(page, 300)


def _read_box(box: Locator) -> str:
    return box.inner_text().replace("\xa0", " ").strip()


# ---------- шаги ----------

def _open_studio_for(page: Page, channel_id: str, account: int = 0) -> bool:
    """Открыть Studio канала по прямому адресу. False — «нет доступа» (активен другой канал аккаунта)."""
    page.goto(studio_url(channel_id, account), wait_until="domcontentloaded")
    ensure_logged_in(page)
    page.wait_for_function("""() => document.querySelector('#upload-icon') ||
        /нет доступа|don.t have (permission|access)/i.test(document.body ? document.body.innerText : '')""",
                           timeout=60_000)
    return page.locator("#upload-icon").count() > 0 and channel_id in page.url


def switch_channel(page: Page, channel_id: str, handle: str, account: int = 0):
    """Сделать канал активным.

    Корень studio.youtube.com ведёт на последний открытый там канал, а studio…/channel/<id>
    открывается только если этот канал активен на YouTube. Переключение — через channel_switcher;
    ?authuser=N показывает каналы N-го Google-аккаунта профиля.
    """
    if not channel_id or _open_studio_for(page, channel_id, account):
        return
    if not handle:
        raise UploadError(f"Активен другой канал, а для {channel_id} не задан handle (@имя) в channels.ini")
    page.goto(f"https://www.youtube.com/channel_switcher?authuser={account}", wait_until="domcontentloaded")
    items = page.locator("ytd-account-item-renderer")
    items.first.wait_for(state="visible", timeout=30_000)
    target = items.filter(has_text=handle)
    if target.count() != 1:
        raise UploadError(f"На странице выбора канала (Google-аккаунт №{account}) не найден {handle}. "
                          "Проверь account в channels.ini: 0 — первый вошедший аккаунт, 1 — второй")
    target.click()
    # клик уводит через /signin?…&feature=channel_switcher на главную YouTube — ждём этого
    page.wait_for_url(lambda u: "channel_switcher" not in u and "/signin" not in u, timeout=30_000)
    page.wait_for_load_state("domcontentloaded")
    _pause(page, 1500)
    if not _open_studio_for(page, channel_id, account):
        raise UploadError(f"После переключения на {handle} Studio всё ещё не открывает канал {channel_id}")


def open_upload_dialog(page: Page, channel_id: str, handle: str = "", account: int = 0):
    switch_channel(page, channel_id, handle, account)  # оставляет открытой Studio нужного канала
    if channel_id and channel_id not in page.url:
        raise UploadError(f"Открыт не тот канал: {page.url}")
    page.locator("#upload-icon").first.click()
    page.wait_for_selector(f"{DLG} input[type=file]", state="attached", timeout=30_000)


def attach_file(page: Page, job: VideoJob) -> str:
    page.locator(f"{DLG} input[type=file]").set_input_files(str(job.video))
    try:
        page.wait_for_selector(f"{DLG} #title-textarea #textbox", timeout=120_000)
    except PwTimeout:
        _check_blockers(page)
        if page.get_by_text(re.compile("лимит|limit", re.I)).count():
            raise UploadError("Похоже, достигнут дневной лимит загрузок YouTube")
        raise
    page.wait_for_function(f"() => document.querySelector('{DLG}')?.getAttribute('video-id')", timeout=120_000)
    return _js(page, f"() => document.querySelector('{DLG}').getAttribute('video-id')")


def fill_details(page: Page, job: VideoJob, notes: list[str]):
    d = page.locator(DLG)
    title = d.locator("#title-textarea #textbox")
    _set_contenteditable(page, title, job.title)
    desc = d.locator("#description-textarea #textbox")
    _set_contenteditable(page, desc, job.description)
    _close_suggestion_popups(page)

    if job.thumbnail:
        set_thumbnail(page, job)
    if d.get_by_text("разовую проверку").count():
        notes.append("ссылки в описании не будут кликабельны, пока канал не пройдёт разовую проверку (телефон)")

    if job.playlist:
        select_playlist(page, job.playlist)

    kids = "VIDEO_MADE_FOR_KIDS_MFK" if job.made_for_kids else "VIDEO_MADE_FOR_KIDS_NOT_MFK"
    d.locator(f"tp-yt-paper-radio-button[name={kids}]").click()

    # дополнительные настройки
    if not d.locator("#tags-container").is_visible():
        d.locator("#toggle-button").click()
        d.locator("#tags-container").wait_for(state="visible")
    # «Изменённый/синтетический контент (ИИ)»: отвечаем только если это задано явно (ai = да/нет)
    if job.ai in {"да", "нет", "yes", "no"}:
        name = "VIDEO_HAS_ALTERED_CONTENT_YES" if job.ai in {"да", "yes"} else "VIDEO_HAS_ALTERED_CONTENT_NO"
        altered = d.locator(f"tp-yt-paper-radio-button[name={name}]")
        if altered.count():
            altered.click()

    if job.tags:
        set_tags(page, job.tags)

    if job.game:
        set_category(page, "CREATOR_VIDEO_CATEGORY_GADGETS")
        set_game(page, job.game)

    # сверка
    got_title = _read_box(title)
    if got_title != job.title.strip():
        raise UploadError(f"Название не совпало после ввода: {got_title!r}")
    got_desc = _read_box(desc)
    if got_desc.split() != job.description.split():
        raise UploadError("Описание не совпало после ввода")


TRUST_DIALOG = "yt-trust-tiers-wizard-dialog"


def _trust_dialog_shown(page: Page) -> bool:
    return bool(_js(page, f"""() => {{
        const d = document.querySelector('{TRUST_DIALOG} ytcp-dialog, {TRUST_DIALOG} tp-yt-paper-dialog');
        return !!d && (d.offsetParent !== null || d.getAttribute('aria-hidden') === 'false') &&
               !!document.querySelector('{TRUST_DIALOG}').innerText.trim();
    }}"""))


def _dismiss_trust_dialog(page: Page):
    page.locator(f"{TRUST_DIALOG} #cancel-button").click()
    _pause(page, 500)


def set_thumbnail(page: Page, job: VideoJob):
    up = page.locator(f"{DLG} ytcp-thumbnail-uploader")
    before = _js(page, f"() => [...document.querySelectorAll('{DLG} ytcp-thumbnail-uploader img')].map(i => i.src)")
    try:
        # скрытый input не реагирует на set_input_files — только через настоящий выбор файла
        with page.expect_file_chooser(timeout=6000) as fc:
            up.locator("#select-button").click()
        fc.value.set_files(str(job.thumbnail))
    except PwTimeout:
        if _trust_dialog_shown(page):
            _dismiss_trust_dialog(page)
            raise UploadError("Своя обложка недоступна: YouTube просит подтвердить номер телефона на этом канале")
        raise UploadError("Не открылся выбор файла обложки")
    for _ in range(20):
        _pause(page, 500)
        err = _js(page, f"""() => [...document.querySelectorAll('{DLG} ytcp-video-thumbnail-editor ytcp-form-error-tip')]
                              .map(e => e.innerText.trim()).filter(Boolean).join('; ')""")
        if err:
            raise UploadError(f"Обложка не принята: {err}")
        after = _js(page, f"() => [...document.querySelectorAll('{DLG} ytcp-thumbnail-uploader img')].map(i => i.src)")
        if after != before:
            return
    raise UploadError("Обложка не появилась в превью")


def select_playlist(page: Page, name: str):
    d = page.locator(DLG)
    d.locator("ytcp-video-metadata-playlists ytcp-dropdown-trigger").click()
    pdlg = page.locator("ytcp-playlist-dialog tp-yt-paper-dialog[aria-label]")
    pdlg.wait_for(state="visible")
    _pause(page, 800)
    # каждый плейлист: <label>Название<ytcp-checkbox-lit test-id=PL… [checked]></label>
    find_js = """(name) => {
        const lbl = [...document.querySelectorAll('ytcp-playlist-dialog #items label')]
            .find(l => l.innerText.trim() === name);
        const cb = lbl && lbl.querySelector('ytcp-checkbox-lit');
        return cb ? {id: cb.id, checked: cb.hasAttribute('checked')} : null;
    }"""
    found = _js(page, find_js, name)
    if not found:
        page.locator("ytcp-playlist-dialog .done-button:visible").click()
        raise UploadError(f"Плейлист {name!r} не найден на канале")
    if not found["checked"]:
        page.locator(f"ytcp-playlist-dialog #items ytcp-checkbox-lit#{found['id']}").click()
        _pause(page, 400)
        if not (_js(page, find_js, name) or {}).get("checked"):
            raise UploadError(f"Не удалось отметить плейлист {name!r}")
    save = page.locator("ytcp-playlist-dialog .save-button:visible")
    if save.count() and save.get_attribute("aria-disabled") != "true":
        save.click()
    else:
        page.locator("ytcp-playlist-dialog .done-button:visible").click()
    pdlg.wait_for(state="hidden")


def set_tags(page: Page, tags: list[str]):
    inp = page.locator(f"{DLG} #tags-container input#text-input")
    inp.click()
    existing = _js(page, f"""() => [...document.querySelectorAll('{DLG} #tags-container ytcp-chip')]
                           .map(c => c.innerText.trim())""")
    for t in tags:
        if t in existing:
            continue
        inp.fill(t)
        page.keyboard.type(",")  # чип создаётся только по настоящему нажатию запятой
        _pause(page, 150)
    _pause(page, 400)
    chips = _js(page, f"""() => [...document.querySelectorAll('{DLG} #tags-container ytcp-chip')]
                        .map(c => c.innerText.trim()).filter(Boolean)""")
    missing = [t for t in tags if t not in chips]
    if missing:
        raise UploadError(f"Теги не добавились: {missing}")


def set_category(page: Page, test_id: str):
    trig = page.locator(f"{DLG} ytcp-form-select#category ytcp-dropdown-trigger")
    trig.click()
    opt = page.locator(f"tp-yt-paper-item[test-id={test_id}]")
    opt.wait_for(state="visible")
    opt.click()
    _pause(page, 500)


def set_game(page: Page, game: str):
    inp = page.locator(f"{DLG} ytcp-form-gaming input")
    inp.wait_for(state="visible")
    inp.click()
    inp.fill("")
    inp.type(game, delay=40)
    # выпадающий список рисуется вне ytcp-form-gaming; у вариантов игры test-id = JSON с "title"
    opts = page.locator("tp-yt-paper-item[role=option][test-id*='\"title\"']:visible")
    try:
        opts.first.wait_for(state="visible", timeout=10_000)
    except PwTimeout:
        raise UploadError(f"YouTube не нашёл игру {game!r}")
    _pause(page, 800)
    items = []
    for i in range(opts.count()):
        try:
            meta = json.loads(opts.nth(i).get_attribute("test-id") or "{}")
        except json.JSONDecodeError:
            continue
        items.append((i, meta.get("title", ""), str(meta.get("year", ""))))
    want = game.strip().lower()
    pick = next((i for i, t, y in items if t.lower() == want or f"{t} {y}".lower() == want), None)
    if pick is None:
        names = ", ".join(f"{t} {y}".strip() for _, t, y in items[:8])
        page.keyboard.press("Escape")
        raise UploadError(f"Игра {game!r} не найдена точно. Варианты: {names}")
    opts.nth(pick).click()
    _pause(page, 500)


def video_id_from(url_or_id: str) -> str:
    s = url_or_id.strip()
    m = re.search(r"(?:youtu\.be/|/shorts/|/live/|[?&]v=|/video/)([\w-]{11})", s)
    if m:
        return m.group(1)
    if re.fullmatch(r"[\w-]{11}", s):
        return s
    raise UploadError(f"Не понял ссылку на связанное видео: {url_or_id!r}")


def set_related_video(page: Page, url_or_id: str):
    """«Похожие видео» в шаге «Дополнения»: только ролики своего канала, ищем по ID."""
    vid = video_id_from(url_or_id)
    page.locator(f"{DLG} #shorts-content-links-add-button").click()
    _pause(page, 1500)
    if _trust_dialog_shown(page):
        _dismiss_trust_dialog(page)
        raise UploadError("«Похожие видео» недоступны: на канале не включены расширенные функции "
                          "(Studio просит подтвердить телефон)")
    search = page.locator("input#search-yours:visible")
    search.wait_for(timeout=15_000)
    search.fill(vid)
    cards = page.locator("ytcp-video-pick-dialog-contents ytcp-entity-card:visible")
    # поиск асинхронный: ждём, пока останется ровно одна карточка
    for _ in range(20):
        _pause(page, 500)
        if cards.count() == 1:
            break
    if cards.count() != 1:
        page.locator("tp-yt-paper-dialog:visible ytcp-icon-button#close").last.click()
        raise UploadError(f"Связанное видео {vid} не найдено среди роликов канала "
                          f"(нашлось {cards.count()}). Оно должно быть на этом же канале и не черновиком")
    title = (cards.first.get_attribute("aria-label") or "").rsplit(", ", 1)[0].strip()
    cards.first.click()
    search.wait_for(state="hidden", timeout=10_000)
    picked = page.locator(f"{DLG} ytcp-shorts-content-links-picker").inner_text()
    if title and title not in picked:
        raise UploadError(f"Связанное видео не встало: ожидалось «{title}»")


def set_visibility(page: Page, job: VideoJob):
    d = page.locator(DLG)
    if job.publish_now or job.publish_at is None:
        name = "PUBLIC" if job.publish_now else "PRIVATE"
        radio = d.locator(f"tp-yt-paper-radio-button[name={name}]")
        if not radio.is_visible():
            exp = d.locator("#first-container-expand-button:visible")
            if exp.count():
                exp.click()
                _pause(page, 600)
        radio.click()
        _pause(page, 500)
        if radio.get_attribute("aria-checked") != "true":
            raise UploadError(f"Не удалось выбрать доступ {name}")
        return
    # Обычный канал: блок «Запланировать» свёрнут (#second-container-expand-button).
    # Канал со спонсорством: радио PUBLISH_FROM_PRIVATE / PUBLISH_FROM_SPONSORS_ONLY, у каждого свой
    # планировщик — нужен обычный (виден и не disabled).
    active = "ytcp-visibility-scheduler:visible:has(#datepicker-trigger:not([disabled]))"
    if d.locator(active).count() == 0:
        exp = d.locator("#second-container-expand-button:visible")
        if exp.count():
            exp.click()
            _pause(page, 800)
    pub = d.locator("tp-yt-paper-radio-button[name=PUBLISH_FROM_PRIVATE]:visible")
    if pub.count() and pub.get_attribute("aria-checked") != "true":
        pub.click()
        _pause(page, 500)
    sch = d.locator(active).first
    trig = sch.locator("#datepicker-trigger")
    trig.wait_for(state="visible", timeout=15_000)
    date_text = _js(page, """(ts) => new Intl.DateTimeFormat(document.documentElement.lang,
                     {day:'numeric', month:'short', year:'numeric'}).format(new Date(ts))""",
                    job.publish_at.timestamp() * 1000)
    trig.click()
    dinp = page.locator("ytcp-date-picker tp-yt-paper-dialog input:visible")
    dinp.wait_for(state="visible")
    dinp.fill(date_text)
    dinp.press("Enter")
    _pause(page, 600)
    tinp = sch.locator("#time-of-day-container input")
    tinp.click()
    tinp.fill(job.publish_at.strftime("%H:%M"))
    tinp.press("Enter")
    _pause(page, 800)
    norm = lambda s: " ".join(s.replace("\xa0", " ").replace(" ", " ").split())  # noqa: E731
    date_text = norm(date_text)
    got_date = norm(trig.inner_text())
    got_time = tinp.input_value().strip()
    if date_text not in got_date or got_time != job.publish_at.strftime("%H:%M"):
        raise UploadError(f"Дата публикации не встала: {got_date} {got_time}, ожидалось {date_text} {job.publish_at:%H:%M}")
    err = _js(page, """() => [...document.querySelectorAll('ytcp-uploads-dialog ytcp-visibility-scheduler ytcp-form-error-tip')]
                               .filter(t => t.offsetParent !== null).map(t => t.innerText.trim()).filter(Boolean).join('; ') || null""")
    if err:
        raise UploadError(f"Studio не принял дату: {err}")


def _mmss(s: str) -> float | None:
    m = re.fullmatch(r"(?:(\d+):)?(\d+):(\d{2})", s.strip())
    if not m:
        return None
    h, mi, se = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
    return h * 3600 + mi * 60 + se


def list_channel_videos(page: Page, channel_id: str, account: int = 0, kind: str = "short",
                        max_pages: int = 4) -> list[dict]:
    """Ролики канала из Studio «Контент» (вкладка Shorts): id, название, длительность, статус, дата."""
    url = studio_url(channel_id, account, f"/videos/{kind}")
    for _ in range(3):  # большие каналы иногда отдают пустую страницу с первого раза
        page.goto(url, wait_until="load")
        try:
            page.wait_for_selector("ytcp-video-row", timeout=40_000)
            break
        except PwTimeout:
            continue
    else:
        return []
    out, seen = [], set()
    for _ in range(max_pages):
        _pause(page, 1500)
        rows = _js(page, """() => [...document.querySelectorAll('ytcp-video-row')].map(r => {
            const m = r.outerHTML.match(/\\/vi\\/([\\w-]{11})\\//);
            const cells = r.innerText.split('\\n').map(s => s.trim()).filter(Boolean);
            return {id: m && m[1], title: (r.querySelector('#video-title')||{}).innerText?.trim() || '', cells};
        })""")
        for r in rows:
            if not r["id"] or r["id"] in seen:
                continue
            seen.add(r["id"])
            cells = r["cells"]
            status = next((c for c in cells if c in {"Опубликовано", "Отложенная публикация", "Черновик",
                                                       "Загружено", "Обработка", "Published", "Scheduled", "Draft"}), "")
            date = next((c for c in cells if re.search(r"\d{4}\s*г\.|\b\d{4}\b", c) and re.search(r"[а-яa-z]", c, re.I)), "")
            out.append({"id": r["id"], "title": r["title"], "duration": _mmss(cells[0]) if cells else None,
                        "status": status, "date": date})
        nxt = page.locator("ytcp-table-footer #navigate-after:not([disabled])")
        if not nxt.count() or nxt.get_attribute("aria-disabled") == "true":
            break
        nxt.click()
    return out


def _pick_related_in_dialog(page: Page, vid: str) -> str:
    """Окно «Выберите видео» уже открыто: найти ролик по ID, выбрать, вернуть его название."""
    search = page.locator("input#search-yours:visible")
    search.wait_for(timeout=15_000)
    search.fill(vid)
    cards = page.locator("ytcp-video-pick-dialog-contents ytcp-entity-card:visible")
    for _ in range(20):
        _pause(page, 500)
        if cards.count() == 1:
            break
    if cards.count() != 1:
        page.locator("tp-yt-paper-dialog:visible ytcp-icon-button#close").last.click()
        raise UploadError(f"Видео {vid} не найдено среди роликов канала (нашлось {cards.count()})")
    title = (cards.first.get_attribute("aria-label") or "").rsplit(", ", 1)[0].strip()
    cards.first.click()
    search.wait_for(state="hidden", timeout=10_000)
    return title


def set_related_on_edit(page: Page, video_id: str, target: str, account: int = 0) -> str:
    """Страница редактирования готового ролика: поставить «Видео по теме» и сохранить.
    Возвращает 'уже стоит' или 'заменено (было: …)'."""
    tid = video_id_from(target)
    page.goto(f"https://studio.youtube.com/video/{video_id}/edit?authuser={account}", wait_until="domcontentloaded")
    page.wait_for_selector("#title-textarea #textbox", timeout=60_000)
    _pause(page, 2000)
    trig = page.locator("ytcp-shorts-content-links-picker #linked-video-editor-link")
    trig.wait_for(timeout=20_000)
    before = " ".join(trig.inner_text().split())
    trig.click()
    _pause(page, 1500)
    if _trust_dialog_shown(page):
        _dismiss_trust_dialog(page)
        raise UploadError("«Видео по теме» недоступно на этом канале")
    title = _pick_related_in_dialog(page, tid)
    _pause(page, 800)
    after = " ".join(trig.inner_text().split())
    if title and title.split("|")[0].strip() not in after:
        raise UploadError(f"«Видео по теме» не встало: в поле «{after}»")
    save = page.locator("ytcp-button#save")
    if save.get_attribute("aria-disabled") == "true":
        return "уже стоит"  # выбрали то же видео — сохранять нечего
    save.click()
    page.wait_for_function("() => document.querySelector('ytcp-button#save')?.getAttribute('aria-disabled') === 'true'",
                           timeout=30_000)
    _pause(page, 1000)
    return f"заменено (было: {before.replace('Видео по теме', '').strip() or 'пусто'})"


def wait_upload_finished(page: Page):
    deadline = time.time() + UPLOAD_TIMEOUT_S
    while time.time() < deadline:
        _check_blockers(page)
        state = _js(page, f"""() => {{
            const p = document.querySelector('{DLG} ytcp-video-upload-progress');
            if (!p) return 'none';
            if (p.hasAttribute('uploading')) return 'uploading';
            return 'done';
        }}""")
        if state == "done":
            return
        page.wait_for_timeout(2000)
    raise UploadError("Видео не догрузилось за 30 минут")


def current_step(page: Page) -> str:
    return _js(page, f"() => document.querySelector('{DLG}').getAttribute('workflow-step')") or ""


AD_RATING_NONE = {"ничего", "нет", "none"}


def set_ad_rating(page: Page, answer: str):
    """Анкета «Требования рекламодателей» (только у монетизированных каналов).

    Поддерживается один ответ — «Ничего из указанного выше». Отвечать за автора без явной
    настройки нельзя: это его заявление о содержании ролика, и после оценки его не изменить.
    """
    q = page.locator(f"{DLG} ytpp-self-certification-questionnaire")
    q.wait_for(state="visible")
    if (answer or "").strip().lower() not in AD_RATING_NONE:
        raise UploadError("Канал монетизирован: нужна оценка «Требования рекламодателей». "
                          "Задай «ad_rating = ничего» в channels.ini или «Реклама: ничего» в info.txt, "
                          "если в роликах нет ничего из списка YouTube")
    none_box = q.locator("ytcp-checkbox-lit").last
    label = none_box.locator("xpath=ancestor::label[1]") if none_box.count() else None
    text = (label.inner_text() if label and label.count() else q.inner_text())
    if "Ничего из указанного" not in text and "None of the above" not in text:
        raise UploadError("Не нашёл пункт «Ничего из указанного выше» в анкете рекламодателей")
    if none_box.get_attribute("checked") is None:
        none_box.click()
        _pause(page, 500)
    submit = q.locator("#submit-questionnaire-button")
    page.wait_for_function(
        f"() => document.querySelector('{DLG} #submit-questionnaire-button')?.getAttribute('aria-disabled') === 'false'",
        timeout=10_000)
    submit.click()
    _pause(page, 1500)


def click_next(page: Page):
    btn = page.locator(f"{DLG} #next-button")
    page.wait_for_function(f"() => document.querySelector('{DLG} #next-button')?.getAttribute('aria-disabled') === 'false'",
                           timeout=60_000)
    btn.click()
    _pause(page, 1200)


def finish(page: Page):
    page.wait_for_function(f"() => document.querySelector('{DLG} #done-button')?.getAttribute('aria-disabled') === 'false'",
                           timeout=60_000)
    page.locator(f"{DLG} #done-button").click()
    # возможные диалоги после «Сохранить/Запланировать»
    for _ in range(20):
        _pause(page, 1000)
        for sel in ("ytcp-prechecks-warning-dialog #primary-action-button",
                    "ytcp-uploads-still-processing-dialog #close-button",
                    "ytcp-video-share-dialog #close-button"):
            b = page.locator(sel)
            if b.count() and b.first.is_visible():
                b.first.click()
                _pause(page, 800)
        if not page.locator(f"{DLG} tp-yt-paper-dialog#dialog").is_visible():
            return
    raise UploadError("Окно загрузки не закрылось после сохранения")


def upload(page: Page, job: VideoJob, channel_id: str, handle: str = "", account: int = 0,
           dry_run: bool = False, log=print) -> Result:
    notes: list[str] = []
    open_upload_dialog(page, channel_id, handle, account)
    log("  файл…")
    video_id = attach_file(page, job)
    url = f"https://youtube.com/shorts/{video_id}"
    log(f"  создан черновик {url}")
    log("  информация…")
    fill_details(page, job, notes)
    # шаги зависят от канала: у монетизированных есть ещё «Требования рекламодателей»
    for _ in range(8):
        click_next(page)
        step = current_step(page)
        if step == "CONTENT_RATINGS":
            log("  требования рекламодателей…")
            set_ad_rating(page, job.ad_rating)
        elif step == "VIDEO_ELEMENTS" and job.related_video:
            log("  похожее видео…")
            set_related_video(page, job.related_video)
        elif step == "REVIEW":
            break
    else:
        raise UploadError(f"Не дошли до шага «Доступ», застряли на {current_step(page)}")
    log("  доступ / расписание…")
    set_visibility(page, job)
    log("  жду окончания загрузки…")
    wait_upload_finished(page)
    if dry_run:
        notes.append("тестовый режим: не нажато «Сохранить», ролик остался приватным черновиком")
        return Result(video_id, url, False, notes)
    finish(page)
    return Result(video_id, url, True, notes)
