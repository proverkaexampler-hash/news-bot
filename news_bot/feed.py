"""Список свежих статей: из RSS/Atom-ленты, а если её нет — со страницы со ссылками."""
import html as htmllib
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode

TRACKING = ("utm_", "fbclid", "gclid", "yclid", "mc_", "ref", "source")


def normalize_url(url):
    p = urlparse(url.strip())
    q = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith(TRACKING)]
    path = p.path.rstrip("/") or "/"
    return urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", urlencode(q), ""))


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _date(text):
    if not text:
        return None
    text = text.strip()
    try:
        d = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse_feed(xml_text):
    """Возвращает [{title, link, published, image}], новые первыми."""
    root = ET.fromstring(xml_text.strip().encode("utf-8") if isinstance(xml_text, str) else xml_text)
    items = []
    for node in root.iter():
        if _local(node.tag) not in ("item", "entry"):
            continue
        d = {"title": "", "link": "", "published": None, "image": "", "categories": [], "description": ""}
        for ch in node:
            name = _local(ch.tag)
            if name == "title":
                d["title"] = (ch.text or "").strip()
            elif name == "link":
                href = ch.attrib.get("href")
                if href and ch.attrib.get("rel", "alternate") == "alternate":
                    d["link"] = d["link"] or href
                elif ch.text and ch.text.strip():
                    d["link"] = ch.text.strip()
            elif name in ("pubDate", "published", "updated", "date"):
                d["published"] = d["published"] or _date(ch.text)
            elif name == "category":
                if ch.text and ch.text.strip():
                    d["categories"].append(ch.text.strip())
            elif name in ("description", "summary", "encoded"):
                raw = ch.text or ""
                if len(raw) > len(d["description"]):
                    d["description"] = raw
            elif name in ("content", "thumbnail", "enclosure"):
                url = ch.attrib.get("url")
                typ = ch.attrib.get("type", "image")
                if url and typ.startswith("image") and not d["image"]:
                    d["image"] = url
        if d["link"]:
            raw_desc = d["description"]
            if not d["image"]:
                m = re.search(r'<img[^>]+?(?:data-src|src)="(https?://[^"]+)"', raw_desc)
                d["image"] = htmllib.unescape(m.group(1)) if m else ""
            text = re.sub(r"<[^>]+>", " ", raw_desc)
            d["description"] = re.sub(r"[ \t]+", " ", htmllib.unescape(text)).strip()
            d["link"] = normalize_url(d["link"])
            items.append(d)
    items.sort(key=lambda x: x["published"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return items


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self._href, self._text = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href, self._text = dict(attrs).get("href"), []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None


def discover_links(html, base_url, regex=""):
    """Запасной способ без RSS: собираем ссылки на статьи со страницы."""
    parser = _Links()
    parser.feed(html)
    host = urlparse(base_url).netloc
    pat = re.compile(regex) if regex else None
    seen, out = set(), []
    for href, text in parser.links:
        url = normalize_url(urljoin(base_url, href))
        pu = urlparse(url)
        if pu.netloc != host or url in seen:
            continue
        if pat:
            if not pat.search(url):
                continue
        elif len([s for s in pu.path.split("/") if s]) < 2 or len(text) < 25:
            continue                      # похоже на меню или рубрику, а не на статью
        seen.add(url)
        out.append({"title": text, "link": url, "published": None, "image": "", "categories": [], "description": ""})
    return out
