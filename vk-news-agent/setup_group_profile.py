#!/usr/bin/env python3
"""Автоматическая установка, ребрендинг и обновление оформления группы ВКонтакте «Греем Мотор» (https://vk.ru/greem_motor)."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

AGENT_DIR = Path(__file__).resolve().parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from agent import (  # noqa: E402
    append_github_summary,
    load_config,
    load_fallback_editorial_card,
    prepare_ai_image_for_post,
)
from ai_writer import create_ai_client, is_yaroslavl_topic, rewrite_existing_post_text  # noqa: E402
from dedup_store import make_post_guid  # noqa: E402
from news_fetcher import NewsCandidate, enrich_candidate_from_webpage  # noqa: E402
from vk_publisher import (  # noqa: E402
    PreparedImage,
    VKAPIError,
    VKPublisher,
    download_and_prepare_image,
    inspect_image_dimensions,
    resolve_configured_vk_group,
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


def extract_existing_photo_attachment(w_item: dict[str, Any]) -> str:
    """Возвращает строку photo<owner_id>_<id> существующего фото поста, чтобы не потерять его при wall.edit."""
    atts: list[str] = []
    for att in w_item.get("attachments") or []:
        if att.get("type") == "photo" and isinstance(att.get("photo"), dict):
            ph = att["photo"]
            oid = ph.get("owner_id")
            pid = ph.get("id")
            if oid is not None and pid is not None:
                atts.append(f"photo{oid}_{pid}")
    return ",".join(atts)


def cleanup_avatar_service_posts(pub: VKPublisher, before_ts: float) -> int:
    """Удаляет автоматический сервисный пост ВК об обновлении фото профиля группы."""
    removed = 0
    recent = pub.get_recent_wall_posts(count=6)
    for it in recent:
        if it.get("is_pinned"):
            continue
        pid = int(it.get("id") or 0)
        p_date = float(it.get("date") or 0)
        p_src = it.get("post_source") or {}
        txt = str(it.get("text") or "").strip()
        is_profile_photo = p_src.get("data") == "profile_photo" or (not txt and p_date >= before_ts - 15)
        if pid > 0 and is_profile_photo and p_date >= before_ts - 30:
            if pub.delete_wall_post(pid):
                removed += 1
    return removed


def rewrite_existing_wall_posts(
    pub: VKPublisher,
    config: dict[str, Any],
    published_state_path: Path,
    wall_items: list[dict[str, Any]],
    *,
    pinned_post_id: int = 0,
    regenerate_local_images: bool = True,
    preview_save_dir: Path | None = None,
    only_post_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Переписывает уже выпущенные посты простым живым языком и обновляет иллюстрации Ярославля."""
    if not wall_items:
        return []

    by_post_id: dict[int, dict[str, Any]] = {}
    if published_state_path.exists():
        try:
            pub_data = json.loads(published_state_path.read_text(encoding="utf-8"))
            for entry in pub_data.get("items") or []:
                pid = entry.get("vk_post_id")
                if isinstance(pid, int) and pid > 0:
                    by_post_id[pid] = entry
        except (OSError, json.JSONDecodeError):
            pass

    ai_client = create_ai_client(config, allow_template_fallback=True)
    rewritten_reports: list[dict[str, Any]] = []

    for w_item in wall_items:
        pid = int(w_item.get("id") or 0)
        if pid <= 0 or pid == pinned_post_id or w_item.get("is_pinned"):
            continue
        if only_post_ids and pid not in only_post_ids:
            continue
        p_src = w_item.get("post_source") or {}
        if p_src.get("data") == "profile_photo":
            continue
        existing_text = str(w_item.get("text") or "").strip()
        if not existing_text:
            continue

        meta = by_post_id.get(pid) or {}
        cat = str(meta.get("category") or "")
        if not cat:
            low = existing_text.lower()
            if "#дорогиярославля" in low:
                cat = "local_76"
            elif "#автосоветы" in low or "#обслуживаниеавто" in low:
                cat = "tip"
            else:
                cat = "news"
        url = str(meta.get("url") or "")
        orig_title = str(meta.get("title") or "").strip()
        title_hint = orig_title or existing_text.splitlines()[0][:100]

        # Подтягиваем первоисточник статьи, чтобы переписать пост по чистым фактам без старых искажений
        source_facts = ""
        if url.startswith(("http://", "https://")) and "news.google.com" not in url:
            try:
                cand = NewsCandidate(
                    title=title_hint,
                    url=url,
                    source_name="source",
                    category=cat,
                    summary="",
                )
                cand = enrich_candidate_from_webpage(cand, timeout=12)
                if len(cand.best_text) >= 120:
                    source_facts = cand.best_text
            except Exception as exc:  # noqa: BLE001
                logger.debug("Не удалось подгрузить первоисточник для поста #%d: %s", pid, exc)

        was_yaroslavl = cat == "local_76" or is_yaroslavl_topic(title_hint, existing_text, cat)
        new_text = rewrite_existing_post_text(
            ai_client,
            existing_text,
            config,
            category=cat,
            source_url=url,
            source_facts=source_facts,
            original_title=orig_title,
            is_local_yaroslavl=was_yaroslavl,
        )

        existing_att = extract_existing_photo_attachment(w_item)
        yaroslavl_post = was_yaroslavl or is_yaroslavl_topic(title_hint, new_text, cat)
        new_img: PreparedImage | None = None

        if not existing_att or (regenerate_local_images and yaroslavl_post):
            cached_preview = preview_save_dir / f"_yaroslavl_preview_{pid}.jpg" if preview_save_dir is not None else None
            if cached_preview is not None and cached_preview.is_file():
                raw_cached = cached_preview.read_bytes()
                dims = inspect_image_dimensions(raw_cached)
                if dims is not None:
                    new_img = PreparedImage(
                        data=raw_cached,
                        mime_type=dims[0],
                        filename=f"yaroslavl_{pid}.jpg",
                        width=dims[1],
                        height=dims[2],
                        source_url=f"cached_ai:{cached_preview.name}",
                    )
            if new_img is None:
                new_img = prepare_ai_image_for_post(
                    ai_client,
                    title=title_hint,
                    post_text=new_text,
                    category=cat,
                    config=config,
                )
            if new_img is None and not existing_att:
                new_img = load_fallback_editorial_card(config, cat)
            if new_img is not None and yaroslavl_post and preview_save_dir is not None and not str(new_img.source_url).startswith("cached_ai:"):
                preview_save_dir.mkdir(parents=True, exist_ok=True)
                (preview_save_dir / f"_yaroslavl_preview_{pid}.jpg").write_bytes(new_img.data)

        try:
            res = pub.edit_wall_post(pid, new_text, image=new_img, keep_attachments=existing_att)
            logger.info(
                "Пост #%d успешно переписан простым языком (Ярославль=%s, новое фото=%s)!",
                pid,
                yaroslavl_post,
                new_img is not None,
            )
            rewritten_reports.append(
                {
                    "post_id": pid,
                    "post_url": res["post_url"],
                    "photo_attached": res["photo_attached"],
                    "yaroslavl_image_regenerated": new_img is not None and yaroslavl_post,
                    "image_source": new_img.source_url if new_img is not None else "kept_existing",
                    "ai_model": getattr(ai_client, "_active_text_model", "") or getattr(ai_client, "name", ""),
                    "preview": new_text[:200],
                }
            )
        except VKAPIError as exc:
            logger.warning("Не удалось переписать пост #%d: %s", pid, exc)

    return rewritten_reports


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

        image_mode = str((config.get("posting") or {}).get("image_mode", "ai_only")).strip().lower()
        if image_mode != "ai_only" and url.startswith(("http://", "https://")):
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
    rewrite_wall: bool = False,
    save_verify_json: Path | None = None,
    only_post_ids: set[int] | None = None,
    keep_all_photos: bool = False,
) -> int:
    config = load_config(config_path)
    group_cfg = config.get("group", {})
    brand_cfg = config.get("branding", {})

    vk_group, fallback_gid = resolve_configured_vk_group(config)
    vk_token = (os.getenv("VK_TOKEN") or "").strip()
    vk_user_token = (
        os.getenv("VK_USER_TOKEN")
        or os.getenv("VK_ADMIN_TOKEN")
        or os.getenv("USER_TOKEN")
        or ""
    ).strip()

    title = str(brand_cfg.get("title") or group_cfg.get("title") or "Греем Мотор")
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

    pub = VKPublisher(
        vk_token=vk_token,
        vk_group=vk_group,
        vk_user_token=vk_user_token,
        fallback_group_id=fallback_gid,
    )
    gid = pub.resolve_group_id()
    b_state = load_branding_state(effective_b_state_path)
    _, slug_only = (None, vk_group)
    group_open_url = vk_group if vk_group.startswith("http") else f"https://vk.ru/{slug_only}"
    report_lines.append(f"- **ID сообщества ВК**: `{gid}` ([открыть группу]({group_open_url}))\n")

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
            av_ts = time.time()
            ok_av = pub.upload_group_avatar(avatar_img.data)
            if ok_av:
                cleanup_avatar_service_posts(pub, av_ts)
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

    wall_items = pub.get_recent_wall_posts(count=30)

    if publish_pinned and pinned_text:
        existing_pin_id = int(b_state.get("pinned_post_id") or 0)
        matched_pin_item: dict[str, Any] | None = None
        if not force_new_pinned and wall_items:
            for w_it in wall_items:
                txt = str(w_it.get("text") or "")
                if (
                    w_it.get("is_pinned")
                    or "Добро пожаловать в «Греем Мотор" in txt
                    or "Добро пожаловать в «АвтоТочк" in txt
                ):
                    existing_pin_id = int(w_it.get("id") or existing_pin_id)
                    matched_pin_item = w_it
                    break
            if matched_pin_item is None and existing_pin_id > 0:
                for w_it in wall_items:
                    if int(w_it.get("id") or 0) == existing_pin_id:
                        matched_pin_item = w_it
                        break

        if existing_pin_id > 0 and not force_new_pinned:
            existing_pin_att = extract_existing_photo_attachment(matched_pin_item or {})
            try:
                if existing_pin_att and b_state.get("pinned_has_photo"):
                    edit_res = pub.edit_wall_post(existing_pin_id, pinned_text, keep_attachments=existing_pin_att)
                else:
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
                    guid=make_post_guid(f"greem-motor-welcome-pin-{gid}"),
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

    rewritten_list: list[dict[str, Any]] = []
    if rewrite_wall and wall_items:
        rewritten_list = rewrite_existing_wall_posts(
            pub,
            config,
            published_state_path,
            wall_items,
            pinned_post_id=int(b_state.get("pinned_post_id") or 0),
            regenerate_local_images=not keep_all_photos,
            preview_save_dir=save_verify_json.parent if save_verify_json is not None else None,
            only_post_ids=only_post_ids,
        )
        if rewritten_list:
            report_lines.append(f"- ✅ **Переписаны выпущенные посты в стиле «{title}»**: `{len(rewritten_list)}` шт.")

    if backfill_photos and wall_items:
        patched = backfill_missing_wall_photos(pub, config, published_state_path, wall_items)
        if patched > 0:
            report_lines.append(f"- ✅ **Догружены фото к прошлым постам на стене**: `{patched}` шт.")

    if save_verify_json is not None:
        grp_raw = pub.call_api(
            "groups.getById",
            {"group_id": str(gid), "fields": "status,description,cover,has_photo"},
            token=pub.vk_user_token or pub.vk_token,
        )
        g_list = grp_raw.get("groups") if isinstance(grp_raw, dict) else grp_raw
        g0 = g_list[0] if isinstance(g_list, list) and g_list else {}
        final_wall = pub.get_recent_wall_posts(count=10)
        verify_payload = {
            "group": {
                "id": gid,
                "name": g0.get("name"),
                "status": g0.get("status"),
                "description_head": str(g0.get("description") or "")[:180],
                "cover_enabled": (g0.get("cover") or {}).get("enabled"),
                "has_photo": g0.get("has_photo"),
            },
            "rewritten_count": len(rewritten_list),
            "rewritten_reports": rewritten_list,
            "wall": [
                {
                    "id": w.get("id"),
                    "is_pinned": bool(w.get("is_pinned")),
                    "attachments": [a.get("type") for a in (w.get("attachments") or [])],
                    "text": w.get("text") or "",
                }
                for w in final_wall
            ],
        }
        save_verify_json.parent.mkdir(parents=True, exist_ok=True)
        save_verify_json.write_text(json.dumps(verify_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    append_github_summary("\n".join(report_lines))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Установка оформления сообщества ВКонтакте «Греем Мотор»")
    p.add_argument("--config", default=str(AGENT_DIR / "config.json"))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-info", action="store_true")
    p.add_argument("--skip-cover", action="store_true")
    p.add_argument("--skip-avatar", action="store_true")
    p.add_argument("--skip-pinned", action="store_true")
    p.add_argument("--force-new-pinned", action="store_true")
    p.add_argument("--skip-backfill", action="store_true")
    p.add_argument("--rewrite-wall", action="store_true", help="Переписать существующие посты стены под новый бренд")
    p.add_argument("--only-post-ids", default="", help="Список ID постов через запятую (например, 187,188)")
    p.add_argument("--keep-all-photos", action="store_true", help="Сохранить текущие фото у всех постов без перегенерации")
    p.add_argument("--save-verify-json", default=None)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    only_ids = {int(x.strip()) for x in str(args.only_post_ids or "").split(",") if x.strip().isdigit()} or None
    return apply_group_branding(
        config_path=Path(args.config),
        dry_run=args.dry_run,
        update_info=not args.skip_info,
        upload_cover=not args.skip_cover,
        upload_avatar=not args.skip_avatar,
        publish_pinned=not args.skip_pinned,
        force_new_pinned=args.force_new_pinned,
        backfill_photos=not args.skip_backfill,
        rewrite_wall=args.rewrite_wall,
        save_verify_json=Path(args.save_verify_json) if args.save_verify_json else None,
        only_post_ids=only_ids,
        keep_all_photos=args.keep_all_photos,
    )


if __name__ == "__main__":
    raise SystemExit(main())
