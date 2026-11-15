"""Что уже залито: сверка папок с роликами на каналах.

python check.py --profile Main                 # отчёт по всем каналам профиля
python check.py --profile Main --channel рыжий # один канал
python check.py --profile Main --mark          # поставить метку uploaded.txt там, где совпадение «точно»
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from yz.browser import open_profile
from yz.config import ROOT, load_channels
from yz.info import InfoError, mark_uploaded, parse_info, pick_video, title_from_folder, uploaded_info, video_duration
from yz.match import match_folders
from yz.studio import list_channel_videos, switch_channel


def local_items(ch):
    items = []
    if not ch.source.exists():
        return items
    for folder in sorted(p for p in ch.source.iterdir() if p.is_dir()):
        info = folder / "info.txt"
        raw = parse_info(info.read_text(encoding="utf-8-sig")) if info.exists() else {}
        title = raw.get("title") or title_from_folder(folder)
        try:
            video = pick_video(folder)
            dur = video_duration(video)
            err = None
        except InfoError as e:
            video, dur, err = None, None, str(e)
        items.append({"key": str(folder), "folder": folder, "title": title, "duration": dur,
                      "uploaded": uploaded_info(folder), "error": err})
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--channel")
    ap.add_argument("--mark", action="store_true", help="записать uploaded.txt для точных совпадений")
    ap.add_argument("--mark-all", action="store_true", help="то же, плюс строки «ВОЗМОЖНО» (после ручной проверки)")
    a = ap.parse_args()

    channels = [c for c in load_channels().values()
                if c.enabled and c.profile == a.profile and (not a.channel or c.name == a.channel)]
    lines = [f"Проверка {datetime.now():%Y-%m-%d %H:%M}"]
    with open_profile(a.profile) as (_b, page):
        for ch in channels:
            items = local_items(ch)
            lines.append(f"\n=== {ch.name}  ({ch.source})  — папок: {len(items)}")
            if not items:
                continue
            switch_channel(page, ch.channel_id, ch.handle, ch.account)
            remote = list_channel_videos(page, ch.channel_id, ch.account)
            pending = [i for i in items if not i["uploaded"]]
            matches = match_folders(pending, remote)
            for it in items:
                name = it["folder"].name
                dur = f"{it['duration']:.0f}с" if it["duration"] else "?"
                if (it["folder"] / "skip.txt").exists():
                    lines.append(f"  − ПРОПУСК (skip)  {name}")
                    continue
                if it["uploaded"]:
                    u = it["uploaded"]
                    lines.append(f"  ✔ ЗАЛИТО (метка)  {name}  → {u.get('url', '')} {u.get('status', '')}")
                    continue
                m = matches.get(it["key"])
                if m and m.level == "точно":
                    url = f"https://youtube.com/shorts/{m.video['id']}"
                    lines.append(f"  ✔ НА КАНАЛЕ       {name} [{dur}]  ≈ «{m.video['title']}» "
                                 f"[{m.video['duration'] or '?'}с] {m.video['status']} {m.video['date']}  {url}")
                    if a.mark or a.mark_all:
                        mark_uploaded(it["folder"], url=url, video_id=m.video["id"], title=m.video["title"],
                                      channel=ch.name, status=m.video["status"], how="check.py")
                elif m:
                    if a.mark_all:
                        mark_uploaded(it["folder"], url=f"https://youtube.com/shorts/{m.video['id']}", video_id=m.video["id"],
                                      title=m.video["title"], channel=ch.name, status=m.video["status"],
                                      how="check.py --mark-all")
                    lines.append(f"  ? ВОЗМОЖНО       {name} [{dur}]  ≈ «{m.video['title']}» "
                                 f"[{m.video['duration'] or '?'}с] {m.video['status']}  https://youtube.com/shorts/{m.video['id']}"
                                 f"  (сходство {m.sim:.0%}{', длительность совпала' if m.dur_ok else ''})")
                else:
                    extra = f"  ⚠ {it['error']}" if it["error"] else ""
                    lines.append(f"  · НЕ ЗАЛИТО       {name} [{dur}]{extra}")
            unmatched = [r for r in remote if r["id"] not in {m.video["id"] for m in matches.values()}]
            if unmatched:
                lines.append(f"  (на канале ещё {len(unmatched)} роликов без папки — старые или из другого места)")
    report = "\n".join(lines)
    print(report)
    (ROOT / "check_report.txt").write_text(report, encoding="utf-8")
    if a.mark or a.mark_all:
        print("\nМетки uploaded.txt поставлены для строк «НА КАНАЛЕ»" + (" и «ВОЗМОЖНО»." if a.mark_all else "."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
