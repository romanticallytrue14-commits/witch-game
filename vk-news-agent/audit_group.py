#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Диагностика публичного сообщества ВКонтакте: кто и когда публиковал записи.

Отвечает на вопросы:
  * есть ли на стене публикации, сделанные через API (тип `post_source.type == "api"` —
    именно так публикуют «роботы»/приложения);
  * когда была последняя публикация робота и последняя публикация вообще;
  * с какой периодичностью робот публиковал и когда примерно ждать следующий пост;
  * продолжает ли кто-то публиковать вручную после остановки робота.

Данные читаются через VK API: сначала с токеном из окружения (`VK_TOKEN` /
`VK_USER_TOKEN`), затем — без токена (для полностью публичных стен).
Если API недоступен, скрипт пытается забрать записи со страницы сообщества
( best-effort, помечается в отчёте ).

Отчёт печатается в Markdown на stdout (его можно добавить в GITHUB_STEP_SUMMARY)
и сохраняется в файл.

Запуск:
    python3 vk-news-agent/audit_group.py --screen-names neurohack_vk,greem_motor
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

VK_API_BASES = ("https://api.vk.ru/method", "https://api.vk.com/method")
API_VERSION = "5.199"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
MSK = dt.timezone(dt.timedelta(hours=3))
SSL_CTX = ssl.create_default_context()

SOURCE_LABELS = {
    "api": "🤖 API (робот/приложение)",
    "vk": "📱 ВКонтакте (сайт/приложение)",
    "email": "✉️ e-mail",
    "rss": "📡 RSS",
    "suggest": "👥 Предложенная запись",
    "auto": "⚙️ Автопостинг ВК",
}


def _log(msg: str) -> None:
    print(f"[audit] {msg}", file=sys.stderr, flush=True)


def post_form(url: str, payload: dict[str, str], timeout: int = 30) -> dict[str, Any] | None:
    """POST-запрос к VK API. Возвращает dict ответа или None при сетевом сбое."""
    encoded = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        _log(f"сетевой сбой {url}: {exc}")
        return None


def fetch_html(url: str, timeout: int = 30) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "ru"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        _log(f"сетевой сбой {url}: {exc}")
        return None


def call_api(
    method: str,
    params: dict[str, Any],
    token: str | None,
    notes: list[str],
) -> tuple[bool, Any]:
    """Вызов метода VK API. Возвращает (успех, response | ошибка)."""
    payload = {k: str(v) for k, v in params.items() if v is not None}
    payload["v"] = API_VERSION
    if token:
        payload["access_token"] = token

    last_network_fail = False
    for base in VK_API_BASES:
        for attempt in range(4):
            data = post_form(f"{base}/{method}", payload)
            if data is None:
                last_network_fail = True
                break  # пробуем резервный хост
            if "error" in data:
                err = data["error"]
                code = int(err.get("error_code", 0))
                if code == 6 and attempt < 3:  # too many requests
                    time.sleep(0.8 * (attempt + 1))
                    continue
                notes.append(f"{method}: ошибка VK {code} — {err.get('error_msg')}")
                return False, err
            return True, data.get("response")
    if last_network_fail:
        notes.append(f"{method}: сеть недоступна к {VK_API_BASES[0]} / {VK_API_BASES[1]}")
    return False, {"error_code": -2, "error_msg": "network unavailable"}


def pick_tokens() -> list[str | None]:
    """Список токенов в порядке приоритета: пользовательский → групповой → без токена."""
    raw = [
        os.environ.get("VK_USER_TOKEN", ""),
        os.environ.get("VK_TOKEN", ""),
        os.environ.get("VK_ACCESS_TOKEN", ""),
        os.environ.get("VK_API_TOKEN", ""),
        os.environ.get("TOKEN", ""),
    ]
    seen: list[str] = []
    for t in raw:
        t = (t or "").strip()
        if t and t not in seen:
            seen.append(t)
    tokens: list[str | None] = list(seen)
    tokens.append(None)  # последняя попытка — без токена
    return tokens


