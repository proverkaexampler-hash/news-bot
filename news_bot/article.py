"""Открываем статью и достаём заголовок, главное фото и чистый текст."""
import html as htmllib
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

from . import net

SKIP_TAGS = {"script", "style", "noscript", "nav", "header", "footer", "aside", "form", "iframe", "svg",
             "button", "select", "template"}
BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "li", "blockquote"}
BOILERPLATE = ("читайте також", "читайте также", "підписуйтесь", "подписывайтесь", "підписуйтеся", "read more",
               "share on", "cookie", "всі права захищені", "все права защищены", "subscribe to", "follow us",
               "реклама", "advertisement", "disclaimer", "не є фінансовою порадою")


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta, self.title, self.imgs = {}, "", []
        self.paras = []                      # (текст, внутри <article>?)
        self._skip = 0
        self._article = 0
        self._buf = None
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and a.get("content"):
                self.meta.setdefault(key, a["content"].strip())
        elif tag == "link" and a.get("rel") == "image_src" and a.get("href"):
            self.meta.setdefault("image_src", a["href"])
        elif tag == "title":
            self._in_title = True
        elif tag == "img" and not self._skip:
            src = a.get("src") or a.get("data-src") or ""
            if src and not src.startswith("data:"):
                self.imgs.append((src, a.get("width") or "", self._article > 0))
        if tag in SKIP_TAGS:
            self._skip += 1
        if tag == "article":
            self._article += 1
        if tag in BLOCK_TAGS and not self._skip:
            self._buf = []

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in BLOCK_TAGS and self._buf is not None:
            text = " ".join("".join(self._buf).split())
            self._buf = None
            if text:
                self.paras.append((text, self._article > 0, tag))
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        if tag == "article" and self._article:
            self._article -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._buf is not None and not self._skip:
            self._buf.append(data)


def _clean_paragraphs(paras):
    out, seen = [], set()
    for text, _, tag in paras:
        low = text.lower()
        if any(b in low for b in BOILERPLATE) and len(text) < 160:
            continue
        if tag == "li" and len(text) < 40:
            continue
        if tag != "p" and not tag.startswith("h") and len(text) < 40:
            continue
        if tag == "p" and len(text) < 40:
            continue
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def extract_text(raw_html, page=None):
    """Чистый текст статьи. Лучший вариант — trafilatura (если установлена), иначе наш простой разбор."""
    try:
        import trafilatura
        text = trafilatura.extract(raw_html, include_comments=False, include_tables=False, favor_precision=True)
        if text and len(text) > 400:
            return text.strip()
    except Exception:
        pass
    page = page or _Page()
    if not page.paras:
        page.feed(raw_html)
    in_article = [p for p in page.paras if p[1]]
    use = in_article if sum(len(p[0]) for p in in_article) >= 500 else page.paras
    return "\n\n".join(_clean_paragraphs(use))


def pick_image(page, base_url):
    for key in ("og:image:secure_url", "og:image", "twitter:image", "twitter:image:src", "image_src"):
        url = page.meta.get(key)
        if url:
            return urljoin(base_url, htmllib.unescape(url))
    for src, width, in_article in page.imgs:                  # запасной вариант: первая картинка из статьи
        if in_article and not re.search(r"(logo|icon|avatar|sprite|pixel|\.svg)", src, re.I):
            if not width.isdigit() or int(width) >= 300:
                return urljoin(base_url, src)
    return ""


def fetch_article(url, max_chars=12000):
    raw = net.get_text(url)
    page = _Page()
    page.feed(raw)
    title = htmllib.unescape(page.meta.get("og:title") or page.title or "").strip()
    return {
        "url": url,
        "title": re.sub(r"\s+", " ", title),
        "image": pick_image(page, url),
        "text": extract_text(raw, page)[:max_chars],
    }
