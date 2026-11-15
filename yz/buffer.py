"""TikTok через Buffer API + Cloudflare R2 (настройка — docs/GUIDE.md, раздел про TikTok).

Видео → R2 (публичная ссылка) → Buffer createPost (customScheduled, время в UTC) → TikTok.
Файл в R2 удаляется, когда пост вышел (sync_cleanup). Учёт пар (post_id, r2_key) — buffer_posts.json.
Ключи — в buffer_config.json (пример: buffer_config.example.json); другой путь можно задать в .env: BUFFER_CONFIG.
"""
from __future__ import annotations

import json
import os
import random
import urllib.error
import urllib.request
import uuid
import warnings
from datetime import date, datetime, timezone
from pathlib import Path

from .config import ROOT
from .info import VideoJob, video_duration

warnings.filterwarnings("ignore", message="Bad certificate in Windows certificate store")

API = "https://api.buffer.com"
UA = {"User-Agent": "youtubeZaliv/1.0"}  # r2.dev режет дефолтный Python-urllib
LEDGER = ROOT / "buffer_posts.json"
DEFAULT_CFG = ROOT / "buffer_config.json"


class BufferError(RuntimeError):
    pass


def load_cfg() -> dict:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    p = Path(os.getenv("BUFFER_CONFIG") or DEFAULT_CFG)
    if not p.exists():
        raise BufferError(f"Нет конфига Buffer/R2: {p}. Скопируй buffer_config.example.json в buffer_config.json "
                          "и заполни (или укажи путь в .env: BUFFER_CONFIG)")
    return json.loads(p.read_text(encoding="utf-8"))