def get_group_info(screen: str, tokens: list[str | None], notes: list[str]) -> dict[str, Any] | None:
    for token in tokens:
        tag = "с токеном" if token else "без токена"
        for params in (
            {"group_ids": screen, "fields": "activity,description,members_count,status,site,verified,can_post"},
            {"group_id": screen, "fields": "activity,description,members_count,status,site,verified,can_post"},
        ):
            ok, resp = call_api("groups.getById", params, token, notes)
            if not ok:
                continue
            items = resp if isinstance(resp, list) else (resp or {}).get("groups") or []
            if items:
                _log(f"groups.getById для {screen} получен ({tag})")
                return items[0]
    return None


def get_wall(screen: str, count: int, tokens: list[str | None], notes: list[str]) -> list[dict[str, Any]] | None:
    for token in tokens:
        tag = "с токеном" if token else "без токена"
        for params in ({"domain": screen, "count": count}, {"owner_id": screen, "count": count}):
            ok, resp = call_api("wall.get", params, token, notes)
            if not ok:
                continue
            items = (resp or {}).get("items") if isinstance(resp, dict) else None
            if items is None and isinstance(resp, dict):
                items = resp.get("response", {}).get("items")
            if items:
                _log(f"wall.get для {screen} получил {len(items)} записей ({tag})")
                return items
            if isinstance(resp, dict) and resp.get("items") == []:
                _log(f"wall.get для {screen}: стена пуста ({tag})")
                return []
    return None


MARKER_PATTERNS = {
    "wall_post": r"wall_post",
    "post_item": r"post_item",
    "wall_item": r"wall_item",
    "class=post": r'class="post\b',
    "Post component": r"\bPost\b",
    "data-date": r"data-date=",
    "publish_date": r"publish_date",
    "post_source": r"post_source",
    "__PRELOADED_STATE__": r"__PRELOADED_STATE__",
    "replies": r"\breplies\b",
    "captcha/security": r"captcha|security check|Проверка безопасности|подтвердите, что вы человек",
    "window.__": r"window\.[A-Za-z_$][\w$]*\s*=",
}


def _visible_text(html: str, limit: int = 900) -> str:
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"\s+", " ", body).strip()
    return body[:limit]


