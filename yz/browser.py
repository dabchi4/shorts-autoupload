"""Подключение patchright к окну профиля ShardBrowser по CDP."""
from __future__ import annotations

from contextlib import contextmanager

from patchright.sync_api import Page, sync_playwright

from .config import load_env
from .shard import Shard


BLOCK_LIGHT = {"media"}                      # видео/аудио в ленте — главный пожиратель трафика
BLOCK_HEAVY = {"media", "image", "font"}     # для разведки и списков


def save_traffic(ctx, level: str = "light"):
    """Не грузить тяжёлые ресурсы (прокси с лимитом трафика). Загрузку нашего файла это не трогает."""
    kinds = BLOCK_HEAVY if level == "heavy" else BLOCK_LIGHT

    def handler(route):
        if route.request.resource_type in kinds:
            return route.abort()
        return route.continue_()

    ctx.route("**/*", handler)


@contextmanager
def open_profile(profile: str):
    """Запускает профиль (или берёт уже запущенный скриптом) и отдаёт (browser, page)."""
    api, token = load_env()
    shard = Shard(api, token)
    p = shard.find_profile(profile)
    ws = shard.start(p["id"])
    with sync_playwright() as pw:
        # is_local=True: файл видео передаётся путём, без лимита 50 МБ
        browser = pw.chromium.connect_over_cdp(ws, is_local=True)
        ctx = browser.contexts[0]  # контекст профиля с его cookies
        page = next((pg for pg in ctx.pages if "studio.youtube.com" in pg.url or "tiktok.com" in pg.url), None)
        if page is None:
            page = ctx.pages[0] if ctx.pages and ctx.pages[0].url in ("about:blank", "chrome://newtab/") else ctx.new_page()
        page.bring_to_front()
        # при выходе patchright просто отключается, окно профиля остаётся открытым
        yield browser, page


def studio_url(channel_id: str = "", account: int | None = None, path: str = "") -> str:
    """При нескольких Google-аккаунтах в профиле Studio открывает канал только с ?authuser=N."""
    base = f"https://studio.youtube.com/channel/{channel_id}{path}" if channel_id else "https://studio.youtube.com/"
    return base + (f"?authuser={account}" if account is not None else "")


def ensure_logged_in(page: Page) -> None:
    if "accounts.google.com" in page.url or "ServiceLogin" in page.url:
        raise RuntimeError("Профиль не залогинен в Google — войди руками в открытом окне и запусти снова.")
