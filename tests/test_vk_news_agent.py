"""Комплексные тесты новостного ИИ-агента ВКонтакте для https://vk.ru/greem_motor."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_DIR = REPO_ROOT / "vk-news-agent"
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

import agent  # noqa: E402
import ai_writer  # noqa: E402
import dedup_store  # noqa: E402
import news_fetcher  # noqa: E402
import vk_publisher  # noqa: E402


class FakeHTTPResponse:
    """Минимальный контекстный менеджер ответа urllib.request.urlopen для юнит-тестов."""

    def __init__(self, body: bytes, headers: dict[str, str] | None = None, url: str = "https://example.com") -> None:
        self._body = body
        self.headers = headers or {"Content-Type": "application/json; charset=utf-8"}
        self._url = url

    def read(self, amt: int | None = None) -> bytes:
        return self._body if amt is None else self._body[:amt]

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def make_minimal_png(width: int = 800, height: int = 500) -> bytes:
    """Генерирует валидный заголовок PNG с заданными размерами и достаточным объёмом байт."""
    import struct

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    chunk_len = struct.pack(">I", len(ihdr))
    header = b"\x89PNG\r\n\x1a\n" + chunk_len + b"IHDR" + ihdr + b"\x00\x00\x00\x00"
    return header + (b"\x42" * 14_000)


class DedupAndEncodingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = agent.load_config(AGENT_DIR / "config.json")
        self.now = datetime(2026, 11, 9, 18, 0, 0, tzinfo=timezone.utc)

    def test_cp1251_drom_rss_decoding_and_parsing(self) -> None:
        xml_str = (
            '<?xml version="1.0" encoding="windows-1251"?>'
            "<rss version=\"2.0\"><channel><title>Drom.ru: Новости</title>"
            "<item>"
            "<title>В России стартовали продажи обновлённого кроссовера Belgee X50</title>"
            "<link>https://news.drom.ru/Belgee-X50-104200.html</link>"
            "<pubDate>Mon, 09 Nov 2026 16:00:00 +0300</pubDate>"
            "<description><![CDATA[Цены начинаются от 2,2 млн рублей у российских дилеров. "
            '<img src="https://s.drom.ru/1/pubs/4/104200/belgee_main.jpg"/>'
            '<img src="https://images.drom.ru/img/drom_88x31.gif"/>'
            "]]></description>"
            "</item></channel></rss>"
        )
        cp1251_bytes = xml_str.replace("ё", "е").encode("cp1251")
        decoded = news_fetcher.decode_http_bytes(cp1251_bytes, "application/rss+xml")
        self.assertIn("Belgee X50", decoded)
        self.assertIn("Цены начинаются", decoded)

        items = news_fetcher.parse_feed_xml(decoded, self.config["sources"][3])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "В России стартовали продажи обновленного кроссовера Belgee X50")
        # Кнопка 88x31 отфильтрована, а основное фото сохранено
        self.assertEqual(items[0].image_urls, ["https://s.drom.ru/1/pubs/4/104200/belgee_main.jpg"])

    def test_strict_yaroslavl_76_filter_blocks_offtopic_and_ads(self) -> None:
        yar_source = next(s for s in self.config["sources"] if "76.ру" in s["name"])

        good_item = news_fetcher.NewsCandidate(
            title="В Ярославле перекроют движение транспорта из-за ремонта асфальта на проспекте Фрунзе",
            url="https://76.ru/text/transport/2026/11/09/111/",
            source_name="76.ру",
            category="local_76",
            summary="Водителям предложили пути объезда пробок.",
            published_at=self.now,
        )
        self.assertTrue(news_fetcher.evaluate_and_score_candidate(good_item, yar_source, self.config, now=self.now))
        self.assertEqual(good_item.category, "local_76")
        self.assertGreater(good_item.score, 35.0)

        airplane_item = news_fetcher.NewsCandidate(
            title="Почему в самолетах не делают квадратные окна: за привычным овалом скрывается трагедия",
            url="https://76.ru/text/transport/2026/11/09/222/",
            source_name="76.ру",
            category="local_76",
            summary="Серия загадочных катастроф заставила инженеров пересмотреть конструкцию авиалайнера.",
            published_at=self.now,
        )
        self.assertFalse(news_fetcher.evaluate_and_score_candidate(airplane_item, yar_source, self.config, now=self.now))

        ad_item = news_fetcher.NewsCandidate(
            title="Выгодные кредиты на кроссоверы в автосалоне Ярославля",
            url="https://76.ru/text/auto/2026/11/09/333/",
            source_name="76.ру",
            category="local_76",
            summary="На правах рекламы. erid: 2SDnjc12345",
            published_at=self.now,
        )
        self.assertFalse(news_fetcher.evaluate_and_score_candidate(ad_item, yar_source, self.config, now=self.now))

    def test_semantic_dedup_across_outlets_and_vk_wall(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "published.json"
            store = dedup_store.DedupStore(state_file)

            store.record_published(
                url="https://www.kolesa.ru/news/belgee-x50-start-prodazh?utm_source=rss",
                title="В России стартовали продажи обновлённого кроссовера Belgee X50 — по цене от 2,2 млн ₽ - RB.ru",
                guid="kolesa-1",
                vk_post_id=101,
            )
            store.save()

            reloaded = dedup_store.DedupStore(state_file)
            # 1) Та же ссылка без utm
            dup1, _ = reloaded.is_duplicate(
                "https://kolesa.ru/news/belgee-x50-start-prodazh/",
                "Другой заголовок",
            )
            self.assertTrue(dup1)

            # 2) Тот же инфоповод из другого СМИ
            dup2, reason2 = reloaded.is_duplicate(
                "https://motor.ru/news/belgee-x50-new-price.htm",
                "Стартовали продажи обновленного кроссовера Belgee X50 в России от 2,2 млн рублей",
            )
            self.assertTrue(dup2, msg=reason2)

            # 3) Совершенно другая новость про ту же марку НЕ считается дублем
            dup3, _ = reloaded.is_duplicate(
                "https://motor.ru/news/belgee-s50-sedan-crash-test.htm",
                "Бюджетный седан Belgee S50 с газовым оборудованием прошёл ресурсные испытания в Минске",
            )
            self.assertFalse(dup3)

            # 4) Пост, выложенный админом вручную на стену ВК
            reloaded.ingest_vk_wall_posts(
                [
                    {
                        "text": "⚡ На трассе М-8 под Ярославлем открыли движение по обновлённому мосту через Которосль!\n\nПодробности...",
                        "attachments": [],
                    }
                ]
            )
            dup4, _ = reloaded.is_duplicate(
                "https://76.ru/text/transport/2026/11/09/999/",
                "На трассе М-8 под Ярославлем открыли движение по обновленному мосту через Которосль",
            )
            self.assertTrue(dup4)


class AIWriterAndFormattingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = agent.load_config(AGENT_DIR / "config.json")

    def test_clean_markdown_and_finalize_vk_post(self) -> None:
        raw_llm = (
            "Вот готовый пост для вашей группы ВКонтакте:\n"
            "### **🚗 АвтоВАЗ обновил комплектации Lada Vesta для зимы**\n\n"
            "С ноября машины получили **подогрев форсунок** и улучшенную защиту [подробнее](https://example.com).\n\n"
            "* Тёплый пакет уже в базе\n"
            "* Новый блок климата\n\n"
            "#авто #лада"
        )
        final_text = ai_writer.finalize_vk_post(
            raw_llm,
            self.config,
            category="news",
            source_name="Колёса.ру",
            source_url="https://www.kolesa.ru/news/lada-vesta",
        )
        self.assertNotIn("**", final_text)
        self.assertNotIn("###", final_text)
        self.assertNotIn("Вот готовый пост", final_text)
        self.assertNotIn("https://www.kolesa.ru", final_text)  # внешние ссылки скрыты для сохранения охватов ВК
        self.assertIn("• Тёплый пакет уже в базе", final_text)
        self.assertIn("📰 По материалам: Колёса.ру", final_text)
        self.assertIn("🔥 Греем Мотор — подписывайтесь: vk.ru/greem_motor", final_text)
        self.assertIn("#greem_motor", final_text)
        self.assertIn("#автоновости@greem_motor", final_text)
        self.assertNotIn("autoyar076", final_text)
        self.assertIn("#автоярославль", final_text)

        # Проверка снятия КАПСЛОКА в заголовке и удаления лишних подзаголовков
        caps_post = ai_writer.clean_markdown_for_vk(
            "🚗 ЧАЙКА СПАСЛА ОТ ШТРАФА ПДД В ЯРОСЛАВЛЕ!\n\n"
            "Птица закрыла номер машины крылом.\n\n"
            "🤔 Вопрос опытным водителям:\n"
            "А вам везло на дороге?"
        )
        self.assertIn("🚗 Чайка спасла от штрафа ПДД в Ярославле!", caps_post)
        self.assertNotIn("ЧАЙКА СПАСЛА", caps_post)
        self.assertNotIn("Вопрос опытным водителям", caps_post)

    def test_yaroslavl_image_prompt_detection(self) -> None:
        tpl = ai_writer.TemplateFallbackClient()
        # 1. Ярославская новость -> в промпте картинки обязательно Ярославль, Волга и купола
        yar_post = (
            "🚗 В Ярославле на Московском проспекте обновили разметку\n\n"
            "Движение стало удобнее.\n\n"
            "📰 По материалам: 76.ру\n"
            "#грееммотор #автоярославль #авто76"
        )
        self.assertTrue(ai_writer.is_yaroslavl_topic("Ремонт в Ярославле", yar_post, "local_76"))
        yar_prompt = ai_writer.build_ai_image_prompt(
            tpl,
            title="Ремонт в Ярославле",
            post_text=yar_post,
            category="local_76",
        )
        self.assertIn("Ярославл", yar_prompt)
        self.assertIn("Волг", yar_prompt)

        # 2. Общероссийская новость (даже с #автоярославль в подвале и вопросом в конце) -> НЕ считается городской новостью Ярославля
        fed_post = (
            "⛽ Проверка бензина на трассах страны\n\n"
            "Крупные сети прошли проверку спокойно.\n\n"
            "А на каких заправках в Ярославле заправляетесь вы?\n\n"
            "📰 По материалам: Motor.ru\n"
            "🔥 Греем Мотор — подписывайтесь: vk.ru/greem_motor\n"
            "#грееммотор #автоярославль #авто76"
        )
        self.assertFalse(ai_writer.is_yaroslavl_topic("Проверка бензина на АЗС", fed_post, "news"))
        fed_prompt = ai_writer.build_ai_image_prompt(
            tpl,
            title="Проверка бензина на АЗС",
            post_text=fed_post,
            category="news",
        )
        self.assertNotIn("Ярославл", fed_prompt)

    def test_gigachat_header_normalization_and_uuid_warning(self) -> None:
        expected_b64 = base64.b64encode(b"cid:csec").decode("ascii")
        self.assertEqual(
            ai_writer.normalize_gigachat_auth_key(f"Authorization: Basic {expected_b64}"),
            expected_b64,
        )
        self.assertEqual(
            ai_writer.normalize_gigachat_auth_key(f'"{expected_b64}"'),
            expected_b64,
        )
        shape = ai_writer.inspect_gigachat_key_shape("019a0000-1111-2222-3333-444455556666")
        self.assertTrue(shape["is_single_uuid"])
        self.assertFalse(shape["is_valid_base64"])

    def test_gigachat_oauth_and_completion_protocol(self) -> None:
        calls: list[tuple[str, dict[str, str], bytes]] = []

        def fake_urlopen(req, timeout=30, context=None):
            headers = {k.lower(): v for k, v in req.header_items()}
            calls.append((req.full_url, headers, req.data or b""))
            if "oauth" in req.full_url:
                return FakeHTTPResponse(
                    json.dumps({"access_token": "giga-tok-123", "expires_at": 4_102_444_800_000}).encode("utf-8")
                )
            return FakeHTTPResponse(
                json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "🚗 Тестовый пост от GigaChat без разметки.",
                                }
                            }
                        ]
                    }
                ).encode("utf-8")
            )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            client = ai_writer.GigaChatClient("my_client_id:my_secret", self.config["ai"]["gigachat"])
            expected_b64 = base64.b64encode(b"my_client_id:my_secret").decode("ascii")
            out1 = client.generate("sys", "usr1", 0.6, 500)
            out2 = client.generate("sys", "usr2", 0.6, 500)

        self.assertIn("Тестовый пост от GigaChat", out1)
        self.assertIn("Тестовый пост от GigaChat", out2)
        # OAuth вызывается только 1 раз благодаря кэшированию токена!
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0][1]["authorization"], f"Basic {expected_b64}")
        self.assertIn(b"scope=GIGACHAT_API_PERS", calls[0][2])
        self.assertEqual(calls[1][1]["authorization"], "Bearer giga-tok-123")

    def test_yandexgpt_protocol(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(req, timeout=30, context=None):
            captured["url"] = req.full_url
            captured["headers"] = {k.lower(): v for k, v in req.header_items()}
            captured["body"] = json.loads((req.data or b"{}").decode("utf-8"))
            return FakeHTTPResponse(
                json.dumps(
                    {
                        "result": {
                            "alternatives": [
                                {"message": {"role": "assistant", "text": "🔧 Ответ от YandexGPT готов!"}}
                            ]
                        }
                    }
                ).encode("utf-8")
            )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            client = ai_writer.YandexGPTClient("b1gfolder76", "AQVNsecretkey", "", self.config["ai"]["yandexgpt"])
            res = client.generate("system text", "user text", 0.5, 800)

        self.assertEqual(res, "🔧 Ответ от YandexGPT готов!")
        self.assertEqual(captured["headers"]["authorization"], "Api-Key AQVNsecretkey")  # type: ignore[index]
        self.assertEqual(captured["headers"]["x-folder-id"], "b1gfolder76")  # type: ignore[index]
        body = captured["body"]
        assert isinstance(body, dict)
        self.assertEqual(body["modelUri"], "gpt://b1gfolder76/yandexgpt/latest")
        self.assertEqual(body["messages"][0]["text"], "system text")

    def test_gigachat_text2image_and_proofread_pass(self) -> None:
        calls: list[tuple[str, dict[str, str], bytes]] = []
        fake_png = make_minimal_png(1024, 1024)

        def fake_urlopen(req, timeout=30, context=None):
            headers = {k.lower(): v for k, v in req.header_items()}
            body = req.data or b""
            calls.append((req.full_url, headers, body))
            if "oauth" in req.full_url:
                return FakeHTTPResponse(
                    json.dumps({"access_token": "giga-img-tok", "expires_at": 4_102_444_800_000}).encode("utf-8")
                )
            if "/files/f123-456-uuid/content" in req.full_url:
                return FakeHTTPResponse(fake_png, headers={"Content-Type": "image/jpeg"})
            payload = json.loads(body.decode("utf-8"))
            if payload.get("function_call") == "auto":
                return FakeHTTPResponse(
                    json.dumps(
                        {
                            "choices": [
                                {
                                    "message": {
                                        "role": "assistant",
                                        "content": 'Готово! <img src="f123-456-uuid" fuse="true"/>',
                                    }
                                }
                            ]
                        }
                    ).encode("utf-8")
                )
            # Корректорский проход (temperature <= 0.2) или генерация текста
            if payload.get("temperature", 1.0) <= 0.25:
                return FakeHTTPResponse(
                    json.dumps(
                        {
                            "choices": [
                                {
                                    "message": {
                                        "role": "assistant",
                                        "content": (
                                            "🚗 Аккумулятор зимой сдаётся именно в то утро, когда вы уже почти не опаздывали\n\n"
                                            "В мороз ниже −20 °C ёмкость батареи падает почти вдвое, поэтому короткие поездки по 10 минут "
                                            "не успевают восполнить заряд от генератора.\n\n"
                                            "💡 Что проверить на выходных: замерьте напряжение мультиметром утром до запуска — норма 12,6–12,7 В.\n\n"
                                            "А ваш аккумулятор этой зимой держится бодро или уже намекал на пенсию?"
                                        ),
                                    }
                                }
                            ]
                        }
                    ).encode("utf-8")
                )
            return FakeHTTPResponse(
                json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": (
                                        "🚗 Акумулятор зимой здаётся именно в то утро когда вы уже почти не опаздывали\n\n"
                                        "В мороз ниже -20 ёмкость падает почти в двое поэтому короткие поездки не зарежают батарею.\n\n"
                                        "💡 Что проверить: замерьте напряжение мультиметром утром до запуска — норма 12,6 В.\n\n"
                                        "А ваш акумулятор держится бодро?"
                                    ),
                                }
                            }
                        ]
                    }
                ).encode("utf-8")
            )

        client_raw = base64.b64encode(b"11111111-2222-3333-4444-555555555555:secret99").decode("ascii")
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            client = ai_writer.GigaChatClient(client_raw, self.config["ai"]["gigachat"])
            cand = news_fetcher.NewsCandidate(
                title="Эксперты напомнили правила проверки аккумулятора в морозы",
                url="https://example.ru/akb-winter",
                source_name="За рулём",
                category="tip",
                summary="В сильный мороз ёмкость АКБ снижается, а короткие поездки не восполняют заряд.",
            )
            final_text = ai_writer.write_news_post(client, cand, self.config)
            prep = agent.prepare_ai_image_for_post(
                client,
                title=cand.title,
                post_text=final_text,
                category="tip",
                config=self.config,
            )

        # Проверяем, что корректор исправил «Акумулятор/здаётся/зарежают» -> «Аккумулятор/сдаётся»
        self.assertIn("Аккумулятор зимой сдаётся", final_text)
        self.assertNotIn("Акумулятор", final_text)
        self.assertIn("📰 По материалам: За рулём", final_text)

        # Проверяем протокол GigaChat text2image: function_call=auto -> /files/{id}/content + X-Client-ID
        self.assertIsNotNone(prep)
        assert prep is not None
        self.assertEqual((prep.width, prep.height), (1024, 1024))
        self.assertEqual(prep.source_url, "ai:GigaChat:f123-456-uuid")

        file_calls = [c for c in calls if "/files/f123-456-uuid/content" in c[0]]
        self.assertEqual(len(file_calls), 1)
        self.assertEqual(file_calls[0][1]["authorization"], "Bearer giga-img-tok")
        # По умолчанию X-Client-ID не отправляется, чтобы совпадать с запросом /chat/completions (без 403 Forbidden)
        self.assertNotIn("x-client-id", file_calls[0][1])
        img_prompt = ai_writer.build_ai_image_prompt(
            ai_writer.TemplateFallbackClient(),
            title=cand.title,
            post_text=final_text,
            category="tip",
        )
        self.assertIn("СТРОГО БЕЗ надписей", img_prompt)
        self.assertIn("без госномера", img_prompt)


class VKPublisherAndEndToEndTests(unittest.TestCase):
    def test_parse_group_identifier_variants(self) -> None:
        self.assertEqual(vk_publisher.parse_group_identifier("https://vk.ru/autoyar076"), (None, "autoyar076"))
        self.assertEqual(vk_publisher.parse_group_identifier("https://vk.com/club195214603"), (195214603, ""))
        self.assertEqual(vk_publisher.parse_group_identifier("-76000111"), (76000111, ""))
        self.assertEqual(
            vk_publisher.normalize_vk_token(
                "https://oauth.vk.com/blank.html#access_token=vk1.a.TESTTOKEN123&expires_in=0&user_id=777"
            ),
            "vk1.a.TESTTOKEN123",
        )

    def test_webp_to_jpeg_conversion_and_image_inspection(self) -> None:
        png_bytes = make_minimal_png(640, 400)
        self.assertEqual(vk_publisher.inspect_image_dimensions(png_bytes), ("png", 640, 400))

        converter = shutil.which("convert")
        if converter:
            with tempfile.TemporaryDirectory() as tmpdir:
                webp_path = os.path.join(tmpdir, "sample.webp")
                subprocess.run(
                    [converter, "-size", "600x400", "xc:navy", webp_path],
                    check=True,
                    capture_output=True,
                )
                webp_bytes = Path(webp_path).read_bytes()
                info = vk_publisher.inspect_image_dimensions(webp_bytes)
                self.assertIsNotNone(info)
                self.assertEqual(info[0], "webp")  # type: ignore[index]

                jpg_bytes = vk_publisher.convert_webp_to_jpeg(webp_bytes)
                self.assertIsNotNone(jpg_bytes)
                jpg_info = vk_publisher.inspect_image_dimensions(jpg_bytes or b"")
                self.assertEqual(jpg_info, ("jpeg", 600, 400))

    def test_vk_publish_with_photo_and_group_token_error27_fallback(self) -> None:
        # Сценарий 1: полный цикл с загрузкой фото (groups.getById v5.199 -> getWallUploadServer -> upload -> saveWallPhoto -> wall.post)
        api_calls: list[str] = []

        def fake_urlopen_full(req, timeout=30, context=None):
            url = req.full_url
            if "groups.getById" in url:
                api_calls.append("groups.getById")
                return FakeHTTPResponse(json.dumps({"response": {"groups": [{"id": 76076076}]}}).encode("utf-8"))
            if "photos.getWallUploadServer" in url:
                api_calls.append("photos.getWallUploadServer")
                return FakeHTTPResponse(
                    json.dumps({"response": {"upload_url": "https://pu.vk.ru/upload.php"}}).encode("utf-8")
                )
            if "pu.vk.ru/upload.php" in url:
                api_calls.append("upload_binary")
                return FakeHTTPResponse(
                    json.dumps({"server": 12, "photo": '[{"photo":"ok"}]', "hash": "abc"}).encode("utf-8")
                )
            if "photos.saveWallPhoto" in url:
                api_calls.append("photos.saveWallPhoto")
                return FakeHTTPResponse(
                    json.dumps({"response": [{"owner_id": -76076076, "id": 555777}]}).encode("utf-8")
                )
            if "wall.post" in url:
                api_calls.append("wall.post")
                body_str = (req.data or b"").decode("utf-8")
                self.assertIn("owner_id=-76076076", body_str)
                self.assertIn("from_group=1", body_str)
                self.assertIn("attachments=photo-76076076_555777", body_str)
                return FakeHTTPResponse(json.dumps({"response": {"post_id": 9001}}).encode("utf-8"))
            raise AssertionError(f"Unexpected URL: {url}")

        pub = vk_publisher.VKPublisher(vk_token="user_admin_token", vk_group="https://vk.ru/autoyar076")
        img = vk_publisher.PreparedImage(
            data=make_minimal_png(),
            filename="news.png",
            mime_type="image/png",
            width=800,
            height=500,
            source_url="https://kolesa.ru/photo.png",
        )
        with patch("urllib.request.urlopen", side_effect=fake_urlopen_full):
            res = pub.publish_post("Пост с картинкой", image=img, guid="guid-1")

        self.assertEqual(res["post_id"], 9001)
        self.assertTrue(res["photo_attached"])
        self.assertEqual(res["post_url"], "https://vk.ru/wall-76076076_9001")
        self.assertEqual(
            api_calls,
            ["groups.getById", "photos.getWallUploadServer", "upload_binary", "photos.saveWallPhoto", "wall.post"],
        )

        # Сценарий 2: ключ сообщества возвращает Error 27 на photos.getWallUploadServer — пост всё равно успешно выходит текстом!
        def fake_urlopen_group_token(req, timeout=30, context=None):
            url = req.full_url
            if "photos.getWallUploadServer" in url:
                return FakeHTTPResponse(
                    json.dumps(
                        {
                            "error": {
                                "error_code": 27,
                                "error_msg": "Group authorization failed: method is unavailable with group auth.",
                            }
                        }
                    ).encode("utf-8")
                )
            if "wall.post" in url:
                return FakeHTTPResponse(json.dumps({"response": {"post_id": 9002}}).encode("utf-8"))
            raise AssertionError(f"Unexpected URL: {url}")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen_group_token):
            res2 = pub.publish_post("Пост по ключу сообщества", image=img, guid="guid-2")

        self.assertEqual(res2["post_id"], 9002)
        self.assertFalse(res2["photo_attached"])

    def test_end_to_end_agent_run_and_workflow_file(self) -> None:
        workflow_path = REPO_ROOT / ".github" / "workflows" / "vk-news-agent.yml"
        self.assertTrue(workflow_path.exists())
        wf_text = workflow_path.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", wf_text)
        self.assertIn("schedule:", wf_text)
        self.assertIn("VK_TOKEN:", wf_text)
        self.assertIn("GIGACHAT_AUTH_KEY:", wf_text)
        self.assertIn("YANDEX_API_KEY:", wf_text)
        self.assertIn("python3 vk-news-agent/agent.py", wf_text)

        rss_sample = (
            '<?xml version="1.0" encoding="utf-8"?>'
            "<rss version=\"2.0\"><channel><title>Колёса.ру</title>"
            "<item>"
            "<title>В Ярославле отремонтировали ключевой участок Московского проспекта для автомобилистов</title>"
            "<link>https://www.kolesa.ru/news/yaroslavl-moskovsky-prospekt</link>"
            f"<pubDate>{datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>"
            "<description><![CDATA[На участке обновили асфальт, разметку и настроили светофоры для снижения пробок. "
            '<img src="https://kolesa-uploads.ru/r/880x/yaroslavl-road.png"/>'
            "]]></description>"
            "</item></channel></rss>"
        ).encode("utf-8")

        png_data = make_minimal_png(880, 520)

        def fake_urlopen_e2e(req, timeout=30, context=None):
            url = req.full_url
            if any(feed in url for feed in ("kolesa.ru/export",)):
                return FakeHTTPResponse(rss_sample, {"Content-Type": "application/xml; charset=utf-8"}, url)
            if any(feed in url for feed in ("5koleso.ru", "motor.ru", "drom.ru", "76.ru", "news.google.com")):
                empty_rss = b'<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel></channel></rss>'
                return FakeHTTPResponse(empty_rss, {"Content-Type": "application/xml"}, url)
            if "yaroslavl-road.png" in url:
                return FakeHTTPResponse(png_data, {"Content-Type": "image/png"}, url)
            if "yaroslavl-moskovsky-prospekt" in url:
                html_page = (
                    "<html><head><meta property='og:image' content='https://kolesa-uploads.ru/r/880x/yaroslavl-road.png'></head>"
                    "<body><p>На Московском проспекте в Ярославле завершили укладку нового износостойкого асфальта "
                    "и перенастроили фазы светофоров для ускорения потока машин в часы пик.</p></body></html>"
                ).encode("utf-8")
                return FakeHTTPResponse(html_page, {"Content-Type": "text/html; charset=utf-8"}, url)
            if "groups.getById" in url:
                return FakeHTTPResponse(json.dumps({"response": {"groups": [{"id": 760076}]}}).encode("utf-8"))
            if "wall.get" in url:
                return FakeHTTPResponse(json.dumps({"response": {"items": []}}).encode("utf-8"))
            if "photos.getWallUploadServer" in url:
                return FakeHTTPResponse(
                    json.dumps({"response": {"upload_url": "https://pu.vk.ru/upload"}}).encode("utf-8")
                )
            if "pu.vk.ru/upload" in url:
                return FakeHTTPResponse(
                    json.dumps({"server": 1, "photo": '[{"ok":1}]', "hash": "h1"}).encode("utf-8")
                )
            if "photos.saveWallPhoto" in url:
                return FakeHTTPResponse(
                    json.dumps({"response": [{"owner_id": -760076, "id": 12345}]}).encode("utf-8")
                )
            if "wall.post" in url:
                return FakeHTTPResponse(json.dumps({"response": {"post_id": 777}}).encode("utf-8"))
            raise AssertionError(f"Unhandled URL in e2e test: {url}")

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "published.json"
            summary_path = Path(tmpdir) / "summary.md"
            env_patch = {
                "VK_TOKEN": "test-vk-token",
                "AI_PROVIDER": "template",
                "GITHUB_STEP_SUMMARY": str(summary_path),
            }
            with patch.dict(os.environ, env_patch, clear=False), patch(
                "urllib.request.urlopen", side_effect=fake_urlopen_e2e
            ):
                rc = agent.run_agent(
                    config_path=AGENT_DIR / "config.json",
                    state_path=state_path,
                    dry_run=False,
                    mode="news",
                )
                self.assertEqual(rc, 0)
                saved_state = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(saved_state["total_posts"], 1)
                self.assertEqual(saved_state["items"][0]["vk_post_id"], 777)
                self.assertIn("https://vk.ru/wall-760076_777", summary_path.read_text(encoding="utf-8"))

                # Второй запуск подряд НЕ публикует ту же самую новость повторно!
                rc2 = agent.run_agent(
                    config_path=AGENT_DIR / "config.json",
                    state_path=state_path,
                    dry_run=False,
                    mode="news",
                )
                self.assertEqual(rc2, 0)
                saved_state_after = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(saved_state_after["total_posts"], 1)

    def test_greem_motor_branding_assets_and_vk_profile_installer(self) -> None:
        import setup_group_profile

        cfg = agent.load_config(AGENT_DIR / "config.json")
        self.assertEqual(cfg["group"]["title"], "Греем Мотор")
        self.assertEqual(cfg["branding"]["title"], "Греем Мотор")
        self.assertIn("#грееммотор", cfg["posting"]["base_hashtags"])
        self.assertNotIn("#автоточка", cfg["posting"]["base_hashtags"])

        cover_info = vk_publisher.inspect_image_dimensions((AGENT_DIR / "branding" / "cover.jpg").read_bytes())
        avatar_info = vk_publisher.inspect_image_dimensions((AGENT_DIR / "branding" / "avatar.jpg").read_bytes())
        pin_info = vk_publisher.inspect_image_dimensions((AGENT_DIR / "branding" / "pinned_post.jpg").read_bytes())
        tip_card_info = vk_publisher.inspect_image_dimensions(
            (AGENT_DIR / "branding" / "cards" / "tip.jpg").read_bytes()
        )
        menu_info = vk_publisher.inspect_image_dimensions(
            (AGENT_DIR / "branding" / "menu" / "menu_news.jpg").read_bytes()
        )
        self.assertEqual(cover_info, ("jpeg", 1920, 768))
        self.assertEqual(avatar_info, ("jpeg", 1024, 1024))
        self.assertEqual(pin_info, ("jpeg", 1280, 800))
        self.assertEqual(tip_card_info, ("jpeg", 1280, 800))
        self.assertEqual(menu_info, ("jpeg", 376, 256))

        called_methods: list[str] = []

        def fake_branding_urlopen(req, timeout=30, context=None):
            url = req.full_url
            body_str = (req.data or b"").decode("utf-8", errors="ignore")
            if "groups.getById" in url:
                called_methods.append("groups.getById")
                return FakeHTTPResponse(
                    json.dumps({"response": {"groups": [{"id": 760076, "name": "Греем Мотор"}]}}).encode("utf-8")
                )
            if "groups.edit" in url:
                called_methods.append("groups.edit")
                self.assertIn("group_id=760076", body_str)
                self.assertIn("subject=1", body_str)
                return FakeHTTPResponse(json.dumps({"response": 1}).encode("utf-8"))
            if "status.set" in url:
                called_methods.append("status.set")
                return FakeHTTPResponse(json.dumps({"response": 1}).encode("utf-8"))
            if "photos.getOwnerCoverPhotoUploadServer" in url:
                called_methods.append("getOwnerCoverPhotoUploadServer")
                self.assertIn("crop_x2=1920", body_str)
                self.assertIn("crop_y2=768", body_str)
                return FakeHTTPResponse(
                    json.dumps({"response": {"upload_url": "https://pu.vk.ru/cover_upl"}}).encode("utf-8")
                )
            if "pu.vk.ru/cover_upl" in url:
                called_methods.append("upload_cover")
                return FakeHTTPResponse(json.dumps({"hash": "chash", "photo": "cphoto"}).encode("utf-8"))
            if "photos.saveOwnerCoverPhoto" in url:
                called_methods.append("saveOwnerCoverPhoto")
                return FakeHTTPResponse(json.dumps({"response": {"images": []}}).encode("utf-8"))
            if "photos.getOwnerPhotoUploadServer" in url:
                called_methods.append("getOwnerPhotoUploadServer")
                # Критическая проверка: строго отрицательный owner_id группы!
                self.assertIn("owner_id=-760076", body_str)
                return FakeHTTPResponse(
                    json.dumps({"response": {"upload_url": "https://pu.vk.ru/avatar_upl"}}).encode("utf-8")
                )
            if "pu.vk.ru/avatar_upl" in url:
                called_methods.append("upload_avatar")
                self.assertIn("_square_crop", body_str)
                self.assertIn("0,0,1024", body_str)
                return FakeHTTPResponse(
                    json.dumps({"server": 7, "hash": "ahash", "photo": "aphoto"}).encode("utf-8")
                )
            if "photos.saveOwnerPhoto" in url:
                called_methods.append("saveOwnerPhoto")
                return FakeHTTPResponse(json.dumps({"response": {"saved": 1}}).encode("utf-8"))
            if "wall.get" in url:
                called_methods.append("wall.get")
                return FakeHTTPResponse(json.dumps({"response": {"items": []}}).encode("utf-8"))
            if "photos.getWallUploadServer" in url:
                called_methods.append("getWallUploadServer")
                return FakeHTTPResponse(
                    json.dumps({"response": {"upload_url": "https://pu.vk.ru/pin_upl"}}).encode("utf-8")
                )
            if "pu.vk.ru/pin_upl" in url:
                called_methods.append("upload_pin_photo")
                return FakeHTTPResponse(
                    json.dumps({"server": 8, "hash": "phash", "photo": '[{"ok":1}]'}).encode("utf-8")
                )
            if "photos.saveWallPhoto" in url:
                called_methods.append("saveWallPhoto")
                return FakeHTTPResponse(
                    json.dumps({"response": [{"owner_id": -760076, "id": 888}]}).encode("utf-8")
                )
            if "wall.post" in url:
                called_methods.append("wall.post")
                return FakeHTTPResponse(json.dumps({"response": {"post_id": 500}}).encode("utf-8"))
            if "wall.pin" in url:
                called_methods.append("wall.pin")
                self.assertIn("owner_id=-760076", body_str)
                self.assertIn("post_id=500", body_str)
                return FakeHTTPResponse(json.dumps({"response": 1}).encode("utf-8"))
            raise AssertionError(f"Unexpected branding URL: {url}")

        with tempfile.TemporaryDirectory() as tmpdir:
            sum_file = Path(tmpdir) / "branding_summary.md"
            tmp_b_state = Path(tmpdir) / "branding_state.json"
            with patch.dict(
                os.environ,
                {"VK_TOKEN": "admin-tok", "GITHUB_STEP_SUMMARY": str(sum_file)},
                clear=False,
            ), patch("urllib.request.urlopen", side_effect=fake_branding_urlopen):
                rc = setup_group_profile.apply_group_branding(
                    config_path=AGENT_DIR / "config.json",
                    branding_state_path=tmp_b_state,
                    dry_run=False,
                    backfill_photos=False,
                )
                self.assertEqual(rc, 0)
                self.assertIn("saveOwnerCoverPhoto", called_methods)
                self.assertIn("saveOwnerPhoto", called_methods)
                self.assertIn("wall.pin", called_methods)
                self.assertIn("Греем Мотор", sum_file.read_text(encoding="utf-8"))

        # Проверяем переписывание уже выпущенного поста: старые теги и адрес убираются, ставится vk.ru/greem_motor
        old_post = (
            "🚗 Зима близко: в АвтоТочке (vk.ru/autoyar076) разобрали выбор авто.\n\n"
            "Важные советы по выбору зимних машин в России.\n\n"
            "📰 По материалам: Motor.ru\n"
            "#автоточка #автоточка76 #autoyar076 #автосоветы@autoyar076"
        )
        rewritten = ai_writer.rewrite_existing_post_text(
            ai_writer.TemplateFallbackClient(),
            old_post,
            cfg,
            category="tip",
        )
        self.assertIn("«Греем Мотор»", rewritten)
        self.assertIn("vk.ru/greem_motor", rewritten)
        self.assertIn("#грееммотор", rewritten)
        self.assertIn("#greem_motor", rewritten)
        self.assertIn("#автосоветы@greem_motor", rewritten)
        self.assertNotIn("#автоточка", rewritten)
        self.assertNotIn("autoyar076", rewritten)
        self.assertIn("📰 По материалам: Motor.ru", rewritten)

        with patch.dict(os.environ, {"VK_GROUP": "https://vk.ru/autoyar076"}, clear=False):
            self.assertEqual(vk_publisher.resolve_configured_vk_group(cfg), ("greem_motor", 85500918))

        self.assertEqual(
            setup_group_profile.extract_existing_photo_attachment(
                {"attachments": [{"type": "photo", "photo": {"owner_id": -85500918, "id": 457239099}}]}
            ),
            "photo-85500918_457239099",
        )


if __name__ == "__main__":
    unittest.main()
