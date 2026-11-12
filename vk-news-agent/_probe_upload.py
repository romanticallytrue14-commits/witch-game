#!/usr/bin/env python3
"""Проверка обхода Flood Control (Error 9) на photos.getWallUploadServer(group_id):
1) photos.getWallUploadServer() без group_id + saveWallPhoto()
2) photos.getAlbums / createAlbum + photos.getUploadServer(album_id, group_id) + photos.save()
3) Прикрепление реального фото к посту #192 через wall.edit и проверка на m.vk.com/wall-85500918_192!
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
import urllib.request
from pathlib import Path
from typing import Any

AGENT_DIR = Path(__file__).resolve().parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from audit_group import fetch_html
from vk_publisher import VKAPIError, VKPublisher, encode_multipart_photo


def main() -> int:
    raw_user = (os.getenv("VK_USER_TOKEN") or "").strip()
    vk_tok = (os.getenv("VK_TOKEN") or "").strip()
    group_id = 85500918
    pub = VKPublisher(vk_token=vk_tok, vk_group=str(group_id), vk_user_token=raw_user, fallback_group_id=group_id)
    user_tok = pub.vk_user_token
    sample_jpg = (AGENT_DIR / "branding" / "cards" / "tip.jpg").read_bytes()

    def _mp(upload_url: str, field: str, fname: str) -> dict[str, Any]:
        body, ctype = encode_multipart_photo(field, fname, "image/jpeg", sample_jpg)
        req = urllib.request.Request(upload_url, data=body, headers={"Content-Type": ctype}, method="POST")
        with urllib.request.urlopen(req, timeout=35, context=pub.ssl_ctx) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))

    out: dict[str, Any] = {}
    working_photo_att: str | None = None

    # Тест 1: photos.getWallUploadServer БЕЗ group_id
    try:
        srv1 = pub.call_api("photos.getWallUploadServer", {}, token=user_tok)
        up1 = _mp(str(srv1["upload_url"]), "photo", "wall_nogroup.jpg")
        saved1 = pub.call_api(
            "photos.saveWallPhoto",
            {"photo": up1.get("photo"), "server": up1.get("server"), "hash": up1.get("hash")},
            token=user_tok,
        )
        it1 = saved1[0]
        att1 = f"photo{it1['owner_id']}_{it1['id']}"
        out["wall_upload_without_group_id"] = {"ok": True, "att": att1, "album_id": it1.get("album_id")}
        working_photo_att = att1
    except Exception as exc:  # noqa: BLE001
        out["wall_upload_without_group_id"] = {"ok": False, "error": str(exc)[:260]}

    # Тест 2: photos.getAlbums + createAlbum + photos.getUploadServer(album_id, group_id) + photos.save
    try:
        albums_resp = pub.call_api("photos.getAlbums", {"owner_id": -group_id}, token=user_tok)
        items = (albums_resp or {}).get("items") or []
        album_id = None
        for alb in items:
            if alb.get("id", 0) > 0:
                album_id = int(alb["id"])
                break
        if not album_id:
            created = pub.call_api(
                "photos.createAlbum",
                {
                    "title": "Иллюстрации Греем Мотор",
                    "group_id": group_id,
                    "upload_by_admins_only": 1,
                    "comments_disabled": 0,
                },
                token=user_tok,
            )
            album_id = int(created["id"])
        srv2 = pub.call_api("photos.getUploadServer", {"album_id": album_id, "group_id": group_id}, token=user_tok)
        up2 = _mp(str(srv2["upload_url"]), "file1", "album_post.jpg")
        saved2 = pub.call_api(
            "photos.save",
            {
                "album_id": album_id,
                "group_id": group_id,
                "server": up2.get("server"),
                "photos_list": up2.get("photos_list"),
                "hash": up2.get("hash"),
            },
            token=user_tok,
        )
        it2 = saved2[0]
        att2 = f"photo{it2['owner_id']}_{it2['id']}"
        out["group_album_upload"] = {"ok": True, "album_id": album_id, "att": att2}
        # Предпочитаем фото с owner_id группы (-85500918)!
        working_photo_att = att2
    except Exception as exc:  # noqa: BLE001
        out["group_album_upload"] = {"ok": False, "error": str(exc)[:260]}

    # Тест 3: wall.get с user_tok
    post_192_text = ""
    try:
        wg = pub.call_api("wall.get", {"owner_id": -group_id, "count": 5}, token=user_tok)
        items = (wg or {}).get("items") or []
        out["wall_get_user"] = {
            "ok": True,
            "posts": [{"id": p.get("id"), "atts": [a.get("type") for a in (p.get("attachments") or [])]} for p in items],
        }
        for p in items:
            if p.get("id") == 192:
                post_192_text = str(p.get("text") or "")
    except Exception as exc:  # noqa: BLE001
        out["wall_get_user"] = {"ok": False, "error": str(exc)[:260]}

    # Тест 4: Если получили публичное фото (att2 или att1), прикрепляем к посту #192 через wall.edit (или проверяем в wall.post drycheck)!
    if working_photo_att and post_192_text:
        try:
            ed = pub.call_api(
                "wall.edit",
                {
                    "owner_id": -group_id,
                    "post_id": 192,
                    "from_group": 1,
                    "message": post_192_text,
                    "attachments": working_photo_att,
                },
                token=user_tok,
            )
            time.sleep(2.0)
            wg_after = pub.call_api("wall.get", {"owner_id": -group_id, "count": 3}, token=user_tok)
            p192 = next((p for p in (wg_after or {}).get("items", []) if p.get("id") == 192), {})
            out["edit_192"] = {
                "ok": True,
                "used_att": working_photo_att,
                "resp": str(ed)[:120],
                "attachments_after_api": [a.get("type") for a in (p192.get("attachments") or [])],
            }
        except Exception as exc:  # noqa: BLE001
            out["edit_192"] = {"ok": False, "used_att": working_photo_att, "error": str(exc)[:260]}

    (AGENT_DIR / "state" / "_upload_probe.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        (AGENT_DIR / "state" / "_upload_probe.json").write_text(
            json.dumps({"fatal": traceback.format_exc()[-1200:]}, indent=2),
            encoding="utf-8",
        )
        raise SystemExit(0)
