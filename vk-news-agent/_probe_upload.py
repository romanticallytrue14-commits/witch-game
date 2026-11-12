#!/usr/bin/env python3
"""Проверка прикрепления фото к посту стены ВК через бессрочный ключ сообщества (VK_TOKEN) и проверку pub.vk_user_token."""

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

from audit_group import extract_embedded_posts, fetch_html
from vk_publisher import VKAPIError, VKPublisher, encode_multipart_photo


def fetch_public_wall_posts(group_id: int) -> list[dict[str, Any]]:
    html = fetch_html(f"https://vk.com/wall-{group_id}")
    if not html:
        return []
    return extract_embedded_posts(html)


def run_probe() -> dict[str, Any]:
    vk_tok = (os.getenv("VK_TOKEN") or "").strip()
    vk_user = (os.getenv("VK_USER_TOKEN") or "").strip()
    group_id = 85500918

    pub = VKPublisher(
        vk_token=vk_tok,
        vk_group=str(group_id),
        vk_user_token=vk_user,
        fallback_group_id=group_id,
    )

    out: dict[str, Any] = {
        "pub_user_token_len": len(pub.vk_user_token),
        "pub_user_token_starts_vk1": pub.vk_user_token.startswith("vk1."),
    }

    # 1. Проверяем НОРМАЛИЗОВАННЫЙ pub.vk_user_token (после извлечения из OAuth URL!)
    if pub.vk_user_token:
        for m_name, m_params in [
            ("users.get", {}),
            ("wall.get", {"owner_id": -group_id, "count": 5}),
            ("photos.getWallUploadServer", {"group_id": group_id}),
        ]:
            try:
                res = pub.call_api(m_name, dict(m_params), token=pub.vk_user_token)
                if m_name == "wall.get" and isinstance(res, dict):
                    items = res.get("items") or []
                    out[f"user_{m_name}"] = {
                        "ok": True,
                        "posts": [
                            {
                                "id": it.get("id"),
                                "attachments": [a.get("type") for a in (it.get("attachments") or [])],
                            }
                            for it in items
                        ],
                    }
                else:
                    out[f"user_{m_name}"] = {"ok": True, "summary": str(res)[:160]}
            except VKAPIError as exc:
                out[f"user_{m_name}"] = {"ok": False, "code": exc.code, "msg": exc.vk_message}

    # 2. Читаем текущие посты стены (через API если жив, иначе через публичный HTML)
    wall_before = pub.get_recent_wall_posts(count=8) if out.get("user_wall.get", {}).get("ok") else fetch_public_wall_posts(group_id)
    out["wall_before"] = [
        {
            "id": p.get("id"),
            "attachments": [a.get("type") for a in (p.get("attachments") or [])],
            "text_len": len(p.get("text") or ""),
        }
        for p in wall_before
    ]

    post_190 = next((p for p in wall_before if p.get("id") == 190), None)
    if not post_190:
        out["note"] = "Пост #190 не найден"
        return out

    text_190 = str(post_190.get("text") or "").strip()
    sample_jpg = (AGENT_DIR / "branding" / "cards" / "news.jpg").read_bytes()

    def _multipart_post(upload_url: str, field: str, fname: str, mime: str, data_bytes: bytes) -> dict[str, Any]:
        body, ctype = encode_multipart_photo(field, fname, mime, data_bytes)
        req = urllib.request.Request(
            upload_url,
            data=body,
            headers={"Content-Type": ctype, "User-Agent": "Mozilla/5.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=35, context=pub.ssl_ctx) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))

    # 3. Загружаем фото ТОЛЬКО через бессрочный ключ сообщества VK_TOKEN (photos.getMessagesUploadServer + saveMessagesPhoto)
    srv = pub.call_api("photos.getMessagesUploadServer", {"peer_id": 0}, token=pub.vk_token)
    up_res = _multipart_post(str(srv["upload_url"]), "photo", "post.jpg", "image/jpeg", sample_jpg)
    saved = pub.call_api(
        "photos.saveMessagesPhoto",
        {"photo": up_res.get("photo"), "server": up_res.get("server"), "hash": up_res.get("hash")},
        token=pub.vk_token,
    )
    item = saved[0]
    att_with_key = f"photo{item['owner_id']}_{item['id']}_{item['access_key']}" if item.get("access_key") else f"photo{item['owner_id']}_{item['id']}"
    att_plain = f"photo{item['owner_id']}_{item['id']}"

    out["uploaded_msg_photo"] = {"att_with_key": att_with_key, "att_plain": att_plain}
    out["edit_attempts_with_group_token"] = []

    for label, cand_att in [("with_access_key", att_with_key), ("without_access_key", att_plain)]:
        try:
            res = pub.call_api(
                "wall.edit",
                {
                    "owner_id": -group_id,
                    "post_id": 190,
                    "from_group": 1,
                    "message": text_190,
                    "attachments": cand_att,
                },
                token=pub.vk_token,
            )
            time.sleep(2.0)
            wall_after = pub.get_recent_wall_posts(count=8) if out.get("user_wall.get", {}).get("ok") else fetch_public_wall_posts(group_id)
            p190_after = next((p for p in wall_after if p.get("id") == 190), {})
            atts_after = [a.get("type") for a in (p190_after.get("attachments") or [])]
            out["edit_attempts_with_group_token"].append(
                {
                    "variant": label,
                    "api_ok": True,
                    "resp": str(res)[:160],
                    "public_attachments_after": atts_after,
                }
            )
            if "photo" in atts_after:
                break
        except Exception as exc:  # noqa: BLE001
            out["edit_attempts_with_group_token"].append({"variant": label, "api_ok": False, "error": str(exc)[:260]})

    return out


def main() -> int:
    try:
        out = run_probe()
    except Exception as exc:  # noqa: BLE001
        out = {"fatal": str(exc), "trace": traceback.format_exc()[-1000:]}
    (AGENT_DIR / "state" / "_upload_probe.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
