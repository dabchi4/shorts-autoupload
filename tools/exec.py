"""Отладка: выполнить python-сниппет с `page` в уже открытом профиле.

python tools/exec.py --profile Main snippet.py
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yz.browser import open_profile  # noqa: E402
from yz.config import ROOT  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--profile", default="Main")
ap.add_argument("snippet")
a = ap.parse_args()

dbg = ROOT / "debug"
dbg.mkdir(exist_ok=True)
code = Path(a.snippet).read_text(encoding="utf-8")
with open_profile(a.profile) as (browser, page):
    exec(compile(code, a.snippet, "exec"), {"page": page, "browser": browser, "DBG": dbg, "__name__": "__snippet__"})