def scrape_wall(screen: str, notes: list[str], group_id: int | None = None) -> list[dict[str, Any]]:
    """Best-effort попытка достать записи со страницы сообщества без API.

    Помимо самих записей наполняет `notes` диагностикой: что именно вернула
    страница (заголовок, маркеры разметки, найденные даты) — чтобы понять,
    реальная ли это стена страницы или заглушка/капча.
    """
    urls = [f"https://m.vk.com/{screen}", f"https://vk.com/{screen}", f"https://vk.com/{screen}?own=1"]
    if group_id:
        urls.append(f"https://vk.com/wall-{group_id}")

    posts: list[dict[str, Any]] = []
    for url in urls:
        html = fetch_html(url)
        if not html:
            notes.append(f"страница {url} недоступна")
            continue

        title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        title_txt = re.sub(r"\s+", " ", title.group(1)).strip() if title else "?"
        markers = {name: len(re.findall(pat, html, re.I)) for name, pat in MARKER_PATTERNS.items()}
        marker_str = ", ".join(f"{k}={v}" for k, v in markers.items() if v)
        notes.append(f"{url}: {len(html)} байт, title={title_txt!r}; маркеры: {marker_str or 'нет'}")

        # Все правдоподобные даты (unix10 цифр, 2020–2030) с контекстом.
        dated: list[tuple[int, str]] = []
        for m in re.finditer(r"\b(1[6-9]\d{9})\b", html):
            ts = int(m.group(1))
            if not (1577836800 <= ts <= 1893456000):  # 2020-01-01 … 2030-01-01
                continue
            ctx = re.sub(r"\s+", " ", html[max(0, m.start() - 90) : m.end() + 90])
            ctx = re.sub(r"<[^>]+>", " ", ctx)
            dated.append((ts, ctx.strip()[:180]))
        seen_ts: set[int] = set()
        uniq: list[tuple[int, str]] = []
        for ts, ctx in sorted(dated, key=lambda x: -x[0]):
            if ts in seen_ts:
                continue
            seen_ts.add(ts)
            uniq.append((ts, ctx))
        if uniq:
            notes.append(
                "кандидаты дат на странице: "
                + "; ".join(f"{fmt_ts(ts)} «{ctx}»" for ts, ctx in uniq[:8])
            )

        # 1) Иногда VK встраивает состояние страницы в JSON.
        m = re.search(r"__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>", html, re.S)
        if m:
            try:
                state = json.loads(m.group(1))
                for node in walk_json(state):
                    if isinstance(node, dict) and "date" in node and ("text" in node or "content" in node):
                        posts.append(
                            {
                                "date": node.get("date"),
                                "text": node.get("text") or node.get("content") or "",
                                "post_source": node.get("post_source") or {},
                                "id": node.get("id"),
                            }
                        )
            except json.JSONDecodeError:
                pass

        # 2) Грубый поиск блоков записей в HTML.
        if not posts:
            for chunk in re.split(r'(?=<div[^>]+class="[^"]*(?:wall_post|post_item|wall_item|post\b)[^"]*")', html):
                dm = re.search(r'"date"\s*:\s*(\d{10})|data-date="(\d{10})"|date="(\d{10})"', chunk)
                if not dm:
                    continue
                ts = int(dm.group(1) or dm.group(2) or dm.group(3))
                if not (1577836800 <= ts <= 1893456000):
                    continue
                text = re.sub(r"<[^>]+>", " ", chunk)
                text = re.sub(r"\s+", " ", text).strip()[:300]
                if len(text) < 10:
                    continue
                posts.append({"date": ts, "text": text, "post_source": {}, "id": None})

        # 3) Диагностика: что вообще за страница (первый экран текста).
        notes.append(f"{url}: первые символы текста — «{_visible_text(html)}»")

        if posts:
            notes.append(f"записи получены скрапингом {url} (не через API)")
            break
    return posts


