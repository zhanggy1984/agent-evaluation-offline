"""7.1 邮件发送单测（app/core/mail.py）。

mock smtplib 验证 SSL/STARTTLS 分支、login/sendmail 调用序列、异常向上抛。
send_smtp_email 是纯标准库函数，宿主直接跑（告警编排在 core/alarm.py，归 test_alarm）。
"""
import smtplib

import pytest

from app.core import mail


def _smtp_fakes(monkeypatch):
    """返回 (ssl_log, plain_log) 调用记录容器。"""
    ssl_log = []
    plain_log = []

    class FakeSSL:
        def __init__(self, host, port, timeout=None):
            ssl_log.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def login(self, u, p):
            ssl_log.append(("login", u))

        def sendmail(self, f, r, m):
            ssl_log.append(("send", f, r))

    class FakePlain:
        def __init__(self, host, port, timeout=None):
            plain_log.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def ehlo(self):
            plain_log.append(("ehlo",))

        def starttls(self):
            plain_log.append(("starttls",))

        def login(self, u, p):
            plain_log.append(("login", u))

        def sendmail(self, f, r, m):
            plain_log.append(("send", f, r))

    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", FakeSSL)
    monkeypatch.setattr(mail.smtplib, "SMTP", FakePlain)
    return ssl_log, plain_log


def test_port_465_uses_ssl(monkeypatch):
    ssl_log, plain_log = _smtp_fakes(monkeypatch)
    mail.send_smtp_email(host="smtp.example.com", port=465, username="u", password="p",
                         from_addr="a@x.com", recipients=["b@x.com"], subject="告警", html="<b>x</b>")
    assert ssl_log[0][1:] == ("smtp.example.com", 465, 10)  # SMTP_SSL(host, port, timeout=10)
    assert "login" in [x[0] for x in ssl_log]
    assert "send" in [x[0] for x in ssl_log]
    assert plain_log == []  # 465 不走 STARTTLS


def test_non_465_starttls(monkeypatch):
    ssl_log, plain_log = _smtp_fakes(monkeypatch)
    mail.send_smtp_email(host="smtp.example.com", port=587, username="u", password="p",
                         from_addr="a@x.com", recipients=["b@x.com"], subject="告警", html="<b>x</b>")
    assert ssl_log == []
    assert plain_log.count(("ehlo",)) == 2  # STARTTLS 前后各一次 ehlo
    assert ("starttls",) in plain_log
    assert "login" in [x[0] for x in plain_log]
    assert "send" in [x[0] for x in plain_log]


def test_exception_propagates(monkeypatch):
    # 发送异常向上抛（由 alarm.py 兜底记 warning，mail 自身不吞）
    class Boom:
        def __init__(self, host, port, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def login(self, u, p):
            raise smtplib.SMTPAuthenticationError(535, b"denied")

        def sendmail(self, f, r, m):
            pass

    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", Boom)
    with pytest.raises(smtplib.SMTPAuthenticationError):
        mail.send_smtp_email(host="h", port=465, username="u", password="p",
                             from_addr="a@x.com", recipients=["b"], subject="s", html="h")
