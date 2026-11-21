"""Regression: background runner must see the API's saved model credentials."""

import json

from libtv_agent.config import Settings
from libtv_agent.runner.service_runtime import (
    _admin_credentials_signature,
    _refresh_ai_credentials,
)


def test_runner_loads_and_refreshes_ai_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    file = tmp_path / "admin-credentials.json"
    settings = Settings(_env_file=None, admin_credentials_file=file)
    assert _admin_credentials_signature(file) is None
    assert settings.openai_api_key is None

    file.write_text(
        json.dumps({
            "modelApiKey": "unit-test-key-1234",
            "modelBaseUrl": "https://example.test/v1",
        }),
        encoding="utf-8",
    )
    signature = _admin_credentials_signature(file)
    assert signature is not None
    _refresh_ai_credentials(settings)
    assert settings.openai_api_key == "unit-test-key-1234"
    assert settings.openai_base_url == "https://example.test/v1"

    # Clearing saved credentials must not leave a stale in-memory API key.
    file.unlink()
    assert _admin_credentials_signature(file) is None
    _refresh_ai_credentials(settings)
    assert settings.openai_api_key is None
    assert settings.openai_base_url is None
