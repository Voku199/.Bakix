"""Konfigurace podle prostředí. Prostředí se vybírá proměnnou BAKIX_ENV.

Výchozí je "production", takže zapomenutá proměnná znamená přísnější nastavení,
ne otevřený debug režim. (Stejný vzor jako Knowix.)
"""

import os
from datetime import timedelta

from dotenv import load_dotenv

# .env se musí načíst dřív, než se vyhodnotí atributy tříd níže (čtou os.environ při importu).
load_dotenv()


def _list(name: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY")

    # Klíč, kterým se šifrují tokeny Bakalářů v databázi (Fernet). Schválně jiný než
    # SECRET_KEY: uniklý klíč na podpis cookies nesmí umět rozšifrovat přístup do školy.
    # Víc klíčů oddělených čárkou = rotace (první šifruje, všechny dešifrují).
    CREDENTIALS_KEY = os.environ.get("CREDENTIALS_KEY")

    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
    SQLALCHEMY_ENGINE_OPTIONS: dict = {}

    SESSION_COOKIE_NAME = "bakix"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)
    # CSRF token žije stejně dlouho jako session, ke které patří.
    WTF_CSRF_TIME_LIMIT = None
    MAX_CONTENT_LENGTH = 64 * 1024  # formuláře jsou malé; nic se nenahrává

    # Celá třída může sedět za jednou školní IP, limit proto nesmí být moc nízký.
    RATELIMIT_STORAGE_URI = os.environ.get("REDIS_URL", "memory://")
    RATELIMIT_DEFAULT = "600 per hour"
    RATELIMIT_HEADERS_ENABLED = True

    # Počet proxy před aplikací (Caddy na Pi = 1). Důvěřujeme jen X-Forwarded-For/-Proto,
    # nikdy X-Forwarded-Host.
    PROXY_COUNT = int(os.environ.get("PROXY_COUNT", "1"))

    # Veřejná adresa webu pro canonical, sitemap a Open Graph. Nebere se z hlavičky Host,
    # aby ji nešlo podvrhnout.
    PUBLIC_URL = os.environ.get("PUBLIC_URL")

    # --- Bakaláři ---
    # Oficiální seznam škol. Přihlásit se jde jen do školy, která v něm je (ochrana proti SSRF).
    SCHOOL_DIRECTORY_URL = os.environ.get(
        "SCHOOL_DIRECTORY_URL", "https://sluzby.bakalari.cz/api/v1/municipality"
    )
    # Školy navíc, které v seznamu chybí: "Název|Město|https://adresa", oddělené čárkou.
    BAKALARI_EXTRA_SCHOOLS = _list("BAKALARI_EXTRA_SCHOOLS")
    BAKALARI_TIMEOUT = float(os.environ.get("BAKALARI_TIMEOUT", "10"))
    # Jak dlouho se drží odpovědi školy v paměti procesu (s). Na disk se neukládají nikdy.
    SCHOOL_CACHE_SECONDS = int(os.environ.get("SCHOOL_CACHE_SECONDS", "120"))
    # Účet, na který se nikdo nepřihlásil tolik dní, smaže `flask --app wsgi cleanup`.
    INACTIVE_ACCOUNT_DAYS = 90

    # --- AI (volitelné, OpenAI-kompatibilní API; stejné proměnné jako Knowix) ---
    # Bez klíče Bakix jen skládá zadání, které si žák vloží do svojí AI. Nic se nikam neposílá.
    AI_API_KEY = os.environ.get("AI_API_KEY")
    AI_BASE_URL = os.environ.get("AI_BASE_URL", "https://api.aimlapi.com/v1")
    AI_MODEL = os.environ.get("AI_MODEL", "gpt-4o-mini")
    AI_TIMEOUT = float(os.environ.get("AI_TIMEOUT", "30"))
    AI_DAILY_REQUESTS_PER_USER = int(os.environ.get("AI_DAILY_REQUESTS_PER_USER", "10"))
    AI_DAILY_REQUESTS_TOTAL = int(os.environ.get("AI_DAILY_REQUESTS_TOTAL", "500"))

    # Sesterský projekt. Prázdná hodnota odkazy na Knowix vypne.
    KNOWIX_URL = os.environ.get("KNOWIX_URL", "https://knowix.cz").rstrip("/")

    # Správce osobních údajů pro /soukromi. V produkci povinné (čl. 13 GDPR).
    PRIVACY_CONTROLLER = os.environ.get("PRIVACY_CONTROLLER")
    PRIVACY_CONTACT_EMAIL = os.environ.get("PRIVACY_CONTACT_EMAIL")

    # Barevná paleta: voku | inkoust | tabule | grafit | svestka (bakix/design.py).
    PALETTE = os.environ.get("BAKIX_PALETTE", "voku")
    # Tvary: kulaty (zaoblené panely i ovládání) | mix (hranatý rám, kulaté ovládání) | hranaty.
    SHAPE = os.environ.get("BAKIX_SHAPE", "kulaty")

    # /styleguide pro všechny (jinak 404).
    STYLEGUIDE_PUBLIC = False

    @classmethod
    def validate(cls) -> None:
        """Kontrola povinných hodnot. Volá se při startu aplikace."""
        from bakix.design import PALETTES, SHAPES

        if cls.PALETTE not in PALETTES:
            raise RuntimeError(
                f"Neznámá paleta BAKIX_PALETTE={cls.PALETTE!r}, na výběr: {', '.join(PALETTES)}"
            )
        if cls.SHAPE not in SHAPES:
            raise RuntimeError(
                f"Neznámé tvary BAKIX_SHAPE={cls.SHAPE!r}, na výběr: {', '.join(SHAPES)}"
            )


