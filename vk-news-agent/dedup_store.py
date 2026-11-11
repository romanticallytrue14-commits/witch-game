"""Хранилище истории публикаций и многоуровневая защита от дублей."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

STOPWORDS_RU = {
    "без", "более", "больше", "будет", "будто", "была", "были", "было", "быть",
    "вам", "вас", "ведь", "весь", "вдоль", "вместо", "вне", "вниз", "внизу",
    "внутри", "вокруг", "вот", "все", "всего", "всей", "всем", "всех", "всю",
    "год", "года", "году", "лет", "даже", "для", "до", "другой", "его", "ему",
    "если", "есть", "еще", "же", "за", "здесь", "из", "из-за", "или", "им",
    "ими", "их", "как", "какая", "какие", "какой", "когда", "кто", "ли", "либо",
    "между", "меня", "мне", "много", "может", "могут", "можно", "мой", "моя",
    "мы", "на", "над", "надо", "наш", "наша", "наши", "не", "него", "нее",
    "ней", "нельзя", "нет", "ни", "нибудь", "никогда", "ним", "них", "ничего",
    "но", "новый", "новая", "новые", "нового", "ну", "о", "об", "обо", "один",
    "одна", "одни", "однако", "около", "он", "она", "они", "оно", "опять",
    "от", "перед", "по", "под", "после", "потом", "потому", "почти", "почему",
    "при", "про", "раз", "разве", "свой", "свои", "себе", "себя", "сегодня",
    "сейчас", "совсем", "так", "такая", "также", "такие", "такой", "там", "те",
    "тебе", "тебя", "тем", "теперь", "то", "тогда", "того", "тоже", "только",
    "том", "тот", "три", "тут", "ты", "уже", "чем", "через", "что", "чтоб",
    "чтобы", "чуть", "эта", "эти", "этим", "этих", "это", "этого", "этой",
    "этом", "этот", "эту", "якобы", "стало", "стали", "стал", "рассказал",
    "рассказали", "назвал", "назвали", "объяснил", "объяснили", "сообщил",
    "сообщили", "заявил", "заявили", "показал", "показали", "новости",
}

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "yclid", "gclid", "fbclid", "from", "ref", "oc", "rss", "ysclid",
}

RU_ENDINGS_RE = re.compile(
    r"(иями|ями|ами|ого|его|ому|ему|ыми|ими|ей|ий|ый|ая|яя|ое|ее|ые|ие|"
    r"ах|ях|ов|ев|ам|ям|ом|ем|ую|юю|ть|ти|ла|ли|ло|на|но|ны|а|я|у|ю|ы|и|е|о)$"
)

SOURCE_SUFFIX_RE = re.compile(r"\s+[-–—|]\s+[^-–—|]{2,45}$")


def normalize_url(url: str) -> str:
    """Нормализует ссылку для точного сравнения (убирает utm-метки, якоря и www)."""
    if not url:
        return ""
    raw = url.strip()
    try:
        parsed = urlparse(raw)
    except ValueError:
        return raw.lower()

    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = re.sub(r"/+$", "", parsed.path or "")
    clean_query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if k.lower() not in TRACKING_PARAMS and not k.lower().startswith("utm_")
    ]
    clean_query.sort()
    return urlunparse((scheme, netloc, path, "", urlencode(clean_query), ""))


def strip_source_suffix(title: str) -> str:
    """Убирает хвост названия СМИ в конце заголовка (например, ' - Ведомости')."""
    if not title:
        return ""
    cleaned = title.strip()
    # В заголовках Google News часто в конце стоит ' - Название СМИ'
    if " - " in cleaned or " — " in cleaned or " | " in cleaned:
        candidate = SOURCE_SUFFIX_RE.sub("", cleaned).strip()
        if len(candidate) >= 18:
            return candidate
    return cleaned


def stem_token(token: str) -> str:
    """Лёгкая нормализация русских и латинских слов для поиска смысловых дублей."""
    word = token.lower().replace("ё", "е")
    if len(word) <= 3:
        return word
    # Если это латиница/марка/модель (belgee, x50, haval, м8) — оставляем как есть
    if re.search(r"[a-z]", word):
        return word
    stemmed = RU_ENDINGS_RE.sub("", word)
    if len(stemmed) < 3:
        return word[:4]
    return stemmed[:7]


def extract_signature_tokens(text: str) -> list[str]:
    """Извлекает значимые нормализованные токены из заголовка или первого абзаца."""
    if not text:
        return []
    cleaned = strip_source_suffix(text).lower().replace("ё", "е")
    raw_tokens = re.findall(r"[a-zа-я0-9]+(?:-[a-zа-я0-9]+)?", cleaned)
    result: list[str] = []
    seen: set[str] = set()
    for tok in raw_tokens:
        tok_clean = tok.strip("-")
        if len(tok_clean) < 2:
            continue
        if len(tok_clean) == 2 and not any(ch.isdigit() for ch in tok_clean):
            continue
        if tok_clean in STOPWORDS_RU:
            continue
        stemmed = stem_token(tok_clean)
        if stemmed in STOPWORDS_RU or len(stemmed) < 2:
            continue
        if stemmed not in seen:
            seen.add(stemmed)
            result.append(stemmed)
    return result


def token_similarity(tokens_a: list[str] | set[str], tokens_b: list[str] | set[str]) -> float:
    """Комбинированная метрика близости двух наборов токенов (0.0 .. 1.0)."""
    set_a = set(tokens_a)
    set_b = set(tokens_b)
    if not set_a or not set_b:
        return 0.0
    inter = len(set_a & set_b)
    if inter == 0:
        return 0.0
    union = len(set_a | set_b)
    jaccard = inter / union
    min_len = min(len(set_a), len(set_b))
    overlap = inter / min_len if min_len > 0 else 0.0
    if min_len >= 5 and overlap >= 0.72:
        return max(jaccard, overlap * 0.85)
    return jaccard


def make_post_guid(seed: str) -> str:
    """Детерминированный идентификатор для параметра guid в VK wall.post."""
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    return f"greem-motor-{digest}"


class DedupStore:
    """Хранит историю опубликованных новостей и проверяет кандидатов на дубли."""

    def __init__(
        self,
        state_path: str | Path,
        similarity_threshold: float = 0.52,
        max_items: int = 500,
    ) -> None:
        self.state_path = Path(state_path)
        self.similarity_threshold = similarity_threshold
        self.max_items = max_items
        self.total_posts: int = 0
        self.used_evergreen_ids: list[str] = []
        self.items: list[dict[str, Any]] = []
        self._wall_signatures: list[set[str]] = []
        self._wall_urls: set[str] = set()
        self.load()

    def load(self) -> None:
        """Загружает state/published.json с защитой от повреждённых данных."""
        if not self.state_path.exists():
            return
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict):
            return

        total = raw.get("total_posts", 0)
        self.total_posts = int(total) if isinstance(total, (int, float)) and total >= 0 else 0

        evergreen = raw.get("used_evergreen_ids", [])
        if isinstance(evergreen, list):
            self.used_evergreen_ids = [str(x) for x in evergreen if isinstance(x, str) and x]

        loaded_items = raw.get("items", [])
        if isinstance(loaded_items, list):
            sanitized: list[dict[str, Any]] = []
            for entry in loaded_items[-self.max_items :]:
                if not isinstance(entry, dict):
                    continue
                title = str(entry.get("title", "")).strip()
                url = str(entry.get("url", "")).strip()
                tokens = entry.get("tokens")
                if not isinstance(tokens, list):
                    tokens = extract_signature_tokens(title)
                else:
                    tokens = [str(t) for t in tokens if isinstance(t, str)]
                sanitized.append(
                    {
                        "url": url,
                        "normalized_url": str(entry.get("normalized_url") or normalize_url(url)),
                        "guid": str(entry.get("guid", "")),
                        "title": title,
                        "tokens": tokens,
                        "category": str(entry.get("category", "news")),
                        "vk_post_id": entry.get("vk_post_id"),
                        "published_at": str(entry.get("published_at", "")),
                    }
                )
            self.items = sanitized

    def save(self) -> None:
        """Атомарно сохраняет state/published.json."""
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "total_posts": self.total_posts,
            "used_evergreen_ids": self.used_evergreen_ids[-200:],
            "items": self.items[-self.max_items :],
        }
        tmp_path = self.state_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp_path, self.state_path)

    def ingest_vk_wall_posts(self, wall_posts: list[dict[str, Any]]) -> None:
        """Добавляет последние посты со стены ВК в оперативную память для сверки дублей."""
        for post in wall_posts:
            if not isinstance(post, dict):
                continue
            text = str(post.get("text", "")).strip()
            if text:
                first_lines = "\n".join(text.splitlines()[:3])
                sig = set(extract_signature_tokens(first_lines))
                if len(sig) >= 3:
                    self._wall_signatures.append(sig)
                for found_url in re.findall(r"https?://[^\s)\]>]+", text):
                    self._wall_urls.add(normalize_url(found_url))
            attachments = post.get("attachments")
            if isinstance(attachments, list):
                for att in attachments:
                    if isinstance(att, dict) and att.get("type") == "link":
                        link_url = att.get("link", {}).get("url")
                        if isinstance(link_url, str) and link_url:
                            self._wall_urls.add(normalize_url(link_url))

    def is_duplicate(self, url: str, title: str, guid: str = "") -> tuple[bool, str]:
        """Проверяет, публиковалась ли уже эта новость (или этот же инфоповод из другого СМИ)."""
        norm_url = normalize_url(url)
        if norm_url:
            if norm_url in self._wall_urls:
                return True, "ссылка уже есть на стене ВК"
            for entry in self.items:
                if entry.get("normalized_url") == norm_url:
                    return True, "ссылка уже публиковалась ранее"

        if guid:
            for entry in self.items:
                if entry.get("guid") and entry.get("guid") == guid:
                    return True, "совпал RSS guid"

        cand_tokens = extract_signature_tokens(title)
        if len(cand_tokens) >= 3:
            for entry in self.items:
                sim = token_similarity(cand_tokens, entry.get("tokens", []))
                if sim >= self.similarity_threshold:
                    return True, f"похожий заголовок уже выходил ({sim:.0%}): {entry.get('title', '')[:60]}"
            for wall_sig in self._wall_signatures:
                sim = token_similarity(cand_tokens, wall_sig)
                if sim >= self.similarity_threshold:
                    return True, f"похожий пост уже есть на стене ВК ({sim:.0%})"

        return False, ""

    def pick_evergreen_topic(
        self,
        topics: list[dict[str, Any]],
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Выбирает ещё не использованную тему автосовета с учётом текущего сезона."""
        if not topics:
            return None
        dt = now or datetime.now(timezone.utc)
        is_winter_season = dt.month in {10, 11, 12, 1, 2, 3}
        preferred_seasons = {"all", "winter" if is_winter_season else "summer"}

        seasonal = [
            t for t in topics
            if isinstance(t, dict) and t.get("season", "all") in preferred_seasons
        ]
        pool = seasonal or [t for t in topics if isinstance(t, dict)]
        if not pool:
            return None

        used_set = set(self.used_evergreen_ids)
        unused = [t for t in pool if str(t.get("id", "")) not in used_set]
        if not unused:
            # Все сезонные темы прошли круг — сбрасываем использованные из этого пула
            pool_ids = {str(t.get("id", "")) for t in pool}
            self.used_evergreen_ids = [x for x in self.used_evergreen_ids if x not in pool_ids]
            unused = pool

        return unused[0]

    def record_published(
        self,
        *,
        url: str,
        title: str,
        guid: str = "",
        category: str = "news",
        vk_post_id: int | None = None,
        evergreen_id: str | None = None,
    ) -> None:
        """Фиксирует успешную публикацию поста."""
        self.total_posts += 1
        if evergreen_id and evergreen_id not in self.used_evergreen_ids:
            self.used_evergreen_ids.append(evergreen_id)
        self.items.append(
            {
                "url": url,
                "normalized_url": normalize_url(url),
                "guid": guid,
                "title": strip_source_suffix(title),
                "tokens": extract_signature_tokens(title),
                "category": category,
                "vk_post_id": vk_post_id,
                "published_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self.items = self.items[-self.max_items :]
