from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database — Replit injects DATABASE_URL automatically for its native Postgres.
    # Locally: set DATABASE_URL in .env, or leave blank to fall back to SQLite dev DB.
    database_url: str = "sqlite:///./musai_dev.db"
    database_url_readonly: str = "sqlite:///./musai_dev.db"

    # UACH credentials
    uach_username: str = ""
    uach_password: str = ""
    sega_username: str = ""
    sega_password: str = ""

    # Moodle hosts
    moodle_base_url_staging: str = "https://capacitacion.uach.mx"
    moodle_base_url_prod: str = "https://campusvirtual.uach.mx"
    sega_base_url: str = "https://sega.uach.mx/usuarios/sign_in"

    # Gemini — PAID key (the owner's own, no-train). Cost discipline lives in musai/ai/.
    gemini_api_key: str = ""
    # flash-lite: $0.30/1M in, $2.50/1M out — ~5x cheaper in / ~3x cheaper out than a full
    # Flash, and MUSAI's assistant work is lookups + short summaries, not reasoning.
    gemini_model: str = "gemini-3.5-flash-lite"

    # Cost ceilings. Everything is counted in TOKENS, which we can measure exactly; dollars
    # are only estimated if the two price fields below are filled in from the billing console
    # (left at 0.0 = "report tokens, don't guess money").
    gemini_price_in_per_mtok: float = 0.0    # USD per 1M input tokens
    gemini_price_out_per_mtok: float = 0.0   # USD per 1M output tokens

    # Daily budgets, per actor per day. The admin cap is high but NOT infinite: a runaway
    # tool loop in the owner's own session is exactly the failure this is here to stop.
    ai_daily_tokens_admin: int = 1_500_000
    ai_daily_tokens_user: int = 50_000
    ai_daily_requests_admin: int = 400
    ai_daily_requests_user: int = 30

    # Monthly free MUSAI usage per professor, in millionths of a USD. $0.10 buys roughly 100
    # assistant questions or 8 course restores — see musai/metering.py for the arithmetic.
    # 🔴 Not enforced by default. The durations behind the estimate have never been measured
    # over a real month, and refusing a colleague's restore on a guess is worse than an
    # overspend of a few cents. The DAILY token budget already stops a runaway loop, which is
    # the failure that actually burns money. Flip this on once the ledger has real rows.
    usage_free_micro_usd: int = 100_000      # $0.10
    usage_enforce: bool = False

    # WhatsApp / Meta (SUSAI)
    whatsapp_verify_token: str = ""
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_app_secret: str = ""
    graph_api_version: str = "v23.0"
    # The owner's own WhatsApp number → recognized as the coordinator/admin (read-only analytics
    # over all his groups). Any form; matched canonically. Empty = no admin recognized.
    susai_admin_phone: str = ""

    # Google sign-in (professor identity). Empty client id/secret = auth NOT configured;
    # the cockpit then refuses to serve rather than falling open (see musai/web/auth.py).
    google_client_id: str = ""
    google_client_secret: str = ""
    # Signs the session cookie. MUST be overridden in .env / Replit Secrets:
    #   python -c "import secrets;print(secrets.token_urlsafe(32))"
    session_secret: str = ""
    # 🔴 The gate. The owner's instruction, 2026-08-13: only @uach.mx accounts, no exceptions
    # and no bypass list — a permissive rail is worse than none. Google's `hd` parameter only
    # hints the account chooser; this value is what actually refuses, server-side.
    allowed_email_domain: str = "uach.mx"
    admin_email: str = "professor@uach.mx"

    # 🔴 Recovery addresses — the answer to "the university owns my admin account, not me".
    # Comma-separated EXACT addresses (never a domain — `gmail.com` here would open MUSAI to
    # the planet). They pass the gate and are issued a session **as `admin_email`**: they are
    # aliases for the owner, not extra accounts. Signing in under a second identity would
    # create a second `Professor` row owning zero courses — a successful login into an empty
    # cockpit, which is not recovery — `auth._session_user()` is what prevents it.
    #
    # ⚠️ This is the allow-list escape hatch `auth._gate()` deliberately did NOT have between
    # 2026-08-13 and 2026-08-16. It exists now by the owner's explicit instruction, and it is
    # narrow on purpose: exact addresses, still requiring a Google-verified email.
    admin_recovery_emails: str = ""
    # Empty locally (the request's own base_url is used); the public origin in prod.
    app_base_url: str = ""

    # 🔴 Encrypts every professor's stored Moodle/SEGA password (musai/security/vault.py).
    # Empty = the vault refuses to store or read ANYTHING; it never falls back to plaintext.
    # Generate with:
    #   python -c "from musai.security.vault import generate_key; print(generate_key())"
    # Rotating it makes every already-stored password unreadable — they are re-entered, not
    # recovered. That is deliberate: the DB alone must never be enough to log in as someone.
    credential_key: str = ""

    # App
    cockpit_password: str = "changeme"
    app_secret_key: str = "changeme-dev-key"
    timezone: str = "America/Chihuahua"
    dry_run: bool = True

    @property
    def auth_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret and self.session_secret)

    @property
    def owner_email(self) -> str:
        """The one identity every admin path resolves to. Courses are scoped to this row."""
        return (self.admin_email or "").strip().lower()

    @property
    def recovery_addresses(self) -> tuple[str, ...]:
        """Exact addresses that may sign in as the owner. Empty by default."""
        return tuple(
            part.strip().lower()
            for part in (self.admin_recovery_emails or "").split(",")
            if part.strip()
        )

    def is_recovery_address(self, email: str) -> bool:
        return (email or "").strip().lower() in self.recovery_addresses

    def is_admin_email(self, email: str) -> bool:
        """Admin = the owner's address, or an alias for it. Never self-grantable."""
        addr = (email or "").strip().lower()
        return bool(addr) and (addr == self.owner_email or self.is_recovery_address(addr))

    @field_validator("sega_username", mode="before")
    @classmethod
    def _default_sega_user(cls, v, info):
        if not v:
            return info.data.get("uach_username", "")
        return v

    @field_validator("sega_password", mode="before")
    @classmethod
    def _default_sega_pass(cls, v, info):
        if not v:
            return info.data.get("uach_password", "")
        return v


settings = Settings()
