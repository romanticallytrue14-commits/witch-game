#!/usr/bin/env python3
"""Главный модуль автономного новостного ИИ-агента для группы ВК https://vk.ru/autoyar076."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Подключаем директорию агента в sys.path при запуске из корня репозитория
AGENT_DIR = Path(__file__).resolve().parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from ai_writer import (  # noqa: E402
    AIProviderError,
    TemplateFallbackClient,
    create_ai_client,
    inspect_gigachat_key_shape,
    select_best_candidate_with_ai,
    write_evergreen_tip_post,
    write_news_post,
)
from dedup_store import DedupStore, make_post_guid  # noqa: E402
from news_fetcher import (  # noqa: E402
    NewsCandidate,
    collect_fresh_candidates,
    enrich_candidate_from_webpage,
    fetch_url_bytes,
)
from vk_publisher import (  # noqa: E402
    PreparedImage,
    VKAPIError,
    VKPublisher,
    download_and_prepare_image,
    inspect_image_dimensions,
)

logger = logging.getLogger("vk_news_agent")


def load_config(config_path: Path) -> dict[str, Any]:
    with config_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Некорректный формат конфигурации: {config_path}")
    return data


def load_fallback_editorial_card(config: dict[str, Any], category: str) -> PreparedImage | None:
    """Загружает фирменную обложку рубрики «АвтоТочка», если у статьи или автосовета нет внешнего фото."""
    cards_map = (config.get("branding") or {}).get("fallback_cards") or {}
    default_rel = {
        "news": "branding/cards/news.jpg",
        "tip": "branding/cards/tip.jpg",
        "local_76": "branding/cards/road.jpg",
    }
    rel_path = str(cards_map.get(category) or default_rel.get(category) or "branding/cards/news.jpg")
    card_path = AGENT_DIR / rel_path
    if not card_path.exists():
        return None
    raw = card_path.read_bytes()
    info = inspect_image_dimensions(raw)
    if info is None:
        return None
    fmt, w, h = info
    mime = "image/png" if fmt == "png" else "image/jpeg"
    ext = "png" if fmt == "png" else "jpg"
    return PreparedImage(
        data=raw,
        filename=f"avtotochka_{category}.{ext}",
        mime_type=mime,
        width=w,
        height=h,
        source_url=f"local:{rel_path}",
    )


def append_github_summary(markdown_block: str) -> None:
    """Записывает красивый отчёт о работе агента во вкладку Summary в GitHub Actions."""
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    try:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(markdown_block + "\n")
    except OSError as exc:
        logger.debug("Не удалось записать GITHUB_STEP_SUMMARY: %s", exc)


def choose_slot_mode(requested_mode: str, total_posts: int, tip_every_n: int) -> str:
    """Определяет желаемую рубрику текущего запуска ('news' или 'tip')."""
    if requested_mode in {"news", "tip"}:
        return requested_mode
    if tip_every_n > 0 and (total_posts + 1) % tip_every_n == 0:
        return "tip"
    return "news"


def run_agent(
    *,
    config_path: Path,
    state_path: Path,
    dry_run: bool = False,
    mode: str = "auto",
    max_posts_override: int | None = None,
    allow_template_fallback: bool = True,
) -> int:
    """Выполняет полный цикл: поиск новостей -> отбор -> генерация ИИ-поста -> публикация в ВК."""
    config = load_config(config_path)
    posting_cfg = config.get("posting", {})
    group_cfg = config.get("group", {})

    vk_group = (
        os.getenv("VK_GROUP")
        or str(group_cfg.get("screen_name") or group_cfg.get("url") or "autoyar076")
    ).strip()
    vk_token = (os.getenv("VK_TOKEN") or "").strip()
    vk_user_token = (os.getenv("VK_USER_TOKEN") or "").strip()

    if not dry_run and not vk_token:
        logger.error(
            "Не задан секрет VK_TOKEN! Добавьте токен ВКонтакте в Settings -> Secrets and variables -> Actions."
        )
        append_github_summary(
            "### ❌ Ошибка настройки\n"
            "Не найден секрет **`VK_TOKEN`**. Добавьте его в `Settings → Secrets and variables → Actions` "
            "(подробная инструкция в `vk-news-agent/README.md`)."
        )
        return 1

    ai_client = create_ai_client(
        config,
        allow_template_fallback=allow_template_fallback or dry_run,
    )
    logger.info("Активный ИИ-генератор: %s", ai_client.name)

    dedup = DedupStore(
        state_path=state_path,
        similarity_threshold=float(posting_cfg.get("title_similarity_threshold", 0.52)),
    )

    publisher: VKPublisher | None = None
    if vk_token:
        publisher = VKPublisher(
            vk_token=vk_token,
            vk_group=vk_group,
            vk_user_token=vk_user_token,
        )
        wall_check_count = int(posting_cfg.get("wall_dedup_check_count", 30))
        if wall_check_count > 0:
            try:
                recent_posts = publisher.get_recent_wall_posts(count=wall_check_count)
                if recent_posts:
                    dedup.ingest_vk_wall_posts(recent_posts)
                    logger.info("Загружено %d последних постов со стены ВК для сверки дублей", len(recent_posts))
                    # Если подключён пользовательский токен, а аватар или фото закрепа ещё не были догружены — догружаем автоматически!
                    b_state_file = state_path.parent / "branding_state.json"
                    if not dry_run and b_state_file.exists():
                        try:
                            b_st = json.loads(b_state_file.read_text(encoding="utf-8"))
                        except (OSError, json.JSONDecodeError):
                            b_st = {}
                        if not b_st.get("avatar_uploaded") or not b_st.get("pinned_has_photo"):
                            logger.info("Обнаружен пользовательский доступ ВК — автоматически догружаем аватар, баннер закрепа и фото постов!")
                            import setup_group_profile

                            setup_group_profile.apply_group_branding(
                                config_path=config_path,
                                branding_state_path=b_state_file,
                                dry_run=False,
                                update_info=True,
                                upload_cover=False,
                                upload_avatar=not bool(b_st.get("avatar_uploaded")),
                                publish_pinned=not bool(b_st.get("pinned_has_photo")),
                                backfill_photos=True,
                            )
            except VKAPIError as exc:
                logger.warning("Предварительная проверка стены пропущена: %s", exc)

    now = datetime.now(timezone.utc)
    max_posts = max_posts_override or int(posting_cfg.get("max_posts_per_run", 1))
    tip_every_n = int(posting_cfg.get("tip_every_n_posts", 3))

    candidates = collect_fresh_candidates(config, dedup, now=now)
    published_count = 0
    group_title = str(group_cfg.get("title", "АвтоТочка"))
    summary_sections: list[str] = [
        f"## 📍 Новостной ИИ-агент «{group_title}» (`{vk_group}`)",
        f"- **Режим запуска**: `{'DRY-RUN (тест без публикации)' if dry_run else 'Боевая публикация на стену ВК'}`",
        f"- **Нейросеть**: `{ai_client.name}`",
        f"- **Найдено свежих уникальных инфоповодов**: `{len(candidates)}`",
    ]

    used_in_this_run: set[str] = set()

    for slot_idx in range(max_posts):
        target_slot = choose_slot_mode(mode, dedup.total_posts + slot_idx, tip_every_n)
        available = [c for c in candidates if c.url not in used_in_this_run]

        chosen_candidate: NewsCandidate | None = None
        evergreen_topic: dict[str, Any] | None = None

        if target_slot == "tip":
            tip_articles = [c for c in available if c.category == "tip"]
            if tip_articles:
                chosen_candidate = select_best_candidate_with_ai(
                    ai_client, tip_articles, config, preferred_mode="tip"
                )
            else:
                evergreen_topic = dedup.pick_evergreen_topic(
                    config.get("evergreen_topics", []),
                    now=now,
                )
                if evergreen_topic is None and mode == "auto" and available:
                    chosen_candidate = select_best_candidate_with_ai(
                        ai_client, available, config, preferred_mode="news"
                    )
        else:
            # Приоритет свежим автоновостям и новостям Ярославля (76 регион)
            news_pool = [c for c in available if c.category in {"local_76", "news"}] or available
            if news_pool:
                chosen_candidate = select_best_candidate_with_ai(
                    ai_client, news_pool, config, preferred_mode="news"
                )
            elif mode == "auto":
                logger.info("Свежих новостей нет — переключаемся на полезный авторазбор для водителей")
                evergreen_topic = dedup.pick_evergreen_topic(
                    config.get("evergreen_topics", []),
                    now=now,
                )

        if chosen_candidate is None and evergreen_topic is None:
            logger.info("Для слота #%d нет новых материалов — завершаем работу.", slot_idx + 1)
            summary_sections.append("\n> ℹ️ Новых уникальных инфоповодов для этого слота пока нет.")
            break

        prepared_img = None
        post_title = ""
        post_source = ""
        post_url_ref = ""
        post_category = "news"
        post_guid = ""
        evergreen_id: str | None = None

        if chosen_candidate is not None:
            used_in_this_run.add(chosen_candidate.url)
            chosen_candidate = enrich_candidate_from_webpage(chosen_candidate)
            post_title = chosen_candidate.title
            post_source = chosen_candidate.source_name
            post_url_ref = chosen_candidate.url
            post_category = chosen_candidate.category
            post_guid = make_post_guid(chosen_candidate.url or chosen_candidate.title)

            logger.info(
                "Готовим пост по материалу [%s | %.1f баллов]: %s (%s)",
                chosen_candidate.category,
                chosen_candidate.score,
                chosen_candidate.title,
                chosen_candidate.source_name,
            )
            try:
                post_text = write_news_post(ai_client, chosen_candidate, config, now=now)
            except AIProviderError as exc:
                logger.warning("Основной ИИ-сервис вернул ошибку (%s) — переключаемся на встроенный редактор.", exc)
                post_text = write_news_post(TemplateFallbackClient(), chosen_candidate, config, now=now)
            if chosen_candidate.image_urls:
                prepared_img = download_and_prepare_image(
                    chosen_candidate.image_urls,
                    referer_url=chosen_candidate.url,
                )
        else:
            assert evergreen_topic is not None
            evergreen_id = str(evergreen_topic.get("id", "tip"))
            post_title = str(evergreen_topic.get("title", "Авторазбор"))
            post_source = f"Авторская рубрика «{group_title}»"
            post_url_ref = f"evergreen://{evergreen_id}/{now.strftime('%Y-%m-%d')}"
            post_category = "tip"
            post_guid = make_post_guid(post_url_ref)

            logger.info("Готовим авторский автосовет [%s]: %s", evergreen_id, post_title)
            try:
                post_text = write_evergreen_tip_post(ai_client, evergreen_topic, config, now=now)
            except AIProviderError as exc:
                logger.warning("Основной ИИ-сервис вернул ошибку (%s) — переключаемся на встроенный редактор.", exc)
                post_text = write_evergreen_tip_post(TemplateFallbackClient(), evergreen_topic, config, now=now)

        if prepared_img is None and posting_cfg.get("use_branded_card_if_no_source_photo", True):
            prepared_img = load_fallback_editorial_card(config, post_category)

        print("\n" + "=" * 72)
        print(f"ПОДГОТОВЛЕН ПОСТ ({post_category.upper()}) — {post_source}")
        print(f"Тема: {post_title}")
        if prepared_img:
            print(
                f"Фото: {prepared_img.width}x{prepared_img.height} ({prepared_img.mime_type}, "
                f"{len(prepared_img.data) // 1024} КБ) <- {prepared_img.source_url}"
            )
        else:
            print("Фото: без вложения изображения")
        print("-" * 72)
        print(post_text)
        print("=" * 72 + "\n")

        if dry_run:
            published_count += 1
            img_status = (
                f"✅ Проверено (`{prepared_img.width}x{prepared_img.height}`, {len(prepared_img.data) // 1024} КБ)"
                if prepared_img
                else "⚪ Нет подходящего фото в источнике"
            )
            summary_sections.append(
                f"### 📝 Предпросмотр поста #{published_count} (`{post_category}`)\n"
                f"- **Инфоповод**: {post_title}\n"
                f"- **Источник**: {post_source}\n"
                f"- **Изображение**: {img_status}\n\n"
                f"```text\n{post_text}\n```"
            )
            continue

        assert publisher is not None
        fallback_link = (
            post_url_ref
            if (
                posting_cfg.get("fallback_link_attachment_if_no_photo", False)
                and post_url_ref.startswith(("http://", "https://"))
            )
            else ""
        )
        result = publisher.publish_post(
            post_text,
            image=prepared_img,
            fallback_link=fallback_link,
            guid=post_guid,
        )
        vk_post_id = result.get("post_id")
        vk_post_url = result.get("post_url")
        logger.info("Пост успешно опубликован на стене ВК: %s", vk_post_url)

        dedup.record_published(
            url=post_url_ref,
            title=post_title,
            guid=chosen_candidate.guid if chosen_candidate else post_guid,
            category=post_category,
            vk_post_id=vk_post_id,
            evergreen_id=evergreen_id,
        )
        dedup.save()
        published_count += 1

        photo_badge = (
            "✅ Фото прикреплено"
            if result.get("photo_attached")
            else "⚪ Только текст (для загрузки фото в ВК добавьте секрет `VK_USER_TOKEN`)"
        )
        summary_sections.append(
            f"### ✅ Опубликован пост #{published_count}\n"
            f"- **Ссылка на запись ВК**: [{vk_post_url}]({vk_post_url})\n"
            f"- **Инфоповод**: {post_title} ({post_source})\n"
            f"- **Вложение**: {photo_badge}\n\n"
            f"```text\n{post_text}\n```"
        )

    append_github_summary("\n\n".join(summary_sections))
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Автономный новостной ИИ-агент для сообщества ВКонтакте https://vk.ru/autoyar076"
    )
    parser.add_argument(
        "--config",
        default=str(AGENT_DIR / "config.json"),
        help="Путь к файлу конфигурации config.json",
    )
    parser.add_argument(
        "--state",
        default=str(AGENT_DIR / "state" / "published.json"),
        help="Путь к файлу истории опубликованных записей published.json",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Тестовый запуск: ищет новости и генерирует пост без отправки на стену ВК",
    )
    parser.add_argument(
        "--mode",
        choices=("auto", "news", "tip"),
        default="auto",
        help="Режим рубрики: auto (чередование новостей и советов), news (только новости), tip (автосовет)",
    )
    parser.add_argument(
        "--max-posts",
        type=int,
        default=None,
        help="Переопределить максимум постов за один запуск",
    )
    parser.add_argument(
        "--allow-template-fallback",
        action="store_true",
        default=True,
        help="Использовать встроенный умный редактор, если не заданы ключи внешней нейросети (включено по умолчанию)",
    )
    parser.add_argument(
        "--strict-ai",
        action="store_true",
        help="Требовать строго внешний ключ GigaChat / YandexGPT (останавливать запуск при его отсутствии)",
    )
    parser.add_argument(
        "--diagnose",
        default=None,
        help="Безопасная проверка прав токенов ВК и ИИ с сохранением отчёта в JSON (без публикации)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Подробное логирование отладки",
    )
    return parser


def run_diagnostics(config_path: Path, report_path: Path) -> int:
    config = load_config(config_path)
    group_cfg = config.get("group", {})
    vk_group = (
        os.getenv("VK_GROUP")
        or str(group_cfg.get("screen_name") or group_cfg.get("url") or "autoyar076")
    ).strip()
    vk_token = (os.getenv("VK_TOKEN") or "").strip()
    vk_user_token = (os.getenv("VK_USER_TOKEN") or "").strip()

    report: dict[str, Any] = {
        "env_present": {
            "VK_TOKEN": bool(vk_token),
            "VK_USER_TOKEN": bool(vk_user_token),
            "GIGACHAT_AUTH_KEY": bool((os.getenv("GIGACHAT_AUTH_KEY") or "").strip()),
            "YANDEX_API_KEY": bool((os.getenv("YANDEX_API_KEY") or "").strip()),
        },
        "gigachat_key_shape": inspect_gigachat_key_shape(os.getenv("GIGACHAT_AUTH_KEY") or ""),
    }

    # 1. Тест нейросети
    try:
        ai_client = create_ai_client(config, allow_template_fallback=True)
        report["ai_client"] = ai_client.name
        sample = ai_client.generate(
            "Ответь одним коротким предложением на русском.",
            "Напиши: Связь с нейросетью установлена.",
            temperature=0.2,
            max_tokens=40,
        )
        report["ai_test"] = {"ok": True, "reply_preview": sample[:100]}
    except Exception as exc:  # noqa: BLE001
        report["ai_test"] = {"ok": False, "error": str(exc)[:300]}

    # 2. Тест ВК (только безопасное чтение прав без публикации)
    if vk_token:
        pub = VKPublisher(vk_token=vk_token, vk_group=vk_group, vk_user_token=vk_user_token)
        try:
            report["vk"] = pub.diagnose_capabilities()
        except Exception as exc:  # noqa: BLE001
            report["vk"] = {"ok": False, "error": str(exc)[:300]}

    # 3. Тест парсинга картинок
    img_probe: dict[str, Any] = {}
    cand = NewsCandidate(
        title="test",
        url="https://motor.ru/news/za-rulem-nazval-polnoprivodnye-mashiny-do-2-mln-rublei-10-11-2026.htm",
        source_name="Motor.ru",
        category="tip",
        summary="test",
    )
    cand = enrich_candidate_from_webpage(cand)
    prep = download_and_prepare_image(cand.image_urls, referer_url=cand.url) if cand.image_urls else None
    img_probe["motor"] = {
        "found_urls": cand.image_urls[:3],
        "prepared": (
            {"w": prep.width, "h": prep.height, "mime": prep.mime_type, "bytes": len(prep.data)}
            if prep
            else None
        ),
    }
    report["image_probe"] = img_probe

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    if args.diagnose:
        return run_diagnostics(Path(args.config), Path(args.diagnose))
    try:
        return run_agent(
            config_path=Path(args.config),
            state_path=Path(args.state),
            dry_run=args.dry_run,
            mode=args.mode,
            max_posts_override=args.max_posts,
            allow_template_fallback=args.allow_template_fallback,
        )
    except (AIProviderError, VKAPIError, ValueError) as exc:
        logger.error("Критическая ошибка агента: %s", exc)
        append_github_summary(f"### ❌ Ошибка выполнения агента\n```text\n{exc}\n```")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
