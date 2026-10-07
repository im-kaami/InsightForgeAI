import re

import pytest

from insightforge.config import Settings, get_settings, validate_settings
from insightforge.db.models import PasswordReset
from insightforge.db.session import SessionLocal
from insightforge.services import mailer

from .test_metric_follows_api import _follow
from .test_model_schedules import _replace_with_shifted_data, _saved_model

PASSWORD = "correct horse battery staple"


class FakeSMTP:
    instances: list["FakeSMTP"] = []
    fail = False

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.calls: list[str] = []
        self.messages = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.calls.append("starttls")

    def login(self, username, password):
        self.calls.append(f"login:{username}")

    def send_message(self, message):
        if FakeSMTP.fail:
            raise OSError("connection refused")
        self.calls.append("send")
        self.messages.append(message)


class FakeSMTPSSL(FakeSMTP):
    pass


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.instances = []
    FakeSMTP.fail = False
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSMTPSSL)
    monkeypatch.setattr(mailer, "start_background", mailer._send_quietly)
    return FakeSMTP


def _enable(monkeypatch, **extra):
    values = {"SMTP_HOST": "mail.example.com", "SMTP_FROM": "insightforge@example.com", **extra}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def _sent(smtp):
    return [message for server in smtp.instances for message in server.messages]


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


async def test_features_follow_the_smtp_settings(client, monkeypatch):
    assert (await client.get("/api/auth/features")).json() == {"email": False}
    _enable(monkeypatch)
    assert (await client.get("/api/auth/features")).json() == {"email": True}
    monkeypatch.setenv("SMTP_FROM", "")
    get_settings.cache_clear()
    assert (await client.get("/api/auth/features")).json() == {"email": False}


async def test_preferences_toggle_email_alerts(client, auth_headers):
    me = (await client.get("/api/auth/me", headers=auth_headers)).json()
    assert me["email_alerts"] is False
    on = await client.put("/api/auth/preferences", headers=auth_headers, json={"email_alerts": True})
    assert on.status_code == 200 and on.json()["email_alerts"] is True
    assert (await client.get("/api/auth/me", headers=auth_headers)).json()["email_alerts"] is True
    token = (
        await client.post("/api/auth/tokens", headers=auth_headers, json={"name": "ci", "scope": "ask"})
    ).json()["token"]
    refused = await client.put("/api/auth/preferences", headers=_bearer(token), json={"email_alerts": False})
    assert refused.status_code in {401, 403}
    assert (await client.get("/api/auth/me", headers=auth_headers)).json()["email_alerts"] is True


async def test_test_email(client, auth_headers, smtp, monkeypatch):
    assert (await client.post("/api/auth/test-email", headers=auth_headers)).status_code == 503
    _enable(monkeypatch)
    sent = await client.post("/api/auth/test-email", headers=auth_headers)
    assert sent.status_code == 204, sent.text
    [message] = _sent(smtp)
    assert message["To"] == "owner@example.com" and message["From"] == "insightforge@example.com"
    again = await client.post("/api/auth/test-email", headers=auth_headers)
    assert again.status_code == 429
    assert len(_sent(smtp)) == 1


async def test_test_email_reports_delivery_problems_generically(client, auth_headers, smtp, monkeypatch):
    _enable(monkeypatch)
    smtp.fail = True
    failed = await client.post("/api/auth/test-email", headers=auth_headers)
    assert failed.status_code == 502
    assert failed.json()["detail"] == "The email could not be sent; check the SMTP settings"
    assert "refused" not in failed.text


