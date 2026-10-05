"""Website address validation (no network)."""

import pytest


def test_server_url_must_be_https(fresh_db):
    from app.data import cloud

    with pytest.raises(cloud.CloudError):
        cloud.set_server_url("http://example.com")
    assert cloud.set_server_url("https://example.workers.dev/") == "https://example.workers.dev"
    assert cloud.set_server_url("http://127.0.0.1:8787") == "http://127.0.0.1:8787"
    assert cloud.share_link("ABC234") == "http://127.0.0.1:8787/?code=ABC234"


def test_default_url_is_https():
    from app.data import cloud

    assert cloud.DEFAULT_URL == "" or cloud.DEFAULT_URL.startswith("https://")
