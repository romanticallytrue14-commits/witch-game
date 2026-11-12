#!/usr/bin/env python3
"""Проверка market.getProductPhotoUploadServer + market.saveProductPhoto (VK API v5.199) с ключом сообщества VK_TOKEN!"""

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
    raw_user = (os.getenv("VK_USER_TOKEN") or "").strip()
    vk_tok = (os.getenv("VK_TOKEN") or "").strip()
    group_id = 85500918
    pub = VKPublisher(vk_token=vk_tok, vk_group=str(group_id), vk_user_token=raw_user, fallback_group_id=group_id)
    sample_jpg = (AGENT_DIR / "branding" / "cards" / "news.jpg").read_bytes()

    def _mp(upload_url: str, field: str, fname: str) -> dict[str, Any]:
        body, ctype = encode_multipart_photo(field, fname, "image/jpeg", sample_jpg)
        req = urllib.request.Request(upload_url, data=body, headers={"Content-Type": ctype}, method="POST")
        with urllib.request.urlopen(req, timeout=35, context=pub.ssl_ctx) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))

    out: dict[str, Any] = {}

    # 1. Пробуем market.getProductPhotoUploadServer с VK_TOKEN (бессрочным ключом сообщества!)
    for tok_label, tok in [("group_token", pub.vk_token), ("user_token", pub.vk_user_token)]:
        try:
            srv = pub.call_api("market.getProductPhotoUploadServer", {"group_id": group_id}, token=tok)
            out[f"{tok_label}_getProductPhotoUploadServer"] = {"ok": True, "keys": sorted(srv.keys()) if isinstance(srv, dict) else str(srv)[:160]}
            upload_url = str(srv.get("upload_url", ""))
            # В новом API market upload поле обычно называется 'file' или 'photo'
            up = _mp(upload_url, "file", "post.jpg")
            out[f"{tok_label}_uploaded_keys"] = sorted(up.keys()) if isinstance(up, dict) else str(up)[:180]
            saved = pub.call_api(
                "market.saveProductPhoto",
                {"upload_response": json.dumps(up, ensure_ascii=False)},
                token=tok,
            )
            out[f"{tok_label}_saveProductPhoto"] = {"ok": True, "saved": saved}
            break
        except Exception as exc:  # noqa: BLE001
            out[f"{tok_label}_market_photo"] = {"ok": False, "error": str(exc)[:300]}

    # 2. Проверим dry-check ссылки на GitHub Pages в wall.post (publish_date=1000 -> 0 постов на стене!)
    for label, test_att in [
        ("gh_pages_root", "https://romanticallytrue14-commits.github.io/witch-game/"),
        ("gh_pages_jpg", "https://romanticallytrue14-commits.github.io/witch-game/vk-news-agent/branding/cards/news.jpg"),
    ]:
        try:
            pub.call_api(
                "wall.post",
                {
                    "owner_id": -group_id,
                    "from_group": 1,
                    "message": "check",
                    "attachments": test_att,
                    "publish_date": 1000,
                },
                token=pub.vk_token,
            )
        except Exception as exc:  # noqa: BLE001
            out[f"drycheck_{label}"] = str(exc)[:240]

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
