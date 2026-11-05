import os

from pydantic_settings import BaseSettings, SettingsConfigDict

PAYPAL_SANDBOX_URL = "https://api-m.sandbox.paypal.com"


class PayPalSettings(BaseSettings):
    """PayPal configuration, read from environment variables or a local .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        # This project's .env wins over variables exported in the shell, which may belong to something else.
        return init_settings, dotenv_settings, env_settings, file_secret_settings

    paypal_client_id: str
    paypal_client_secret: str
    paypal_base_url: str = PAYPAL_SANDBOX_URL
    currency: str = "USD"


class Settings(PayPalSettings):
    """Full app configuration."""

    telegram_bot_token: str
    # Shared secret Telegram echoes back on each webhook call, so we can reject forged updates.
    telegram_webhook_secret: str = ""

    # Where this server is reachable from the buyer's browser (PayPal redirects back here)
    # and, when it is https, from Telegram (webhook mode). Locally, long polling is used instead.
    # Render sets RENDER_EXTERNAL_URL itself, so a deployment doesn't need PUBLIC_BASE_URL.
    public_base_url: str = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8000")
    database_path: str = "data/app.db"

    # If empty, the OpenAI SDK looks for credentials itself.
    openai_api_key: str = ""
    openai_model: str = "gpt-6.1-terra"
    # If set, a chat must send this code once before the bot does anything for it. Empty means open to everyone.
    access_code: str = ""
    # Show notes about the simulated stores and /ff in the chat. Turn off when recording the demo video.
    demo_hints: bool = True

    @property
    def use_webhook(self) -> bool:
        return self.public_base_url.startswith("https://")