async def test_forgot_password(client, smtp, monkeypatch):
    body = {"email": "amy@example.com", "password": PASSWORD}
    await client.post("/api/auth/register", json=body)
    unavailable = await client.post("/api/auth/password/forgot", json={"email": "amy@example.com"})
    assert unavailable.status_code == 503
    _enable(monkeypatch, APP_BASE_URL="https://insights.example.com/")
    ghost = await client.post("/api/auth/password/forgot", json={"email": "ghost@example.com"})
    assert ghost.status_code == 204 and _sent(smtp) == []
    known = await client.post("/api/auth/password/forgot", json={"email": " AMY@example.com "})
    assert known.status_code == 204
    [message] = _sent(smtp)
    assert message["To"] == "amy@example.com"
    text = message.get_content()
    link = re.search(r"https://insights\.example\.com/reset#(ifr_[\w-]+)", text)
    assert link and "ignore this email" in text and "1 hour" in text
    again = await client.post("/api/auth/password/forgot", json={"email": "amy@example.com"})
    assert again.status_code == 204 and len(_sent(smtp)) == 1
    db = SessionLocal()
    try:
        stored = db.query(PasswordReset).one()
        assert link.group(1) not in stored.token_hash and len(stored.token_hash) == 64
    finally:
        db.close()
    reset = await client.post(
        "/api/auth/password/reset", json={"token": link.group(1), "new_password": "brand new password"}
    )
    assert reset.status_code == 204
    changed = {"email": "amy@example.com", "password": "brand new password"}
    login = await client.post("/api/auth/login", json=changed)
    assert login.status_code == 200


def _alert_body(smtp):
    [message] = _sent(smtp)
    text = message.get_content()
    link = [line for line in text.splitlines() if "/datasets/" in line]
    assert len(link) == 1
    return message, text, link[0]


async def test_metric_alert_emails_hold_names_and_a_link_only(client, auth_headers, smtp, monkeypatch):
    _enable(monkeypatch)
    await client.put("/api/auth/preferences", headers=auth_headers, json={"email_alerts": True})
    dataset, response = await _follow(client, auth_headers, threshold_percent=0.01)
    follow = response.json()
    check = (await client.post(f"/api/follows/{follow['id']}/check", headers=auth_headers)).json()
    assert check["alert"] is True
    message, text, link = _alert_body(smtp)
    assert message["Subject"].startswith("InsightForge alert: Revenue on ")
    assert "moved past its alert threshold" in text and link.endswith(f"/datasets/{dataset['id']}")
    rest = text.replace(link, "")
    for number in (check["current"], check["previous"], check["change_percent"]):
        for form in (f"{number:,.0f}", f"{number:,.2f}", f"{number}", str(int(number))):
            assert form not in rest
    assert not re.search(r"\d", rest.replace("InsightForge", ""))


async def test_metric_emails_respect_the_opt_in_and_the_alert_flag(client, auth_headers, smtp, monkeypatch):
    _enable(monkeypatch)
    dataset, response = await _follow(client, auth_headers, threshold_percent=1000)
    follow = response.json()
    await client.put("/api/auth/preferences", headers=auth_headers, json={"email_alerts": True})
    quiet = (await client.post(f"/api/follows/{follow['id']}/check", headers=auth_headers)).json()
    assert quiet["alert"] is False and _sent(smtp) == []
    await client.put("/api/auth/preferences", headers=auth_headers, json={"email_alerts": False})
    await client.put(f"/api/datasets/{dataset['id']}/metrics", headers=auth_headers, json={"metrics": []})
    failed = (await client.post(f"/api/follows/{follow['id']}/check", headers=auth_headers)).json()
    assert failed["alert"] is True and _sent(smtp) == []
    await client.put("/api/auth/preferences", headers=auth_headers, json={"email_alerts": True})
    failed = (await client.post(f"/api/follows/{follow['id']}/check", headers=auth_headers)).json()
    assert failed["status"] == "failed"
    _, text, _ = _alert_body(smtp)
    assert "its check could not run" in text


