from pydantic_settings import BaseSettings, SettingsConfigDict

PAYPAL_SANDBOX_URL = "https://api-m.sandbox.paypal.com"


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables or a local .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: str
    # Shared secret Telegram echoes back on each webhook call, so we can reject forged updates.
    telegram_webhook_secret: str = ""

    paypal_client_id: str
    paypal_client_secret: str
    paypal_base_url: str = PAYPAL_SANDBOX_URL
    currency: str = "USD"

    # Where this server is reachable from the buyer's browser (PayPal redirects back here)
    # and, when it is https, from Telegram (webhook mode). Locally, long polling is used instead.
    public_base_url: str = "http://localhost:8000"
    database_path: str = "data/app.db"

    @property
    def use_webhook(self) -> bool:
        return self.public_base_url.startswith("https://")