def gql(cfg: dict, query: str, variables: dict | None = None) -> dict:
    req = urllib.request.Request(API, data=json.dumps({"query": query, "variables": variables or {}}).encode(),
                                 headers={"Authorization": f"Bearer {cfg['buffer_api_key']}",
                                          "Content-Type": "application/json", **UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise BufferError(f"Buffer HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")
    if data.get("errors"):
        raise BufferError("Buffer: " + "; ".join(e.get("message", "") for e in data["errors"]))
    return data["data"]


def org_id(cfg) -> str:
    return gql(cfg, "{ account { organizations { id } } }")["account"]["organizations"][0]["id"]


def channel(cfg, account: str) -> dict:
    q = ("query($o: OrganizationId!) { channels(input:{organizationId:$o}) "
         "{ id name service timezone isDisconnected isLocked metadata { ... on TiktokMetadata { defaultToReminders } } } }")
    chans = gql(cfg, q, {"o": org_id(cfg)})["channels"]
    c = next((c for c in chans if c["service"] == "tiktok" and c["name"] == account), None)
    if not c:
        raise BufferError(f"В Buffer нет TikTok-аккаунта {account}. Есть: "
                          + ", ".join(c["name"] for c in chans if c["service"] == "tiktok"))
    if c["isDisconnected"] or c["isLocked"]:
        raise BufferError(f"TikTok {account} в Buffer отключён или заблокирован — переподключи в buffer.com")
    if (c.get("metadata") or {}).get("defaultToReminders"):
        raise BufferError(f"У {account} в Buffer режим напоминаний — включи автопубликацию")
    return c


def list_posts(cfg, channel_id: str, statuses=("scheduled", "sending", "sent")) -> list[dict]:
    """Посты канала в Buffer → формат как у списков YouTube/TikTok: id, title, date (ISO, местное), status."""
    q = ("query($o: OrganizationId!, $c: [ChannelId!], $s: [PostStatus!]) { posts(first: 100, input:{organizationId:$o, "
         "filter:{status:$s, channelIds:$c}}) { edges { node { id status dueAt sentAt text externalLink } } } }")
    oid = org_id(cfg)
    out = []
    for st in statuses:
        for e in gql(cfg, q, {"o": oid, "c": [channel_id], "s": [st]})["posts"]["edges"]:
            n = e["node"]
            when = n.get("dueAt") or n.get("sentAt")
            local = datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone() if when else None
            out.append({"id": n["id"], "title": n["text"] or "", "duration": None,
                        "date": local.strftime("%Y-%m-%d %H:%M") if local else "",
                        "status": "Опубликовано" if n["status"] == "sent" else "Отложенная публикация",
                        "link": n.get("externalLink")})
    return out


# ---------- R2 ----------

def _r2(cfg):
    import boto3
    return boto3.client("s3", endpoint_url=cfg.get("r2_endpoint") or f"https://{cfg['r2_account_id']}.r2.cloudflarestorage.com",
                        aws_access_key_id=cfg["r2_access_key_id"], aws_secret_access_key=cfg["r2_secret_access_key"],
                        region_name="auto")


def r2_upload(cfg, path: Path) -> tuple[str, str]:
    key = f"tiktok/{date.today():%Y-%m-%d}/{uuid.uuid4().hex}.mp4"
    _r2(cfg).upload_file(str(path), cfg["r2_bucket"], key, ExtraArgs={"ContentType": "video/mp4"})
    url = cfg["r2_public_url"].rstrip("/") + "/" + key
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD", headers=UA), timeout=30) as r:
            ok = r.status == 200
    except urllib.error.URLError:
        ok = False
    if not ok:
        r2_delete(cfg, key)
        raise BufferError(f"Файл в R2 не открывается по публичной ссылке {url}")
    return key, url


def r2_delete(cfg, key: str):
    _r2(cfg).delete_object(Bucket=cfg["r2_bucket"], Key=key)


# ---------- посты ----------

def create_post(cfg, channel_id: str, text: str, url: str, when_local: datetime | None, ai: bool,
                thumb_ms: int | None, draft: bool = False) -> dict:
    inp = {"channelId": channel_id, "text": text, "schedulingType": "automatic",
           "mode": "customScheduled" if when_local else "shareNow", "needsApproval": False, "tagIds": [],
           "assets": [{"video": {"url": url, **({"metadata": {"thumbnailOffset": thumb_ms}} if thumb_ms else {})}}],
           "metadata": {"tiktok": {"isAiGenerated": ai}}}
    if when_local:
        utc = when_local.astimezone().astimezone(timezone.utc)
        inp["dueAt"] = utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    if draft:
        inp["saveToDraft"] = True
    q = """mutation($i: CreatePostInput!) { createPost(input: $i) {
        ... on PostActionSuccess { post { id status dueAt } }
        ... on MutationError { message } } }"""
    res = gql(cfg, q, {"i": inp})["createPost"]
    if "post" not in res:
        raise BufferError(f"Buffer отказал: {res.get('message')}")
    return res["post"]


def get_post(cfg, post_id: str) -> dict | None:
    q = "query($id: PostId!) { post(input:{id:$id}) { id status dueAt sentAt externalLink error { message rawError } } }"
    try:
        return gql(cfg, q, {"id": post_id})["post"]
    except BufferError as e:
        if "not found" in str(e).lower():
            return None  # пост удалён в Buffer
        raise


def delete_post(cfg, post_id: str):
    gql(cfg, "mutation($id: PostId!) { deletePost(input: { id: $id }) { __typename } }", {"id": post_id})


def _ledger() -> list[dict]:
    return json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else []


def _save_ledger(rows):
    LEDGER.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


def post_video(cfg, ch: dict, job: VideoJob, text: str, draft: bool = False, log=print) -> dict:
    dur = video_duration(job.video)
    thumb = random.randint(1000, max(1001, int(dur * 1000) - 1000)) if dur else None
    log(f"  R2: гружу {job.video.stat().st_size / 1e6:.0f} МБ…")
    key, url = r2_upload(cfg, job.video)
    try:
        post = create_post(cfg, ch["id"], text, url, None if job.publish_now else job.publish_at,
                           ai=job.ai in {"да", "yes"}, thumb_ms=thumb, draft=draft)
    except Exception:
        r2_delete(cfg, key)  # пост не создался — файл в R2 не нужен
        raise
    if draft:
        # тестовый прогон: цепочка R2 → Buffer проверена, убираем за собой
        log(f"  черновик {post['id']} создан ({post['status']}), удаляю его и файл из R2")
        delete_post(cfg, post["id"])
        r2_delete(cfg, key)
        return post
    rows = _ledger()
    rows.append({"post_id": post["id"], "account": ch["name"], "r2_key": key, "folder": str(job.folder),
                 "text": text, "due_at": post.get("dueAt"), "status": post["status"],
                 "r2_deleted": False, "created": datetime.now().isoformat(timespec="seconds")})
    _save_ledger(rows)
    return post


def sync_cleanup(cfg, log=print):
    """Обновить статусы постов и удалить из R2 файлы вышедших/удалённых постов."""
    rows = _ledger()
    changed = False
    for r in rows:
        if r.get("r2_deleted") and r["status"] in ("sent", "deleted"):
            continue
        p = get_post(cfg, r["post_id"])
        st = p["status"] if p else "deleted"
        if st != r["status"]:
            r["status"], changed = st, True
            if p and p.get("externalLink"):
                r["link"] = p["externalLink"]
            if st == "error":
                r["error"] = ((p or {}).get("error") or {}).get("message")
                log(f"  ✖ Buffer: пост «{r['text'][:40]}» — ошибка: {r['error']}")
        if st in ("sent", "deleted") and not r.get("r2_deleted"):
            try:
                r2_delete(cfg, r["r2_key"])
                r["r2_deleted"], changed = True, True
            except Exception as e:  # noqa: BLE001
                log(f"  не смог удалить {r['r2_key']} из R2: {e}")
    if changed:
        _save_ledger(rows)
    return rows
