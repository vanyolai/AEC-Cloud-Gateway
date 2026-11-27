import hashlib
import json
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from aec_cloud_gateway import (
    AECClient, APIError, AuthenticationError, ProtocolError,
    RateLimitError, SessionExpiredError, TransportError,
)

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://monitor.ai-ec.cloud:8443"


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def response(payload=None, status=200, headers=None):
    r = Mock()
    r.status_code = status
    r.headers = headers or {}
    r.json.return_value = payload
    return r


@pytest.fixture
def client(monkeypatch):
    c = AECClient("example@example.invalid", "example-password")
    monkeypatch.setattr("aec_cloud_gateway.client.time.sleep", Mock())
    yield c
    c.close()


def script(client, *results):
    queue = iter(results)

    def send(method, url, **kwargs):
        item = next(queue)
        if isinstance(item, Exception):
            raise item
        if url.endswith("/user/login") and item.status_code == 200 and item.json.return_value == {"result": 0}:
            client._session.cookies.set("JSESSIONID", "synthetic-cookie")
        return item

    mock = Mock(side_effect=send)
    client._session.request = mock
    return mock


def login_response():
    return response(fixture("login"))


def test_login_and_all_routes(client):
    home = fixture("home_count")
    device = fixture("device")
    mock = script(client, login_response(), response({"result": 0}),
                  response({"result": 0}), response(home), response(device))
    assert client.get_plants() == {"result": 0}
    client.get_home_control_sn(123)
    assert client.get_home_count_data(123, "EXAMPLE-INVERTER") == home
    assert client.get_device_by_sn("EXAMPLE-INVERTER", date(2026, 11, 26)) == device
    calls = mock.call_args_list
    assert [(x.args[0], x.args[1]) for x in calls] == [
        ("POST", BASE + "/user/login"), ("GET", BASE + "/plant/getPlantVos"),
        ("GET", BASE + "/energy/getHomeControlSn/123"),
        ("POST", BASE + "/energy/getHomeCountData"),
        ("POST", BASE + "/device/getDeviceBySn"),
    ]
    assert calls[0].kwargs["json"] == {
        "email": "example@example.invalid",
        "password": hashlib.md5(b"example-password", usedforsecurity=False).hexdigest(),
        "phoneOs": 1, "phoneModel": "1.1", "appVersion": "V1.1",
    }
    assert calls[3].kwargs["params"] == {"plantId": "123", "deviceSn": "EXAMPLE-INVERTER"}
    assert calls[4].kwargs["params"] == {"deviceType": 55, "time": "2026-11-26", "sn": "EXAMPLE-INVERTER"}
    assert all(x.kwargs["allow_redirects"] is False and x.kwargs["timeout"] == 20 for x in calls)
    assert device["data"]["deviceDetail"] is None
    assert device["data"]["deviceDetailInfoVo"]["soc"] == 0


@pytest.mark.parametrize("expired", [response(status=401), response({"result": 1, "msg": "Please login"}), response({"msg": "Please login"})])
def test_session_renewal(client, expired):
    mock = script(client, login_response(), expired, login_response(), response({"result": 0}))
    assert client.get_plants() == {"result": 0}
    assert mock.call_count == 4
    assert mock.call_args_list[1].args == mock.call_args_list[3].args
    assert client.request_attempts == 4
    assert client.successful_logins == 2
    assert client.session_renewals == 1
    assert client.request_retries == 0


def test_session_renewal_bounded(client):
    mock = script(client, login_response(), response(status=401), login_response(), response(status=401))
    with pytest.raises(SessionExpiredError):
        client.get_plants()
    assert mock.call_count == 4
    assert not client._session.cookies


def test_retries(client):
    mock = script(client, login_response(), requests.Timeout("secret"), response(status=503), response({"result": 0}))
    assert client.get_plants() == {"result": 0}
    assert mock.call_count == 4
    assert client.request_attempts == 4
    assert client.request_retries == 2
    assert client.successful_logins == 1


