#!/usr/bin/env python3
"""Проверка публичных фото-методов ВК (market / market_album / messages), доступных ключу сообщества VK_TOKEN."""

from __future__ import annotations

import json
import os
import sys
import traceback
import urllib.request
from pathlib import Path
from typing import Any

AGENT_DIR = Path(__file__).resolve().parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from vk_publisher import VKPublisher, encode_multipart_photo


def main() -> int:
    vk_tok = (os.getenv("VK_TOKEN") or "").strip()
    group_id = 85500918
    pub = VKPublisher(vk_token=vk_tok, vk_group=str(group_id), fallback_group_id=group_id)
    sample_jpg = (AGENT_DIR / "branding" / "cards" / "news.jpg").read_bytes()

    def _mp(upload_url: str, field: str, fname: str, mime: str, raw: bytes) -> dict[str, Any]:
        body, ctype = encode_multipart_photo(field, fname, mime, raw)
        req = urllib.request.Request(
            upload_url,
            data=body,
            headers={"Content-Type": ctype, "User-Agent": "Mozilla/5.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=35, context=pub.ssl_ctx) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))

    out: dict[str, Any] = {}

    # 1. Market Album Photo
    try:
        srv = pub.call_api("photos.getMarketAlbumUploadServer", {"group_id": group_id}, token=pub.vk_token)
        up = _mp(str(srv["upload_url"]), "file", "card.jpg", "image/jpeg", sample_jpg)
        saved = pub.call_api(
            "photos.saveMarketAlbumPhoto",
            {
                "group_id": group_id,
                "photo": up.get("photo") or up.get("file"),
                "server": up.get("server"),
                "hash": up.get("hash"),
            },
            token=pub.vk_token,
        )
        out["market_album_photo"] = {"ok": True, "saved": saved}
    except Exception as exc:  # noqa: BLE001
        out["market_album_photo"] = {"ok": False, "error": str(exc)[:300]}

    # 2. Market Photo
    try:
        srv = pub.call_api("photos.getMarketUploadServer", {"group_id": group_id, "main_photo": 0}, token=pub.vk_token)
        up = _mp(str(srv["upload_url"]), "file", "card.jpg", "image/jpeg", sample_jpg)
        saved = pub.call_api(
            "photos.saveMarketPhoto",
            {
                "group_id": group_id,
                "photo": up.get("photo"),
                "server": up.get("server"),
                "hash": up.get("hash"),
                "crop_data": up.get("crop_data"),
                "crop_hash": up.get("crop_hash"),
            },
            token=pub.vk_token,
        )
        out["market_photo"] = {"ok": True, "saved": saved}
    except Exception as exc:  # noqa: BLE001
        out["market_photo"] = {"ok": False, "error": str(exc)[:300]}

    # 3. Chat upload / другие методы
    for m_name, m_params in [
        ("photos.getAlbums", {"owner_id": -group_id}),
        ("photos.getUploadServer", {"group_id": group_id}),
    ]:
        try:
            r = pub.call_api(m_name, m_params, token=pub.vk_token)
            out[m_name] = {"ok": True, "summary": str(r)[:220]}
        except Exception as exc:  # noqa: BLE001
            out[m_name] = {"ok": False, "error": str(exc)[:220]}

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
            json.dumps({"fatal": traceback.format_exc()[-1000:]}, indent=2),
            encoding="utf-8",
        )
        raise SystemExit(0)
