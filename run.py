"""Заливка Shorts из папок каналов (source в channels.ini).

python run.py --profile Main --dry-run          # заполнить всё, но не нажимать «Сохранить»
python run.py --profile Main                    # залить всё незалитое для каналов профиля
python run.py --profile Main --channel дашка    # только один канал
python run.py --profile Main --only "D:\\Видео\\Канал\\2026-11-12 Название ролика"
python run.py --profile Main --limit 2          # не больше 2 роликов на канал за запуск

Уже залитое пропускается: папки с меткой uploaded.txt и ролики, похожие на уже лежащие на канале
(см. check.py). После успешной заливки в папку ролика пишется uploaded.txt со ссылкой.
"""
from __future__ import annotations

import argparse
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from contextlib import nullcontext

from yz import buffer, tiktok
from yz.browser import open_profile, save_traffic
from yz.config import ERRORS, ROOT, load_channels
from yz.info import SKIP, InfoError, load_job, mark_uploaded, uploaded_info, video_duration
from yz.match import match_folders
from yz.schedule import assign_slots
from yz.studio import list_channel_videos, switch_channel, upload


def log(msg: str):
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(ROOT / "upload.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def save_error(page, job, exc: Exception) -> Path:
    d = ERRORS / f"{datetime.now():%Y%m%d_%H%M%S}_{job.channel}_{job.folder.name[:40]}"
    d.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(d / "screen.png"))
        (d / "page.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        pass
    (d / "error.txt").write_text("".join(traceback.format_exception(exc)), encoding="utf-8")
    return d


def pending_jobs(ch, only: Path | None):
    if only:
        folders = [only.resolve()] if only.resolve().parent == ch.source.resolve() else []
    else:
        folders = sorted(p for p in ch.source.iterdir() if p.is_dir()) if ch.source.exists() else []
    jobs = []
    for folder in folders:
        if ch.already_posted(folder) or (folder / SKIP).exists():
            continue
        try:
            jobs.append(load_job(folder, ch.name, ch.defaults))
        except InfoError as e:
            log(f"ПРОПУСК {ch.name}: {e}")
    return jobs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True, help="имя профиля ShardBrowser")
    ap.add_argument("--channel", help="только этот канал (секция channels.ini)")
    ap.add_argument("--only", type=Path, help="одна папка с роликом")
    ap.add_argument("--limit", type=int, default=0, help="максимум роликов на канал за запуск")
    ap.add_argument("--dry-run", action="store_true", help="не нажимать «Сохранить»")
    ap.add_argument("--plan", action="store_true", help="только показать, что и когда будет залито")
    ap.add_argument("--now", action="store_true", help="опубликовать сразу (только вместе с --only)")
    ap.add_argument("--force", action="store_true", help="заливать даже если похожий ролик уже есть на канале")
    a = ap.parse_args()

    channels = [c for c in load_channels().values()
                if c.enabled and c.profile == a.profile and (not a.channel or c.name == a.channel)]
    plan = [(ch, pending_jobs(ch, a.only)) for ch in channels]
    plan = [(ch, jobs) for ch, jobs in plan if jobs]
    if not plan:
        log("Нечего заливать")
        return 0
    log(f"К заливке: " + ", ".join(f"{ch.name} — {len(j)}" for ch, j in plan) + (" (ТЕСТОВЫЙ РЕЖИМ)" if a.dry_run else ""))

    ok = fail = 0
    reserved_slots: dict[str, set] = {}  # аккаунт → слоты, выданные в этом запуске (общие для его папок)
    need_browser = any(ch.platform != "buffer" for ch, _ in plan)
    bcfg = buffer.load_cfg() if any(ch.platform == "buffer" for ch, _ in plan) else None
    if bcfg:
        buffer.sync_cleanup(bcfg, log=log)  # обновить статусы прошлых постов и почистить R2
    with (open_profile(a.profile) if need_browser else nullcontext((None, None))) as (_browser, page):
        for ch, jobs in plan:
            log(f"=== {ch.name} ({ch.platform})")
            bch = None
            if ch.platform == "buffer":
                bch = buffer.channel(bcfg, ch.defaults["tiktok_account"])
                remote = buffer.list_posts(bcfg, bch["id"])
                queued = sum(1 for r in remote if r["status"] == "Отложенная публикация")
                free = int(ch.defaults.get("buffer_queue_limit", "10")) - queued
                if free <= 0:
                    log(f"  очередь Buffer заполнена ({queued} запланировано) — канал пропущен до публикации")
                    continue
                if not a.limit or a.limit > free:
                    a_limit_buffer = free
                else:
                    a_limit_buffer = a.limit
            elif ch.platform == "tiktok":
                save_traffic(page.context, "light")  # прокси с лимитом: не грузить видео ленты
                remote = tiktok.list_posts(page)
            else:
                switch_channel(page, ch.channel_id, ch.handle, ch.account)
                remote = list_channel_videos(page, ch.channel_id, ch.account)

            log(f"  на площадке найдено роликов: {len(remote)}")
            if not remote and not a.force and ch.platform != "buffer":
                log("  ✖ Не удалось прочитать список роликов — без него нельзя проверить дубли, канал пропущен")
                fail += 1
                continue
            # защита от повторной заливки
            if not a.force:
                local = [{"key": str(j.folder), "title": j.title, "duration": video_duration(j.video)} for j in jobs]
                dup = match_folders(local, remote)
                fresh = []
                for j in jobs:
                    m = dup.get(str(j.folder))
                    if m:
                        log(f"  ПРОПУСК {j.folder.name}: похоже, уже на канале ({m.level}) — «{m.video['title']}» "
                            f"{m.video.get('date', '')} id {m.video['id']}. Проверь и запусти check.py --mark, "
                            f"или --force, если это другой ролик")
                    else:
                        fresh.append(j)
                jobs = fresh
            if ch.platform == "buffer":
                jobs = jobs[: a_limit_buffer]  # бесплатный Buffer: максимум 10 запланированных на канал
            elif a.limit:
                jobs = jobs[: a.limit]

            if a.now:
                if not a.only:
                    raise SystemExit("--now только вместе с --only (одна папка)")
                for j in jobs:
                    j.publish_now, j.publish_at = True, None
            # время публикации для роликов без «Публикация:» — по слотам канала
            slots = ch.defaults.get("slots", "")
            if ch.platform == "tiktok" and not slots and not a.now:
                log("  TikTok без slots и без --now опубликовал бы всё разом — канал пропущен")
                continue
            if slots:
                acc = ch.defaults.get("tiktok_account") or ch.name
                assign_slots([j for j in jobs if not j.publish_now], slots, remote, ch.source, marker=ch.marker,
                             reserved=reserved_slots.setdefault(acc, set()))
            if ch.platform in ("tiktok", "buffer"):
                limit = datetime.now() + timedelta(days=9, hours=12)  # TikTok планирует максимум ~10 дней вперёд
                far = [j for j in jobs if j.publish_at and j.publish_at > limit]
                for j in far:
                    log(f"  ОТЛОЖЕНО {j.folder.name}: слот {j.publish_at:%d.%m %H:%M} дальше 10 дней — в следующий запуск")
                jobs = [j for j in jobs if j not in far]

            for job in jobs:
                when = ("СРАЗУ ПУБЛИЧНО" if job.publish_now else
                        f"{job.publish_at:%d.%m %H:%M}" if job.publish_at else "приватно, без даты")
                log(f"▶ {job.folder.name} → «{job.title}» [{when}]"
                    + (f"  (видео: {job.video.name}, обложка: {job.thumbnail.name if job.thumbnail else '—'}, "
                       f"похожее: {job.related_video or '—'})" if a.plan else ""))
                if a.plan:
                    continue
                try:
                    if ch.platform == "buffer":
                        text = tiktok.tiktok_caption(job, ch.defaults.get("title_suffix", ""))
                        log(f"  подпись TikTok: «{text}»")
                        post = buffer.post_video(bcfg, bch, job, text, draft=a.dry_run, log=log)
                        log(f"✔ Buffer: пост {post['id']} — {post['status']} {post.get('dueAt') or ''}")
                        if not a.dry_run:
                            mark_uploaded(job.folder, ch.marker, title=text, channel=ch.name, platform="tiktok",
                                          account=bch["name"], via="buffer", post_id=post["id"],
                                          publish_at=(job.publish_at or datetime.now()).isoformat(timespec="minutes"),
                                          status="Отложенная публикация" if job.publish_at else "Опубликовано",
                                          how="run.py")
                        ok += 1
                        continue
                    if ch.platform == "tiktok":
                        caption = tiktok.tiktok_caption(job, ch.defaults.get("title_suffix", ""))
                        log(f"  подпись TikTok: «{caption}»")
                        r = tiktok.upload(page, job, caption, dry_run=a.dry_run, log=log)
                        sched = r.get("scheduled")
                        log(f"✔ TikTok: {('запланировано' if sched else 'опубликовано') if r.get('posted') else r.get('note')}")
                        if r.get("posted"):
                            mark_uploaded(job.folder, ch.marker, title=caption, channel=ch.name, platform="tiktok",
                                          publish_at=(job.publish_at if sched else datetime.now()).isoformat(timespec="minutes"),
                                          status="Отложенная публикация" if sched else "Опубликовано", how="run.py")
                        ok += 1
                        continue
                    res = upload(page, job, ch.channel_id, ch.handle, ch.account, dry_run=a.dry_run, log=log)
                    for n in res.notes:
                        log(f"  {n}")
                    log(f"✔ {res.url}")
                    if res.published:
                        mark_uploaded(job.folder, url=res.url, video_id=res.video_id, title=job.title, channel=ch.name,
                                      publish_at=(datetime.now() if job.publish_now else job.publish_at).isoformat(timespec="minutes")
                                      if (job.publish_now or job.publish_at) else None,
                                      status="Опубликовано" if job.publish_now else
                                      "Отложенная публикация" if job.publish_at else "Ограниченный доступ",
                                      how="run.py")
                    ok += 1
                except Exception as e:  # noqa: BLE001 — один упавший ролик не останавливает остальные
                    d = save_error(page, job, e)
                    log(f"✖ {job.folder.name}: {e}  (скриншот: {d.relative_to(ROOT)})")
                    fail += 1
                    if a.dry_run or a.only:
                        break
    log(f"Готово: успешно {ok}, ошибок {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
