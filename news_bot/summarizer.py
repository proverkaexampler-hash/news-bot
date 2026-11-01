"""Резюме статьи на украинском через бесплатные нейросети (Gemini, затем Groq как запасной вариант)."""
import dataclasses
import json
import re
import time

import requests

SYSTEM = """You are the editor of a Ukrainian-language Telegram channel about cryptocurrency and finance news. You receive the text of ONE news article and write a short, accurate retelling in Ukrainian for the channel.

STRICT RULES
1. Use ONLY facts that are present in the article text. Never invent, round or "improve" numbers, dates, names, quotes or causes. If something is unclear or missing, leave it out.
2. Write natural, literate Ukrainian (not a word-for-word translation). Keep names, tickers and projects recognisable: Bitcoin -> Біткоїн, Ethereum -> Ефіріум, Binance stays Binance, tickers such as BTC, ETH, BNB stay in Latin. Transliterate personal names into Ukrainian.
3. Neutral news tone. No hype, no clickbait, no emojis, no markdown, no HTML, no hashtags. Never give investment advice; attribute forecasts and opinions to the people who gave them ("за словами ...", "на думку аналітика ...").
4. "summary": 3-5 short paragraphs separated by a blank line, between {min} and {max} characters in total. Paragraph 1: the main event with the most important numbers, who and when. Next paragraphs: key details, context, named experts' reactions and forecasts. Last paragraph only if the article mentions it: risks or what to watch next.
5. "title": precise Ukrainian headline, at most 110 characters, no clickbait, no final period, no quotation marks around it.
6. If the text is NOT a real news article (advertising, promo or bonus announcement, cookie banner, paywall stub, list of links, fewer than about 300 meaningful characters), set "skip": true and explain briefly in "skip_reason".
Return ONLY a JSON object: {{"skip": false, "skip_reason": "", "title": "...", "summary": "..."}}"""


class SummarizeError(Exception):
    pass


class TooShort(SummarizeError):
    """В статье почти нет текста (например, анонс подкаста) — публиковать нечего."""


def build_user_prompt(article):
    return f"Article URL: {article['url']}\nOriginal title: {article['title']}\n\nArticle text:\n{article['text']}"


