"""Сборщик, фильтр и ранжировщик автомобильных новостей из RSS/Atom-лент."""

from __future__ import annotations

import gzip
import html
import logging
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from dedup_store import (
    DedupStore,
    extract_signature_tokens,
    strip_source_suffix,
    token_similarity,
)

logger = logging.getLogger("vk_news_agent.fetcher")

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
SOCIAL_BOT_USER_AGENT = "Mozilla/5.0 (compatible; vkShare; +http://vk.com/dev/Share)"
SSO_BOUNCE_MARKERS = ("auth-controller", "unity-id-sdk", "/sso/", "passport.", "id.rambler.ru")

BAD_IMAGE_PATTERNS = (
    "logo",
    "avatar",
    "icon",
    "favicon",
    "sprite",
    "88x31",
    "banner",
    "pixel",
    "counter",
    "mc.yandex",
    "top100",
    "googleusercontent.com",
    "gstatic.com",
    "1x1",
    "spacer",
    ".svg",
    ".gif",
)

XML_ENCODING_RE = re.compile(rb'<\?xml[^>]*encoding=["\']([^"\']+)["\']', re.IGNORECASE)
HTML_CHARSET_RE = re.compile(rb'charset=["\']?([a-zA-Z0-9_-]+)', re.IGNORECASE)
IMG_SRC_RE = re.compile(
    r'<(?:img|source)\b[^>]*?\b(?:data-src|data-original|src|srcset)\s*=\s*(?:["\']([^"\']+)["\']|([^\s>"\']+))',
    re.IGNORECASE,
)
OG_IMAGE_RE = re.compile(
    r'<meta\b[^>]*?\b(?:property|name|itemprop)\s*=\s*["\']?(?:og:image(?::secure_url|:url)?|vk:image|twitter:image(?::src)?|image)["\']?[^>]*?\bcontent\s*=\s*(?:["\']([^"\']+)["\']|([^\s>"\']+))'
    r'|<meta\b[^>]*?\bcontent\s*=\s*(?:["\']([^"\']+)["\']|([^\s>"\']+))[^>]*?\b(?:property|name|itemprop)\s*=\s*["\']?(?:og:image(?::secure_url|:url)?|vk:image|twitter:image(?::src)?|image)["\']?',
    re.IGNORECASE,
)
JSONLD_IMAGE_RE = re.compile(
    r'"image"\s*:\s*(?:\[\s*)?(?:\{\s*[^}]*?"url"\s*:\s*"(https?:[^"]+)"|"(https?:[^"]+)")',
    re.IGNORECASE,
)
OG_DESC_RE = re.compile(
    r'<meta\b[^>]*?\b(?:property|name)\s*=\s*["\']?(?:og:description|description)["\']?[^>]*?\bcontent\s*=\s*["\']([^"\']+)["\']'
    r'|<meta\b[^>]*?\bcontent\s*=\s*["\']([^"\']+)["\'][^>]*?\b(?:property|name)\s*=\s*["\']?(?:og:description|description)["\']?',
    re.IGNORECASE,
)
PARAGRAPH_RE = re.compile(r"<p(?:\s[^>]*)?>(.*?)</p>", re.IGNORECASE | re.DOTALL)


@dataclass
class NewsCandidate:
    """Единая карточка найденной новости или автостатьи."""

    title: str
    url: str
    source_name: str
    category: str  # "news" | "tip" | "local_76"
    summary: str
    full_text: str = ""
    published_at: datetime | None = None
    guid: str = ""
    image_urls: list[str] = field(default_factory=list)
    score: float = 0.0
    matched_topics: list[str] = field(default_factory=list)

    @property
    def best_text(self) -> str:
        return self.full_text if len(self.full_text) > len(self.summary) else self.summary


def decode_http_bytes(raw_bytes: bytes, content_type_header: str = "") -> str:
    """Корректно декодирует UTF-8, Windows-1251 (Drom.ru и др.) и KOI8-R."""
    candidates: list[str] = []

    if content_type_header:
        m = re.search(r"charset=([a-zA-Z0-9_-]+)", content_type_header, re.IGNORECASE)
        if m:
            candidates.append(m.group(1).strip().lower())

    head = raw_bytes[:1024]
    m_xml = XML_ENCODING_RE.search(head)
    if m_xml:
        candidates.append(m_xml.group(1).decode("ascii", errors="ignore").strip().lower())

    m_html = HTML_CHARSET_RE.search(raw_bytes[:4096])
    if m_html:
        candidates.append(m_html.group(1).decode("ascii", errors="ignore").strip().lower())

    for fallback_enc in ("utf-8", "cp1251", "windows-1251", "koi8-r"):
        if fallback_enc not in candidates:
            candidates.append(fallback_enc)

    for enc in candidates:
        try:
            return raw_bytes.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue

    return raw_bytes.decode("utf-8", errors="replace")


