"""Загрузка страниц. Вынесено отдельно, чтобы в тестах подменять сеть."""
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")


def get(url, timeout=25, headers=None, **kw):
    h = {"User-Agent": UA, "Accept-Language": "uk,ru;q=0.8,en;q=0.6",
         "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
    h.update(headers or {})
    r = requests.get(url, headers=h, timeout=timeout, allow_redirects=True, **kw)
    r.raise_for_status()
    return r


def get_text(url, timeout=25):
    r = get(url, timeout)
    enc = (r.encoding or "").lower()
    if not enc or enc == "iso-8859-1":          # сервер не указал кодировку — почти всегда это UTF-8
        enc = "utf-8"
    return r.content.decode(enc, errors="replace")


def get_conditional(url, etag=None, modified=None, timeout=25):
    """Запрос ленты «только если изменилась». Возвращает (код, текст, etag, last_modified); при коде 304 текст пустой."""
    h = {}
    if etag:
        h["If-None-Match"] = etag
    if modified:
        h["If-Modified-Since"] = modified
    r = get(url, timeout, headers=h)
    if r.status_code == 304:
        return 304, "", etag, modified
    enc = (r.encoding or "").lower()
    if not enc or enc == "iso-8859-1":
        enc = "utf-8"
    return r.status_code, r.content.decode(enc, errors="replace"), r.headers.get("ETag"), r.headers.get("Last-Modified")
