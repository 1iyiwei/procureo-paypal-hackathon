from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from intent_agent.config import Settings
from intent_agent.web import WEBHOOK_PATH, create_app


def make_client():
    settings = Settings(
        _env_file=None, telegram_bot_token="42:TEST", telegram_webhook_secret="s", paypal_client_id="a", paypal_client_secret="b"
    )
    app = create_app(settings, checkout=SimpleNamespace(), agent=SimpleNamespace())
    client = TestClient(app)
    feed = AsyncMock()
    return app, client, feed


def post(client, update_id, secret="s"):
    return client.post(WEBHOOK_PATH, json={"update_id": update_id}, headers={"X-Telegram-Bot-Api-Secret-Token": secret})


def test_a_resent_update_is_handled_once():
    app, client, feed = make_client()
    with client:
        app.state.bot, app.state.dp = SimpleNamespace(), SimpleNamespace(feed_update=feed)
        for update_id in (1, 1, 2):
            assert post(client, update_id).status_code == 200
    assert [call.args[1].update_id for call in feed.call_args_list] == [1, 2]


def test_a_webhook_call_without_the_secret_is_refused():
    app, client, feed = make_client()
    with client:
        app.state.bot, app.state.dp = SimpleNamespace(), SimpleNamespace(feed_update=feed)
        assert post(client, 1, secret="wrong").status_code == 403
    feed.assert_not_called()
