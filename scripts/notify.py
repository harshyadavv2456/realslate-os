#!/usr/bin/env python3
"""Send a status message to Telegram and/or email.

Configured only through environment variables (GitHub Actions secrets):
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, EMAIL_SENDER, EMAIL_RECIPIENTS (comma-separated)

Any channel that is not configured is skipped. Never exits non-zero, so a
notification problem cannot fail the pipeline.

Usage: python scripts/notify.py "Title" "Message body"
"""
import json
import os
import smtplib
import sys
import urllib.request
from email.message import EmailMessage


def telegram(title: str, body: str) -> None:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("telegram: not configured, skipped")
        return
    data = json.dumps({"chat_id": chat, "text": f"{title}\n\n{body}"[:4000]}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data, headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        print(f"telegram: sent ({r.status})")


def email(title: str, body: str) -> None:
    host, sender = os.environ.get("SMTP_HOST"), os.environ.get("EMAIL_SENDER")
    rcpt = [r.strip() for r in os.environ.get("EMAIL_RECIPIENTS", "").split(",") if r.strip()]
    if not host or not sender or not rcpt:
        print("email: not configured, skipped")
        return
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = title, sender, ", ".join(rcpt)
    msg.set_content(body)
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT") or 587), timeout=20) as s:
        s.starttls()
        if os.environ.get("SMTP_USER"):
            s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
        s.send_message(msg)
    print("email: sent")


def main() -> None:
    title = sys.argv[1] if len(sys.argv) > 1 else "RealSlate"
    body = sys.argv[2] if len(sys.argv) > 2 else ""
    for send in (telegram, email):
        try:
            send(title, body)
        except Exception as e:  # noqa: BLE001 - notifications are best-effort
            print(f"{send.__name__}: failed: {type(e).__name__}")


if __name__ == "__main__":
    main()
