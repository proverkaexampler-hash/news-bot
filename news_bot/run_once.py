"""Бот новостей: один проход (run) или цикл «почти в реальном времени» (watch). Запускается GitHub Actions."""
import dataclasses
import sys
import time
from datetime import datetime, timezone, timedelta

from . import article as article_mod, feed, net, summarizer, tg
from .config import Config
from .store import MemoryStore, FileStore, SheetStore, DONE


def log(msg):
    print(msg, flush=True)


def collect(cfg):
    if cfg.feed_url:
        return feed.parse_feed(net.get_text(cfg.feed_url))
    return feed.discover_links(net.get_text(cfg.site_url), cfg.site_url, cfg.link_regex)


def notify_admin(cfg, text):
    if cfg.admin_chat_id and cfg.telegram_token and not cfg.dry_run:
        try:
            tg.send_message(cfg.telegram_token, cfg.admin_chat_id, text)
        except Exception:
            pass


META_KEY = "meta:gemini_model"
REPROBE_DAYS = 7


def stored_model(store, now):
    """Модель Gemini, которая работала в прошлый раз. Раз в неделю забываем её, чтобы заново проверить более новые."""
    row = store.get(META_KEY)
    if not row or not row["status"]:
        return "", None
    try:
        age = now.replace(tzinfo=None) - datetime.strptime(row["date"], "%Y-%m-%d %H:%M")
    except ValueError:
        return "", row
    return ("" if age > timedelta(days=REPROBE_DAYS) else row["status"]), row


def remember_model(store, model_used, now):
    provider, _, model = model_used.partition("/")
    if provider != "gemini":
        return
    pref, row = stored_model(store, now)
    if not row or row["status"] != model or not pref:
        store.set(META_KEY, model, 0, "")


class FeedWatcher:
    """Спрашивает у сайта «лента изменилась?» — если нет, берёт прошлый список без загрузки."""
    def __init__(self, cfg):
        self.cfg, self.etag, self.modified, self._items = cfg, None, None, []

    def items(self):
        cfg = self.cfg
        if cfg.feed_url:
            status, text, self.etag, self.modified = net.get_conditional(cfg.feed_url, self.etag, self.modified)
            if status != 304:
                self._items = feed.parse_feed(text)
        else:
            self._items = collect(cfg)
        return self._items


def _recent(row, seconds):
    """Была ли запись обновлена менее чем `seconds` секунд назад (время в таблице — по часам сервера)."""
    try:
        return (datetime.now() - datetime.strptime(row["date"], "%Y-%m-%d %H:%M")).total_seconds() < seconds
    except (ValueError, KeyError, TypeError):
        return False


def run(cfg, store, now=None, items=None):
    now = now or datetime.now(timezone.utc)
    pref, _ = stored_model(store, now)
    cfg = dataclasses.replace(cfg, preferred_model=pref)
    items = collect(cfg) if items is None else items
    stats = {"posted": 0, "skipped": 0, "failed": 0, "backlog": 0}

    items = [it for it in items if not any(c.lower() in cfg.skip_categories for c in it.get("categories", []))] \
        if store.is_empty() else items
    if store.is_empty() and items:                          # самый первый запуск: не заливаем канал старыми новостями
        newest = items[:cfg.first_run_posts]
        store.seed([(it["link"], "skipped_initial", it["title"]) for it in items[cfg.first_run_posts:]])
        queue = list(reversed(newest))
        log(f"Первый запуск: публикуем последние {len(queue)}, остальные пропускаем")
    else:
        queue = []
        for it in reversed(items):                            # от старых к новым
            row = store.get(it["link"])
            if any(cat.lower() in cfg.skip_categories for cat in it.get("categories", [])):
                if not row:
                    store.set(it["link"], "skipped", 0, it["title"])
                continue
            if row and (row["status"] in DONE or row["attempts"] >= cfg.max_attempts):
                continue
            if row and row["status"] in ("failed", "posting") and _recent(row, cfg.retry_delay):
                continue                                         # после ошибки даём сайту/нейросети время прийти в себя
            if it["published"] and now - it["published"] > timedelta(hours=cfg.max_age_hours):
                store.set(it["link"], "skipped_old", row["attempts"] if row else 0, it["title"])
                continue
            queue.append(it)

    if queue:
        log(f"Новых статей к публикации: {len(queue)}")
    stats["backlog"] = max(0, len(queue) - cfg.max_per_run)
    t0 = time.monotonic()
    for it in queue[:cfg.max_per_run]:
        if time.monotonic() - t0 > cfg.run_budget:
            log("Время запуска на исходе — остальные статьи обработаем в следующий раз")
            break
        url = it["link"]
        prev = store.get(url)
        attempts = (prev["attempts"] if prev else 0) + 1
        store.set(url, "posting", attempts, it["title"])
        try:
            art = article_mod.fetch_article(url)
            if len(art["text"]) < 300 and len(it.get("description", "")) > len(art["text"]):
                art["text"] = it["description"]                          # у коротких заметок весь текст — в описании ленты
            art["image"] = (art["image"] or it.get("image", "")) if cfg.use_photos else ""
            art["image"] = art["image"] or cfg.fallback_image_url
            t1 = time.monotonic()
            result, model = summarizer.summarize(art, cfg)
            log(f"Резюме готово за {time.monotonic() - t1:.0f} с [{model}]")
            remember_model(store, model, now)
            if result.get("skip"):
                store.set(url, "skipped", attempts, it["title"])
                log(f"Пропущено ({result['skip_reason']}): {url}")
                stats["skipped"] += 1
                continue
            tg.post(cfg, art, result)
            store.set(url, "posted", attempts, result["title"])
            log(f"Опубликовано [{model}]: {result['title']}")
            stats["posted"] += 1
        except summarizer.TooShort as e:
            store.set(url, "skipped", attempts, it["title"])
            log(f"Пропущено ({e}): {url}")
            stats["skipped"] += 1
        except Exception as e:                               # одна плохая статья не должна ломать весь запуск
            status = "failed" if attempts < cfg.max_attempts else "gave_up"
            store.set(url, status, attempts, it["title"])
            stats["failed"] += 1
            log(f"Ошибка ({status}, попытка {attempts}) {url}: {type(e).__name__}: {e}")
            if status == "gave_up":
                notify_admin(cfg, f"⚠️ Не удалось опубликовать статью после {attempts} попыток:\n{url}\n{e}")
    if queue:
        log(f"Итог: {stats}")
    return stats


