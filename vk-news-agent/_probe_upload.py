#!/usr/bin/env python3
"""Безопасная диагностика всех способов загрузки изображений в ВК с текущими токенами."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

AGENT_DIR = Path(__file__).resolve().parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from vk_publisher import VKAPIError, VKPublisher, encode_multipart_photo, inspect_vk_token_shape


def main() -> int:
    vk_tok = (os.getenv("VK_TOKEN") or "").strip()
    vk_user = (os.getenv("VK_USER_TOKEN") or "").strip()
    group_id = 85500918

    pub = VKPublisher(
        vk_token=vk_tok or vk_user,
        vk_group=str(group_id),
        vk_user_token=vk_user,
        fallback_group_id=group_id,
    )

    sample_jpg = (AGENT_DIR / "branding" / "cards" / "news.jpg").read_bytes()
    out: dict[str, Any] = {
        "tokens": {},
        "upload_probes": {},
    }

    for label, raw in [
        ("VK_TOKEN", os.getenv("VK_TOKEN") or ""),
        ("VK_USER_TOKEN", os.getenv("VK_USER_TOKEN") or ""),
        ("VK_ADMIN_TOKEN", os.getenv("VK_ADMIN_TOKEN") or ""),
        ("USER_TOKEN", os.getenv("USER_TOKEN") or ""),
    ]:
        clean = pub.vk_token if label == "VK_TOKEN" else VKPublisher._normalize_vk_token(raw) if hasattr(VKPublisher, "_normalize_vk_token") else raw.strip()
        info: dict[str, Any] = {"present": bool(raw.strip()), "shape": inspect_vk_token_shape(raw)}
        if raw.strip():
            tok_val = vk_tok if label == "VK_TOKEN" else vk_user
            for m_name, m_params in [
                ("users.get", {}),
                ("groups.getTokenPermissions", {}),
                ("groups.getById", {"group_id": str(group_id)}),
            ]:
                try:
                    res = pub.call_api(m_name, dict(m_params), token=tok_val)
                    info[m_name] = {"ok": True, "summary": str(res)[:220]}
                except VKAPIError as exc:
                    info[m_name] = {"ok": False, "code": exc.code, "msg": exc.vk_message}
        out["tokens"][label] = info

    # Тестируем все возможные способы загрузки картинки через постоянный ключ сообщества VK_TOKEN
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

    # 1. photos.getMessagesUploadServer (peer_id=0) + photos.saveMessagesPhoto
    for peer_val in (0, 2000000001):
        key = f"messages_photo_peer_{peer_val}"
        try:
            srv = pub.call_api("photos.getMessagesUploadServer", {"peer_id": peer_val}, token=pub.vk_token)
            up_url = str((srv or {}).get("upload_url") or "")
            up_res = _multipart_post(up_url, "photo", "post.jpg", "image/jpeg", sample_jpg)
            saved = pub.call_api(
                "photos.saveMessagesPhoto",
                {
                    "photo": up_res.get("photo"),
                    "server": up_res.get("server"),
                    "hash": up_res.get("hash"),
                },
                token=pub.vk_token,
            )
            item = saved[0] if isinstance(saved, list) and saved else {}
            out["upload_probes"][key] = {
                "ok": True,
                "owner_id": item.get("owner_id"),
                "id": item.get("id"),
                "has_access_key": bool(item.get("access_key")),
                "att_with_key": f"photo{item.get('owner_id')}_{item.get('id')}_{item.get('access_key')}" if item.get("access_key") else "",
                "att_plain": f"photo{item.get('owner_id')}_{item.get('id')}",
            }
            break
        except Exception as exc:  # noqa: BLE001
            out["upload_probes"][key] = {"ok": False, "error": str(exc)[:260]}

    # 2. docs.getWallUploadServer + docs.save (JPEG и GIF)
    for ext, mime in [("jpg", "image/jpeg"), ("gif", "image/gif")]:
        key = f"docs_wall_{ext}"
        try:
            payload_bytes = sample_jpg
            if ext == "gif":
                gif_tmp = Path("/tmp/_probe.gif")
                subprocess.run(["convert", str(AGENT_DIR / "branding" / "cards" / "news.jpg"), "-resize", "800x500", str(gif_tmp)], check=True)
                payload_bytes = gif_tmp.read_bytes()
            srv = pub.call_api("docs.getWallUploadServer", {"group_id": group_id}, token=pub.vk_token)
            up_url = str((srv or {}).get("upload_url") or "")
            up_res = _multipart_post(up_url, "file", f"illustration.{ext}", mime, payload_bytes)
            saved = pub.call_api("docs.save", {"file": up_res.get("file"), "title": f"greem_motor.{ext}"}, token=pub.vk_token)
            doc_obj = saved.get("doc") if isinstance(saved, dict) else (saved[0] if isinstance(saved, list) and saved else {})
            out["upload_probes"][key] = {
                "ok": True,
                "type": doc_obj.get("type"),
                "owner_id": doc_obj.get("owner_id"),
                "id": doc_obj.get("id"),
                "has_preview": bool(doc_obj.get("preview")),
                "att": f"doc{doc_obj.get('owner_id')}_{doc_obj.get('id')}",
            }
        except Exception as exc:  # noqa: BLE001
            out["upload_probes"][key] = {"ok": False, "error": str(exc)[:260]}

    # 3. photos.getWallUploadServer с pub.vk_token напрямую
    try:
        srv = pub.call_api("photos.getWallUploadServer", {"group_id": group_id}, token=pub.vk_token)
        out["upload_probes"]["wall_photo_group_token"] = {"ok": True, "has_url": bool((srv or {}).get("upload_url"))}
    except Exception as exc:  # noqa: BLE001
        out["upload_probes"]["wall_photo_group_token"] = {"ok": False, "error": str(exc)[:260]}

    # 4. Если messages_photo сработал — проверим, принимает ли wall.edit его на реальном посте #190 (которому как раз не хватает картинки!)
    msg_probe = out["upload_probes"].get("messages_photo_peer_0") or {}
    if msg_probe.get("ok"):
        # Читаем текст поста #190 через wall.getById (доступен ли он с ключом группы?)
        try:
            by_id = pub.call_api("wall.getById", {"posts": f"-{group_id}_190"}, token=pub.vk_token)
            out["upload_probes"]["wall_getById_group_token"] = {"ok": True, "summary": str(by_id)[:220]}
        except Exception as exc:  # noqa: BLE001
            out["upload_probes"]["wall_getById_group_token"] = {"ok": False, "error": str(exc)[:260]}

    target = AGENT_DIR / "state" / "_upload_probe.json"
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
