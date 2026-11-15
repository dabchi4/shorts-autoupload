"""Массово поставить «Видео по теме» в недавних Shorts канала.

python tools/relink.py --profile Main --channel дашка --days 14 --list        # только показать, что будет изменено
python tools/relink.py --profile Main --channel дашка --days 14 --limit 1     # изменить один (проверка)
python tools/relink.py --profile Main --channel дашка --days 14               # изменить все
Цель — related_video канала из channels.ini (или --target).
"""
import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yz.browser import open_profile  # noqa: E402
from yz.config import load_channels  # noqa: E402
from yz.schedule import parse_studio_date  # noqa: E402
from yz.studio import UploadError, list_channel_videos, set_related_on_edit, switch_channel, video_id_from  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--profile", required=True)
ap.add_argument("--channel", required=True)
ap.add_argument("--days", type=int, default=14)
ap.add_argument("--target")
ap.add_argument("--limit", type=int, default=0)
ap.add_argument("--list", action="store_true")
a = ap.parse_args()

ch = load_channels()[a.channel]
target = a.target or ch.defaults.get("related_video", "")
tid = video_id_from(target)
since = date.today() - timedelta(days=a.days)
with open_profile(a.profile) as (_b, page):
    switch_channel(page, ch.channel_id, ch.handle, ch.account)
    vids = list_channel_videos(page, ch.channel_id, ch.account, max_pages=4)
    todo = []
    for v in vids:
        if v["id"] == tid or v["status"] not in {"Опубликовано", "Отложенная публикация"}:
            continue
        d = parse_studio_date(v["date"])
        if v["status"] == "Отложенная публикация" or (d and d >= since):
            todo.append(v)
    print(f"Цель: {tid}. Роликов за {a.days} дн.: {len(todo)}")
    for v in todo:
        print(f"  {v['id']}  {v['status']:<22} {v['date']:<18} {v['title']}")
    if a.list:
        sys.exit(0)
    if a.limit:
        todo = todo[: a.limit]
    ok = 0
    for v in todo:
        try:
            res = set_related_on_edit(page, v["id"], target, ch.account)
            ok += 1
            print(f"✔ {v['id']} {v['title'][:50]} — {res}", flush=True)
        except UploadError as e:
            print(f"✖ {v['id']} {v['title'][:50]} — {e}", flush=True)
    print(f"Готово: {ok} из {len(todo)}")
