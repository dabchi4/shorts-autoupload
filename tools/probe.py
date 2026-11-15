"""Проверка связки: профиль ShardBrowser → Studio. Сохраняет скриншот и HTML в debug/.

python tools/probe.py --profile "Имя профиля" [--url https://studio.youtube.com/...]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yz.browser import ensure_logged_in, open_profile, studio_url  # noqa: E402
from yz.config import ROOT, load_env  # noqa: E402
from yz.shard import Shard  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--profile")
ap.add_argument("--url", default=studio_url())
ap.add_argument("--list", action="store_true", help="показать профили ShardBrowser")
a = ap.parse_args()

if a.list or not a.profile:
    api, token = load_env()
    for p in Shard(api, token).profiles():
        print(json.dumps({k: p.get(k) for k in ("id", "name", "running", "folder")}, ensure_ascii=False))
    sys.exit(0)

out = ROOT / "debug"
out.mkdir(exist_ok=True)
with open_profile(a.profile) as (browser, page):
    page.goto(a.url, wait_until="domcontentloaded")
    page.wait_for_timeout(5000)
    ensure_logged_in(page)
    print("URL:", page.url)
    print("TITLE:", page.title())
    print("LANG:", page.evaluate("document.documentElement.lang", isolated_context=False))
    page.screenshot(path=str(out / "probe.png"))
    (out / "probe.html").write_text(page.content(), encoding="utf-8")
    print("saved debug/probe.png, debug/probe.html")
