"""Разбор папки с роликом: видео + info.txt (+ обложка) и метка «уже залито»."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv"}
IMAGE_EXT = {".jpg", ".jpeg", ".png"}
NO_SUBS_RE = re.compile(r"\(\s*без субтитров\s*\)", re.I)
DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\s+")
UPLOADED = "uploaded.txt"
SKIP = "skip.txt"  # положить в папку ролика — скрипт её не заливает
_EMO = "\U0001F000-\U0001FAFF☀-➿⌀-⏿⬀-⯿️‍⃣"
TRAIL_EMOJI_RE = re.compile(rf"\s*([{_EMO}]+)\s*$")
LEAD_EMOJI_RE = re.compile(rf"^[{_EMO}]")


def emoji_first(title: str) -> str:
    """«Краш поддалась мне в споре 😏» → «😏 Краш поддалась мне в споре»."""
    if LEAD_EMOJI_RE.match(title):
        return title
    m = TRAIL_EMOJI_RE.search(title)
    if not m or m.start() == 0:
        return title
    return f"{m.group(1)} {title[:m.start()].rstrip()}"

# ключ в info.txt (в нижнем регистре) → поле
KEYS = {
    "название": "title",
    "описание": "description",
    "теги": "tags",
    "игра": "game",
    "плейлист": "playlist",
    "публикация": "publish_at",
    "связанное видео": "related_video",
    "обложка": "thumbnail",
    "для детей": "made_for_kids",
    "реклама": "ad_rating",
    "ии": "ai",
    # служебные — читаются, но в YouTube не отправляются
    "длительность": "duration",
    "вопрос в комменты": "comment_question",
    "исходник": "source",
}
MULTILINE = {"description"}
DATE_FORMATS = ("%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M", "%Y-%m-%dT%H:%M")


class InfoError(ValueError):
    pass


@dataclass
class VideoJob:
    folder: Path
    channel: str
    video: Path
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    game: str = ""
    playlist: str = ""
    publish_at: datetime | None = None
    publish_now: bool = False   # «Публикация: сейчас» — сразу открытый доступ
    related_video: str = ""
    thumbnail: Path | None = None
    made_for_kids: bool = False
    ad_rating: str = ""   # «ничего» → в анкете рекламодателей «Ничего из указанного выше»
    ai: str = ""          # «да»/«нет» — вопрос про ИИ/изменённый контент; пусто — не трогать
    comment_question: str = ""

    @property
    def name(self) -> str:
        return f"{self.channel}/{self.folder.name}"


def parse_info(text: str) -> dict[str, str]:
    """Строки «Ключ: значение». Многострочное только «Описание:» — оно забирает всё до следующего
    известного ключа (поэтому «Ссылка: https://…» внутри описания не ломает разбор).
    Неизвестные «Ключ: …» вне описания просто пропускаются."""
    out: dict[str, str] = {}
    current = None
    buf: list[str] = []
    key_re = re.compile(r"^\s*([^:]{2,30}):\s?(.*)$")

    def flush():
        if current:
            out[current] = "\n".join(buf).strip()

    for line in text.splitlines():
        m = key_re.match(line)
        key = m.group(1).strip().lower() if m else None
        if key in KEYS:
            flush()
            current = KEYS[key]
            buf = [m.group(2)]
        elif current in MULTILINE:
            buf.append(line)
        elif m:  # неизвестный ключ вне описания — закрываем текущее поле
            flush()
            current, buf = None, []
        # прочие строки вне описания (пояснения, пустые) игнорируем
    flush()
    return out


def parse_date(value: str) -> datetime:
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            pass
    raise InfoError(f"Не понял дату публикации {value!r}. Пример: 2026-11-15 18:00")


def pick_video(folder: Path) -> Path:
    """Видео в самой папке (подпапки вроде «до зазыва» не смотрим), версия «(без субтитров)» — запасная."""
    videos = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXT]
    main = [p for p in videos if not NO_SUBS_RE.search(p.stem)]
    if len(main) == 1:
        return main[0].resolve()
    if not main and len(videos) == 1:
        return videos[0].resolve()
    names = ", ".join(p.name for p in (main or videos)) or "нет"
    raise InfoError(f"{folder.name}: не понял, какое видео заливать ({names})")


def video_duration(path: Path) -> float | None:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True, timeout=30)
        return float(out.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def title_from_folder(folder: Path) -> str:
    return DATE_PREFIX_RE.sub("", folder.name).strip()


# ---------- метка «уже залито» ----------

def uploaded_info(folder: Path, marker: str = UPLOADED) -> dict | None:
    p = folder / marker
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"raw": p.read_text(encoding="utf-8", errors="replace")}


def mark_uploaded(folder: Path, marker: str = UPLOADED, **data):
    data.setdefault("marked_at", datetime.now().isoformat(timespec="seconds"))
    (folder / marker).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------- сборка задания ----------

def load_job(folder: Path, channel: str, defaults: dict[str, str] | None = None) -> VideoJob:
    defaults = defaults or {}
    info_path = folder / "info.txt"
    raw = parse_info(info_path.read_text(encoding="utf-8-sig")) if info_path.exists() else {}

    video = pick_video(folder)

    title = raw.get("title", "").strip() or title_from_folder(folder)
    if not title:
        raise InfoError(f"{folder.name}: нет названия")
    if defaults.get("emoji_first", "да").strip().lower() not in {"нет", "no", "0"}:
        title = emoji_first(title)
    title_core = title  # без суффикса канала — для описания
    suffix = defaults.get("title_suffix", "")
    if suffix and not title.endswith(suffix.strip()):
        title = title + suffix
    if len(title) > 100:
        raise InfoError(f"{folder.name}: название вместе с суффиксом канала длиннее 100 символов ({len(title)})")
    if "<" in title or ">" in title:
        raise InfoError(f"{folder.name}: YouTube не принимает символы < и > в названии")

    description = raw.get("description", "")
    if defaults.get("title_in_description", "").strip().lower() in {"да", "yes", "1"}:
        description = (title_core + ("\n\n" + description if description else "")).strip()
    footer = defaults.get("description_footer", "").replace("\\n", "\n")
    if footer:
        description = (description + "\n\n" + footer).strip()
    if len(description) > 5000:
        raise InfoError(f"{folder.name}: описание длиннее 5000 символов")

    tags_src = raw.get("tags") or defaults.get("tags", "")
    tags = [t.strip().lstrip("#") for t in re.split(r"[,\n]", tags_src) if t.strip()]
    if sum(len(t) for t in tags) + max(len(tags) - 1, 0) > 500:
        raise InfoError(f"{folder.name}: теги длиннее 500 символов в сумме")

    thumb = None
    if raw.get("thumbnail"):
        thumb = (folder / raw["thumbnail"].strip()).resolve()
        if not thumb.exists():
            raise InfoError(f"{folder.name}: нет файла обложки {thumb.name}")
    else:
        images = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXT]
        if len(images) == 1:
            thumb = images[0].resolve()
        elif len(images) > 1:
            raise InfoError(f"{folder.name}: несколько картинок — укажи «Обложка: файл.jpg»")

    when = raw.get("publish_at", "").strip()
    publish_now = when.lower() in {"сейчас", "now"}
    publish_at = parse_date(when) if when and not publish_now else None
    kids = raw.get("made_for_kids", "нет").strip().lower() in {"да", "yes", "true", "1"}

    return VideoJob(
        folder=folder,
        channel=channel,
        video=video,
        title=title,
        description=description,
        tags=tags,
        game=raw.get("game") or defaults.get("game", ""),
        playlist=raw.get("playlist") or defaults.get("playlist", ""),
        publish_at=publish_at,
        publish_now=publish_now,
        related_video=(raw.get("related_video") or defaults.get("related_video", "")).strip(),
        thumbnail=thumb,
        made_for_kids=kids,
        ad_rating=(raw.get("ad_rating") or defaults.get("ad_rating", "")).strip(),
        ai=(raw.get("ai") or defaults.get("ai", "")).strip().lower(),
        comment_question=raw.get("comment_question", ""),
    )
