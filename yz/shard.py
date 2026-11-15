"""Клиент локального API ShardBrowser (127.0.0.1:40325)."""
from __future__ import annotations

import requests


class ShardError(RuntimeError):
    pass


class Shard:
    def __init__(self, base_url: str, token: str):
        if not token:
            raise ShardError(
                "Нет SHARD_TOKEN. Скопируй токен в ShardBrowser: Settings → Automation API, "
                "и впиши его в файл .env (SHARD_TOKEN=...)."
            )
        self.base = base_url.rstrip("/")
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token}"

    def _req(self, method: str, path: str, **kw):
        r = self.s.request(method, self.base + path, timeout=kw.pop("timeout", 15), **kw)
        if r.status_code == 401:
            raise ShardError("ShardBrowser отклонил токен (401). Проверь SHARD_TOKEN в .env.")
        if r.status_code >= 400:
            raise ShardError(f"ShardBrowser {method} {path}: {r.status_code} {r.text[:300]}")
        return r.json() if r.content else None

    def profiles(self) -> list[dict]:
        data = self._req("GET", "/profiles")
        return data if isinstance(data, list) else data.get("profiles", data.get("items", []))

    def find_profile(self, name_or_id: str) -> dict:
        items = self.profiles()
        for p in items:
            if p.get("id") == name_or_id or p.get("name") == name_or_id:
                return p
        names = ", ".join(repr(p.get("name")) for p in items)
        raise ShardError(f"Профиль {name_or_id!r} не найден. Есть: {names}")

    def profile(self, pid: str) -> dict:
        return self._req("GET", f"/profiles/{pid}")

    def start(self, pid: str) -> str:
        """Запускает профиль (видимое окно) и возвращает CDP websocket URL."""
        info = self.profile(pid)
        if info.get("running"):
            cdp = info.get("cdp") or {}
            if cdp.get("web_socket_debugger_url"):
                return cdp["web_socket_debugger_url"]
            raise ShardError(
                "Профиль уже открыт из окна ShardBrowser — к такому окну скрипт подключиться не может. "
                "Закрой это окно браузера и запусти заново через скрипт (вход в аккаунт сохранится)."
            )
        data = self._req("POST", f"/profiles/{pid}/start", json={"headless": False}, timeout=60)
        cdp = (data or {}).get("cdp") or {}
        url = cdp.get("web_socket_debugger_url")
        if not url:
            raise ShardError(f"ShardBrowser не отдал CDP: {(data or {}).get('cdp_error')}")
        return url