def watch(cfg, store, loop_seconds, poll_seconds, sleep=time.sleep, clock=time.monotonic, now_fn=None):
    """Режим «почти в реальном времени»: проверяем ленту каждые poll_seconds секунд, пока не истечёт loop_seconds."""
    watcher, end, last_clear, last_err = FeedWatcher(cfg), clock() + loop_seconds, clock(), None
    log(f"Режим реального времени: проверка ленты каждые {poll_seconds} с, работаем {loop_seconds // 60} мин")
    total = {"posted": 0, "skipped": 0, "failed": 0}
    while True:
        backlog = 0
        try:
            now = now_fn() if now_fn else datetime.now(timezone.utc)
            stats = run(cfg, store, now=now, items=watcher.items())
            for k in total:
                total[k] += stats[k]
            backlog, last_err = stats["backlog"], None
        except Exception as e:                                   # сайт/сеть недоступны — не падаем, пробуем снова
            msg = f"{type(e).__name__}: {e}"
            if msg != last_err:
                log(f"Ошибка проверки ленты: {msg[:300]}")
            last_err = msg
        if clock() >= end:
            break
        if clock() - last_clear > 1800:                          # раз в полчаса даём «плохим» моделям второй шанс
            summarizer.BAD_MODELS.clear()
            last_clear = clock()
        sleep(5 if backlog else poll_seconds)
    log(f"Цикл завершён. Всего: {total}")
    return total


def build_store(cfg):
    if cfg.dry_run:
        return MemoryStore()
    if cfg.sheet_id and cfg.google_credentials_json:
        return SheetStore(cfg.google_credentials_json, cfg.sheet_id)
    if cfg.state_file:
        return FileStore(cfg.state_file)
    raise SystemExit("Не настроено хранилище: нужны SHEET_ID и GOOGLE_CREDENTIALS_JSON (или STATE_FILE для проверки).")


def main():
    cfg = Config.from_env()
    problems = []
    if not cfg.dry_run and not (cfg.telegram_token and cfg.channel_id):
        problems.append("TELEGRAM_TOKEN и CHANNEL_ID")
    if not (cfg.feed_url or cfg.site_url):
        problems.append("FEED_URL (или SITE_URL)")
    if not (cfg.gemini_key or cfg.groq_key):
        problems.append("GEMINI_API_KEY (или GROQ_API_KEY)")
    if problems:
        sys.exit("Не заданы настройки: " + ", ".join(problems))
    store = build_store(cfg)
    if cfg.loop_seconds > 0:
        watch(cfg, store, cfg.loop_seconds, cfg.poll_seconds)
    else:
        run(cfg, store)


if __name__ == "__main__":
    main()
