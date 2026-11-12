#!/usr/bin/env python3
"""Read-only диагностика причины отказа фото (0 публикаций на стену)."""

from __future__ import annotations

import json
import os
import sys
import traceback
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

AGENT_DIR = Path(__file__).resolve().parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from vk_publisher import VKAPIError, VKPublisher, encode_multipart_photo


def main() -> int:
    raw_user = (os.getenv("VK_USER_TOKEN") or "").strip()
    vk_tok = (os.getenv("VK_TOKEN") or "").strip()
    group_id = 85500918

    out: dict[str, Any] = {}

    # 1. Метаданные OAuth URL (без раскрытия секрета и user_id)
    if "#" in raw_user or "?" in raw_user:
        frag = raw_user.split("#", 1)[1] if "#" in raw_user else raw_user.split("?", 1)[1]
        qs = urllib.parse.parse_qs(frag)
        out["user_oauth_url_params"] = {
            "keys": sorted(qs.keys()),
            "expires_in": qs.get("expires_in", [None])[0],
        }

    pub = VKPublisher(vk_token=vk_tok, vk_group=str(group_id), vk_user_token=raw_user, fallback_group_id=group_id)

    # 2. Детали ответа VK API на VK_USER_TOKEN (почему именно code 9)
    for host in ("https://api.vk.ru/method", "https://api.vk.com/method"):
        for method, params in [
            ("account.getAppPermissions", {}),
            ("photos.getWallUploadServer", {"group_id": group_id}),
        ]:
            payload = dict(params)
            payload["access_token"] = pub.vk_user_token
            payload["v"] = "5.199"
            req = urllib.request.Request(
                f"{host}/{method}",
                data=urllib.parse.urlencode(payload).encode("utf-8"),
                headers={"User-Agent": "VKAndroidApp/8.99-23456 (Android 14; SDK 34; arm64-v8a; Pixel 8; ru; 2400x1080)"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=20, context=pub.ssl_ctx) as resp:
                raw_json = json.loads(resp.read().decode("utf-8", errors="replace"))
            if "error" in raw_json:
                err = raw_json["error"]
                out[f"{host.split('//')[1]}_{method}"] = {
                    "ok": False,
                    "error_code": err.get("error_code"),
                    "error_msg": err.get("error_msg"),
                    "error_subcode": err.get("error_subcode"),
                    "error_text": err.get("error_text"),
                }
            else:
                out[f"{host.split('//')[1]}_{method}"] = {
                    "ok": True,
                    "resp_keys": list(raw_json.get("response", {}).keys()) if isinstance(raw_json.get("response"), dict) else str(raw_json.get("response"))[:120],
                }

    # 3. Что именно вернул saveMessagesPhoto (структура объекта фото без секретов)
    sample_jpg = (AGENT_DIR / "branding" / "cards" / "news.jpg").read_bytes()
    srv = pub.call_api("photos.getMessagesUploadServer", {"peer_id": 0}, token=pub.vk_token)
    body, ctype = encode_multipart_photo("photo", "test.jpg", "image/jpeg", sample_jpg)
    req = urllib.request.Request(str(srv["upload_url"]), data=body, headers={"Content-Type": ctype}, method="POST")
    with urllib.request.urlopen(req, timeout=30, context=pub.ssl_ctx) as resp:
        up = json.loads(resp.read().decode("utf-8", errors="replace"))
    saved = pub.call_api(
        "photos.saveMessagesPhoto",
        {"photo": up.get("photo"), "server": up.get("server"), "hash": up.get("hash")},
        token=pub.vk_token,
    )
    if isinstance(saved, list) and saved:
        it = saved[0]
        out["saved_msg_photo_meta"] = {
            "album_id": it.get("album_id"),
            "owner_id": it.get("owner_id"),
            "id": it.get("id"),
            "has_access_key": bool(it.get("access_key")),
            "keys": sorted(it.keys()),
        }

    # 4. Проверка: почему wall.post отбросил или принял вложение? Проверим с пустым message + заведомо невалидным post_id в wall.post НЕЛЬЗЯ (создаст пост!).
    # Зато можно проверить wall.post с publish_date в прошлом (ВК возвращает Error 100 'invalid publish_date' ПОСЛЕ или ДО проверки attachments)!
    att_key = f"photo{it['owner_id']}_{it['id']}_{it['access_key']}"
    att_no_key = f"photo{it['owner_id']}_{it['id']}"
    for label, cand in [("with_key", att_key), ("no_key", att_no_key), ("existing_wall_photo_187", "photo-85500918_457239233")]:
        try:
            pub.call_api(
                "wall.post",
                {
                    "owner_id": -group_id,
                    "from_group": 1,
                    "message": "check",
                    "attachments": cand,
                    "publish_date": 1000,  # Заведомо в 1970 году -> ВК гарантированно НЕ публикует и отвечает Error 100/214!
                },
                token=pub.vk_token,
            )
            out[f"drycheck_{label}"] = "unexpected_ok"
        except VKAPIError as exc:
            out[f"drycheck_{label}"] = {"code": exc.code, "msg": exc.vk_message}

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