def test_retry_after(client):
    mock = script(client, login_response(), response(status=429, headers={"Retry-After": "5"}), response({"result": 0}))
    client.get_plants()
    from aec_cloud_gateway.client import time
    time.sleep.assert_called_once_with(5)
    assert mock.call_count == 3


def test_long_retry_after(client):
    mock = script(client, login_response(), response(status=429, headers={"Retry-After": "120"}))
    with pytest.raises(RateLimitError):
        client.get_plants()
    assert mock.call_count == 2


def test_retry_exhaustion(client):
    mock = script(client, login_response(), *[response(status=503) for _ in range(3)])
    with pytest.raises(TransportError):
        client.get_plants()
    assert mock.call_count == 4


@pytest.mark.parametrize("status", [302, 403, 404])
def test_no_retry_other_http_errors(client, status):
    mock = script(client, login_response(), response(status=status))
    with pytest.raises(TransportError):
        client.get_plants()
    assert mock.call_count == 2


def test_tls_error_is_not_retried(client):
    mock = script(client, requests.exceptions.SSLError("sensitive URL"))
    with pytest.raises(TransportError, match="TLS verification failed"):
        client.get_plants()
    assert mock.call_count == 1


def test_rejected_login(client):
    mock = script(client, response({"result": 1, "msg": "secret details"}))
    with pytest.raises(AuthenticationError) as exc:
        client.get_plants()
    assert "secret" not in str(exc.value)
    assert mock.call_count == 1


def test_missing_cookie(client):
    client._session.request = Mock(return_value=login_response())
    with pytest.raises(AuthenticationError, match="JSESSIONID"):
        client.login()


def test_missing_login_marker(client):
    script(client, response({"data": {}}))
    with pytest.raises(ProtocolError):
        client.login()


@pytest.mark.parametrize("payload", [[], None, "text"])
def test_invalid_json_shape(client, payload):
    script(client, login_response(), response(payload))
    with pytest.raises(ProtocolError):
        client.get_plants()


def test_non_json(client):
    bad = response()
    bad.json.side_effect = ValueError("html")
    script(client, login_response(), bad)
    with pytest.raises(ProtocolError):
        client.get_plants()


def test_api_failure_not_renewed(client):
    mock = script(client, login_response(), response({"result": 1, "msg": "other error"}))
    with pytest.raises(APIError):
        client.get_plants()
    assert mock.call_count == 2


def test_raw_null_zero(client):
    data = {"soc": 0, "other": None}
    script(client, login_response(), response(data))
    assert client.get_plants() == data


@pytest.mark.parametrize("day", ["20261126", "2026-02-30", "2026-W48-4", None])
def test_invalid_day(client, day):
    with pytest.raises(ValueError):
        client.get_device_by_sn("EXAMPLE", day)


@pytest.mark.parametrize("identifier", ["../secret", "x/y", "", True, "a?b"])
def test_invalid_identifier(client, identifier):
    with pytest.raises(ValueError):
        client.get_home_control_sn(identifier)


def test_env_and_closed(monkeypatch):
    monkeypatch.setenv("AEC_EMAIL", "example@example.invalid")
    monkeypatch.setenv("AEC_PASSWORD", "example-password")
    with AECClient.from_env() as c:
        assert isinstance(c, AECClient)
    with pytest.raises(RuntimeError):
        c.get_plants()


@pytest.mark.parametrize("url", ["http://example.invalid", "https://u:p@example.invalid", "https://example.invalid/path"])
def test_insecure_or_invalid_base_url(url):
    with pytest.raises(ValueError):
        AECClient("example@example.invalid", "password", base_url=url)


@pytest.mark.parametrize("result", [None, False, "0"])
def test_malformed_result_not_success(client, result):
    script(client, login_response(), response({"result": result}))
    with pytest.raises(APIError):
        client.get_plants()