def walk_json(node: Any):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from walk_json(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk_json(v)


def fmt_ts(ts: int | float | None) -> str:
    if not ts:
        return "—"
    return dt.datetime.fromtimestamp(int(ts), MSK).strftime("%Y-%m-%d %H:%M MSK")


def day_key(ts: int | float) -> str:
    return dt.datetime.fromtimestamp(int(ts), MSK).strftime("%Y-%m-%d")


def source_label(post: dict[str, Any]) -> str:
    src = post.get("post_source") or {}
    stype = src.get("type") or "?"
    label = SOURCE_LABELS.get(stype, stype)
    platform = src.get("platform")
    if platform:
        label += f" ({platform})"
    return label


def short(text: str, limit: int = 110) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    text = text.replace("|", "\\|")
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def build_report(screen: str, group: dict[str, Any] | None, posts: list[dict[str, Any]], notes: list[str]) -> str:
    now = dt.datetime.now(MSK)
    lines: list[str] = []
    lines.append(f"# Аудит сообщества vk.ru/{screen}")
    lines.append("")
    lines.append(f"_Сформировано: {now.strftime('%Y-%m-%d %H:%M MSK')}_")
    lines.append("")

    if group:
        gid = group.get("id") or group.get("group_id")
        lines.append("## Сообщество")
        lines.append("")
        lines.append(f"- **Название:** {group.get('name')}")
        lines.append(f"- **ID:** {gid}, адрес: `{group.get('screen_name')}`")
        if group.get("activity"):
            lines.append(f"- **Тематика (статус):** {group.get('activity')}")
        if group.get("status"):
            lines.append(f"- **Статус:** {group.get('status')}")
        if group.get("members_count") is not None:
            lines.append(f"- **Участников:** {group.get('members_count')}")
        if group.get("description"):
            desc = re.sub(r"\s+", " ", group["description"]).strip()
            lines.append(f"- **Описание:** {desc[:400]}{'…' if len(desc) > 400 else ''}")
        lines.append("")
    else:
        lines.append("## Сообщество")
        lines.append("")
        lines.append("_Информация о сообществе получить не удалось (см. замечания ниже)._")
        lines.append("")

    lines.append("## Стена: последние записи")
    lines.append("")
    if not posts:
        lines.append("_Записей получить не удалось или стена пуста._")
        lines.append("")
    else:
        lines.append("| # | Дата | Кто публиковал | Запись |")
        lines.append("|---|------|----------------|--------|")
        for i, p in enumerate(posts[:25], 1):
            pin = "📌 " if p.get("is_pinned") else ""
            lines.append(
                f"| {i} | {fmt_ts(p.get('date'))} | {source_label(p)} | {pin}{short(p.get('text') or '')} |"
            )
        lines.append("")

    # --- Статистика ---
    dated = [p for p in posts if p.get("date")]
    dated.sort(key=lambda p: int(p["date"]), reverse=True)
    api_posts = [p for p in dated if ((p.get("post_source") or {}).get("type") == "api")]
    other_posts = [p for p in dated if ((p.get("post_source") or {}).get("type") != "api")]

    lines.append("## Статистика публикаций")
    lines.append("")
    if dated:
        lines.append(f"- Всего записей в выборке: **{len(dated)}** (с {fmt_ts(dated[-1].get('date'))})")
        lines.append(f"- Публикации через API («робот»): **{len(api_posts)}**")
        lines.append(f"- Ручные/прочие публикации: **{len(other_posts)}**")
        lines.append(f"- Последняя запись вообще: **{fmt_ts(dated[0].get('date'))}**")
        if api_posts:
            lines.append(f"- Последняя публикация робота (API): **{fmt_ts(api_posts[0].get('date'))}**")
            gaps = [
                int(api_posts[i]["date"]) - int(api_posts[i + 1]["date"])
                for i in range(len(api_posts) - 1)
                if int(api_posts[i]["date"]) > int(api_posts[i + 1]["date"])
            ]
            if gaps:
                med = sorted(gaps)[len(gaps) // 2]
                lines.append(f"- Средний интервал между публикациями робота: **{med / 3600:.1f} ч**")
                expected = int(api_posts[0]["date"]) + med
                lines.append(f"- Ожидаемая следующая публикация (по ритму): **{fmt_ts(expected)}**")
        else:
            lines.append("- Публикаций через API в выборке **нет** — на стене только ручные записи.")
        lines.append("")

        # Публикации по дням (последние 14 дней)
        per_day: dict[str, int] = {}
        for p in dated:
            if int(p["date"]) >= int(now.timestamp()) - 14 * 86400:
                per_day[day_key(p["date"])] = per_day.get(day_key(p["date"]), 0) + 1
        if per_day:
            lines.append("### Публикации по дням (MSK, последние 14 дней)")
            lines.append("")
            for day in sorted(per_day, reverse=True):
                lines.append(f"- {day}: {per_day[day]}")
            lines.append("")
    else:
        lines.append("_Нет данных о датах записей._")
        lines.append("")

    # --- Выводы ---
    lines.append("## Выводы")
    lines.append("")
    now_ts = int(now.timestamp())
    if not dated:
        lines.append("- ❗ Записи на стене получить не удалось — диагностика робота невозможна по ним.")
    else:
        last_any = int(dated[0]["date"])
        age_any = (now_ts - last_any) / 3600
        if api_posts:
            last_api = int(api_posts[0]["date"])
            age_api = (now_ts - last_api) / 3600
            gaps = [
                int(api_posts[i]["date"]) - int(api_posts[i + 1]["date"])
                for i in range(len(api_posts) - 1)
                if int(api_posts[i]["date"]) > int(api_posts[i + 1]["date"])
            ]
            median_gap_h = (sorted(gaps)[len(gaps) // 2] / 3600) if gaps else 0.0
            # «Робот молчит», если его пауза заметно длиннее обычного ритма (но не меньше 36 ч)
            stop_threshold_h = max(36.0, 1.5 * median_gap_h) if median_gap_h else 36.0
            if age_api <= stop_threshold_h:
                lines.append(
                    f"- ✅ Робот публикует: последняя запись через API {fmt_ts(last_api)} "
                    f"({age_api:.1f} ч назад, порог остановки {stop_threshold_h:.0f} ч)."
                )
            else:
                lines.append(
                    f"- 🤖 Робот перестал публиковать: последняя запись через API — {fmt_ts(last_api)} "
                    f"(**{age_api / 24:.1f} дней назад**, обычный ритм {median_gap_h:.1f} ч)."
                )
            manual_after = [p for p in other_posts if int(p["date"]) > last_api]
            if manual_after:
                lines.append(
                    f"- ✋ После последней записи робота вручную/другим способом размещено записей: "
                    f"**{len(manual_after)}**, последняя — {fmt_ts(manual_after[0]['date'])}."
                )
            if age_api > stop_threshold_h and not manual_after:
                lines.append(
                    "- ⚠️ После остановки робота новых записей не появлялось вообще — "
                    "стена полностью зависла (никто не публикует вручную)."
                )
        else:
            lines.append(
                "- ❗ В выборке нет ни одной публикации через API: либо робот никогда не публиковал "
                "на эту стену (смотрите не ту группу / публикации уходят в другое место), "
                "либо его записи были удалены с стены."
            )
        if age_any > 72:
            lines.append(
                f"- ⚠️ Последняя запись на стене в целом старше 3 суток ({fmt_ts(last_any)}) — "
                "стена не обновлялась независимо от робота."
            )

    if notes:
        lines.append("")
        lines.append("## Замечания и ошибки")
        lines.append("")
        seen: set[str] = set()
        for n in notes:
            if n not in seen:
                seen.add(n)
                lines.append(f"- {n}")
        lines.append("")

    return "\n".join(lines) + "\n"


def audit_one(screen: str, tokens: list[str | None], count: int) -> str:
    notes: list[str] = []
    group = get_group_info(screen, tokens, notes)
    posts = get_wall(screen, count, tokens, notes)
    if posts is None:
        gid = None
        if group:
            raw_gid = group.get("id") or group.get("group_id")
            gid = int(raw_gid) if raw_gid else None
        posts = scrape_wall(screen, notes, group_id=gid)
        if posts:
            notes.append("API недоступен/закрыт — использован запасной способ чтения стены")
        else:
            notes.append("ни API, ни страница сообщества не вернули записей")
    return build_report(screen, group, posts or [], notes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Аудит публичного сообщества ВКонтакте")
    parser.add_argument(
        "--screen-names",
        default=os.environ.get("AUDIT_SCREEN_NAMES", "neurohack_vk"),
        help="Адреса сообществ через запятую (по умолчанию: neurohack_vk)",
    )
    parser.add_argument("--count", type=int, default=50, help="Сколько записей читать со стены")
    parser.add_argument(
        "--out-dir",
        default="vk-news-agent/state/audit",
        help="Куда сохранить отчёты-файлы",
    )
    parser.add_argument("--no-file", action="store_true", help="Не сохранять файлы, только stdout")
    args = parser.parse_args(argv)

    tokens = pick_tokens()
    if tokens[-1] is None and len(tokens) == 1:
        _log("токены VK не найдены в окружении — работаю без токена (только публичные данные)")

    screens = [s.strip() for s in args.screen_names.split(",") if s.strip()]
    if not screens:
        print("Не указаны адреса сообществ.", file=sys.stderr)
        return 2

    exit_code = 0
    for screen in screens:
        safe = re.sub(r"[^A-Za-z0-9_-]", "", screen) or "group"
        _log(f"аудит {screen} ...")
        try:
            report = audit_one(screen, tokens, args.count)
        except Exception as exc:  # noqa: BLE001 — диагностика не должна падать
            report = f"# Аудит vk.ru/{screen}\n\nОшибка: {exc}\n"
            exit_code = 1
        print(report)
        if not args.no_file:
            os.makedirs(args.out_dir, exist_ok=True)
            path = os.path.join(args.out_dir, f"{safe}.md")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(report)
            _log(f"отчёт сохранён: {path}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