async def test_model_alert_emails(client, auth_headers, smtp, monkeypatch):
    _enable(monkeypatch)
    await client.put("/api/auth/preferences", headers=auth_headers, json={"email_alerts": True})
    dataset, model = await _saved_model(client, auth_headers)
    await client.put(f"/api/models/{model['id']}/schedule", headers=auth_headers, json={"cron": "0 6 * * *"})
    manual = await client.post(f"/api/models/{model['id']}/score", headers=auth_headers, json={})
    assert manual.status_code == 200 and _sent(smtp) == []
    calm = (await client.post(f"/api/models/{model['id']}/schedule/run-now", headers=auth_headers)).json()
    assert calm["alert"] is False and _sent(smtp) == []
    await _replace_with_shifted_data(client, auth_headers, dataset)
    drift = (await client.post(f"/api/models/{model['id']}/schedule/run-now", headers=auth_headers)).json()
    assert drift["alert"] is True
    message, text, link = _alert_body(smtp)
    assert message["Subject"] == f"InsightForge alert: model {model['name']}"
    assert "recommends retraining" in text and link.endswith(f"/datasets/{dataset['id']}")
    assert str(drift["max_psi"]) not in text


async def test_model_failure_emails_say_the_check_could_not_run(client, auth_headers, smtp, monkeypatch):
    _enable(monkeypatch)
    await client.put("/api/auth/preferences", headers=auth_headers, json={"email_alerts": True})
    dataset, model = await _saved_model(client, auth_headers)
    await client.put(f"/api/models/{model['id']}/schedule", headers=auth_headers, json={"cron": "0 6 * * *"})
    rule = {"kind": "row_count", "table": dataset["tables"][0], "severity": "blocking", "min": 10_000_000}
    await client.put(f"/api/datasets/{dataset['id']}/rules", headers=auth_headers, json={"rules": [rule]})
    failed = (await client.post(f"/api/models/{model['id']}/schedule/run-now", headers=auth_headers)).json()
    assert failed["status"] == "failed"
    _, text, _ = _alert_body(smtp)
    assert "the scheduled check could not run" in text


async def test_connection_security_modes(smtp, monkeypatch):
    _enable(monkeypatch, SMTP_SECURITY="starttls", SMTP_USERNAME="mailer", SMTP_PASSWORD="secret")
    mailer.send_email("a@example.com", "Hello", "Body")
    [server] = smtp.instances
    assert type(server) is FakeSMTP and (server.host, server.port) == ("mail.example.com", 587)
    assert server.calls == ["starttls", "login:mailer", "send"]
    assert server.timeout == 10
    smtp.instances.clear()
    _enable(monkeypatch, SMTP_SECURITY="ssl", SMTP_PORT="465", SMTP_USERNAME="")
    mailer.send_email("a@example.com", "Hello", "Body")
    [server] = smtp.instances
    assert isinstance(server, FakeSMTPSSL) and server.calls == ["send"]
    smtp.instances.clear()
    _enable(monkeypatch, SMTP_SECURITY="none", SMTP_PORT="25")
    mailer.send_email("a@example.com", "Hello", "Body")
    assert smtp.instances[0].calls == ["send"]


def test_failures_become_mail_errors_without_details(smtp, monkeypatch):
    _enable(monkeypatch)
    smtp.fail = True
    with pytest.raises(mailer.MailError) as caught:
        mailer.send_email("a@example.com", "Hello", "Body")
    assert str(caught.value) == "OSError"


def test_plain_smtp_to_a_remote_host_is_a_configuration_problem():
    base = {"smtp_from": "a@example.com", "jwt_secret": "x" * 40, "app_secret": "y" * 40}
    remote = Settings(smtp_host="mail.example.com", smtp_security="none", **base)
    assert any("SMTP_SECURITY=none" in problem for problem in validate_settings(remote))
    for fine in (
        Settings(smtp_host="127.0.0.1", smtp_security="none", **base),
        Settings(smtp_host="localhost", smtp_security="none", **base),
        Settings(smtp_host="mail.example.com", smtp_security="starttls", **base),
        Settings(smtp_host="", smtp_security="none", **base),
    ):
        assert not any("SMTP_SECURITY" in problem for problem in validate_settings(fine))