class DevelopmentConfig(Config):
    DEBUG = True
    SECRET_KEY = Config.SECRET_KEY or "dev-only-nepouzivat-v-produkci"
    # Pevný vývojový klíč (platný Fernet klíč), ať se po restartu nemusíš znovu přihlašovat.
    CREDENTIALS_KEY = Config.CREDENTIALS_KEY or "ZGV2LW9ubHktbmVwb3V6aXZhdC12LXByb2R1a2NpISE="
    SQLALCHEMY_DATABASE_URI = Config.SQLALCHEMY_DATABASE_URI or "sqlite:///bakix_dev.db"
    PROXY_COUNT = 0
    PUBLIC_URL = Config.PUBLIC_URL or "http://127.0.0.1:5000"
    STYLEGUIDE_PUBLIC = True
    PRIVACY_CONTROLLER = Config.PRIVACY_CONTROLLER or "Provozovatel (vyplň PRIVACY_CONTROLLER)"
    PRIVACY_CONTACT_EMAIL = Config.PRIVACY_CONTACT_EMAIL or "kontakt@bakix.test"


class TestingConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret"  # noqa: S105  (jen pro testy)
    CREDENTIALS_KEY = "dGVzdC1rZXktdGVzdC1rZXktdGVzdC1rZXktMTIzNDU="
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False
    RATELIMIT_STORAGE_URI = "memory://"
    PROXY_COUNT = 0
    PUBLIC_URL = "https://bakix.test"
    SCHOOL_DIRECTORY_URL = "https://seznam.bakix.test/municipality"
    BAKALARI_EXTRA_SCHOOLS: list[str] = []
    AI_API_KEY = None
    AI_BASE_URL = "https://ai.bakix.test/v1"
    AI_DAILY_REQUESTS_PER_USER = 3
    AI_DAILY_REQUESTS_TOTAL = 100
    KNOWIX_URL = "https://knowix.cz"
    PRIVACY_CONTROLLER = "Testovací Provozovatel"
    PRIVACY_CONTACT_EMAIL = "soukromi@bakix.test"
    PALETTE = "voku"
    SHAPE = "kulaty"


class ProductionConfig(Config):
    # Prefix __Host- vynutí v prohlížeči Secure, Path=/ a žádnou Domain: cookie nejde
    # přepsat z jiné subdomény.
    SESSION_COOKIE_NAME = "__Host-bakix"
    SESSION_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"
    PUBLIC_URL = Config.PUBLIC_URL or "https://bakix.cz"

    @classmethod
    def validate(cls) -> None:
        super().validate()
        required = {
            "SECRET_KEY": "SECRET_KEY",
            "CREDENTIALS_KEY": "CREDENTIALS_KEY",
            "SQLALCHEMY_DATABASE_URI": "DATABASE_URL",
            "PRIVACY_CONTROLLER": "PRIVACY_CONTROLLER",
            "PRIVACY_CONTACT_EMAIL": "PRIVACY_CONTACT_EMAIL",
        }
        missing = [env for attr, env in required.items() if not getattr(cls, attr)]
        if missing:
            raise RuntimeError(
                "Chybí povinná konfigurace pro produkci (proměnné prostředí): " + ", ".join(missing)
            )
        if cls.SECRET_KEY == cls.CREDENTIALS_KEY:
            raise RuntimeError("SECRET_KEY a CREDENTIALS_KEY musí být dva různé klíče.")


CONFIGS: dict[str, type[Config]] = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config(name: str | None = None) -> type[Config]:
    name = name or os.environ.get("BAKIX_ENV", "production")
    try:
        return CONFIGS[name]
    except KeyError:
        raise RuntimeError(
            f"Neznámé prostředí BAKIX_ENV={name!r}, povolené: {', '.join(CONFIGS)}"
        ) from None
