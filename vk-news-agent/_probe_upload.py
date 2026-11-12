#!/usr/bin/env python3
"""Боевой тест публикации 1 свежего поста через run_agent и проверка наличия фото на публичной стене vk.com/wall-85500918."""

from __future__ import annotations

import json
import re
import sys
import time
import traceback
from pathlib import Path
from typing import Any

AGENT_DIR = Path(__file__).resolve().parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

import agent
from audit_group import extract_embedded_posts, fetch_html


def main() -> int:
    state_path = AGENT_DIR / "state" / "published.json"
    out: dict[str, Any] = {}
    try:
        rc = agent.run_agent(
            config_path=AGENT_DIR / "config.json",
            state_path=state_path,
            dry_run=False,
            max_posts_override=1,
        )
        out["agent_rc"] = rc
        pub_data = json.loads(state_path.read_text(encoding="utf-8"))
        latest_history = (pub_data.get("history") or [None])[0]
        out["latest_published_entry"] = latest_history

        time.sleep(2.5)
        html = fetch_html("https://vk.com/wall-85500918") or ""
        posts = extract_embedded_posts(html) if html else []
        out["public_wall_posts"] = [
            {
                "id": p.get("id"),
                "has_attachments_meta": "attachments_meta" in p,
                "has_compact_attachments": "compact_attachments_before_cut" in p,
                "attachments_meta_preview": str(p.get("attachments_meta", ""))[:260],
                "text_head": str(p.get("text", ""))[:180],
            }
            for p in posts[:5]
        ]
        if latest_history and latest_history.get("post_id"):
            pid = latest_history["post_id"]
            out["html_mentions_post_photo"] = bool(
                re.search(rf"wall-85500918_{pid}.{{0,4000}}photo-85500918_", html, flags=re.DOTALL)
                or ("photo-85500918_" in str(out["public_wall_posts"][0]))
            )
    except Exception as exc:  # noqa: BLE001
        out["fatal"] = str(exc)
        out["trace"] = traceback.format_exc()[-1200:]

    (AGENT_DIR / "state" / "_upload_probe.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
