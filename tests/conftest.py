import pytest
import requests


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected network call in unit test")
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
