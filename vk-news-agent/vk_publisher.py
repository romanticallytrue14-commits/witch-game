"""Клиент VK API (api.vk.ru / api.vk.com) для загрузки фото и публикации постов на стену группы."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import ssl
import struct
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any

from news_fetcher import USER_AGENT

logger = logging.getLogger("vk_news_agent.vk")

VK_API_VERSION = "5.199"
VK_API_BASES = (
    "https://api.vk.ru/method",
    "https://api.vk.com/method",
)


class VKAPIError(RuntimeError):
    """Ошибка ответа метода VK API."""

    def __init__(self, code: int, message: str, method: str = "") -> None:
        self.code = code
        self.vk_message = message
        self.method = method
        super().__init__(f"VK API error {code} в методе {method}: {message}")


@dataclass
class PreparedImage:
    """Проверенное и готовое к загрузке в ВК изображение."""

    data: bytes
    filename: str
    mime_type: str
    width: int
    height: int
    source_url: str


def inspect_image_dimensions(data: bytes) -> tuple[str, int, int] | None:
    """Определяет реальный формат (jpeg/png/webp/gif) и размеры (width, height) по заголовку файла."""
    if len(data) < 32:
        return None

    # PNG
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        width, height = struct.unpack(">II", data[16:24])
        return "png", int(width), int(height)

    # GIF
    if data[:6] in (b"GIF87a", b"GIF89a"):
        width, height = struct.unpack("<HH", data[6:10])
        return "gif", int(width), int(height)

    # WebP (VP8 / VP8L / VP8X)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        chunk = data[12:16]
        if chunk == b"VP8X" and len(data) >= 30:
            w = 1 + int.from_bytes(data[24:27], "little")
            h = 1 + int.from_bytes(data[27:30], "little")
            return "webp", w, h
        if chunk == b"VP8 " and len(data) >= 30:
            w, h = struct.unpack("<HH", data[26:30])
            return "webp", w & 0x3FFF, h & 0x3FFF
        if chunk == b"VP8L" and len(data) >= 25:
            bits = int.from_bytes(data[21:25], "little")
            w = (bits & 0x3FFF) + 1
            h = ((bits >> 14) & 0x3FFF) + 1
            return "webp", w, h
        return "webp", 800, 600

    # JPEG (сканируем SOF-маркеры)
    if data[:2] == b"\xff\xd8":
        idx = 2
        size = len(data)
        sof_markers = {
            0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
            0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
        }
        while idx + 9 < size:
            if data[idx] != 0xFF:
                idx += 1
                continue
            while idx < size and data[idx] == 0xFF:
                idx += 1
            if idx >= size:
                break
            marker = data[idx]
            idx += 1
            if marker in (0xD8, 0xD9) or (0xD0 <= marker <= 0xD7):
                continue
            if idx + 2 > size:
                break
            seg_len = struct.unpack(">H", data[idx : idx + 2])[0]
            if seg_len < 2 or idx + seg_len > size:
                break
            if marker in sof_markers and idx + 7 <= size:
                height, width = struct.unpack(">HH", data[idx + 3 : idx + 7])
                return "jpeg", int(width), int(height)
            idx += seg_len
        return "jpeg", 800, 600

    return None


def convert_webp_to_jpeg(webp_bytes: bytes) -> bytes | None:
    """Конвертирует WebP в JPEG через ImageMagick (convert) или ffmpeg для совместимости с VK API."""
    converter = shutil.which("convert") or shutil.which("magick")
    if converter:
        with tempfile.TemporaryDirectory() as tmpdir:
            in_path = os.path.join(tmpdir, "in.webp")
            out_path = os.path.join(tmpdir, "out.jpg")
            with open(in_path, "wb") as f:
                f.write(webp_bytes)
            cmd = [converter, in_path, "-quality", "90", out_path]
            res = subprocess.run(cmd, capture_output=True, timeout=15, check=False)
            if res.returncode == 0 and os.path.exists(out_path):
                with open(out_path, "rb") as f:
                    return f.read()

    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin:
        with tempfile.TemporaryDirectory() as tmpdir:
            in_path = os.path.join(tmpdir, "in.webp")
            out_path = os.path.join(tmpdir, "out.jpg")
            with open(in_path, "wb") as f:
                f.write(webp_bytes)
            cmd = [ffmpeg_bin, "-y", "-i", in_path, "-q:v", "3", out_path]
            res = subprocess.run(cmd, capture_output=True, timeout=15, check=False)
            if res.returncode == 0 and os.path.exists(out_path):
                with open(out_path, "rb") as f:
                    return f.read()

    return None


def download_and_prepare_image(
    image_urls: list[str],
    referer_url: str = "",
    min_bytes: int = 10_000,
    min_width: int = 320,
    min_height: int = 180,
) -> PreparedImage | None:
    """Скачивает первую подходящую качественную картинку из списка и готовит к отправке в ВК."""
    ctx = ssl.create_default_context()
    origin = ""
    if referer_url:
        parsed_ref = urllib.parse.urlparse(referer_url)
        if parsed_ref.scheme and parsed_ref.netloc:
            origin = f"{parsed_ref.scheme}://{parsed_ref.netloc}/"

    for img_url in image_urls[:6]:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "image/jpeg,image/png,image/webp,image/*;q=0.8,*/*;q=0.5",
        }
        if origin:
            headers["Referer"] = origin

        try:
            req = urllib.request.Request(img_url, headers=headers)
            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                raw = resp.read(12 * 1024 * 1024)  # максимум 12 МБ
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            logger.debug("Не удалось скачать изображение %s: %s", img_url, exc)
            continue

        if len(raw) < min_bytes:
            logger.debug("Пропущено слишком маленькое изображение (%d байт): %s", len(raw), img_url)
            continue

        info = inspect_image_dimensions(raw)
        if info is None:
            logger.debug("Файл не распознан как изображение: %s", img_url)
            continue

        fmt, width, height = info
        if width < min_width or height < min_height:
            logger.debug("Слишком маленькое разрешение %dx%d: %s", width, height, img_url)
            continue

        # Проверка пропорций для ВК (отношение сторон не более 1:20)
        ratio = max(width / max(height, 1), height / max(width, 1))
        if ratio > 10.0:
            continue

        if fmt == "webp":
            converted = convert_webp_to_jpeg(raw)
            if converted:
                conv_info = inspect_image_dimensions(converted)
                if conv_info:
                    _, width, height = conv_info
                return PreparedImage(
                    data=converted,
                    filename="news.jpg",
                    mime_type="image/jpeg",
                    width=width,
                    height=height,
                    source_url=img_url,
                )
            logger.debug("Не удалось конвертировать WebP в JPEG, пробуем следующее фото: %s", img_url)
            continue

        if fmt == "png":
            return PreparedImage(
                data=raw,
                filename="news.png",
                mime_type="image/png",
                width=width,
                height=height,
                source_url=img_url,
            )

        return PreparedImage(
            data=raw,
            filename="news.jpg",
            mime_type="image/jpeg",
            width=width,
            height=height,
            source_url=img_url,
        )

    return None


def inspect_vk_token_shape(raw_tok: str) -> dict[str, Any]:
    """Безопасная проверка формата токена ВК (без вывода секретных символов)."""
    s = (raw_tok or "").strip()
    return {
        "present": bool(s),
        "len": len(s),
        "contains_access_token_param": "access_token=" in s,
        "contains_ampersand": "&" in s,
        "starts_with_http": s.lower().startswith("http"),
        "starts_with_vk1": s.startswith("vk1.a."),
        "starts_with_vk2": s.startswith("vk2.a."),
        "has_quotes_or_spaces": bool(re.search(r'[\s"\'`]', s)),
    }


def normalize_vk_token(raw_tok: str) -> str:
    """Извлекает чистый access_token, даже если пользователь скопировал весь URL из адресной строки."""
    s = (raw_tok or "").strip().strip("\"'`").strip()
    if "access_token=" in s:
        after = s.split("access_token=", 1)[1]
        s = after.split("&", 1)[0].split("#", 1)[0].strip()
    elif "&" in s and ("expires_in=" in s or "user_id=" in s):
        s = s.split("&", 1)[0].strip()
    return s


def parse_group_identifier(raw_group: str) -> tuple[int | None, str]:
    """Разбирает ссылку или короткое имя группы ('https://vk.ru/autoyar076', '-12345', 'club12345')."""
    cleaned = raw_group.strip()
    if not cleaned:
        raise ValueError("Не указан адрес или ID группы ВК")

    # Убираем домен vk.ru / vk.com
    cleaned = re.sub(r"^https?://(?:m\.)?vk\.(?:ru|com)/", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip("/").split("?")[0].split("#")[0]

    if re.fullmatch(r"-?\d+", cleaned):
        return abs(int(cleaned)), ""

    m_club = re.fullmatch(r"(?:club|public|event)(\d+)", cleaned, flags=re.IGNORECASE)
    if m_club:
        return int(m_club.group(1)), ""

    return None, cleaned


def encode_multipart_photo(
    field_name: str,
    filename: str,
    mime_type: str,
    file_bytes: bytes,
    extra_fields: dict[str, str] | None = None,
) -> tuple[bytes, str]:
    """Формирует тело multipart/form-data для загрузки фото на upload_url ВК без внешних библиотек."""
    boundary = f"----VKAgentBoundary{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    if extra_fields:
        for k, v in extra_fields.items():
            chunks.append(
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{k}"\r\n\r\n'
                    f"{v}\r\n"
                ).encode("utf-8")
            )
    chunks.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8")
    )
    chunks.append(file_bytes)
    chunks.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    content_type = f"multipart/form-data; boundary={boundary}"
    return b"".join(chunks), content_type


class VKPublisher:
    """Публикатор записей на стену сообщества ВКонтакте."""

    def __init__(
        self,
        vk_token: str,
        vk_group: str = "autoyar076",
        vk_user_token: str = "",
        api_version: str = VK_API_VERSION,
    ) -> None:
        self.raw_vk_token = vk_token
        self.raw_vk_user_token = vk_user_token
        self.vk_token = normalize_vk_token(vk_token)
        self.vk_user_token = normalize_vk_token(vk_user_token)
        self.raw_group = vk_group.strip() or "autoyar076"
        self.api_version = api_version
        self.ssl_ctx = ssl.create_default_context()
        self._resolved_group_id: int | None = None

    def call_api(self, method: str, params: dict[str, Any], token: str | None = None) -> Any:
        """Вызывает метод VK API на api.vk.ru с резервным переключением на api.vk.com."""
        effective_token = (token or self.vk_token).strip()
        if not effective_token:
            raise VKAPIError(-1, "Не задан токен доступа ВКонтакте (VK_TOKEN)", method=method)

        payload = {k: str(v) for k, v in params.items() if v is not None}
        payload["access_token"] = effective_token
        payload["v"] = self.api_version
        encoded = urllib.parse.urlencode(payload).encode("utf-8")

        last_net_err: Exception | None = None
        for base in VK_API_BASES:
            url = f"{base}/{method}"
            req = urllib.request.Request(
                url,
                data=encoded,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": USER_AGENT,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=30, context=self.ssl_ctx) as resp:
                    data = json.loads(resp.read().decode("utf-8", errors="replace"))
                if "error" in data:
                    err = data["error"]
                    raise VKAPIError(
                        int(err.get("error_code", 0)),
                        str(err.get("error_msg", "Unknown VK error")),
                        method=method,
                    )
                return data.get("response")
            except VKAPIError:
                raise
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                last_net_err = exc
                logger.warning("Сбой соединения с %s (%s), пробуем резервный хост...", base, exc)
                continue

        raise VKAPIError(-2, f"Сеть недоступна при вызове {method}: {last_net_err}", method=method)

    def resolve_group_id(self) -> int:
        """Определяет числовой ID сообщества (например, для https://vk.ru/autoyar076)."""
        if self._resolved_group_id is not None:
            return self._resolved_group_id

        numeric_id, screen_name = parse_group_identifier(self.raw_group)
        if numeric_id is not None:
            self._resolved_group_id = numeric_id
            return numeric_id

        # 1. Пробуем groups.getById (работает и с ключом сообщества, и с токеном пользователя)
        try:
            resp = self.call_api("groups.getById", {"group_id": screen_name})
            # В VK API 5.199 возвращается {"groups": [{"id": ...}]}, а в старых версиях список [{"id": ...}]
            groups_list = resp.get("groups") if isinstance(resp, dict) else resp
            if isinstance(groups_list, list) and groups_list:
                gid = int(groups_list[0]["id"])
                self._resolved_group_id = abs(gid)
                logger.info("Группа «%s» определена: group_id=%d", screen_name, self._resolved_group_id)
                return self._resolved_group_id
        except (VKAPIError, KeyError, ValueError, TypeError) as exc:
            logger.debug("groups.getById не вернул ID для %s: %s", screen_name, exc)

        # 2. Резервный способ через utils.resolveScreenName
        resp = self.call_api(
            "utils.resolveScreenName",
            {"screen_name": screen_name},
            token=self.vk_user_token or self.vk_token,
        )
        if isinstance(resp, dict) and resp.get("object_id"):
            gid = abs(int(resp["object_id"]))
            self._resolved_group_id = gid
            logger.info("Короткое имя «%s» разрешено в group_id=%d", screen_name, gid)
            return gid

        raise VKAPIError(
            100,
            f"Не удалось определить числовой ID группы «{screen_name}». Проверьте адрес группы или укажите числовой ID в VK_GROUP.",
            method="resolve_group_id",
        )

    def get_recent_wall_posts(self, count: int = 30) -> list[dict[str, Any]]:
        """Читает последние посты со стены группы для дополнительной защиты от повторов."""
        group_id = self.resolve_group_id()
        token_for_read = self.vk_user_token or self.vk_token
        try:
            resp = self.call_api(
                "wall.get",
                {"owner_id": -group_id, "count": min(max(count, 1), 100)},
                token=token_for_read,
            )
            if isinstance(resp, dict) and isinstance(resp.get("items"), list):
                return resp["items"]
        except VKAPIError as exc:
            # С ключом сообщества метод wall.get недоступен (код 27) — это штатная ситуация
            if exc.code in {15, 27}:
                logger.info(
                    "Чтение стены wall.get пропущено (используется ключ сообщества; дедупликация работает по state/published.json)."
                )
            else:
                logger.warning("Не удалось прочитать стену ВК для проверки дублей: %s", exc)
        return []

    def upload_wall_photo(self, image: PreparedImage) -> str | None:
        """Загружает фото на сервер ВК и возвращает строку attachment вида 'photo123_456'."""
        group_id = self.resolve_group_id()
        photo_token = self.vk_user_token or self.vk_token

        try:
            upload_srv = self.call_api(
                "photos.getWallUploadServer",
                {"group_id": group_id},
                token=photo_token,
            )
        except VKAPIError as exc:
            if exc.code in {15, 27}:
                logger.warning(
                    "ВК отклонил загрузку фото с кодом %d (%s): методы photos.* требуют токен пользователя-админа "
                    "(VK_TOKEN пользователя с правами wall,photos,groups,offline или отдельный секрет VK_USER_TOKEN). "
                    "Пост будет опубликован без вложенного фото.",
                    exc.code,
                    exc.vk_message,
                )
                return None
            logger.warning("Ошибка photos.getWallUploadServer: %s", exc)
            return None

        upload_url = str((upload_srv or {}).get("upload_url", "")).strip()
        if not upload_url:
            logger.warning("photos.getWallUploadServer не вернул upload_url")
            return None

        body, content_type = encode_multipart_photo(
            "photo",
            image.filename,
            image.mime_type,
            image.data,
        )
        req = urllib.request.Request(
            upload_url,
            data=body,
            headers={
                "Content-Type": content_type,
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=35, context=self.ssl_ctx) as resp:
                uploaded = json.loads(resp.read().decode("utf-8", errors="replace"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            logger.warning("Сбой загрузки файла фото на upload_url ВК: %s", exc)
            return None

        photo_field = uploaded.get("photo")
        if not photo_field or photo_field == "[]":
            logger.warning("Сервер загрузки ВК не принял файл изображения (пустое поле photo)")
            return None

        try:
            saved = self.call_api(
                "photos.saveWallPhoto",
                {
                    "group_id": group_id,
                    "photo": photo_field,
                    "server": uploaded.get("server"),
                    "hash": uploaded.get("hash"),
                },
                token=photo_token,
            )
        except VKAPIError as exc:
            logger.warning("Ошибка сохранения фото photos.saveWallPhoto: %s", exc)
            return None

        if isinstance(saved, list) and saved:
            item = saved[0]
            owner_id = item.get("owner_id")
            media_id = item.get("id")
            access_key = item.get("access_key")
            if owner_id is not None and media_id is not None:
                att = f"photo{owner_id}_{media_id}"
                if access_key:
                    att = f"{att}_{access_key}"
                return att

        return None

    def publish_post(
        self,
        message: str,
        *,
        image: PreparedImage | None = None,
        fallback_link: str = "",
        guid: str = "",
    ) -> dict[str, Any]:
        """Публикует готовый пост сразу на стену сообщества от имени группы."""
        group_id = self.resolve_group_id()
        attachments: list[str] = []

        photo_att: str | None = None
        if image is not None:
            photo_att = self.upload_wall_photo(image)
            if photo_att:
                attachments.append(photo_att)

        if not attachments and fallback_link:
            attachments.append(fallback_link)

        params: dict[str, Any] = {
            "owner_id": -group_id,
            "from_group": 1,
            "message": message,
        }
        if attachments:
            params["attachments"] = ",".join(attachments)
        if guid:
            params["guid"] = guid

        try:
            resp = self.call_api("wall.post", params, token=self.vk_token)
        except VKAPIError as exc:
            # С ключом сообщества голая ссылка в attachments вызывает Error 100 (link_photo_sizing_rule)
            if exc.code == 100 and "attachments" in params and not photo_att:
                logger.warning("ВК отклонил ссылочное вложение (%s) — публикуем текст без карточки ссылки.", exc.vk_message)
                params.pop("attachments", None)
                attachments = []
                resp = self.call_api("wall.post", params, token=self.vk_token)
            elif exc.code in {15, 27, 1051} and self.vk_user_token:
                resp = self.call_api("wall.post", params, token=self.vk_user_token)
            else:
                raise
        post_id = int((resp or {}).get("post_id", 0))
        post_url = f"https://vk.ru/wall-{group_id}_{post_id}" if post_id else f"https://vk.ru/{self.raw_group}"
        return {
            "group_id": group_id,
            "post_id": post_id,
            "post_url": post_url,
            "photo_attached": bool(photo_att),
            "attachments": attachments,
        }

    def edit_wall_post(
        self,
        post_id: int,
        message: str,
        *,
        image: PreparedImage | None = None,
    ) -> dict[str, Any]:
        """Обновляет существующий пост на стене сообщества (включая добавление/замену фото)."""
        group_id = self.resolve_group_id()
        photo_att: str | None = None
        if image is not None:
            photo_att = self.upload_wall_photo(image)

        params: dict[str, Any] = {
            "owner_id": -group_id,
            "post_id": post_id,
            "from_group": 1,
            "message": message,
        }
        if photo_att:
            params["attachments"] = photo_att

        edit_token = self.vk_user_token or self.vk_token
        try:
            self.call_api("wall.edit", params, token=edit_token)
        except VKAPIError as exc:
            if edit_token != self.vk_token:
                self.call_api("wall.edit", params, token=self.vk_token)
            else:
                raise exc

        return {
            "group_id": group_id,
            "post_id": post_id,
            "post_url": f"https://vk.ru/wall-{group_id}_{post_id}",
            "photo_attached": bool(photo_att),
        }

    def update_group_info(
        self,
        *,
        title: str = "",
        description: str = "",
        status_text: str = "",
        subject: int | None = None,
    ) -> dict[str, bool]:
        """Обновляет название, тематику, описание и строку статуса сообщества ВКонтакте."""
        group_id = self.resolve_group_id()
        outcomes = {"info_updated": False, "status_updated": False}

        edit_params: dict[str, Any] = {"group_id": group_id}
        if title.strip():
            edit_params["title"] = title.strip()
        if description.strip():
            edit_params["description"] = description.strip()
        if subject is not None and int(subject) > 0:
            edit_params["subject"] = int(subject)

        if len(edit_params) > 1:
            self.call_api("groups.edit", edit_params, token=self.vk_token)
            outcomes["info_updated"] = True
            logger.info("Название и описание группы (ID %d) успешно обновлены.", group_id)

        if status_text.strip():
            trimmed_status = status_text.strip()[:140]
            try:
                self.call_api(
                    "status.set",
                    {"group_id": group_id, "text": trimmed_status},
                    token=self.vk_user_token or self.vk_token,
                )
                outcomes["status_updated"] = True
                logger.info("Статус сообщества успешно обновлён.")
            except VKAPIError as exc:
                logger.warning("Не удалось обновить строку статуса через API (%s) — можно вставить вручную.", exc)

        return outcomes

    def upload_group_cover(
        self,
        cover_bytes: bytes,
        width: int = 1920,
        height: int = 768,
    ) -> bool:
        """Загружает и устанавливает широкоформатную обложку (шапку 1920x768) сообщества."""
        group_id = self.resolve_group_id()
        srv = self.call_api(
            "photos.getOwnerCoverPhotoUploadServer",
            {
                "group_id": group_id,
                "crop_x": 0,
                "crop_y": 0,
                "crop_x2": width,
                "crop_y2": height,
            },
            token=self.vk_token,
        )
        upload_url = str((srv or {}).get("upload_url", "")).strip()
        if not upload_url:
            raise VKAPIError(-3, "VK не вернул upload_url для обложки сообщества", method="upload_group_cover")

        body, content_type = encode_multipart_photo("photo", "cover.jpg", "image/jpeg", cover_bytes)
        req = urllib.request.Request(
            upload_url,
            data=body,
            headers={"Content-Type": content_type, "User-Agent": USER_AGENT},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=35, context=self.ssl_ctx) as resp:
            uploaded = json.loads(resp.read().decode("utf-8", errors="replace"))

        if not uploaded.get("photo") or not uploaded.get("hash"):
            raise VKAPIError(-3, f"Сервер загрузки обложки отклонил файл: {uploaded}", method="upload_group_cover")

        self.call_api(
            "photos.saveOwnerCoverPhoto",
            {"hash": uploaded["hash"], "photo": uploaded["photo"]},
            token=self.vk_token,
        )
        logger.info("Широкоформатная обложка 1920x768 успешно установлена в группу ID %d!", group_id)
        return True

    def upload_group_avatar(self, avatar_bytes: bytes) -> bool:
        """Загружает и устанавливает главный аватар сообщества (строго owner_id=-group_id)."""
        group_id = abs(self.resolve_group_id())
        if group_id <= 0:
            raise ValueError("Некорректный group_id для загрузки аватара")

        photo_token = self.vk_user_token or self.vk_token
        try:
            srv = self.call_api(
                "photos.getOwnerPhotoUploadServer",
                {"owner_id": -group_id},
                token=photo_token,
            )
        except VKAPIError as exc:
            if exc.code in {15, 27}:
                logger.warning(
                    "Загрузка аватара через API требует пользовательский токен администратора (%s). "
                    "Файл vk-news-agent/branding/avatar.jpg готов для быстрой ручной загрузки.",
                    exc.vk_message,
                )
                return False
            raise

        upload_url = str((srv or {}).get("upload_url", "")).strip()
        if not upload_url:
            return False

        info = inspect_image_dimensions(avatar_bytes)
        extra_crop: dict[str, str] | None = None
        if info is not None:
            _, aw, ah = info
            side = min(aw, ah)
            extra_crop = {"_square_crop": f"0,0,{side}"}

        body, content_type = encode_multipart_photo(
            "photo",
            "avatar.jpg",
            "image/jpeg",
            avatar_bytes,
            extra_fields=extra_crop,
        )
        req = urllib.request.Request(
            upload_url,
            data=body,
            headers={"Content-Type": content_type, "User-Agent": USER_AGENT},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=35, context=self.ssl_ctx) as resp:
            uploaded = json.loads(resp.read().decode("utf-8", errors="replace"))

        if not uploaded.get("photo") or not uploaded.get("hash"):
            logger.warning("Сервер загрузки аватара не принял файл: %s", uploaded)
            return False

        self.call_api(
            "photos.saveOwnerPhoto",
            {
                "server": uploaded.get("server"),
                "hash": uploaded["hash"],
                "photo": uploaded["photo"],
            },
            token=photo_token,
        )
        logger.info("Аватар сообщества успешно установлен в группу ID %d!", group_id)
        return True

    def diagnose_capabilities(self) -> dict[str, Any]:
        """Безопасная проверка прав токена ВК (без публикации на стену и без вывода самих ключей)."""
        diag: dict[str, Any] = {
            "has_vk_token": bool(self.vk_token),
            "has_vk_user_token": bool(self.vk_user_token),
            "vk_token_shape": inspect_vk_token_shape(self.raw_vk_token),
            "vk_user_token_shape": inspect_vk_token_shape(self.raw_vk_user_token),
        }
        group_id = self.resolve_group_id()
        diag["group_id"] = group_id

        # 1. Проверяем тип основного токена и пользовательского токена
        try:
            users = self.call_api("users.get", {}, token=self.vk_token)
            diag["vk_token_is_user"] = bool(isinstance(users, list) and len(users) > 0)
        except VKAPIError as exc:
            diag["vk_token_is_user"] = False
            diag["users_get_error"] = {"code": exc.code, "msg": exc.vk_message}

        if self.vk_user_token:
            try:
                u2 = self.call_api("users.get", {}, token=self.vk_user_token)
                diag["vk_user_token_valid_user"] = bool(isinstance(u2, list) and len(u2) > 0)
            except VKAPIError as exc:
                diag["vk_user_token_valid_user"] = False
                diag["vk_user_token_error"] = {"code": exc.code, "msg": exc.vk_message}

        try:
            perms = self.call_api("groups.getTokenPermissions", {}, token=self.vk_token)
            diag["community_token_permissions"] = perms
        except VKAPIError as exc:
            diag["community_token_permissions_error"] = {"code": exc.code, "msg": exc.vk_message}

        photo_token = self.vk_user_token or self.vk_token

        # 2. Проверяем доступность различных методов загрузки медиа
        probes = [
            ("photos.getWallUploadServer", {"group_id": group_id}, photo_token),
            ("photos.getOwnerPhotoUploadServer", {"owner_id": -group_id}, photo_token),
            ("photos.getOwnerCoverPhotoUploadServer", {"group_id": group_id, "crop_x2": 1920, "crop_y2": 768}, self.vk_token),
            ("photos.getMessagesUploadServer", {"peer_id": 0}, self.vk_token),
            ("docs.getWallUploadServer", {"group_id": group_id}, self.vk_token),
        ]
        methods_status: dict[str, Any] = {}
        for m_name, m_params, m_tok in probes:
            try:
                res = self.call_api(m_name, m_params, token=m_tok)
                methods_status[m_name] = {"ok": bool(res and res.get("upload_url"))}
            except VKAPIError as exc:
                methods_status[m_name] = {"ok": False, "code": exc.code, "msg": exc.vk_message}
        diag["methods"] = methods_status

        # 3. Текущее состояние группы и последних постов (типы вложений, закреп, обложка, аватар)
        try:
            grp = self.call_api(
                "groups.getById",
                {"group_id": str(group_id), "fields": "cover,description,status,photo_200,has_photo"},
                token=self.vk_token,
            )
            groups_list = grp.get("groups") if isinstance(grp, dict) else grp
            if isinstance(groups_list, list) and groups_list:
                g0 = groups_list[0]
                diag["group_state"] = {
                    "name": g0.get("name"),
                    "status": g0.get("status"),
                    "has_photo": g0.get("has_photo"),
                    "photo_200": g0.get("photo_200"),
                    "cover_enabled": (g0.get("cover") or {}).get("enabled"),
                }
        except VKAPIError as exc:
            diag["group_state_error"] = {"code": exc.code, "msg": exc.vk_message}

        try:
            wall = self.call_api(
                "wall.get",
                {"owner_id": -group_id, "count": 5},
                token=photo_token,
            )
            items = (wall or {}).get("items") or []
            diag["recent_wall"] = [
                {
                    "id": it.get("id"),
                    "is_pinned": it.get("is_pinned"),
                    "text_head": (it.get("text") or "")[:60],
                    "attachments": [(a.get("type") or "") for a in (it.get("attachments") or [])],
                }
                for it in items
            ]
        except VKAPIError as exc:
            diag["recent_wall_error"] = {"code": exc.code, "msg": exc.vk_message}

        return diag

    def pin_wall_post(self, post_id: int) -> bool:

        """Закрепляет запись вверху стены сообщества."""
        if post_id <= 0:
            return False
        group_id = self.resolve_group_id()
        try:
            self.call_api(
                "wall.pin",
                {"owner_id": -group_id, "post_id": post_id},
                token=self.vk_user_token or self.vk_token,
            )
            logger.info("Пост #%d закреплён в шапке стены группы!", post_id)
            return True
        except VKAPIError as exc:
            logger.warning("Не удалось автоматически закрепить пост #%d (%s) — закрепите его в 1 клик в ВК.", post_id, exc)
            return False

