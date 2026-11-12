"""ИИ-редактор постов для ВКонтакте (GigaChat, YandexGPT, OpenAI-совместимые API)."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import ssl
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any

from news_fetcher import NewsCandidate

logger = logging.getLogger("vk_news_agent.ai")

META_PREAMBLE_RE = re.compile(
    r"^(вот\s+(готовый\s+|исправленный\s+|отредактированный\s+)?(пост|текст|вариант)[^\n]*:\s*"
    r"|конечно[,!]?\s*вот\s+вариант[^\n]*:\s*"
    r"|готовый\s+текст\s+поста[^\n]*:\s*"
    r"|исправленный\s+(текст|вариант|пост)[^\n]*:\s*"
    r"|отредактированный\s+(текст|вариант|пост)[^\n]*:\s*)",
    re.IGNORECASE,
)
GIGACHAT_IMG_TAG_RE = re.compile(r'<img\b[^>]*?\bsrc=["\']([A-Za-z0-9_-]{8,128})["\'][^>]*>', re.IGNORECASE)

MONTH_SEASON_HINT = {
    1: "январь, разгар зимы, морозы, реагенты и снежная каша на дорогах",
    2: "февраль, морозная зима, перепады температур и колейность",
    3: "март, ранняя весна, таяние снега, ямы на асфальте после зимы",
    4: "апрель, весенний сезон шиномонтажа, мойка кузова и радиаторов после зимы",
    5: "май, начало сезона дальних поездок и дачных трасс",
    6: "июнь, лето, жара, нагрузка на систему охлаждения и кондиционер",
    7: "июль, разгар лета, отпуска и дальние поездки по трассе",
    8: "август, конец лета, трассовые пробеги и подготовка авто к осени",
    9: "сентябрь, дождливая осень, сокращение светового дня, проверка света и щёток",
    10: "октябрь, похолодание, первые заморозки утром и подготовка к шиномонтажу",
    11: "ноябрь, предзимье в Ярославской области, гололёд, слякоть, реагенты и первые минусы",
    12: "декабрь, зима, снегопады, морозы и короткие городские поездки с прогревами",
}


class AIProviderError(RuntimeError):
    """Ошибка обращения к API нейросети."""


def _http_post_json(
    url: str,
    headers: dict[str, str],
    body_bytes: bytes,
    ssl_ctx: ssl.SSLContext | None = None,
    timeout: int = 45,
    retries: int = 2,
) -> dict[str, Any]:
    """Отправляет POST-запрос с повторами при временных ошибках 429 / 5xx."""
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            err_body = ""
            try:
                err_body = exc.read().decode("utf-8", errors="replace")[:500]
            except OSError:
                pass
            last_err = AIProviderError(f"HTTP {exc.code} при обращении к {url}: {err_body}")
            if exc.code in {429, 500, 502, 503, 504} and attempt < retries:
                wait_sec = 2.5 * (attempt + 1)
                logger.warning("Временный ответ %d от ИИ API, повтор через %.1f с...", exc.code, wait_sec)
                time.sleep(wait_sec)
                continue
            raise last_err from exc
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last_err = AIProviderError(f"Ошибка сети/JSON при обращении к {url}: {exc}")
            if attempt < retries:
                time.sleep(2.0 * (attempt + 1))
                continue
            raise last_err from exc

    raise last_err or AIProviderError("Неизвестная ошибка запроса к ИИ API")


def inspect_gigachat_key_shape(raw_key: str) -> dict[str, Any]:
    """Возвращает ТОЛЬКО безопасные структурные метаданные ключа (без единого символа самого ключа)."""
    s = raw_key or ""
    stripped = s.strip().strip("\"'`").strip()
    lower = stripped.lower()
    had_auth_header = lower.startswith("authorization:")
    if had_auth_header:
        stripped = stripped.split(":", 1)[1].strip().strip("\"'`").strip()
        lower = stripped.lower()
    had_basic = lower.startswith("basic ")
    had_bearer = lower.startswith("bearer ")
    if had_basic:
        stripped = stripped[6:].strip()
    elif had_bearer:
        stripped = stripped[7:].strip()

    is_uuid = bool(re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", stripped))
    is_colon_pair = bool(":" in stripped and " " not in stripped)
    b64_ok = False
    b64_decoded_len = 0
    b64_has_colon = False
    try:
        compact = re.sub(r"\s+", "", stripped)
        padded = compact + ("=" * ((4 - len(compact) % 4) % 4))
        dec = base64.b64decode(padded, validate=True)
        b64_ok = True
        b64_decoded_len = len(dec)
        dec_txt = dec.decode("utf-8", errors="ignore")
        b64_has_colon = ":" in dec_txt
    except Exception:  # noqa: BLE001
        pass

    return {
        "raw_len": len(s),
        "cleaned_len": len(stripped),
        "had_auth_header": had_auth_header,
        "had_basic_prefix": had_basic,
        "had_bearer_prefix": had_bearer,
        "is_single_uuid": is_uuid,
        "is_raw_colon_pair": is_colon_pair,
        "is_valid_base64": b64_ok,
        "base64_decoded_len": b64_decoded_len,
        "base64_decoded_has_colon": b64_has_colon,
        "looks_like_jwt": stripped.count(".") == 2 and len(stripped) > 80,
    }


def normalize_gigachat_auth_key(raw_key: str) -> str:
    """Принимает Basic-токен, Authorization: Basic ..., готовый Base64 ключ или client_id:client_secret."""
    cleaned = (raw_key or "").strip().strip("\"'`").strip()
    if cleaned.lower().startswith("authorization:"):
        cleaned = cleaned.split(":", 1)[1].strip().strip("\"'`").strip()
    if cleaned.lower().startswith("basic "):
        cleaned = cleaned[6:].strip().strip("\"'`").strip()
    elif cleaned.lower().startswith("bearer "):
        cleaned = cleaned[7:].strip().strip("\"'`").strip()
    if ":" in cleaned and " " not in cleaned and not cleaned.lower().startswith("http"):
        return base64.b64encode(cleaned.encode("utf-8")).decode("ascii")
    return re.sub(r"\s+", "", cleaned)


class GigaChatClient:
    """Клиент официального REST API Сбер GigaChat (OAuth 2.0 + chat/completions)."""

    name = "GigaChat"

    def __init__(self, auth_key: str, cfg: dict[str, Any]) -> None:
        self.raw_key = auth_key
        self.auth_key = normalize_gigachat_auth_key(auth_key)
        self.scope = os.getenv("GIGACHAT_SCOPE") or str(cfg.get("scope", "GIGACHAT_API_PERS"))
        self.model = os.getenv("GIGACHAT_MODEL") or str(cfg.get("model", "GigaChat-2"))
        self.image_model = os.getenv("GIGACHAT_IMAGE_MODEL") or str(cfg.get("image_model", "GigaChat"))
        self._active_text_model: str = ""
        self.oauth_url = os.getenv("GIGACHAT_OAUTH_URL") or str(
            cfg.get("oauth_url", "https://ngw.devices.sberbank.ru:9443/api/v2/oauth")
        )
        self.chat_urls = [
            u
            for u in [
                os.getenv("GIGACHAT_CHAT_URL") or "",
                str(cfg.get("chat_url", "")),
                "https://gigachat.devices.sberbank.ru/api/v1/chat/completions",
                "https://api.giga.chat/v1/chat/completions",
            ]
            if u
        ]
        # Убираем дубли с сохранением порядка
        self.chat_urls = list(dict.fromkeys(self.chat_urls))
        self.ssl_ctx = self._build_ssl_context(cfg)
        self._access_token: str = ""
        self._token_expires_at: float = 0.0

    @staticmethod
    def _build_ssl_context(cfg: dict[str, Any]) -> ssl.SSLContext:
        ca_bundle = (os.getenv("GIGACHAT_CA_BUNDLE") or "").strip()
        if ca_bundle and os.path.exists(ca_bundle):
            return ssl.create_default_context(cafile=ca_bundle)

        env_verify = (os.getenv("GIGACHAT_VERIFY_SSL") or "").strip().lower()
        if env_verify in {"1", "true", "yes"}:
            return ssl.create_default_context()
        if env_verify in {"0", "false", "no"}:
            return ssl._create_unverified_context()

        if cfg.get("verify_ssl", False):
            return ssl.create_default_context()
        # На облачных Ubuntu-раннерах GitHub Actions нет корневого сертификата НУЦ Минцифры
        return ssl._create_unverified_context()

    def _get_access_token(self) -> str:
        now = time.time()
        if self._access_token and now < self._token_expires_at - 60:
            return self._access_token

        # Если пользователь передал уже готовый JWT access_token
        shape = inspect_gigachat_key_shape(self.raw_key)
        if shape["looks_like_jwt"]:
            self._access_token = self.auth_key
            self._token_expires_at = now + 900.0
            return self._access_token

        scopes_to_try = list(
            dict.fromkeys([self.scope, "GIGACHAT_API_PERS", "GIGACHAT_API_B2B", "GIGACHAT_API_CORP"])
        )
        last_err: Exception | None = None
        data: dict[str, Any] = {}
        for sc in scopes_to_try:
            body = f"scope={sc}".encode("utf-8")
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "RqUID": str(uuid.uuid4()),
                "Authorization": f"Basic {self.auth_key}",
            }
            try:
                data = _http_post_json(self.oauth_url, headers, body, ssl_ctx=self.ssl_ctx, timeout=30, retries=1)
                self.scope = sc
                last_err = None
                break
            except AIProviderError as exc:
                last_err = exc
                if "header is incorrect" in str(exc):
                    break
                continue
        if last_err is not None:
            if shape["is_single_uuid"]:
                raise AIProviderError(
                    "В секрете GIGACHAT_AUTH_KEY указан 36-символьный Client ID проекта вместо ключа авторизации "
                    "(Authorization key, ~100 символов Base64). В Sber Studio → Настройки API нажмите "
                    "«Получить новый ключ» и вставьте длинный Authorization key в GIGACHAT_AUTH_KEY."
                ) from last_err
            raise last_err

        token = str(data.get("access_token", "")).strip()
        if not token:
            raise AIProviderError(f"GigaChat OAuth не вернул access_token: {data}")

        expires_at_ms = data.get("expires_at")
        if isinstance(expires_at_ms, (int, float)) and expires_at_ms > 1_000_000_000_000:
            self._token_expires_at = float(expires_at_ms) / 1000.0
        else:
            self._token_expires_at = now + 1700.0
        self._access_token = token
        return token

    def generate(self, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int) -> str:
        token = self._get_access_token()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        model_candidates = (
            [self._active_text_model]
            if self._active_text_model
            else list(dict.fromkeys([self.model, "GigaChat-2", "GigaChat"]))
        )
        last_err: Exception | None = None
        data: dict[str, Any] = {}
        for mdl in model_candidates:
            payload = {
                "model": mdl,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            }
            body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            for chat_url in self.chat_urls:
                try:
                    data = _http_post_json(
                        chat_url,
                        headers,
                        body_bytes,
                        ssl_ctx=self.ssl_ctx,
                        timeout=55,
                        retries=2,
                    )
                    self._active_text_model = mdl
                    last_err = None
                    break
                except AIProviderError as exc:
                    last_err = exc
                    continue
            if last_err is None:
                break
        if last_err is not None:
            raise last_err
        choices = data.get("choices") or []
        if not choices:
            raise AIProviderError(f"Пустой ответ GigaChat: {data}")
        content = str(choices[0].get("message", {}).get("content", "")).strip()
        if not content:
            raise AIProviderError("GigaChat вернул пустую строку текста")
        return content

    def _extract_client_id(self) -> str:
        """Извлекает UUID client_id из Base64-ключа (для заголовка X-Client-ID при скачивании файлов)."""
        try:
            padded = self.auth_key + ("=" * ((4 - len(self.auth_key) % 4) % 4))
            decoded = base64.b64decode(padded).decode("utf-8", errors="ignore")
            if ":" in decoded:
                cid = decoded.split(":", 1)[0].strip()
                if re.fullmatch(r"[0-9a-fA-F-]{32,36}", cid):
                    return cid
        except Exception:  # noqa: BLE001
            pass
        return ""

    def _download_file_bytes(
        self,
        api_base: str,
        file_id: str,
        token: str,
        x_client_id: str = "",
    ) -> bytes:
        """Скачивает сгенерированное GigaChat изображение через GET /files/{file_id}/content.

        По документации Сбера заголовок X-Client-ID при скачивании должен ТОЧНО совпадать
        с тем, передавался ли X-Client-ID в запросе генерации /chat/completions (иначе HTTP 403).
        """
        file_url = f"{api_base.rstrip('/')}/files/{file_id}/content"
        header_variants: list[dict[str, str]] = [
            {
                "Accept": "application/jpg",
                "Authorization": f"Bearer {token}",
                **({"X-Client-ID": x_client_id} if x_client_id else {}),
            }
        ]
        fallback_cid = self._extract_client_id()
        if not x_client_id and fallback_cid:
            header_variants.append(
                {
                    "Accept": "application/jpg",
                    "Authorization": f"Bearer {token}",
                    "X-Client-ID": fallback_cid,
                }
            )

        last_err: Exception | None = None
        for hdrs in header_variants:
            for attempt in range(2):
                req = urllib.request.Request(file_url, headers=hdrs, method="GET")
                try:
                    with urllib.request.urlopen(req, timeout=60, context=self.ssl_ctx) as resp:
                        raw = resp.read()
                    if raw.startswith((b"\xff\xd8", b"\x89PNG", b"RIFF")):
                        return raw
                    try:
                        as_json = json.loads(raw.decode("utf-8", errors="ignore"))
                        if isinstance(as_json, dict) and as_json.get("content"):
                            return base64.b64decode(str(as_json["content"]))
                    except Exception:  # noqa: BLE001
                        pass
                    if len(raw) > 2048:
                        return raw
                    raise AIProviderError(f"Слишком маленький ответ файла GigaChat ({len(raw)} байт)")
                except urllib.error.HTTPError as exc:
                    last_err = exc
                    if exc.code == 403:
                        break  # пробуем следующий вариант заголовка X-Client-ID
                    if attempt < 1:
                        time.sleep(2.0)
                        continue
                except (urllib.error.URLError, TimeoutError, OSError) as exc:
                    last_err = exc
                    if attempt < 1:
                        time.sleep(2.0)
                        continue
        raise AIProviderError(f"Не удалось скачать изображение {file_id} из GigaChat: {last_err}")

    def generate_image_bytes(self, prompt: str, system_prompt: str = "") -> tuple[bytes, str]:
        """Генерирует уникальную иллюстрацию через встроенный text2image (Kandinsky) в GigaChat."""
        token = self._get_access_token()
        clean_prompt = prompt.strip()
        if not clean_prompt.lower().startswith("нарисуй"):
            clean_prompt = f"Нарисуй {clean_prompt}"

        sys_msg = (
            system_prompt.strip()
            or "Ты — профессиональный автомобильный иллюстратор и фотохудожник журнала «Греем Мотор»."
        )

        prompts_to_try = [
            clean_prompt,
            f"Нарисуй фотореалистичную автомобильную иллюстрацию без каких-либо надписей и букв: {prompt.strip()}",
        ]

        last_err: Exception | None = None
        for user_msg in prompts_to_try:
            payload = {
                "model": self.image_model,
                "messages": [
                    {"role": "system", "content": sys_msg},
                    {"role": "user", "content": user_msg},
                ],
                "function_call": "auto",
                "temperature": 0.7,
                "stream": False,
            }
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
            }
            body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

            for chat_url in self.chat_urls:
                try:
                    data = _http_post_json(
                        chat_url,
                        headers,
                        body_bytes,
                        ssl_ctx=self.ssl_ctx,
                        timeout=95,
                        retries=1,
                    )
                    choices = data.get("choices") or []
                    content = str((choices[0].get("message") or {}).get("content", "")) if choices else ""
                    m = GIGACHAT_IMG_TAG_RE.search(content)
                    if not m:
                        last_err = AIProviderError(
                            f"GigaChat не вызвал генерацию картинки (ответ без <img src=...>): {content[:160]}"
                        )
                        break  # переходим ко второй более жёсткой формулировке "Нарисуй..."
                    file_id = m.group(1)
                    api_base = chat_url.rsplit("/chat/completions", 1)[0]
                    img_bytes = self._download_file_bytes(api_base, file_id, token)
                    return img_bytes, file_id
                except AIProviderError as exc:
                    last_err = exc
                    continue

        raise last_err or AIProviderError("Не удалось сгенерировать изображение через GigaChat")


class YandexGPTClient:
    """Клиент Yandex Cloud Foundation Models API (YandexGPT / YandexGPT Lite)."""

    name = "YandexGPT"

    def __init__(self, folder_id: str, api_key: str, iam_token: str, cfg: dict[str, Any]) -> None:
        self.folder_id = folder_id.strip()
        self.api_key = api_key.strip()
        self.iam_token = iam_token.strip()
        self.model = os.getenv("YANDEX_MODEL") or str(cfg.get("model", "yandexgpt/latest"))
        self.url = os.getenv("YANDEX_COMPLETION_URL") or str(
            cfg.get("completion_url", "https://llm.api.cloud.yandex.net/foundationModels/v1/completion")
        )
        self.ssl_ctx = ssl.create_default_context()

    def _auth_header(self) -> str:
        if self.iam_token:
            return f"Bearer {self.iam_token}"
        # Автоопределение, если пользователь случайно положил IAM-токен t1.* в YANDEX_API_KEY
        if self.api_key.startswith("t1."):
            return f"Bearer {self.api_key}"
        return f"Api-Key {self.api_key}"

    def _model_uri(self) -> str:
        if self.model.startswith("gpt://"):
            return self.model
        return f"gpt://{self.folder_id}/{self.model.lstrip('/')}"

    def generate(self, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int) -> str:
        payload = {
            "modelUri": self._model_uri(),
            "completionOptions": {
                "stream": False,
                "temperature": temperature,
                "maxTokens": str(max_tokens),
            },
            "messages": [
                {"role": "system", "text": system_prompt},
                {"role": "user", "text": user_prompt},
            ],
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": self._auth_header(),
            "x-folder-id": self.folder_id,
        }
        data = _http_post_json(
            self.url,
            headers,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            ssl_ctx=self.ssl_ctx,
            timeout=50,
        )
        alternatives = data.get("result", {}).get("alternatives") or []
        if not alternatives:
            raise AIProviderError(f"Пустой ответ YandexGPT: {data}")
        text = str(alternatives[0].get("message", {}).get("text", "")).strip()
        if not text:
            raise AIProviderError("YandexGPT вернул пустую строку текста")
        return text


class OpenAICompatibleClient:
    """Резервный клиент для любого OpenAI-совместимого API (DeepSeek, OpenRouter, Groq и др.)."""

    name = "OpenAI-Compatible"

    def __init__(self, api_key: str, cfg: dict[str, Any]) -> None:
        self.api_key = api_key.strip()
        base = (os.getenv("OPENAI_BASE_URL") or str(cfg.get("base_url", "https://api.deepseek.com/v1"))).rstrip("/")
        self.chat_url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
        self.model = os.getenv("OPENAI_MODEL") or str(cfg.get("model", "deepseek-chat"))
        self.ssl_ctx = ssl.create_default_context()

    def generate(self, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        data = _http_post_json(
            self.chat_url,
            headers,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            ssl_ctx=self.ssl_ctx,
            timeout=50,
        )
        choices = data.get("choices") or []
        if not choices:
            raise AIProviderError(f"Пустой ответ OpenAI-совместимого API: {data}")
        content = str(choices[0].get("message", {}).get("content", "")).strip()
        if not content:
            raise AIProviderError("OpenAI-совместимый API вернул пустую строку")
        return content


class TemplateFallbackClient:
    """Умный локальный редактор-саммаризатор (работает даже если ключи GigaChat/YandexGPT ещё не добавлены)."""

    name = "BuiltIn-SmartEditor"

    @staticmethod
    def _pick_emoji_and_question(category: str, title: str, body: str) -> tuple[str, str, str]:
        lower = f"{title} {body}".lower().replace("ё", "е")
        if category == "local_76" or "ярослав" in lower:
            return (
                "🛣",
                "Учитывайте актуальную дорожную обстановку при планировании маршрута по городу и области.",
                "Водители Ярославля, уже заметили изменения на этом участке? Делитесь обстановкой в комментариях 👇",
            )
        if any(w in lower for w in ("штраф", "пдд", "гибдд", "гаи", "осаго", "закон", "правов", "лишен")):
            return (
                "⚡",
                "Проверьте актуальность документов и будьте внимательны к дорожной разметке и знакам.",
                "Как считаете, такие меры реально помогут на дорогах или только добавят хлопот водителям? Пишите мнение 👇",
            )
        if category == "tip" or any(w in lower for w in ("масло", "мотор", "двигател", "акпп", "вариатор", "ремонт", "шины", "аккумулятор", "пробег")):
            return (
                "🔧",
                "Своевременная профилактика обходится в разы дешевле капитального ремонта узлов.",
                "А как вы обслуживаете этот узел на своей машине? Поделитесь проверенным опытом в комментариях 👇",
            )
        if any(w in lower for w in ("цен", "рубл", "млн", "продаж", "рынок", "дилер", "купит", "кроссовер", "седан", "внедорожник")):
            return (
                "🚗",
                "Перед покупкой автомобиля обязательно сравнивайте реальные условия дилеров и стоимость содержания.",
                "Как вам такая модель и ценник в нынешних реалиях авторынка? Взяли бы себе — обсудим в комментариях 👇",
            )
        return (
            "🚗",
            "Следите за техническим состоянием автомобиля и главными событиями авторынка вместе с «Греем Мотор».",
            "А что вы думаете по этому поводу? Делитесь мнением в комментариях 👇",
        )

    def generate(self, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int) -> str:
        title = ""
        category = "news"
        raw_fact_lines: list[str] = []
        is_evergreen = "ТЕМА РАЗБОРА:" in user_prompt or "ТЕМА СОВЕТА:" in user_prompt
        capture = False

        for raw_ln in user_prompt.splitlines():
            ln = raw_ln.strip()
            if not ln:
                if capture and raw_fact_lines and raw_fact_lines[-1] != "":
                    raw_fact_lines.append("")
                continue
            if ln.startswith("КАТЕГОРИЯ:"):
                category = ln.split(":", 1)[1].strip()
            elif ln.startswith(("ЗАГОЛОВОК:", "ЗАГОЛОВОК ИСТОЧНИКА:", "ТЕМА РАЗБОРА:", "ТЕМА СОВЕТА:")):
                title = ln.split(":", 1)[1].strip()
            elif ln.startswith(("ФАКТУРА НОВОСТИ:", "ТЕКСТ НОВОСТИ:", "КЛЮЧЕВЫЕ ТЕЗИСЫ:", "О ЧЁМ РАССКАЗАТЬ:")):
                capture = True
            elif ln.startswith(("ТРЕБОВАНИЯ К ПОСТУ:", "Напиши короткий,", "Напиши понятный")):
                capture = False
            elif capture:
                raw_fact_lines.append(ln)

        # Собираем чистые абзацы из фактуры статьи без обрыва на полуслове
        paragraphs: list[str] = []
        buf: list[str] = []
        for ln in raw_fact_lines:
            if ln == "":
                if buf:
                    paragraphs.append(" ".join(buf))
                    buf = []
            else:
                buf.append(ln)
        if buf:
            paragraphs.append(" ".join(buf))

        selected_paras: list[str] = []
        total_chars = 0
        for p in paragraphs:
            p_clean = p.strip()
            if len(p_clean) < 35:
                continue
            # Избегаем дублирования заголовка слово в слово
            if title and p_clean.lower().startswith(title.lower()[:35]) and len(p_clean) < len(title) + 25:
                continue
            if total_chars + len(p_clean) > 1050 and selected_paras:
                break
            selected_paras.append(p_clean)
            total_chars += len(p_clean)
            if len(selected_paras) >= 3:
                break

        if not selected_paras and paragraphs:
            selected_paras = [paragraphs[0][:900]]

        body = "\n\n".join(selected_paras).strip()
        if is_evergreen:
            category = "tip"
        emoji, tip_line, question_line = self._pick_emoji_and_question(category, title, body)
        headline = title or "Главное для автомобилистов"

        if is_evergreen:
            return (
                f"{emoji} {headline}\n\n"
                f"Разбираем важную тему для каждого автовладельца:\n"
                f"{body}\n\n"
                f"💡 Практический вывод: {tip_line}\n\n"
                f"{question_line}"
            )

        return (
            f"{emoji} {headline}\n\n"
            f"{body}\n\n"
            f"💡 Что важно учесть водителю: {tip_line}\n\n"
            f"{question_line}"
        )


def create_ai_client(config: dict[str, Any], allow_template_fallback: bool = False) -> Any:
    """Создаёт подходящий клиент нейросети на основе секретов окружения и config.json."""
    ai_cfg = config.get("ai", {})
    requested = (os.getenv("AI_PROVIDER") or str(ai_cfg.get("provider", "auto"))).strip().lower()

    giga_key = (
        os.getenv("GIGACHAT_AUTH_KEY")
        or os.getenv("GIGACHAT_CREDENTIALS")
        or os.getenv("GIGACHAT_KEY")
        or os.getenv("GIGACHAT_API_KEY")
        or os.getenv("SBER_API_KEY")
        or ""
    ).strip()
    giga_client_id = (os.getenv("GIGACHAT_CLIENT_ID") or "").strip()
    giga_client_secret = (os.getenv("GIGACHAT_CLIENT_SECRET") or os.getenv("GIGACHAT_SECRET") or "").strip()
    if giga_client_secret:
        if giga_client_id:
            giga_key = f"{giga_client_id}:{giga_client_secret}"
        elif giga_key and inspect_gigachat_key_shape(giga_key)["is_single_uuid"]:
            giga_key = f"{giga_key}:{giga_client_secret}"

    yandex_folder = (os.getenv("YANDEX_FOLDER_ID") or "").strip()
    yandex_key = (os.getenv("YANDEX_API_KEY") or os.getenv("YANDEX_GPT_API_KEY") or "").strip()
    yandex_iam = (os.getenv("YANDEX_IAM_TOKEN") or "").strip()

    openai_key = (os.getenv("OPENAI_API_KEY") or "").strip()

    if requested == "gigachat":
        if not giga_key:
            raise AIProviderError("Выбран провайдер gigachat, но не задан секрет GIGACHAT_AUTH_KEY")
        return GigaChatClient(giga_key, ai_cfg.get("gigachat", {}))

    if requested == "yandexgpt":
        if not yandex_folder or not (yandex_key or yandex_iam):
            raise AIProviderError(
                "Выбран провайдер yandexgpt, но не заданы YANDEX_FOLDER_ID и YANDEX_API_KEY"
            )
        return YandexGPTClient(yandex_folder, yandex_key, yandex_iam, ai_cfg.get("yandexgpt", {}))

    if requested == "openai":
        if not openai_key:
            raise AIProviderError("Выбран провайдер openai, но не задан секрет OPENAI_API_KEY")
        return OpenAICompatibleClient(openai_key, ai_cfg.get("openai_compatible", {}))

    if requested == "template":
        return TemplateFallbackClient()

    # Режим auto: приоритет российским нейросетям (GigaChat -> YandexGPT -> OpenAI -> Template)
    if giga_key:
        return GigaChatClient(giga_key, ai_cfg.get("gigachat", {}))
    if yandex_folder and (yandex_key or yandex_iam):
        return YandexGPTClient(yandex_folder, yandex_key, yandex_iam, ai_cfg.get("yandexgpt", {}))
    if openai_key:
        return OpenAICompatibleClient(openai_key, ai_cfg.get("openai_compatible", {}))
    if allow_template_fallback:
        logger.warning("Ключи ИИ не найдены — используется встроенный шаблон предпросмотра")
        return TemplateFallbackClient()

    raise AIProviderError(
        "Не найден ключ нейросети! Добавьте в GitHub Secrets либо GIGACHAT_AUTH_KEY (Сбер GigaChat), "
        "либо пару YANDEX_API_KEY + YANDEX_FOLDER_ID (YandexGPT)."
    )


UPPERCASE_ACRONYMS = {
    "ПДД", "ГИБДД", "ГАИ", "ОСАГО", "КАСКО", "АЗС", "ДТП", "АКПП", "МКПП",
    "КПП", "ВАЗ", "УАЗ", "ГАЗ", "КАМАЗ", "АВТОВАЗ", "РФ", "КНР", "СССР",
    "НДС", "ФНС", "ЦБ", "МВД", "МЧС", "ТО", "СТО", "ЖКХ", "ТЭЦ", "АКБ", "ДВС",
}

YAROSLAVL_GEO_MARKERS = (
    "ярослав",
    "рыбинск",
    "тутаев",
    "переславл",
    "ростов велик",
    "углич",
    "мышкин",
    "данилов",
    "гаврилов-ям",
    "пошехонь",
    "любим",
    "некоуз",
    "брейтов",
    "борисоглебск",
    "76 регион",
    "76-й регион",
    "76-м регион",
    "брагино",
    "заволжск",
    "нефтестро",
    "суздалк",
    "дядьков",
    "липовая гора",
    "которосл",
)

FLUFF_SUBHEADER_RE = re.compile(
    r"^(?:[^\wа-яА-ЯёЁ]{0,6}\s*)?(?:"
    r"Вопрос\s+[^:\n]{0,40}|"
    r"Делимся\s+опытом|"
    r"Ключевые\s+(?:находки|моменты|выводы)|"
    r"Но\s+не\s+всё\s+так\s+однозначно|"
    r"Советы\s+бывалым|"
    r"Что\s+(?:ещ[её]\s+)?(?:важно|стоит)\s+уч[еи]тывать[^:\n]{0,30}|"
    r"Что\s+важно\s+учесть\s+водителю|"
    r"На\s+заметку\s+водителю"
    r")[.:…!]*\s*$",
    flags=re.IGNORECASE,
)


def humanize_technical_jargon(text: str, *, aggressive_source_mode: bool = False) -> str:
    """Переводит сухие лабораторные коды и ГОСТ-обозначения на простой язык обычных водителей."""
    out = text
    # Марки топлива: АИ-95-К5 -> АИ-95, АИ-92-К4 -> АИ-92, ДТ-З-К5 -> зимний дизель
    out = re.sub(r"\bАИ-(\d{2,3})-К[2-5]\b", r"АИ-\1", out, flags=re.IGNORECASE)
    out = re.sub(r"\bДТ-З-К[2-5]\b", "зимний дизель", out, flags=re.IGNORECASE)
    out = re.sub(r"\bДТ-[АЛЕ]-К[2-5]\b", "дизельное топливо", out, flags=re.IGNORECASE)
    # Убираем скобочные лабораторные уточнения вида «(169 мг/кг)» или «(К2)»
    out = re.sub(r"\s*\(\s*(?:до\s+)?\d+[.,]?\d*\s*мг/кг[^)]*\)", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\s*\(\s*(?:класс\s*)?К[2-5]\s*\)", "", out, flags=re.IGNORECASE)

    if aggressive_source_mode:
        def _replace_mg_kg(m: re.Match[str]) -> str:
            raw_num = m.group(1).replace(",", ".")
            try:
                val = float(raw_num)
            except ValueError:
                return "отклонение по сере"
            if val >= 100:
                return "огромное превышение нормы по сере (в десятки раз выше допустимого)"
            if val >= 20:
                return "серьёзное превышение допустимой нормы серы"
            return "чистый показатель серы в пределах строгой нормы"

        out = re.sub(r"(\d+[.,]?\d*)\s*мг/кг(?:\s+серы)?", _replace_mg_kg, out, flags=re.IGNORECASE)
        out = re.sub(
            r"(?:экологическ[а-яё]+\s+)?класс[а-яё]*\s+К[23]\b",
            "устаревшего низкого качества",
            out,
            flags=re.IGNORECASE,
        )
        out = re.sub(
            r"(?:экологическ[а-яё]+\s+)?класс[а-яё]*\s+К[45]\b",
            "современного стандарта качества",
            out,
            flags=re.IGNORECASE,
        )
        out = re.sub(r"\bК[23]\b", "низкого качества", out)
        out = re.sub(r"\bК[45]\b", "высокого стандарта", out)
        out = re.sub(r"\bМИЦ\s+ГСМ\b", "независимая лаборатория", out, flags=re.IGNORECASE)
        out = re.sub(r"\bвегетативной\s+нервной\s+системы\s*\(ВНС\)", "нервной системы", out, flags=re.IGNORECASE)
        out = re.sub(r"\bВНС\b", "нервной системы", out)

    return out


def _normalize_screaming_headline(line: str) -> str:
    """Превращает кричащий ЗАГОЛОВОК КАПСОМ в спокойный человеческий регистр с сохранением аббревиатур."""
    cyr_letters = [ch for ch in line if "а" <= ch.lower() <= "я" or ch.lower() == "ё"]
    if len(cyr_letters) < 6:
        return line
    upper_count = sum(1 for ch in cyr_letters if ch.isupper())
    if upper_count / len(cyr_letters) < 0.65:
        return line

    def _fix_word(m: re.Match[str]) -> str:
        w = m.group(0)
        if w.upper() in UPPERCASE_ACRONYMS:
            return w.upper()
        return w.lower()

    lowered = re.sub(r"[А-ЯЁа-яё]+", _fix_word, line)
    # Делаем заглавной первую букву заголовка и первую букву после «: », «! », «? », «. »
    chars = list(lowered)
    cap_next = True
    for idx, ch in enumerate(chars):
        if cap_next and ch.isalpha():
            chars[idx] = ch.upper()
            cap_next = False
        elif ch in ".!?":
            cap_next = True
    res = "".join(chars)
    res = re.sub(r"\bярослав([а-яё]*)\b", lambda m: "Ярослав" + m.group(1), res)
    res = re.sub(r"\bросси([а-яё]*)\b", lambda m: "Росси" + m.group(1), res)
    return res


def strip_vk_footer_lines(text: str) -> str:
    """Возвращает чистый текст поста без служебного подвала, ссылки на источник и хештегов."""
    clean_lines: list[str] = []
    for raw_ln in (text or "").splitlines():
        s = raw_ln.strip()
        if s.startswith(("📰 По материалам:", "🔥 Греем Мотор —", "Подробнее:")):
            continue
        if s.startswith("#") and all(tok.startswith("#") for tok in s.split()):
            continue
        clean_lines.append(raw_ln)
    return "\n".join(clean_lines).strip()


def is_yaroslavl_topic(title: str, text: str = "", category: str = "") -> bool:
    """Определяет, касается ли новость Ярославля или Ярославской области (76 региона)."""
    if category == "local_76":
        return True
    body_no_footer = strip_vk_footer_lines(text)
    paragraphs = [p.strip() for p in body_no_footer.split("\n\n") if p.strip()]
    # Исключаем последний абзац-вопрос подписчикам, чтобы федеральная новость с вопросом
    # «А где заправляетесь вы в Ярославле?» не считалась городской ярославской новостью
    core_paragraphs = paragraphs[:-1] if len(paragraphs) >= 2 and "?" in paragraphs[-1] else paragraphs
    haystack = f"{title}\n" + "\n".join(core_paragraphs)
    haystack_norm = haystack.lower().replace("ё", "е")
    return any(marker in haystack_norm for marker in YAROSLAVL_GEO_MARKERS)


def clean_markdown_for_vk(raw_text: str) -> str:
    """Удаляет Markdown-разметку, КАПСЛОК и канцелярские подзаголовки для живой подачи ВКонтакте."""
    text = raw_text.strip()
    # Убираем обрамляющие кавычки или кодовые блоки, если модель обернула ответ
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = META_PREAMBLE_RE.sub("", text).strip()
    if len(text) > 20 and text.startswith('"') and text.endswith('"'):
        text = text[1:-1].strip()

    # Убираем возможные HTML-теги <img> от GigaChat, если вдруг попали в текст
    text = re.sub(r"<img\b[^>]*>", "", text, flags=re.IGNORECASE)
    # Markdown-ссылки [анкор](url) -> анкор
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1", text)
    # Заголовки ### Текст -> Текст
    text = re.sub(r"(?m)^#{1,6}\s+", "", text)
    # Жирный / курсив **текст**, __текст__, *текст* (внутри слов не трогаем)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"(?m)^\*\s+", "• ", text)
    text = re.sub(r"(?m)^-\s+", "• ", text)
    text = re.sub(r"(?m)^[✅✔☑️]\s*", "• ", text)
    text = text.replace("**", "").replace("__", "")
    # Чистим случайно склеенные/неуместные эмодзи от LLM (например, обезьяну 🦧, попугая 🦜 или рассыпавшийся «👨⚕️»)
    text = re.sub(r"[🦜🦧🦍🐒🧟🧞🧜🧚]+", "", text)
    text = re.sub(r"[👨👩]\u200d?⚕\ufe0f?", "", text)
    text = humanize_technical_jargon(text, aggressive_source_mode=False)
    # Аккуратная чистка задвоенных пробелов и случайной двойной пунктуации ("..", ",,")
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"(?<!\.)\.\.(?!\.)", ".", text)
    text = re.sub(r",,+", ",", text)

    # Вычищаем случайные хештеги и лишние казённые подзаголовки-разделители
    raw_lines = [ln.rstrip() for ln in text.splitlines()]
    while raw_lines and re.fullmatch(r"(?:\s*#[a-zA-Zа-яА-ЯёЁ0-9_@]+)+\s*", raw_lines[-1]):
        raw_lines.pop()

    lines: list[str] = []
    first_nonempty_done = False
    for ln in raw_lines:
        stripped = ln.strip()
        if stripped and not first_nonempty_done:
            ln = _normalize_screaming_headline(stripped)
            first_nonempty_done = True
        elif stripped and FLUFF_SUBHEADER_RE.match(stripped):
            continue
        lines.append(ln)

    cleaned = "\n".join(lines).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def build_hashtags(config: dict[str, Any], category: str) -> str:
    """Формирует аккуратную строку хештегов сообщества greem_motor без повторов."""
    posting_cfg = config.get("posting", {})
    group_cfg = config.get("group", {})
    screen_name = str(group_cfg.get("screen_name") or "greem_motor").strip().lstrip("@") or "greem_motor"

    base_tags = posting_cfg.get(
        "base_hashtags",
        ["#грееммотор", "#грееммотор76", f"#{screen_name}", "#автоярославль", "#авто76"],
    )
    cat_map = posting_cfg.get("category_hashtags", {})
    extra_tags = cat_map.get(category, []) if isinstance(cat_map, dict) else []

    seen: set[str] = set()
    ordered: list[str] = []
    for raw_tag in [*base_tags, *extra_tags]:
        tag = str(raw_tag).strip()
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = f"#{tag}"
        tag_norm = re.sub(r"[^#@a-zA-Zа-яА-ЯёЁ0-9_]", "", tag.lower())
        tag_norm = tag_norm.replace("@autoyar076", f"@{screen_name.lower()}")
        if tag_norm == "#autoyar076":
            tag_norm = f"#{screen_name.lower()}"
        if len(tag_norm) <= 1 or tag_norm in seen:
            continue
        seen.add(tag_norm)
        ordered.append(tag_norm)

    return " ".join(ordered)


def finalize_vk_post(
    ai_text: str,
    config: dict[str, Any],
    *,
    category: str,
    source_name: str = "",
    source_url: str = "",
) -> str:
    """Финальная сборка и проверка поста перед отправкой на стену ВК."""
    posting_cfg = config.get("posting", {})
    group_cfg = config.get("group", {})
    group_title = str(group_cfg.get("title") or "Греем Мотор").strip()
    screen_name = str(group_cfg.get("screen_name") or "greem_motor").strip().lstrip("@") or "greem_motor"
    group_short_link = f"vk.ru/{screen_name}"

    body = clean_markdown_for_vk(ai_text)
    # Автоматическая замена прежнего короткого адреса группы, если он встретился в тексте
    body = re.sub(r"(?:https?://)?(?:m\.)?vk\.(?:ru|com)/autoyar076\b", group_short_link, body, flags=re.IGNORECASE)
    body = re.sub(r"@autoyar076\b", f"@{screen_name}", body, flags=re.IGNORECASE)

    # Ограничиваем длину основного текста, чтобы пост легко читался в ленте ВК
    if len(body) > 3300:
        body = body[:3290].rsplit("\n", 1)[0].strip()

    footer_lines: list[str] = []
    if (
        source_name
        and posting_cfg.get("mention_source_name", True)
        and source_name.lower() not in body.lower()
    ):
        footer_lines.append(f"📰 По материалам: {source_name}")

    if source_url and posting_cfg.get("include_external_url_in_text", False):
        footer_lines.append(f"Подробнее: {source_url}")

    if posting_cfg.get("include_group_signature", True) and group_short_link.lower() not in body.lower():
        footer_lines.append(f"🔥 {group_title} — подписывайтесь: {group_short_link}")

    hashtags = build_hashtags(config, category)
    if hashtags:
        footer_lines.append(hashtags)

    if footer_lines:
        return f"{body}\n\n" + "\n".join(footer_lines)
    return body


def select_best_candidate_with_ai(
    client: Any,
    candidates: list[NewsCandidate],
    config: dict[str, Any],
    preferred_mode: str = "auto",
) -> NewsCandidate:
    """Просит ИИ выбрать самый интересный для автомобилистов Ярославля и РФ инфоповод из топ-5."""
    if len(candidates) == 1 or isinstance(client, TemplateFallbackClient):
        return candidates[0]

    ai_cfg = config.get("ai", {})
    if not ai_cfg.get("smart_candidate_pick", True):
        return candidates[0]

    shortlist = candidates[:5]
    numbered = []
    for idx, cand in enumerate(shortlist, start=1):
        snippet = cand.best_text[:220].replace("\n", " ")
        numbered.append(
            f"{idx}. [{cand.category}] {cand.title} (Источник: {cand.source_name}) — {snippet}"
        )

    mode_hint = (
        "Выбери материал с максимальной практической пользой для обычных водителей (обслуживание, авто с пробегом, понятные житейские советы)."
        if preferred_mode == "tip"
        else "Выбери самую понятную, интересную и близкую простым водителям новость для жителей Ярославля и России."
    )

    group_title = str(config.get("group", {}).get("title", "Греем Мотор"))
    system_prompt = (
        f"Ты главный редактор автомобильного сообщества ВКонтакте «{group_title}» (Ярославль). "
        "Ответь ТОЛЬКО одной цифрой — номером лучшего инфоповода из списка."
    )
    user_prompt = f"{mode_hint}\n\nКандидаты:\n" + "\n".join(numbered) + "\n\nНомер лучшего варианта (только цифра 1..5):"

    try:
        answer = client.generate(system_prompt, user_prompt, temperature=0.2, max_tokens=20)
        m = re.search(r"[1-5]", answer)
        if m:
            idx = int(m.group(0)) - 1
            if 0 <= idx < len(shortlist):
                logger.info("ИИ-редактор выбрал кандидата №%d: «%s»", idx + 1, shortlist[idx].title)
                return shortlist[idx]
    except AIProviderError as exc:
        logger.warning("Не удалось выполнить ИИ-ранжирование кандидатов, берём топ-1 по рейтингу: %s", exc)

    return shortlist[0]


def proofread_vk_post_with_ai(
    client: Any,
    draft_text: str,
    source_facts: str,
    config: dict[str, Any],
    *,
    require_yaroslavl: bool = False,
) -> str:
    """Второй проход ИИ-редактора: делает текст максимально простым, живым и понятным обычным людям."""
    if isinstance(client, TemplateFallbackClient):
        return draft_text
    if not (config.get("ai") or {}).get("proofread_posts", True):
        return draft_text

    cleaned_draft = clean_markdown_for_vk(draft_text)
    if len(cleaned_draft) < 120:
        return draft_text

    yar_rule = (
        "\n- ОБЯЗАТЕЛЬНО: событие произошло в Ярославле — проверь, чтобы город «Ярославль» (например, «в Ярославле») "
        "был прямо указан в заголовке или первом абзаце и не потерялся!"
        if require_yaroslavl
        else ""
    )

    group_title = str(config.get("group", {}).get("title", "Греем Мотор"))
    system_prompt = (
        f"Ты шеф-редактор популярного водительского сообщества ВКонтакте «{group_title}».\n"
        "Твоя главная задача — отредактировать черновик поста так, чтобы он читался ЛЕГКО, ПРОСТО И ПО-ЧЕЛОВЕЧЕСКИ "
        "для самых обычных водителей (без технарской зауми, канцелярита и анкетных списков).\n"
        "ЧТО НУЖНО ПРОВЕРИТЬ И ИСПРАВИТЬ ОБЯЗАТЕЛЬНО:\n"
        "1. ПОНЯТНЫЙ ЗАГОЛОВОК НА ПЕРВОЙ СТРОКЕ: первая строка должна быть чётким, конкретным заголовком обычным шрифтом "
        "(с 1 уместным эмодзи в начале и БЕЗ двоеточия на конце), из которого сразу ясна суть темы. Замени размытые "
        "заходы вроде «Вот такая история произошла...» или «Советы эксперта помогут...:» на ясный заголовок."
        f"{yar_rule}\n"
        "2. ПРОСТОЙ ЧЕЛОВЕЧЕСКИЙ ЯЗЫК БЕЗ СПИСКОВ С ГАЛОЧКАМИ: убери любые лабораторные единицы и коды (никаких «мг/кг», "
        "«классов К2/К3/К5», «ВНС», «МИЦ ГСМ», «эксплуатационных характеристик»). Перепиши сухие списки с галочками "
        "и двоеточиями («До трёх лет: ... / Школьники: ...») в 3–4 плавных, живых и тёплых абзаца обычной человеческой речью.\n"
        "3. ТОЧНОСТЬ И ЗДРАВЫЙ СМЫСЛ: не искажай факты из исходника (например, если птица пролетала мимо камеры и "
        "прикрыла номер крылом в полёте — не пиши, что она садилась перед машиной). Не читай скучных нотаций вроде "
        "«эта история напоминает о важности мыть номера» — в курьёзной новости оставь лёгкую добрую интонацию.\n"
        "4. БЕЗУПРЕЧНАЯ РУССКАЯ ГРАММАТИКА И ОБРАЩЕНИЕ НА «ВЫ»: первая строка должна быть ОТДЕЛЬНЫМ коротким заголовком "
        "(после него — пустая строка и основной текст), проверь падежи («избежать штрафа») и обращайся на «вы» даже в заголовке "
        "(не «Заправляйся», а «Заправляйтесь», без первого лица «Надеюсь»).\n"
        "5. Строго без Markdown (**звёздочек**, ### заголовков), без хештегов и без комментариев от себя.\n"
        "Верни ТОЛЬКО готовый чистый текст поста."
    )
    clean_facts = humanize_technical_jargon(source_facts, aggressive_source_mode=True) if source_facts else ""
    facts_block = f"ИСХОДНАЯ НОВОСТЬ ДЛЯ ПРОВЕРКИ ФАКТОВ:\n{clean_facts[:1800]}\n\n" if clean_facts else ""
    user_prompt = f"{facts_block}ЧЕРНОВИК ПОСТА:\n{cleaned_draft}"

    try:
        checked = client.generate(system_prompt, user_prompt, temperature=0.2, max_tokens=1200)
        checked_clean = clean_markdown_for_vk(checked)
        if len(checked_clean) >= max(150, int(len(cleaned_draft) * 0.5)):
            return checked_clean
    except AIProviderError as exc:
        logger.warning("Проход ИИ-корректора пропущен, оставляем основной текст: %s", exc)

    return draft_text


def build_ai_image_prompt(
    client: Any,
    *,
    title: str,
    post_text: str,
    category: str,
) -> str:
    """Формирует сюжет для генерации иллюстрации (с узнаваемым Ярославлем для ярославских новостей)."""
    yaroslavl_scene = is_yaroslavl_topic(title, post_text, category)

    anti_artifact_tail = (
        " Фотореалистичная журнальная автомобильная иллюстрация, естественный выразительный свет, "
        "чёткая правильная геометрия автомобиля, колёс и фар. "
        "СТРОГО БЕЗ надписей, БЕЗ букв, БЕЗ цифр, БЕЗ вывесок, БЕЗ дорожных знаков с текстом, "
        "пустая чёрная рамка без госномера, без лиц и рук крупным планом."
    )
    yaroslavl_visual_tail = (
        " Действие происходит в городе Ярославле: на заднем плане узнаваемая панорама Ярославля — "
        "набережная широкой реки Волги, старинные белокаменные ярославские храмы с золотыми и зелёными куполами, "
        "шатровыми колокольнями и красивый арочный мост через Волгу."
    )

    if yaroslavl_scene:
        fallback_prompt = (
            f"Нарисуй атмосферную городскую автомобильную сцену в Ярославле по теме «{title}»: "
            "современный автомобиль на ухоженной улице Ярославля у набережной Волги, "
            "на фоне узнаваемых белокаменных ярославских церквей с золотыми и изумрудными куполами "
            "и арочного моста через Волгу."
            + anti_artifact_tail
        )
    else:
        default_scenes = {
            "tip": (
                f"Нарисуй понятную и тёплую автомобильную сцену по теме «{title}»: "
                "ухоженный современный автомобиль в чистом уютном гараже-сервисе с тёплым светом ламп."
            ),
            "news": (
                f"Нарисуй реалистичную автомобильную сцену по теме «{title}»: "
                "современный автомобиль крупным планом на красивой российской дороге в вечернем свете фонарей."
            ),
        }
        fallback_prompt = default_scenes.get(category, default_scenes["news"]) + anti_artifact_tail

    if isinstance(client, TemplateFallbackClient):
        return fallback_prompt

    clean_story = strip_vk_footer_lines(post_text)[:600]
    if yaroslavl_scene:
        system_prompt = (
            "Ты арт-директор автомобильного журнала в Ярославле. Придумай ОДНО короткое (2–3 предложения на русском языке) "
            "описание фотореалистичной сцены для иллюстратора по теме новости.\n"
            "ГЛАВНОЕ ТРЕБОВАНИЕ — НОВОСТЬ ПРО ЯРОСЛАВЛЬ:\n"
            "1. Начни строго со слова «Нарисуй».\n"
            "2. ОБЯЗАТЕЛЬНО покажи узнаваемый Ярославль в кадре: набережную Волги и Стрелку, старинные белокаменные "
            "ярославские храмы с золотыми и зелёными куполами, арочный Октябрьский мост через Волгу или уютные исторические "
            "купеческие улицы центра Ярославля.\n"
            "3. На переднем плане органично изобрази автомобиль и ключевую деталь сюжета новости (например, летящую белую чайку, "
            "дорожный ремонт, зимнюю улицу или поток машин).\n"
            "4. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО просить нарисовать любые надписи, буквы, цифры, вывески, дорожные знаки с текстом "
            "и госномера!"
        )
    else:
        system_prompt = (
            "Ты арт-директор автомобильного журнала. Придумай ОДНО короткое (2–3 предложения на русском языке) "
            "описание понятной фотореалистичной сцены для иллюстратора по теме поста.\n"
            "ПРАВИЛА ГЕНЕРАЦИИ:\n"
            "1. Начни строго со слова «Нарисуй».\n"
            "2. Главный герой кадра — реалистичный автомобиль в жизненной дорожной или сервисной обстановке, которая сразу "
            "понятна обычному водителю по смыслу новости.\n"
            "3. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО просить нарисовать любые надписи, буквы, цифры, ценники, вывески, экраны с текстом, "
            "госномера, а также лица или пальцы рук крупным планом!"
        )

    location_line = "Место действия: город Ярославль (обязательно покажи архитектуру Ярославля и Волгу).\n" if yaroslavl_scene else ""
    user_prompt = (
        f"{location_line}Тема поста: {title}\nКраткий сюжет:\n{clean_story}\n\n"
        "Описание сцены (начни со слова «Нарисуй»):"
    )

    try:
        scene = client.generate(system_prompt, user_prompt, temperature=0.5, max_tokens=160).strip()
        scene = clean_markdown_for_vk(scene).replace("\n", " ").strip()
        if len(scene) >= 30:
            if not scene.lower().startswith("нарисуй"):
                scene = f"Нарисуй {scene}"
            if yaroslavl_scene and "ярослав" not in scene.lower():
                scene = scene.rstrip(".") + "." + yaroslavl_visual_tail
            elif yaroslavl_scene and "волг" not in scene.lower() and "купол" not in scene.lower():
                scene = scene.rstrip(".") + "." + yaroslavl_visual_tail
            return scene + anti_artifact_tail
    except AIProviderError as exc:
        logger.warning("Арт-директор использовал базовый сюжет иллюстрации: %s", exc)

    return fallback_prompt


def generate_ai_post_illustration(
    client: Any,
    *,
    title: str,
    post_text: str,
    category: str,
    config: dict[str, Any],
) -> tuple[bytes, str] | None:
    """Генерирует собственную уникальную ИИ-иллюстрацию к посту (0% чужих авторских прав)."""
    if not (config.get("ai") or {}).get("generate_images", True):
        return None
    if not hasattr(client, "generate_image_bytes"):
        return None

    img_prompt = build_ai_image_prompt(
        client,
        title=title,
        post_text=post_text,
        category=category,
    )
    logger.info("Запрашиваем генерацию уникальной ИИ-иллюстрации в %s...", getattr(client, "name", "AI"))
    img_bytes, file_id = client.generate_image_bytes(img_prompt)
    logger.info("ИИ-иллюстрация успешно создана (id=%s, %d КБ)", file_id, len(img_bytes) // 1024)
    return img_bytes, f"ai:{getattr(client, 'name', 'AI')}:{file_id}"


def write_news_post(
    client: Any,
    candidate: NewsCandidate,
    config: dict[str, Any],
    now: datetime | None = None,
) -> str:
    """Генерирует простой, живой и понятный каждому водителю пост по новости."""
    ai_cfg = config.get("ai", {})
    temperature = float(ai_cfg.get("temperature", 0.6))
    max_tokens = int(ai_cfg.get("max_tokens", 1100))

    group_cfg = config.get("group", {})
    group_title = str(group_cfg.get("title", "Греем Мотор"))
    group_url = str(group_cfg.get("url") or "https://vk.ru/greem_motor")
    system_prompt = (
        f"Ты автор и ведущий водительского сообщества ВКонтакте «{group_title}» ({group_url}).\n"
        "ГЛАВНОЕ ПРАВИЛО — ПИШИ ПРОСТО И ПОНЯТНО ДЛЯ ОБЫЧНЫХ ЛЮДЕЙ:\n"
        "- Рассказывай новость так, как толковый водитель простыми словами пересказывает её другу или соседу во дворе.\n"
        "- Никаких сложных технических терминов, ГОСТов, химических норм (вроде «мг/кг», «классов К2/К5»), чиновничьих "
        "оборотов и канцелярита («эксплуатационные характеристики», «инцидент произошёл», «халатность автовладельца»). "
        "Если в новости есть сложный параметр — сразу объясни по-человечески, что это значит на деле для обычной машины и кошелька.\n"
        "- Пиши цельным живым рассказом в 3 коротких абзацах (по 2–3 простых предложения). НЕ дроби пост на подзаголовки "
        "с галочками и НЕ пиши заголовок КАПСЛОКОМ.\n"
        "- Если тема практическая (бензин, ремонт, цены, покупка авто, ПДД) — просто и по-житейски подскажи в тексте, "
        "как водителю не переплатить и поберечь машину. Если же новость — забавный дорожный курьёз, просто с улыбкой "
        "расскажи историю БЕЗ натянутых советов и нравоучений.\n"
        "- Лёгкая добрая улыбка уместна там, где позволяет тема (в темах ДТП и происшествий пиши спокойно и серьёзно). "
        "Строго не выдумывай историй от первого лица и про «знакомых».\n"
        "- Не притягивай за уши погоду и не вставляй шаблонные фразы «Для ярославцев особенно актуально...», если сама новость общероссийская.\n"
        "- Обращайся к читателям уважительно на «вы», не используй Markdown (**звёздочки**, ###) и не пиши хештеги."
    )

    excerpt = humanize_technical_jargon(candidate.best_text[:2800], aggressive_source_mode=True)
    yaroslavl_news = is_yaroslavl_topic(candidate.title, excerpt, candidate.category)
    city_hint = (
        "- ВАЖНО: эта новость произошла в Ярославле / Ярославской области — обязательно укажи город прямо в заголовке на первой строке.\n"
        if yaroslavl_news
        else ""
    )
    user_prompt = (
        f"ИСТОЧНИК: {candidate.source_name}\n"
        f"КАТЕГОРИЯ: {candidate.category}\n"
        f"ЗАГОЛОВОК ИСТОЧНИКА: {candidate.title}\n\n"
        f"ТЕКСТ НОВОСТИ:\n{excerpt}\n\n"
        "Напиши короткий, очень понятный и живой пост для обычных людей (650–1200 знаков):\n"
        "1) Первая строка: чёткий, конкретный заголовок обычным шрифтом (без КАПСЛОКА и без двоеточия на конце) с 1 уместным эмодзи в начале.\n"
        f"{city_hint}"
        "2) 2–3 коротких связных абзаца человеческим языком (БЕЗ списков с галочками ✅ и БЕЗ подзаголовков): что произошло и почему это важно обычному водителю.\n"
        "3) В конце — один простой жизненный вопрос подписчикам для обсуждения в комментариях.\n"
        "Выведи ТОЛЬКО готовый текст поста."
    )

    raw_post = client.generate(system_prompt, user_prompt, temperature=temperature, max_tokens=max_tokens)
    checked_post = proofread_vk_post_with_ai(
        client,
        raw_post,
        f"{candidate.title}\n{excerpt}",
        config,
        require_yaroslavl=yaroslavl_news,
    )
    return finalize_vk_post(
        checked_post,
        config,
        category=candidate.category,
        source_name=candidate.source_name,
        source_url=candidate.url,
    )


def write_evergreen_tip_post(
    client: Any,
    topic: dict[str, Any],
    config: dict[str, Any],
    now: datetime | None = None,
) -> str:
    """Генерирует понятный каждому водителю житейский совет по уходу за автомобилем."""
    dt = now or datetime.now(timezone.utc)
    season_note = MONTH_SEASON_HINT.get(dt.month, "текущий сезон")
    ai_cfg = config.get("ai", {})
    temperature = float(ai_cfg.get("temperature", 0.6))
    max_tokens = int(ai_cfg.get("max_tokens", 1150))

    group_title = str(config.get("group", {}).get("title", "Греем Мотор"))
    system_prompt = (
        f"Ты добрый, опытный автомеханик и автор сообщества ВКонтакте «{group_title}» (Ярославль).\n"
        "Объясняй устройство и обслуживание машины МАКСИМАЛЬНО ПРОСТЫМИ ЧЕЛОВЕЧЕСКИМИ СЛОВАМИ — чтобы всё сразу понял "
        "даже новичок, который вчера сел за руль.\n"
        "ПРАВИЛА:\n"
        "1. Никакой зауми, сложных терминов без простого пояснения, никакого КАПСЛОКА и никакого Markdown (**звёздочек**, ###).\n"
        "2. Пиши коротко, тепло и по делу (800–1350 знаков), с уважительным обращением на «вы».\n"
        "3. Не пиши хештеги (система добавит их сама)."
    )

    title = str(topic.get("title", "Полезный совет автомобилисту"))
    angle = str(topic.get("angle", ""))

    user_prompt = (
        f"СЕЗОН: {season_note}.\n"
        f"ТЕМА СОВЕТА: {title}\n\n"
        f"О ЧЁМ РАССКАЗАТЬ:\n{angle}\n\n"
        "Напиши понятный каждому водителю пост:\n"
        "- Первая строка: живой понятный заголовок (начни с 🔧 или 🛞, без КАПСЛОКА).\n"
        "- Короткое объяснение простыми словами, почему на эту мелочь стоит обратить внимание.\n"
        "- 3–4 коротких и конкретных шага (через тире «—»), что легко проверить самому без лишних трат.\n"
        "- В конце — простой вопрос к читателям об их опыте.\n"
        "Выведи ТОЛЬКО готовый текст поста."
    )

    raw_post = client.generate(system_prompt, user_prompt, temperature=temperature, max_tokens=max_tokens)
    checked_post = proofread_vk_post_with_ai(client, raw_post, f"{title}\n{angle}", config)
    return finalize_vk_post(
        checked_post,
        config,
        category="tip",
        source_name="",
        source_url="",
    )


def rewrite_existing_post_text(
    client: Any,
    existing_text: str,
    config: dict[str, Any],
    *,
    category: str = "news",
    source_name: str = "",
    source_url: str = "",
    source_facts: str = "",
    original_title: str = "",
    is_local_yaroslavl: bool = False,
) -> str:
    """Переписывает уже выпущенный пост простым, живым и понятным для обычных людей языком."""
    lines: list[str] = []
    detected_source = source_name.strip()
    for raw_ln in (existing_text or "").splitlines():
        s = raw_ln.strip()
        if s.startswith("📰 По материалам:"):
            if not detected_source:
                detected_source = s.split(":", 1)[1].strip()
            continue
        if s.startswith("🔥 Греем Мотор —") or s.startswith("Подробнее:"):
            continue
        if s.startswith("#") and " " not in s.replace(" #", ""):
            continue
        if s.startswith("#") and all(tok.startswith("#") for tok in s.split()):
            continue
        lines.append(raw_ln)

    clean_fact_base = "\n".join(lines).strip()
    clean_fact_base = re.sub(r"«?АвтоТочк[а-яА-ЯёЁ]*»?", "«Греем Мотор»", clean_fact_base, flags=re.IGNORECASE)
    clean_fact_base = re.sub(r"(?:https?://)?(?:m\.)?vk\.(?:ru|com)/autoyar076\b", "vk.ru/greem_motor", clean_fact_base, flags=re.IGNORECASE)
    clean_fact_base = re.sub(r"@autoyar076\b", "@greem_motor", clean_fact_base, flags=re.IGNORECASE)

    raw_primary = source_facts.strip() if len(source_facts.strip()) >= 100 else clean_fact_base
    if original_title.strip():
        raw_primary = f"Заголовок: {original_title.strip()}\n\n{raw_primary}"
    primary_facts = humanize_technical_jargon(raw_primary, aggressive_source_mode=True)

    yaroslavl_flag = is_local_yaroslavl or is_yaroslavl_topic(original_title, primary_facts, category)

    if isinstance(client, TemplateFallbackClient) or len(primary_facts) < 80:
        return finalize_vk_post(
            clean_fact_base,
            config,
            category=category,
            source_name=detected_source,
            source_url=source_url,
        )

    yar_instruction = (
        "- ВАЖНО: эта история произошла в Ярославле — обязательно укажи «в Ярославле» прямо в заголовке на первой строке "
        "(не теряй название города!).\n"
        if yaroslavl_flag
        else ""
    )

    group_cfg = config.get("group", {})
    group_title = str(group_cfg.get("title", "Греем Мотор"))
    group_url = str(group_cfg.get("url") or "https://vk.ru/greem_motor")
    system_prompt = (
        f"Ты шеф-редактор водительского сообщества ВКонтакте «{group_title}» ({group_url}). "
        "Твоя задача — переписать пост С НУЛЯ максимально ПРОСТЫМ, ЧЕЛОВЕЧЕСКИМ И ПОНЯТНЫМ языком для обычных людей.\n"
        "ЧЕГО В ПОСТЕ БЫТЬ КАТЕГОРИЧЕСКИ НЕ ДОЛЖНО:\n"
        "- Никаких лабораторных единиц и непонятных кодов (никаких «мг/кг», «классов К2/К3/К5», «МИЦ ГСМ», «ВНС», "
        "«эксплуатационных характеристик»). Переводи всё на обычный человеческий язык: например, «в бензине оказалось "
        "слишком много серы, из-за чего страдают свечи и двигатель и растёт расход».\n"
        "- Никаких списков с галочками ✅ и никаких дробных подзаголовков внутри текста (никаких «До трёх лет: ...», "
        "«Что ещё стоит учитывать:», «Ключевые находки:»). Пиши плавным связным рассказом в 3 коротких абзацах!\n"
        "- Никаких размытых первых строк вроде «Вот такая история произошла...» или «Советы врача помогут...:».\n"
        "- Никаких выдумок (не пиши, что птица садилась перед машиной, если она пролетала мимо камеры и закрыла номер крылом в полёте).\n"
        "КАК НАПИСАТЬ ОТЛИЧНО:\n"
        "- Первая строка: ясный, конкретный заголовок обычным шрифтом (без КАПСЛОКА и без двоеточия на конце) с 1 уместным эмодзи.\n"
        f"{yar_instruction}"
        "- Далее 3 коротких связных абзаца живым, тёплым человеческим языком: в чём суть и какой простой вывод для обычного водителя.\n"
        "- В конце — один короткий, естественный вопрос читателям (на «вы») для обсуждения в комментариях.\n"
        "- Грамотный русский язык (проверь падежи: «избежать штрафа», а не «избежать штраф»), строго без Markdown (**звёздочек**, ###), без ссылок и без хештегов."
    )
    user_prompt = (
        f"КАТЕГОРИЯ: {category}\n"
        f"ФАКТЫ НОВОСТИ:\n{primary_facts[:2800]}\n\n"
        "Напиши простой, связный и понятный каждому человеку пост (650–1150 знаков) без списков с галочками. Выведи ТОЛЬКО готовый текст поста."
    )

    draft = client.generate(system_prompt, user_prompt, temperature=0.5, max_tokens=1150)
    checked = proofread_vk_post_with_ai(
        client,
        draft,
        primary_facts,
        config,
        require_yaroslavl=yaroslavl_flag,
    )
    return finalize_vk_post(
        checked,
        config,
        category=category,
        source_name=detected_source,
        source_url=source_url,
    )

