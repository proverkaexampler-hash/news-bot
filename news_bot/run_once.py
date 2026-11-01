"""Один проход бота: проверить ленту, опубликовать до N новых новостей. Запускается GitHub Actions по расписанию."""
import sys
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


def run(cfg, store, now=None):
    now = now or datetime.now(timezone.utc)
    items = collect(cfg)
    log(f"В ленте статей: {len(items)}")
    stats = {"posted": 0, "skipped": 0, "failed": 0}

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
            if it["published"] and now - it["published"] > timedelta(hours=cfg.max_age_hours):
                store.set(it["link"], "skipped_old", row["attempts"] if row else 0, it["title"])
                continue
            queue.append(it)

    for it in queue[:cfg.max_per_run]:
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
            result, model = summarizer.summarize(art, cfg)
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
    log(f"Итог: {stats}")
    return stats


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
    run(cfg, build_store(cfg))


if __name__ == "__main__":
    main()
