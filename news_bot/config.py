"""Настройки бота: берутся из переменных окружения (на GitHub — из Secrets)."""
import os
from dataclasses import dataclass


def _int(name, default):
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _list(name, default):
    raw = os.environ.get(name, "") or default
    return [x.strip() for x in raw.split(",") if x.strip()]


@dataclass
class Config:
    telegram_token: str = ""
    channel_id: str = ""                 # @username канала или числовой id вида -100...
    feed_url: str = ""                   # RSS/Atom-лента сайта
    site_url: str = ""                   # запасной вариант: главная страница со ссылками на статьи
    link_regex: str = ""                 # шаблон ссылок на статьи для site_url (необязательно)
    gemini_key: str = ""
    groq_key: str = ""
    gemini_models: list = None
    groq_models: list = None
    llm_order: list = None
    google_credentials_json: str = ""
    sheet_id: str = ""
    state_file: str = ""                 # если нет Google Таблицы: JSON-файл (для тестов)
    max_per_run: int = 3                 # сколько постов публиковать за один запуск
    first_run_posts: int = 3             # сколько последних новостей опубликовать при самом первом запуске
    max_age_hours: int = 48              # более старые новости не публикуем
    max_attempts: int = 3                # сколько раз пробуем статью, прежде чем отказаться
    summary_min: int = 600
    summary_max: int = 1800
    title_prefix: str = "📰 "
    repeat_title: bool = True            # повторять заголовок в начале второго сообщения (как в старом боте)
    source_label: str = "Джерело"
    skip_categories: list = None         # рубрики, которые не публикуем (у ForkLog «Лонгриды» — гостевые/рекламные)
    use_photos: bool = True              # False — не брать фото из статей
    fallback_image_url: str = ""         # картинка по умолчанию, если фото нет или оно отключено
    preferred_model: str = ""            # модель, которая сработала в прошлый раз (запоминается в таблице)
    loop_seconds: int = 0                # >0 — работать циклом столько секунд (режим реального времени)
    poll_seconds: int = 60               # как часто проверять ленту в режиме реального времени
    retry_delay: int = 300               # через сколько секунд повторять статью после ошибки
    llm_budget: int = 150                # сколько секунд максимум тратим на нейросети для одной статьи
    run_budget: int = 420                # после скольких секунд работы не брать новые статьи (лимит запуска — 10 минут)
    dry_run: bool = False
    admin_chat_id: str = ""

    @classmethod
    def from_env(cls):
        e = os.environ.get
        return cls(
            telegram_token=e("TELEGRAM_TOKEN", ""), channel_id=e("CHANNEL_ID", ""),
            feed_url=e("FEED_URL", ""), site_url=e("SITE_URL", ""), link_regex=e("LINK_REGEX", ""),
            gemini_key=e("GEMINI_API_KEY", ""), groq_key=e("GROQ_API_KEY", ""),
            gemini_models=_list("GEMINI_MODELS", ""),
            groq_models=_list("GROQ_MODELS", "openai/gpt-oss-120b,llama-3.3-70b-versatile"),
            llm_order=_list("LLM_ORDER", "gemini,groq"),
            google_credentials_json=e("GOOGLE_CREDENTIALS_JSON", ""), sheet_id=e("SHEET_ID", ""),
            state_file=e("STATE_FILE", ""),
            max_per_run=_int("MAX_PER_RUN", 3), first_run_posts=_int("FIRST_RUN_POSTS", 3),
            max_age_hours=_int("MAX_AGE_HOURS", 48), max_attempts=_int("MAX_ATTEMPTS", 3),
            summary_min=_int("SUMMARY_MIN", 600), summary_max=_int("SUMMARY_MAX", 1800),
            title_prefix=("" if e("TITLE_PREFIX") == "-" else (e("TITLE_PREFIX") or "📰 ")),
            repeat_title=((e("REPEAT_TITLE") or "1") != "0"),
            source_label=e("SOURCE_LABEL") or "Джерело",
            loop_seconds=_int("LOOP_SECONDS", 0), poll_seconds=max(20, _int("POLL_SECONDS", 60)),
            retry_delay=_int("RETRY_DELAY", 300),
            llm_budget=_int("LLM_BUDGET", 150), run_budget=_int("RUN_BUDGET", 420),
            skip_categories=[x.lower() for x in _list("SKIP_CATEGORIES", "Лонгриды")],
            use_photos=((e("USE_PHOTOS") or "1") != "0"), fallback_image_url=e("FALLBACK_IMAGE_URL", ""),
            dry_run=(e("DRY_RUN", "") .lower() in ("1", "true", "yes")), admin_chat_id=e("ADMIN_CHAT_ID", ""),
        )
