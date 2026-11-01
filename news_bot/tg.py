"""Отправка в Telegram-канал: сначала фото с заголовком, затем резюме (в Telegram разные лимиты на подпись и текст)."""
import html
import io
import time
from urllib.parse import urlparse

import requests
from PIL import Image

from . import net

CAPTION_LIMIT, TEXT_LIMIT = 1000, 4000


class TelegramError(Exception):
    pass


def call(token, method, data=None, files=None, retries=3):
    url = f"https://api.telegram.org/bot{token}/{method}"
    for _ in range(retries):
        try:
            r = requests.post(url, data=data, files=files, timeout=60)
        except requests.RequestException as e:                 # в тексте ошибки может быть токен — не выводим его
            raise TelegramError(f"{method}: сеть ({type(e).__name__})")
        try:
            j = r.json()
        except ValueError:
            j = {}
        if r.status_code == 429:
            time.sleep(int(j.get("parameters", {}).get("retry_after", 5)) + 1)
            continue
        if j.get("ok"):
            return j["result"]
        raise TelegramError(f"{method}: {j.get('description') or r.text[:160]}")
    raise TelegramError(f"{method}: слишком много запросов")


def split_text(text, limit=TEXT_LIMIT):
    """Режем по абзацам, затем по предложениям — чтобы не рвать слова."""
    chunks, cur = [], ""
    for para in text.split("\n\n"):
        pieces = [para]
        while len(pieces[-1]) > limit:
            tail = pieces.pop()
            cut = max(tail.rfind(". ", 0, limit), tail.rfind(" ", 0, limit))
            cut = cut + 1 if cut > 0 else limit
            pieces += [tail[:cut].strip(), tail[cut:].strip()]
        for p in pieces:
            if cur and len(cur) + len(p) + 2 > limit:
                chunks.append(cur)
                cur = p
            else:
                cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        chunks.append(cur)
    return chunks


def send_message(token, chat, text):
    ids = []
    for chunk in split_text(text):
        res = call(token, "sendMessage", {"chat_id": chat, "text": chunk, "parse_mode": "HTML",
                                          "disable_web_page_preview": "true"})
        ids.append(res["message_id"])
    return ids


def download_image(url, referer):
    """Скачиваем фото и приводим к JPEG до 10 МБ. Возвращает bytes или None, если фото не подходит."""
    try:
        data = net.get(url, headers={"Referer": referer, "Accept": "image/*,*/*;q=0.8"}).content
        im = Image.open(io.BytesIO(data))
        w, h = im.size
        if w < 200 or h < 100 or max(w / h, h / w) > 4:
            return None
        im = im.convert("RGB")
        im.thumbnail((2560, 2560))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=88)
        return out.getvalue() if out.tell() < 10 * 1024 * 1024 else None
    except Exception:
        return None


def send_photo(token, chat, image_url, caption, referer):
    """Telegram сам скачивает фото по ссылке; если сайт не отдаёт — скачиваем и загружаем сами."""
    try:
        return call(token, "sendPhoto", {"chat_id": chat, "photo": image_url, "caption": caption,
                                         "parse_mode": "HTML"})["message_id"]
    except TelegramError:
        pass
    data = download_image(image_url, referer)
    if not data:
        return None
    return call(token, "sendPhoto", {"chat_id": chat, "caption": caption, "parse_mode": "HTML"},
                files={"photo": ("photo.jpg", data, "image/jpeg")})["message_id"]


def compose(article, result, cfg):
    esc = lambda s: html.escape(s, quote=False)
    title_html = f"<b>{esc(cfg.title_prefix + result['title'])}</b>"
    host = urlparse(article["url"]).netloc.replace("www.", "")
    source = f'🔗 <a href="{html.escape(article["url"], quote=True)}">{esc(cfg.source_label)}: {esc(host)}</a>'
    caption = title_html if len(title_html) <= CAPTION_LIMIT else title_html[:CAPTION_LIMIT - 1] + "…</b>"
    body = esc(result["summary"]) + "\n\n" + source
    return caption, title_html, body


def post(cfg, article, result):
    """Публикует новость. Возвращает список id сообщений."""
    caption, title_html, body = compose(article, result, cfg)
    if cfg.dry_run:
        print(f"--- [DRY RUN] фото: {article['image'] or 'нет'}\n{caption}\n\n{body}\n---")
        return []
    ids = []
    photo_id = send_photo(cfg.telegram_token, cfg.channel_id, article["image"], caption, article["url"]) \
        if article["image"] else None
    if photo_id:
        ids.append(photo_id)
        text = (title_html + "\n\n" + body) if cfg.repeat_title else body
    else:
        text = title_html + "\n\n" + body                        # без фото заголовок обязателен
    ids += send_message(cfg.telegram_token, cfg.channel_id, text)
    return ids