def parse_json(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b < a:
        raise SummarizeError("модель вернула не JSON")
    try:
        return json.loads(text[a:b + 1])
    except json.JSONDecodeError as e:
        raise SummarizeError(f"битый JSON: {e}")


def cyrillic_ratio(text):
    letters = [c for c in text if c.isalpha()]
    return sum("\u0400" <= c <= "\u04FF" for c in letters) / len(letters) if letters else 0


def clean(text):
    text = re.sub(r"[*_`#]+", "", text or "")                    # на случай, если модель всё же дала markdown
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def validate(data, cfg):
    if not isinstance(data, dict):
        raise SummarizeError("ответ не объект")
    if data.get("skip"):
        return {"skip": True, "skip_reason": str(data.get("skip_reason", ""))[:200]}
    title, summary = clean(data.get("title", "")).rstrip("."), clean(data.get("summary", ""))
    if not title or len(title) > 160:
        raise SummarizeError("заголовок пустой или слишком длинный")
    if len(summary) < min(cfg.summary_min, 400):
        raise SummarizeError(f"резюме слишком короткое ({len(summary)} симв.)")
    if len(summary) > cfg.summary_max + 600:
        cut = summary[:cfg.summary_max + 300]
        summary = cut[:max(cut.rfind(". "), cut.rfind(".\n"))].strip() + "." if "." in cut else cut
    if cyrillic_ratio(title + " " + summary) < 0.6:
        raise SummarizeError("текст не на украинском")
    return {"skip": False, "title": title, "summary": summary}


def _post(url, headers, body, timeout=(10, 60)):
    return requests.post(url, headers=headers, json=body, timeout=timeout)


def _with_retries(call, what):
    """429 и 5xx — подождать и повторить; остальное — сразу ошибка."""
    for attempt in range(3):
        try:
            r = call()
        except requests.Timeout:
            raise SummarizeError(f"{what}: таймаут (нет ответа)")
        except requests.RequestException as e:
            raise SummarizeError(f"{what}: сеть ({type(e).__name__})")
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            time.sleep(8 * (attempt + 1))
            continue
        if r.status_code >= 400:
            raise SummarizeError(f"{what}: HTTP {r.status_code} {r.text[:160]}")
        return r.json()
    raise SummarizeError(f"{what}: не удалось")


def call_gemini(system, user, model, key):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {"systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"responseMimeType": "application/json"}}
    data = _with_retries(lambda: _post(url, {"x-goog-api-key": key}, body), f"gemini/{model}")
    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    except (KeyError, IndexError, TypeError):
        raise SummarizeError(f"gemini/{model}: пустой ответ ({str(data)[:120]})")
    if not text.strip():
        raise SummarizeError(f"gemini/{model}: пустой ответ")
    return text


def call_groq(system, user, model, key):
    body = {"model": model, "temperature": 0.2, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    data = _with_retries(lambda: _post("https://api.groq.com/openai/v1/chat/completions",
                                       {"Authorization": f"Bearer {key}"}, body), f"groq/{model}")
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise SummarizeError(f"groq/{model}: пустой ответ")


GEMINI_FALLBACK = ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite"]


def discover_gemini_models(key):
    """Спрашиваем у Google, какие модели доступны этому ключу, и берём самые новые «flash» без preview/image/tts."""
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models", headers={"x-goog-api-key": key},
                         params={"pageSize": 200}, timeout=30)
        r.raise_for_status()
        found = []
        for m in r.json().get("models", []):
            name = m.get("name", "").replace("models/", "")
            match = re.fullmatch(r"gemini-(\d+(?:\.\d+)?)-flash(-lite)?", name)
            if match and "generateContent" in m.get("supportedGenerationMethods", []):
                found.append((bool(match.group(2)), -float(match.group(1)), name))   # обычные модели раньше lite, новые версии раньше старых
        return [n for _, _, n in sorted(found)][:4]
    except Exception:
        return []


def gemini_candidates(cfg):
    return cfg.gemini_models or discover_gemini_models(cfg.gemini_key) or GEMINI_FALLBACK


def summarize(article, cfg):
    """Пробуем модели по очереди. Возвращает (результат, 'провайдер/модель') или бросает SummarizeError."""
    n = len(article["text"])
    if n < 120:
        raise TooShort(f"в статье слишком мало текста ({n} симв.)")
    if n < 900:                                   # короткая заметка: и резюме должно быть короче оригинала
        cfg = dataclasses.replace(cfg, summary_min=min(cfg.summary_min, 180), summary_max=min(cfg.summary_max, max(400, int(n * 0.9))))
    system = SYSTEM.format(min=cfg.summary_min, max=cfg.summary_max)
    user = build_user_prompt(article)
    errors = []
    started = time.monotonic()
    for provider in cfg.llm_order:
        key = cfg.gemini_key if provider == "gemini" else cfg.groq_key
        models = gemini_candidates(cfg) if provider == "gemini" else cfg.groq_models
        fn = call_gemini if provider == "gemini" else call_groq
        if not key:
            continue
        for model in models:
            if time.monotonic() - started > cfg.llm_budget:
                errors.append(f"исчерпан бюджет времени {cfg.llm_budget} с")
                raise SummarizeError("; ".join(errors[-4:]))
            for strict in (False, True):
                try:
                    prompt = user + ("\n\nIMPORTANT: your previous answer was rejected. Answer ONLY in Ukrainian, "
                                     "valid JSON, with the required length." if strict else "")
                    result = validate(parse_json(fn(system, prompt, model, key)), cfg)
                    return result, f"{provider}/{model}"
                except SummarizeError as e:
                    errors.append(str(e))
                    if "HTTP" in str(e) or "таймаут" in str(e) or "сеть" in str(e):     # повтор той же модели не поможет
                        break
    raise SummarizeError("; ".join(errors[-4:]) or "не настроен ни один ключ нейросети")
