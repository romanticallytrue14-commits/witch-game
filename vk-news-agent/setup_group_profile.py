#!/usr/bin/env python3
"""Автоматическая установка оформления группы ВКонтакте «АвтоТочка» (https://vk.ru/autoyar076)."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from agent import append_github_summary, load_config  # noqa: E402
from dedup_store import make_post_guid  # noqa: E402
from vk_publisher import (  # noqa: E402
    PreparedImage,
    VKAPIError,
    VKPublisher,
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


def apply_group_branding(
    *,
    config_path: Path,
    dry_run: bool = False,
    update_info: bool = True,
    upload_cover: bool = True,
    upload_avatar: bool = True,
    publish_pinned: bool = True,
) -> int:
    config = load_config(config_path)
    group_cfg = config.get("group", {})
    brand_cfg = config.get("branding", {})

    vk_group = (
        os.getenv("VK_GROUP")
        or str(group_cfg.get("screen_name") or group_cfg.get("url") or "autoyar076")
    ).strip()
    vk_token = (os.getenv("VK_TOKEN") or "").strip()
    vk_user_token = (os.getenv("VK_USER_TOKEN") or "").strip()

    title = str(brand_cfg.get("title") or group_cfg.get("title") or "АвтоТочка")
    status_text = str(brand_cfg.get("status") or "")
    description = str(brand_cfg.get("description") or "")
    pinned_text = str(brand_cfg.get("pinned_post_text") or "")

    avatar_path = AGENT_DIR / str(brand_cfg.get("avatar_path", "branding/avatar.jpg"))
    cover_path = AGENT_DIR / str(brand_cfg.get("cover_path", "branding/cover.jpg"))
    pinned_img_path = AGENT_DIR / str(brand_cfg.get("pinned_image_path", "branding/pinned_post.jpg"))

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
    report_lines.append(f"- **ID сообщества ВК**: `{gid}` ([открыть группу](https://vk.ru/{vk_group}))\n")

    if update_info:
        try:
            info_res = pub.update_group_info(
                title=title,
                description=description,
                status_text=status_text,
            )
            report_lines.append(
                f"- ✅ **Название и описание**: обновлены (`статус`: {'обновлён' if info_res['status_updated'] else 'пропущен'})"
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
                report_lines.append("- ✅ **Аватар сообщества**: установлен")
            else:
                report_lines.append("- ℹ️ **Аватар сообщества**: требует пользовательский токен админа (файл `branding/avatar.jpg`)")
        except VKAPIError as exc:
            logger.warning("Не удалось загрузить аватар через API: %s", exc)
            report_lines.append(f"- ⚠️ **Аватар**: `{exc.vk_message}` (загрузите `branding/avatar.jpg` вручную)")

    if publish_pinned and pinned_text:
        try:
            post_res = pub.publish_post(
                pinned_text,
                image=pinned_img,
                guid=make_post_guid(f"avtotochka-welcome-pin-{gid}"),
            )
            post_id = int(post_res.get("post_id") or 0)
            pinned_ok = pub.pin_wall_post(post_id) if post_id else False
            pin_note = "и закреплён в шапке стены" if pinned_ok else "(закрепите запись вручную в меню поста)"
            report_lines.append(
                f"- ✅ **Приветственный пост**: [{post_res['post_url']}]({post_res['post_url']}) {pin_note}"
            )
        except VKAPIError as exc:
            logger.warning("Не удалось опубликовать закреплённый пост: %s", exc)
            report_lines.append(f"- ⚠️ **Приветственный пост**: `{exc.vk_message}`")

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
    )


if __name__ == "__main__":
    raise SystemExit(main())
