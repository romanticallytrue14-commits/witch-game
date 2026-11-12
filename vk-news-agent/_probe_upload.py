#!/usr/bin/env python3
"""Read-only проверка обхода Error 9 через execute / docs / video и проверка доступных методов (0 постов на стену)."""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

AGENT_DIR = Path(__file__).resolve().parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from vk_publisher import VKPublisher


def main() -> int:
    raw_user = (os.getenv("VK_USER_TOKEN") or "").strip()
    vk_tok = (os.getenv("VK_TOKEN") or "").strip()
    group_id = 85500918
    pub = VKPublisher(vk_token=vk_tok, vk_group=str(group_id), vk_user_token=raw_user, fallback_group_id=group_id)
    u = pub.vk_user_token

    out: dict[str, Any] = {}

    # 1. Пробуем execute с VK_USER_TOKEN!
    for name, code in [
        ("exec_getWallUploadServer", f'return API.photos.getWallUploadServer({{"group_id": {group_id}}});'),
        ("exec_wallGet", f'return API.wall.get({{"owner_id": -{group_id}, "count": 2}});'),
    ]:
        try:
            r = pub.call_api("execute", {"code": code}, token=u)
            out[name] = {"ok": True, "summary": str(r)[:240]}
        except Exception as exc:  # noqa: BLE001
            out[name] = {"ok": False, "error": str(exc)[:240]}

    # 2. Другие методы загрузки с VK_USER_TOKEN (не трогающие стену)
    for m_name, m_params in [
        ("docs.getWallUploadServer", {"group_id": group_id}),
        ("docs.getMessagesUploadServer", {"type": "doc"}),
        ("photos.getChatUploadServer", {"chat_id": 1}),
        ("photos.getMarketUploadServer", {"group_id": group_id, "main_photo": 1}),
        ("video.save", {"group_id": group_id, "name": "test", "wallpost": 0}),
    ]:
        try:
            r = pub.call_api(m_name, m_params, token=u)
            out[f"user_{m_name}"] = {"ok": True, "keys": list(r.keys()) if isinstance(r, dict) else str(r)[:140]}
        except Exception as exc:  # noqa: BLE001
            out[f"user_{m_name}"] = {"ok": False, "error": str(exc)[:220]}

    # 3. Проверим также execute с VK_TOKEN (групповым ключом)!
    for name, code in [
        ("group_exec_getWallUploadServer", f'return API.photos.getWallUploadServer({{"group_id": {group_id}}});'),
        ("group_exec_wallGet", f'return API.wall.get({{"owner_id": -{group_id}, "count": 2}});'),
    ]:
        try:
            r = pub.call_api("execute", {"code": code}, token=pub.vk_token)
            out[name] = {"ok": True, "summary": str(r)[:240]}
        except Exception as exc:  # noqa: BLE001
            out[name] = {"ok": False, "error": str(exc)[:240]}

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
