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
    r"^(вот\s+(готовый\s+)?пост[^\n]*:\s*|конечно[,!]?\s*вот\s+вариант[^\n]*:\s*|готовый\s+текст\s+поста[^\n]*:\s*)",
    re.IGNORECASE,
)

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
        self.model = os.getenv("GIGACHAT_MODEL") or str(cfg.get("model", "GigaChat"))
        self.oauth_url = os.getenv("GIGACHAT_OAUTH_URL") or str(
            cfg.get("oauth_url", "https://ngw.devices.sberbank.ru:9443/api/v2/oauth")
        )
        self.chat_urls = [
            u
            for u in [
                os.getenv("GIGACHAT_CHAT_URL") or "",
                str(cfg.get("chat_url", "")),
                "https://api.giga.chat/v1/chat/completions",
                "https://gigachat.devices.sberbank.ru/api/v1/chat/completions",
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
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_err: Exception | None = None
        data: dict[str, Any] = {}
        for chat_url in self.chat_urls:
            try:
                data = _http_post_json(
                    chat_url,
                    headers,
                    body_bytes,
                    ssl_ctx=self.ssl_ctx,
                    timeout=50,
                    retries=1,
                )
                last_err = None
                break
            except AIProviderError as exc:
                last_err = exc
                continue
        if last_err is not None:
            raise last_err
        choices = data.get("choices") or []
        if not choices:
            raise AIProviderError(f"Пустой ответ GigaChat: {data}")
        content = str(choices[0].get("message", {}).get("content", "")).strip()
        if not content:
            raise AIProviderError("GigaChat вернул пустую строку текста")
        return content


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
            "Следите за техническим состоянием автомобиля и актуальными новостями авторынка вместе с «АвтоТочкой».",
            "А что вы думаете по этому поводу? Делитесь мнением в комментариях 👇",
        )

    def generate(self, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int) -> str:
        title = ""
        category = "news"
        raw_fact_lines: list[str] = []
        is_evergreen = "ТЕМА РАЗБОРА:" in user_prompt
        capture = False

        for raw_ln in user_prompt.splitlines():
            ln = raw_ln.strip()
            if not ln:
                if capture and raw_fact_lines and raw_fact_lines[-1] != "":
                    raw_fact_lines.append("")
                continue
            if ln.startswith("КАТЕГОРИЯ:"):
                category = ln.split(":", 1)[1].strip()
            elif ln.startswith(("ЗАГОЛОВОК:", "ТЕМА РАЗБОРА:")):
                title = ln.split(":", 1)[1].strip()
            elif ln.startswith(("ФАКТУРА НОВОСТИ:", "КЛЮЧЕВЫЕ ТЕЗИСЫ:")):
                capture = True
            elif ln.startswith("ТРЕБОВАНИЯ К ПОСТУ:"):
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


def clean_markdown_for_vk(raw_text: str) -> str:
    """Удаляет Markdown-разметку (**жирный**, ### заголовки и др.), которую не поддерживает стена ВК."""
    text = raw_text.strip()
    # Убираем обрамляющие кавычки или кодовые блоки, если модель обернула ответ
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = META_PREAMBLE_RE.sub("", text).strip()
    if len(text) > 20 and text.startswith('"') and text.endswith('"'):
        text = text[1:-1].strip()

    # Markdown-ссылки [анкор](url) -> анкор
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1", text)
    # Заголовки ### Текст -> Текст
    text = re.sub(r"(?m)^#{1,6}\s+", "", text)
    # Жирный / курсив **текст**, __текст__, *текст* (внутри слов не трогаем)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"(?m)^\*\s+", "• ", text)
    text = re.sub(r"(?m)^-\s+", "• ", text)
    text = text.replace("**", "").replace("__", "")

    # Вычищаем случайные хештеги внутри ответа модели (мы добавим аккуратную строку хештегов сами)
    lines = [ln.rstrip() for ln in text.splitlines()]
    while lines and re.fullmatch(r"(?:\s*#[a-zA-Zа-яА-ЯёЁ0-9_@]+)+\s*", lines[-1]):
        lines.pop()

    cleaned = "\n".join(lines).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def build_hashtags(config: dict[str, Any], category: str) -> str:
    """Формирует аккуратную строку хештегов группы autoyar076 без повторов."""
    posting_cfg = config.get("posting", {})
    base_tags = posting_cfg.get("base_hashtags", ["#autoyar076", "#автоярославль", "#авто76"])
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
    body = clean_markdown_for_vk(ai_text)

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
        "Выбери материал с максимальной практической пользой для водителей (обслуживание, авто с пробегом, советы)."
        if preferred_mode == "tip"
        else "Выбери самую важную, живую и цепляющую новость для российских автомобилистов и жителей Ярославля (76 регион)."
    )

    group_title = str(config.get("group", {}).get("title", "АвтоТочка"))
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


