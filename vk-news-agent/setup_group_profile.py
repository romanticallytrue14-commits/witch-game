#!/usr/bin/env python3
"""Автоматическая установка и обновление оформления группы ВКонтакте «АвтоТочка» (https://vk.ru/autoyar076)."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

AGENT_DIR = Path(__file__).resolve().parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from agent import append_github_summary, load_config, load_fallback_editorial_card  # noqa: E402
from dedup_store import make_post_guid  # noqa: E402
from news_fetcher import NewsCandidate, enrich_candidate_from_webpage  # noqa: E402
from vk_publisher import (  # noqa: E402
    PreparedImage,
    VKAPIError,
    VKPublisher,
    download_and_prepare_image,
    inspect_image_dimensions,
)

logger = logging.getLogger("vk_news_agent.branding")


def load_local_prepared_image(img_path: Path) -> PreparedImage:
    raw = img_path.read_bytes()
    info = inspect_image_dimensions(raw)
    if info is None:
        raise ValueError(f"Файл не является корректным изображением: {img_path}")
    fmt, width, height = info
    mime = "image/png" if fmt == "png" else "image/jpeg"
    ext = "png" if fmt == "png" else "jpg"
    return PreparedImage(
        data=raw,
        filename=f"branding.{ext}",
        mime_type=mime,
        width=width,
        height=height,
        source_url=str(img_path.name),
    )


def load_branding_state(state_path: Path) -> dict[str, Any]:
    if not state_path.exists():
        return {}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_branding_state(state_path: Path, data: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def backfill_missing_wall_photos(
    pub: VKPublisher,
    config: dict[str, Any],
    published_state_path: Path,
    wall_items: list[dict[str, Any]],
) -> int:
    """Догружает пропущенные фото к недавним постам на стене (в пределах окна редактирования ВК)."""
    if not wall_items or not published_state_path.exists():
        return 0
    try:
        pub_data = json.loads(published_state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0

    by_post_id: dict[int, dict[str, Any]] = {}
    for entry in pub_data.get("items") or []:
        pid = entry.get("vk_post_id")
        if isinstance(pid, int) and pid > 0:
            by_post_id[pid] = entry

    updated = 0
    for w_item in wall_items:
        pid = int(w_item.get("id") or 0)
        if pid not in by_post_id:
            continue
        if w_item.get("attachments"):
            continue
        existing_text = str(w_item.get("text") or "").strip()
        if not existing_text:
            continue

        meta = by_post_id[pid]
        url = str(meta.get("url") or "")
        cat = str(meta.get("category") or "news")
        prep_img: PreparedImage | None = None

        if url.startswith(("http://", "https://")):
            cand = NewsCandidate(
                title=str(meta.get("title") or ""),
                url=url,
                source_name="source",
                category=cat,
                summary="",
            )
            cand = enrich_candidate_from_webpage(cand)
            if cand.image_urls:
                prep_img = download_and_prepare_image(cand.image_urls, referer_url=cand.url)

        if prep_img is None:
            prep_img = load_fallback_editorial_card(config, cat)
        if prep_img is None:
            continue

        try:
            res = pub.edit_wall_post(pid, existing_text, image=prep_img)
            if res.get("photo_attached"):
                updated += 1
                logger.info("К существующему посту #%d успешно прикреплено фото!", pid)
        except VKAPIError as exc:
            logger.warning("Не удалось обновить фото у поста #%d: %s", pid, exc)

    return updated


def apply_group_branding(
    *,
    config_path: Path,
    branding_state_path: Path | None = None,
    dry_run: bool = False,
    update_info: bool = True,
    upload_cover: bool = True,
    upload_avatar: bool = True,
    publish_pinned: bool = True,
    force_new_pinned: bool = False,
    backfill_photos: bool = True,
) -> int:
    config = load_config(config_path)
    group_cfg = config.get("group", {})
    brand_cfg = config.get("branding", {})

    vk_group = (
        os.getenv("VK_GROUP")
        or str(group_cfg.get("screen_name") or group_cfg.get("url") or "autoyar076")
    ).strip()
    vk_token = (os.getenv("VK_TOKEN") or "").strip()
    vk_user_token = (
        os.getenv("VK_USER_TOKEN")
        or os.getenv("VK_ADMIN_TOKEN")
        or os.getenv("USER_TOKEN")
        or ""
    ).strip()

    title = str(brand_cfg.get("title") or group_cfg.get("title") or "АвтоТочка")
    subject = brand_cfg.get("subject", 1)
    status_text = str(brand_cfg.get("status") or "")
    description = str(brand_cfg.get("description") or "")
    pinned_text = str(brand_cfg.get("pinned_post_text") or "")

    avatar_path = AGENT_DIR / str(brand_cfg.get("avatar_path", "branding/avatar.jpg"))
    cover_path = AGENT_DIR / str(brand_cfg.get("cover_path", "branding/cover.jpg"))
    pinned_img_path = AGENT_DIR / str(brand_cfg.get("pinned_image_path", "branding/pinned_post.jpg"))
    effective_b_state_path = branding_state_path or (config_path.parent / "state" / "branding_state.json")
    published_state_path = config_path.parent / "state" / "published.json"

    cover_img = load_local_prepared_image(cover_path)
    avatar_img = load_local_prepared_image(avatar_path)
    pinned_img = load_local_prepared_image(pinned_img_path)

    report_lines = [
        f"## 🎨 Оформление группы «{title}» (`{vk_group}`)",
        f"- **Режим**: `{'DRY-RUN (предпросмотр без изменений в ВК)' if dry_run else 'Установка оформления в ВК'}`",
        f"- **Обложка**: `{cover_path.name}` (`{cover_img.width}x{cover_img.height}` px)",
        f"- **Аватар**: `{avatar_path.name}` (`{avatar_img.width}x{avatar_img.height}` px)",
        f"- **Баннер закрепа**: `{pinned_img_path.name}` (`{pinned_img.width}x{pinned_img.height}` px)",
    ]

    if dry_run:
        report_lines.append(
            f"\n### Тексты к установке\n- **Название**: `{title}`\n- **Статус**: `{status_text}`\n\n"
            f"**Описание группы:**\n```text\n{description}\n```\n\n"
            f"**Закреплённый пост:**\n```text\n{pinned_text}\n```"
        )
        append_github_summary("\n".join(report_lines))
        logger.info("DRY-RUN завершён: все файлы и тексты оформления «%s» проверены.", title)
        return 0

    if not vk_token:
        logger.error("Не найден секрет VK_TOKEN! Добавьте токен администратора группы в GitHub Secrets.")
        append_github_summary(
            "### ❌ Не задан `VK_TOKEN`\n"
            "Добавьте токен администратора группы в `Settings → Secrets and variables → Actions`, "
            "либо скачайте готовые картинки из папки `vk-news-agent/branding/`."
        )
        return 1

    pub = VKPublisher(vk_token=vk_token, vk_group=vk_group, vk_user_token=vk_user_token)
    gid = pub.resolve_group_id()
    b_state = load_branding_state(effective_b_state_path)
    report_lines.append(f"- **ID сообщества ВК**: `{gid}` ([открыть группу](https://vk.ru/{vk_group}))\n")

    if update_info:
        try:
            info_res = pub.update_group_info(
                title=title,
                description=description,
                status_text=status_text,
                subject=int(subject) if subject else None,
            )
            report_lines.append(
                f"- ✅ **Название, тематика и описание**: обновлены (`статус`: {'обновлён' if info_res['status_updated'] else 'требует VK_USER_TOKEN'})"
            )
        except VKAPIError as exc:
            logger.warning("Не удалось обновить описание через API: %s", exc)
            report_lines.append(f"- ⚠️ **Название и описание**: `{exc.vk_message}` (тексты есть в `branding/GROUP_SETUP.md`)")

    if upload_cover:
        try:
            pub.upload_group_cover(cover_img.data, width=cover_img.width, height=cover_img.height)
            report_lines.append("- ✅ **Шапка (обложка 1920×768)**: установлена")
        except VKAPIError as exc:
            logger.warning("Не удалось загрузить обложку через API: %s", exc)
            report_lines.append(f"- ⚠️ **Шапка**: `{exc.vk_message}` (загрузите `branding/cover.jpg` вручную)")

    if upload_avatar:
        try:
            ok_av = pub.upload_group_avatar(avatar_img.data)
            if ok_av:
                b_state["avatar_uploaded"] = True
                save_branding_state(effective_b_state_path, b_state)
                report_lines.append("- ✅ **Аватар сообщества (1024×1024)**: установлен")
            else:
                report_lines.append(
                    "- ℹ️ **Аватар сообщества**: ключ группы не имеет прав на `photos.getOwnerPhotoUploadServer` "
                    "(добавьте `VK_USER_TOKEN` или загрузите `vk-news-agent/branding/avatar.jpg` в 2 клика в ВК)"
                )
        except VKAPIError as exc:
            logger.warning("Не удалось загрузить аватар через API: %s", exc)
            report_lines.append(f"- ⚠️ **Аватар**: `{exc.vk_message}` (загрузите `branding/avatar.jpg` вручную)")

    wall_items = pub.get_recent_wall_posts(count=20)

    if publish_pinned and pinned_text:
        existing_pin_id = int(b_state.get("pinned_post_id") or 0)
        if not force_new_pinned and wall_items:
            for w_it in wall_items:
                txt = str(w_it.get("text") or "")
                if "Добро пожаловать в «АвтоТочк" in txt or (w_it.get("is_pinned") and "#автоточка" in txt.lower()):
                    existing_pin_id = int(w_it.get("id") or existing_pin_id)
                    break

        if existing_pin_id > 0 and not force_new_pinned:
            try:
                edit_res = pub.edit_wall_post(existing_pin_id, pinned_text, image=pinned_img)
                pub.pin_wall_post(existing_pin_id)
                b_state["pinned_post_id"] = existing_pin_id
                b_state["pinned_has_photo"] = bool(edit_res.get("photo_attached"))
                save_branding_state(effective_b_state_path, b_state)
                att_note = "с баннером `pinned_post.jpg`" if edit_res.get("photo_attached") else "(текст обновлён; для баннера нужен `VK_USER_TOKEN`)"
                report_lines.append(
                    f"- ✅ **Приветственный пост обновлён**: [{edit_res['post_url']}]({edit_res['post_url']}) {att_note}"
                )
            except VKAPIError as exc:
                logger.info("Редактирование закрепа #%d недоступно (%s) — дубль не создаём.", existing_pin_id, exc)
                report_lines.append(
                    f"- ℹ️ **Приветственный пост**: запись `wall-{gid}_{existing_pin_id}` уже на стене "
                    f"(добавьте `VK_USER_TOKEN`, чтобы прикрепить к ней баннер `pinned_post.jpg`, или запустите с `force_new_pinned`)"
                )
        else:
            try:
                post_res = pub.publish_post(
                    pinned_text,
                    image=pinned_img,
                    guid=make_post_guid(f"avtotochka-welcome-pin-{gid}"),
                )
                post_id = int(post_res.get("post_id") or 0)
                pinned_ok = pub.pin_wall_post(post_id) if post_id else False
                if post_id > 0:
                    b_state["pinned_post_id"] = post_id
                    b_state["pinned_has_photo"] = bool(post_res.get("photo_attached"))
                    save_branding_state(effective_b_state_path, b_state)
                pin_note = "и закреплён в шапке стены" if pinned_ok else "(закрепите запись в меню поста)"
                report_lines.append(
                    f"- ✅ **Приветственный пост**: [{post_res['post_url']}]({post_res['post_url']}) {pin_note}"
                )
            except VKAPIError as exc:
                logger.warning("Не удалось опубликовать закреплённый пост: %s", exc)
                report_lines.append(f"- ⚠️ **Приветственный пост**: `{exc.vk_message}`")

    if backfill_photos and wall_items:
        patched = backfill_missing_wall_photos(pub, config, published_state_path, wall_items)
        if patched > 0:
            report_lines.append(f"- ✅ **Догружены фото к прошлым постам на стене**: `{patched}` шт.")

    append_github_summary("\n".join(report_lines))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Установка оформления сообщества ВКонтакте «АвтоТочка»")
    p.add_argument("--config", default=str(AGENT_DIR / "config.json"))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-info", action="store_true")
    p.add_argument("--skip-cover", action="store_true")
    p.add_argument("--skip-avatar", action="store_true")
    p.add_argument("--skip-pinned", action="store_true")
    p.add_argument("--force-new-pinned", action="store_true")
    p.add_argument("--skip-backfill", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    return apply_group_branding(
        config_path=Path(args.config),
        dry_run=args.dry_run,
        update_info=not args.skip_info,
        upload_cover=not args.skip_cover,
        upload_avatar=not args.skip_avatar,
        publish_pinned=not args.skip_pinned,
        force_new_pinned=args.force_new_pinned,
        backfill_photos=not args.skip_backfill,
    )


if __name__ == "__main__":
    raise SystemExit(main())