def _decompress_response(raw: bytes, encoding: str) -> bytes:
    enc = (encoding or "").lower()
    if enc == "gzip" or raw[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(raw)
        except OSError:
            return raw
    if enc == "deflate":
        try:
            return zlib.decompress(raw)
        except zlib.error:
            try:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
            except zlib.error:
                return raw
    return raw


def fetch_url_bytes(url: str, timeout: int = 15) -> tuple[bytes, str, str]:
    """Скачивает URL; если СМИ (например, Motor.ru / Rambler) редиректит на SSO-заглушку, повторяет как vkShare."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/xml, text/xml, text/html;q=0.9, */*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
        },
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        raw = _decompress_response(resp.read(), resp.headers.get("Content-Encoding") or "")
        content_type = resp.headers.get("Content-Type") or ""
        final_url = resp.geturl() or url

    if any(marker in final_url.lower() for marker in SSO_BOUNCE_MARKERS):
        for ua in (SOCIAL_BOT_USER_AGENT, "TelegramBot (like TwitterBot)", USER_AGENT):
            try:
                bot_req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": ua,
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "ru-RU,ru;q=0.9",
                        "Accept-Encoding": "gzip, deflate",
                    },
                )
                opener = urllib.request.build_opener(
                    urllib.request.HTTPCookieProcessor(),
                    urllib.request.HTTPSHandler(context=ctx),
                )
                with opener.open(bot_req, timeout=timeout) as b_resp:
                    b_raw = _decompress_response(b_resp.read(), b_resp.headers.get("Content-Encoding") or "")
                    b_ct = b_resp.headers.get("Content-Type") or ""
                    b_final = b_resp.geturl() or url
                if len(b_raw) > 2500 and not any(m in b_final.lower() for m in SSO_BOUNCE_MARKERS):
                    return b_raw, b_ct, b_final
            except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                continue
        final_url = url

    return raw, content_type, final_url


def is_usable_image_url(url: str) -> bool:
    """Отсекает логотипы, счётчики, SVG/GIF-заглушки и слишком короткие ссылки."""
    if not url or not isinstance(url, str):
        return False
    cleaned = url.strip()
    if not cleaned.startswith(("http://", "https://")):
        return False
    lower = cleaned.lower()
    for bad in BAD_IMAGE_PATTERNS:
        if bad in lower:
            return False
    return True


def prioritize_image_urls(urls: list[str], base_url: str = "") -> list[str]:
    """Нормализует, фильтрует и сортирует ссылки на фото (JPG/PNG выше WebP)."""
    seen: set[str] = set()
    jpg_png: list[str] = []
    other: list[str] = []

    for raw in urls:
        if not raw:
            continue
        resolved = html.unescape(raw.strip())
        if base_url and resolved.startswith("/"):
            resolved = urllib.parse.urljoin(base_url, resolved)
        if not is_usable_image_url(resolved):
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        path_lower = urllib.parse.urlparse(resolved).path.lower()
        if path_lower.endswith((".jpg", ".jpeg", ".png")):
            jpg_png.append(resolved)
        else:
            other.append(resolved)

    return jpg_png + other


def clean_html_to_text(raw_html: str) -> str:
    """Превращает HTML из RSS/статьи в чистый читаемый текст с абзацами."""
    if not raw_html:
        return ""
    text = re.sub(r"<(script|style|noscript|iframe)[^>]*>.*?</\1>", " ", raw_html, flags=re.I | re.S)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|li|h[1-6]|blockquote)>", "\n\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ").replace("\u200b", "")
    lines = []
    for line in text.splitlines():
        compact = re.sub(r"[ \t]+", " ", line).strip()
        if not compact:
            continue
        # Убираем типичный мусор внизу RSS-заметок
        if re.match(r"^(читайте также|смотрите также|подписывайтесь на|источник:|фото:)", compact, re.I):
            continue
        lines.append(compact)
    return "\n\n".join(lines).strip()


def parse_pub_date(raw_date: str) -> datetime | None:
    """Парсит RFC-2822 и ISO-8601 даты из RSS/Atom в UTC."""
    if not raw_date:
        return None
    val = raw_date.strip()
    try:
        dt = parsedate_to_datetime(val)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        pass

    iso_val = val.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso_val)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower() if "}" in tag else tag.lower()


def parse_feed_xml(xml_text: str, source_cfg: dict[str, Any]) -> list[NewsCandidate]:
    """Универсальный парсер RSS 2.0 и Atom XML."""
    # Заменяем объявленную кодировку в прологе, т.к. строка уже декодирована в Unicode
    sanitized = re.sub(
        r'(<\?xml[^>]*encoding=["\'])([^"\']+)(["\'])',
        r"\1utf-8\3",
        xml_text.lstrip("\ufeff \t\r\n"),
        count=1,
        flags=re.IGNORECASE,
    )
    try:
        root = ET.fromstring(sanitized.encode("utf-8"))
    except ET.ParseError as exc:
        logger.warning("Не удалось разобрать XML ленты %s: %s", source_cfg.get("name"), exc)
        return []

    default_source_name = str(source_cfg.get("name", "Автоновости"))
    default_category = str(source_cfg.get("category", "news"))
    feed_url = str(source_cfg.get("url", ""))

    items: list[NewsCandidate] = []
    root_tag = _local_tag(root.tag)

    # Собираем элементы <item> (RSS) или <entry> (Atom)
    item_nodes: list[ET.Element] = []
    if root_tag == "feed":
        item_nodes = [el for el in root if _local_tag(el.tag) == "entry"]
    else:
        for el in root.iter():
            if _local_tag(el.tag) == "item":
                item_nodes.append(el)

    for node in item_nodes:
        title = ""
        link = ""
        guid = ""
        pub_date_raw = ""
        description_html = ""
        content_encoded_html = ""
        sub_source_name = ""
        raw_images: list[str] = []
        categories: list[str] = []

        for child in node:
            ctag = _local_tag(child.tag)
            text_val = (child.text or "").strip()

            if ctag == "title" and not title:
                title = html.unescape(text_val).replace("\xa0", " ").strip()
            elif ctag == "link":
                href = (child.attrib.get("href") or "").strip()
                rel = (child.attrib.get("rel") or "alternate").strip().lower()
                if href and rel in {"alternate", ""}:
                    link = href
                elif text_val and not link:
                    link = text_val
            elif ctag in {"guid", "id"} and not guid:
                guid = text_val
            elif ctag in {"pubdate", "published", "updated", "date"} and not pub_date_raw:
                pub_date_raw = text_val
            elif ctag in {"description", "summary"} and text_val:
                description_html = text_val
            elif ctag in {"encoded", "content", "full-text", "fulltext"} and text_val:
                if len(text_val) > len(content_encoded_html):
                    content_encoded_html = text_val
            elif ctag == "source" and text_val:
                sub_source_name = text_val
            elif ctag == "category" and text_val:
                categories.append(text_val)
        # Ищем вложения на любой глубине внутри <item>/<entry> (включая <media:group><media:content .../>)
        for sub in node.iter():
            stag = _local_tag(sub.tag)
            if stag == "enclosure":
                enc_url = (sub.attrib.get("url") or "").strip()
                enc_type = (sub.attrib.get("type") or "").lower()
                if enc_url and (not enc_type or enc_type.startswith("image/")):
                    raw_images.append(enc_url)
            elif stag in {"thumbnail", "content"} and sub.attrib.get("url"):
                media_url = (sub.attrib.get("url") or "").strip()
                medium = (sub.attrib.get("medium") or "").lower()
                mtype = (sub.attrib.get("type") or "").lower()
                if (not mtype and not medium) or medium == "image" or mtype.startswith("image/"):
                    raw_images.append(media_url)

        if not title or not link:
            continue

        # Достаём картинки из HTML-блоков
        for html_chunk in (content_encoded_html, description_html):
            if html_chunk:
                for m_img in IMG_SRC_RE.finditer(html_chunk):
                    raw_src = (m_img.group(1) or m_img.group(2) or "").strip()
                    if raw_src:
                        first_part = raw_src.split(",")[0].strip().split(" ")[0].strip()
                        if first_part:
                            raw_images.append(first_part)

        clean_desc = clean_html_to_text(description_html)
        clean_full = clean_html_to_text(content_encoded_html)

        # В Google News description часто просто дублирует заголовок со ссылкой
        if "news.google.com" in feed_url and token_similarity(
            extract_signature_tokens(clean_desc), extract_signature_tokens(title)
        ) >= 0.75:
            clean_desc = ""

        clean_title = strip_source_suffix(title)
        effective_source = sub_source_name or default_source_name
        summary_text = clean_desc or (clean_full[:600] if clean_full else "")
        if categories and not summary_text:
            summary_text = f"Рубрика: {', '.join(categories)}."

        items.append(
            NewsCandidate(
                title=clean_title,
                url=link.strip(),
                source_name=effective_source,
                category=default_category,
                summary=summary_text,
                full_text=clean_full,
                published_at=parse_pub_date(pub_date_raw),
                guid=guid or link.strip(),
                image_urls=prioritize_image_urls(raw_images, base_url=link),
            )
        )

    return items


def evaluate_and_score_candidate(
    item: NewsCandidate,
    source_cfg: dict[str, Any],
    config: dict[str, Any],
    now: datetime | None = None,
) -> bool:
    """Проверяет релевантность/свежесть кандидата и выставляет итоговый балл."""
    dt_now = now or datetime.now(timezone.utc)
    posting_cfg = config.get("posting", {})
    filters_cfg = config.get("filters", {})

    max_age_hours = float(posting_cfg.get("max_news_age_hours", 48))
    if item.published_at is not None:
        age = dt_now - item.published_at
        if age > timedelta(hours=max_age_hours):
            return False

    combined_lower = f"{item.title}\n{item.summary}\n{item.full_text[:1500]}".lower().replace("ё", "е")
    title_lower = item.title.lower().replace("ё", "е")

    # 1. Исключаем рекламу, авиацию, шоу-бизнес и нерелевантные темы
    for excl in filters_cfg.get("exclude_keywords", []):
        excl_norm = str(excl).lower().replace("ё", "е")
        if excl_norm and excl_norm in combined_lower:
            return False

    # 2. Строгий фильтр автотематики для городских порталов (76.ру) и поисковых лент
    strict_keywords = [
        str(k).lower().replace("ё", "е")
        for k in filters_cfg.get("strict_auto_keywords", [])
        if k
    ]
    matched_auto = [kw for kw in strict_keywords if kw in combined_lower]
    if source_cfg.get("strict_auto_filter", False):
        # Для общих лент требуем попадание авто-ключа в заголовок или минимум 2 разных авто-ключа в текст
        in_title = any(kw in title_lower for kw in strict_keywords)
        if not in_title and len(matched_auto) < 2:
            return False

    # 3. Уточняем категорию (local_76 / tip / news)
    if any(loc in combined_lower for loc in ("ярослав", "рыбинск", "тутаев", "переславл", "76 регион", "76.ru")):
        item.category = "local_76"
    else:
        tip_kws = [
            str(k).lower().replace("ё", "е")
            for k in filters_cfg.get("tip_indicator_keywords", [])
            if k
        ]
        if any(tk in title_lower for tk in tip_kws) or sum(1 for tk in tip_kws if tk in combined_lower) >= 2:
            item.category = "tip"

    # 4. Считаем рейтинг кандидата
    score = float(source_cfg.get("weight", 10))

    # Бонус за свежесть (до +14 баллов за новости последних часов)
    if item.published_at is not None:
        age_hours = max(0.0, (dt_now - item.published_at).total_seconds() / 3600.0)
        freshness = max(0.0, 14.0 - (age_hours * 0.35))
        score += freshness
    else:
        score += 4.0

    # Бонусы за приоритетные темы (Ярославль, ПДД, штрафы, авто с пробегом, обслуживание)
    boost_map = filters_cfg.get("boost_keywords", {})
    if isinstance(boost_map, dict):
        for kw, pts in boost_map.items():
            kw_norm = str(kw).lower().replace("ё", "е")
            if kw_norm in title_lower:
                score += float(pts)
                item.matched_topics.append(kw)
            elif kw_norm in combined_lower:
                score += float(pts) * 0.45
                item.matched_topics.append(kw)

    # Бонус за полноту фактуры и наличие качественного фото
    text_len = len(item.best_text)
    if text_len >= 600:
        score += 6.0
    elif text_len >= 200:
        score += 3.0

    if item.image_urls:
        score += 5.0

    item.score = round(score, 2)
    min_score = float(posting_cfg.get("min_relevance_score", 12))
    return item.score >= min_score


def deduplicate_batch_clusters(candidates: list[NewsCandidate]) -> list[NewsCandidate]:
    """Группирует одинаковые новости от разных СМИ и оставляет самую полную/свежую версию."""
    sorted_cands = sorted(
        candidates,
        key=lambda c: (c.score, len(c.image_urls) > 0, len(c.best_text)),
        reverse=True,
    )
    unique: list[NewsCandidate] = []
    signatures: list[list[str]] = []

    for cand in sorted_cands:
        sig = extract_signature_tokens(cand.title)
        is_cluster_dup = False
        for existing_sig in signatures:
            if token_similarity(sig, existing_sig) >= 0.50:
                is_cluster_dup = True
                break
        if not is_cluster_dup:
            unique.append(cand)
            signatures.append(sig)

    return unique


def enrich_candidate_from_webpage(item: NewsCandidate, timeout: int = 12) -> NewsCandidate:
    """Догружает og:image и абзацы статьи со страницы источника, если в RSS мало деталей."""
    needs_image = len(item.image_urls) == 0
    needs_text = len(item.best_text) < 350
    if not needs_image and not needs_text:
        return item

    # Ссылки-обёртки Google News используют JS-редирект; не тратим время, если не редиректнуло по HTTP
    try:
        raw_bytes, content_type, final_url = fetch_url_bytes(item.url, timeout=timeout)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        logger.debug("Не удалось догрузить страницу %s: %s", item.url, exc)
        return item

    if "news.google.com" in final_url:
        return item

    item.url = final_url
    page_html = decode_http_bytes(raw_bytes, content_type)
    # Вырезаем гигантские inline-<style>, чтобы поиск мета-тегов и иллюстраций работал по всей странице мгновенно
    html_no_style = re.sub(r"<style\b[^>]*>.*?</style>", " ", page_html, flags=re.I | re.S)

    if needs_image:
        found_og: list[str] = []
        for m in OG_IMAGE_RE.finditer(html_no_style):
            img = next((g for g in m.groups() if g), "")
            if img:
                found_og.append(img)
        for m_ld in JSONLD_IMAGE_RE.finditer(html_no_style):
            img = (m_ld.group(1) or m_ld.group(2) or "").replace("\\/", "/")
            if img:
                found_og.append(img)
        if not found_og:
            for m_img in IMG_SRC_RE.finditer(html_no_style):
                raw_src = (m_img.group(1) or m_img.group(2) or "").strip()
                if not raw_src:
                    continue
                # Для srcset="url1 1x, url2 2x" берём первый валидный URL
                first_part = raw_src.split(",")[0].strip().split(" ")[0].strip()
                if first_part:
                    found_og.append(first_part)
        item.image_urls = prioritize_image_urls(found_og, base_url=final_url)

    if needs_text:
        meta_desc = ""
        m_desc = OG_DESC_RE.search(html_no_style)
        if m_desc:
            meta_desc = clean_html_to_text(m_desc.group(1) or m_desc.group(2) or "")

        paragraphs: list[str] = []
        for p_match in PARAGRAPH_RE.finditer(html_no_style):
            p_txt = clean_html_to_text(p_match.group(1))
            if len(p_txt) < 45:
                continue
            if any(
                skip in p_txt.lower()
                for skip in ("cookie", "персональных данных", "подпишитесь", "все права защищены", "реклама")
            ):
                continue
            paragraphs.append(p_txt)
            if sum(len(x) for x in paragraphs) >= 2400:
                break

        assembled = "\n\n".join(paragraphs).strip()
        if len(assembled) > len(item.full_text):
            item.full_text = assembled
        elif meta_desc and len(meta_desc) > len(item.summary):
            item.summary = meta_desc

    return item


def collect_fresh_candidates(
    config: dict[str, Any],
    dedup: DedupStore,
    now: datetime | None = None,
) -> list[NewsCandidate]:
    """Опрашивает все активные RSS-источники, отсеивает дубли и возвращает отсортированных кандидатов."""
    sources = config.get("sources", [])
    raw_pool: list[NewsCandidate] = []

    for src in sources:
        if not isinstance(src, dict) or not src.get("enabled", True):
            continue
        url = str(src.get("url", "")).strip()
        name = str(src.get("name", url))
        if not url:
            continue

        try:
            raw_bytes, content_type, _ = fetch_url_bytes(url, timeout=15)
            xml_text = decode_http_bytes(raw_bytes, content_type)
            parsed_items = parse_feed_xml(xml_text, src)
            logger.info("Источник «%s»: получено %d записей", name, len(parsed_items))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            logger.warning("Источник «%s» временно недоступен: %s", name, exc)
            continue

        kept = 0
        for item in parsed_items:
            if not evaluate_and_score_candidate(item, src, config, now=now):
                continue
            is_dup, reason = dedup.is_duplicate(item.url, item.title, item.guid)
            if is_dup:
                logger.debug("Пропущен дубль «%s» (%s)", item.title[:55], reason)
                continue
            raw_pool.append(item)
            kept += 1
        logger.info("Источник «%s»: прошло фильтр и дедупликацию %d", name, kept)

    clustered = deduplicate_batch_clusters(raw_pool)
    logger.info("Итого уникальных свежих кандидатов после кластеризации: %d", len(clustered))
    return clustered