def write_news_post(
    client: Any,
    candidate: NewsCandidate,
    config: dict[str, Any],
    now: datetime | None = None,
) -> str:
    """Генерирует живой авторский пост по найденной новости или автостатье."""
    dt = now or datetime.now(timezone.utc)
    season_note = MONTH_SEASON_HINT.get(dt.month, "текущий сезон")
    ai_cfg = config.get("ai", {})
    temperature = float(ai_cfg.get("temperature", 0.65))
    max_tokens = int(ai_cfg.get("max_tokens", 1200))

    group_title = str(config.get("group", {}).get("title", "АвтоТочка"))
    system_prompt = (
        "Ты опытный автоэксперт и главный редактор живого автомобильного сообщества ВКонтакте "
        f"«{group_title}» (Ярославль и Ярославская область, 76 регион, https://vk.ru/autoyar076). "
        "Твоя аудитория — реальные водители, автовладельцы, покупатели машин с пробегом и любители авто.\n"
        "Пиши живо, по делу, человеческим языком без канцелярита и штампов вроде «В современном мире».\n"
        "СТРОГИЕ ПРАВИЛА ФОРМАТИРОВАНИЯ ВКОНТАКТЕ:\n"
        "1. НИКОГДА не используй Markdown: никаких звёздочек **жирный**, решёток ### для заголовков и [ссылок](url).\n"
        "2. Не выдумывай цифры, штрафы, цены или законы, которых нет в фактуре новости.\n"
        "3. Не вставляй внешние ссылки и хештеги (хештеги система добавит сама автоматически).\n"
        "4. Объём текста: 700–1600 знаков, разбивка на короткие удобные абзацы по 2–3 предложения."
    )

    local_hint = (
        "Новость напрямую касается Ярославля / 76 региона — сделай тёплый акцент для местных водителей."
        if candidate.category == "local_76"
        else "Если уместно по смыслу (без натяжки), добавь короткую практическую ремарку для российских/ярославских условий эксплуатации."
    )

    excerpt = candidate.best_text[:2800]
    user_prompt = (
        f"СЕЗОН СЕЙЧАС: {season_note}.\n"
        f"ИСТОЧНИК: {candidate.source_name}\n"
        f"КАТЕГОРИЯ: {candidate.category}\n"
        f"ЗАГОЛОВОК: {candidate.title}\n\n"
        f"ФАКТУРА НОВОСТИ:\n{excerpt}\n\n"
        "ТРЕБОВАНИЯ К ПОСТУ:\n"
        "- Первая строка: яркий, цепляющий заголовок с 1 уместным эмодзи в начале (например 🚗, ⚡, 🔧, 🛞, 📌).\n"
        "- Далее: суть события простым и бодрым языком с ключевыми деталями и цифрами из фактуры.\n"
        "- Отдельный короткий блок с подзаголовком «💡 Что важно учесть водителю:» (1–2 практических совета или вывод по теме).\n"
        f"- {local_hint}\n"
        "- В самом конце: 1 живой вопрос к подписчикам, чтобы водители захотели обсудить тему в комментариях.\n"
        "- Выведи ТОЛЬКО готовый текст поста без вступлений от себя."
    )

    raw_post = client.generate(system_prompt, user_prompt, temperature=temperature, max_tokens=max_tokens)
    return finalize_vk_post(
        raw_post,
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
    """Генерирует полезный авторский пост-разбор из рубрики «Полезно водителю / Авторазбор»."""
    dt = now or datetime.now(timezone.utc)
    season_note = MONTH_SEASON_HINT.get(dt.month, "текущий сезон")
    ai_cfg = config.get("ai", {})
    temperature = float(ai_cfg.get("temperature", 0.65))
    max_tokens = int(ai_cfg.get("max_tokens", 1200))

    group_title = str(config.get("group", {}).get("title", "АвтоТочка"))
    system_prompt = (
        "Ты практикующий мастер-приёмщик, автоподборщик и редактор сообщества ВКонтакте "
        f"«{group_title}» (Ярославль, 76 регион). Пиши экспертно, дружелюбно, с конкретной технической пользой, "
        "как опытный товарищ по гаражу и автосервису.\n"
        "СТРОГИЕ ПРАВИЛА ФОРМАТИРОВАНИЯ ВКОНТАКТЕ:\n"
        "1. НИКОГДА не используй Markdown: никаких **звёздочек**, __подчёркиваний__ и ### заголовков.\n"
        "2. Для списков используй аккуратные маркеры «•» или цифры «1)», «2)».\n"
        "3. Не пиши хештеги (система добавит их сама).\n"
        "4. Объём: 900–1800 знаков, короткие абзацы с пустыми строками между ними."
    )

    title = str(topic.get("title", "Полезный совет автомобилисту"))
    angle = str(topic.get("angle", ""))

    user_prompt = (
        f"СЕЗОН СЕЙЧАС: {season_note}.\n"
        f"ТЕМА РАЗБОРА: {title}\n\n"
        f"КЛЮЧЕВЫЕ ТЕЗИСЫ:\n{angle}\n\n"
        "ТРЕБОВАНИЯ К ПОСТУ:\n"
        "- Первая строка: сильный заголовок рубрики (начни с 🔧 или 🛞).\n"
        "- Короткое жизненное вступление (почему на этом часто обжигаются водители в городе и на трассе).\n"
        "- Чёткий практический чек-лист из 3–5 пунктов без воды и мифов.\n"
        "- Короткий итог: как сэкономить ресурс машины и деньги на ремонте.\n"
        "- В конце: живой вопрос к подписчикам об их личном опыте.\n"
        "- Выведи ТОЛЬКО готовый текст поста."
    )

    raw_post = client.generate(system_prompt, user_prompt, temperature=temperature, max_tokens=max_tokens)
    return finalize_vk_post(
        raw_post,
        config,
        category="tip",
        source_name="",
        source_url="",
    )
