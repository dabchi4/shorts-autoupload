"""Настройки: .env (токен ShardBrowser) и channels.ini (каналы)."""
from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
QUEUE = ROOT / "queue"
DONE = ROOT / "done"
ERRORS = ROOT / "errors"


@dataclass
class Channel:
    name: str          # имя папки в queue/
    profile: str       # имя или id профиля ShardBrowser
    channel_id: str    # UC... (проверка, что открыт нужный канал)
    handle: str        # @имя — по нему переключаемся между каналами
    account: int       # номер Google-аккаунта в профиле (authuser): 0 — первый вошедший, 1 — второй…
    source: Path       # папка с роликами канала (каждый ролик — подпапка)
    enabled: bool      # enabled = нет → канал не заливается и не проверяется
    platform: str      # youtube | tiktok
    defaults: dict[str, str]

    @property
    def marker(self) -> str:
        """Файл-метка «залито» в папке ролика: у YouTube — uploaded.txt, у остальных — свой на каждое назначение."""
        if self.platform == "youtube":
            return "uploaded.txt"
        # TikTok (браузер или Buffer) — метка на аккаунт, чтобы разные способы заливки видели друг друга
        return f"uploaded_tiktok_{self.defaults.get('tiktok_account') or self.name}.txt"

    def already_posted(self, folder: Path) -> bool:
        """Метка этого назначения или tiktok.txt от TikTokAutopost/post.py с этим аккаунтом."""
        if (folder / self.marker).exists():
            return True
        acc = self.defaults.get("tiktok_account")
        legacy = folder / "tiktok.txt"
        if acc and self.platform != "youtube" and legacy.exists():
            try:
                import json
                return any(d.get("account") == acc for d in json.loads(legacy.read_text(encoding="utf-8")))
            except ValueError:
                return False
        return False


def load_env() -> tuple[str, str]:
    load_dotenv(ROOT / ".env")
    return os.getenv("SHARD_API", "http://127.0.0.1:40325"), os.getenv("SHARD_TOKEN", "")


def load_channels(path: Path = ROOT / "channels.ini") -> dict[str, Channel]:
    if not path.exists():
        raise FileNotFoundError(f"Нет {path.name} — скопируй channels.example.ini и заполни")
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(path, encoding="utf-8-sig")
    out = {}
    for name in cp.sections():
        sec = dict(cp[name])
        out[name] = Channel(
            name=name,
            profile=sec.pop("profile"),
            channel_id=sec.pop("channel_id", "").strip(),
            handle=sec.pop("handle", "").strip(),
            account=int(sec.pop("account", "0").strip() or 0),
            source=Path(sec.pop("source", "").strip() or (QUEUE / name)),
            enabled=sec.pop("enabled", "да").strip().lower() not in {"нет", "no", "false", "0"},
            platform=sec.pop("platform", "youtube").strip().lower(),
            defaults=sec,
        )
    return out
