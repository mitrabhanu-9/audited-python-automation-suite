import html
import logging
import os
import sys
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Dict
from urllib.parse import urlparse

import requests

# --- Security & Operational Configuration ---
RATE_API_URL = "https://api.frankfurter.app/latest?from=GBP&to=INR,USD"
ALLOWED_API_HOSTS = {"api.frankfurter.app", "api.telegram.org"}
REQUEST_TIMEOUT = 10  # Seconds
ALERT_THRESHOLD_INR = Decimal("105.00")  # Alert trigger target

# Secrets strictly loaded from environment variables (Never hardcoded!)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("AlertBotAuditor")


def validate_endpoint(url: str) -> None:
    """Blocks SSRF by enforcing HTTPS and strict domain allowlisting."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc not in ALLOWED_API_HOSTS:
        raise ValueError(f"Blocked untrusted API endpoint: {url}")


def fetch_live_rates(url: str) -> Dict[str, Decimal]:
    """Fetches live currency rates and validates JSON schema strictly."""
    validate_endpoint(url)
    logger.info("Connecting to live rate API...")

    resp = requests.get(
        url,
        headers={"User-Agent": "MarketAlertAuditor/1.0"},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    payload = resp.json()

    if not isinstance(payload, dict) or "rates" not in payload:
        raise ValueError("Invalid API JSON structure: 'rates' missing.")

    raw_rates = payload["rates"]
    validated_rates: Dict[str, Decimal] = {}

    for currency in ("INR", "USD"):
        if currency not in raw_rates:
            raise ValueError(f"Missing expected currency key: {currency}")
        try:
            rate_dec = Decimal(str(raw_rates[currency])).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        except InvalidOperation as exc:
            raise ValueError(f"Corrupted numeric rate for {currency}: {raw_rates[currency]!r}") from exc

        if not rate_dec.is_finite() or rate_dec <= Decimal("0"):
            raise ValueError(f"Out-of-bounds rate for {currency}: {rate_dec}")

        validated_rates[currency] = rate_dec

    return validated_rates


def send_telegram_alert(message_text: str) -> bool:
    """Sends sanitized HTML alert via Telegram Bot API or runs in safe Dry-Run mode."""
    safe_message = message_text[:4000]  # Telegram 4096-char safety cap

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram env credentials not set -> Running in AUDIT DRY-RUN mode.")
        print("\n" + "=" * 50)
        print("[SIMULATED TELEGRAM ALERT PAYLOAD]")
        print(safe_message)
        print("=" * 50 + "\n")
        return True

    tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    validate_endpoint(tg_url)

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": safe_message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    resp = requests.post(tg_url, json=payload, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    result = resp.json()

    if not result.get("ok"):
        raise RuntimeError(f"Telegram API rejected message: {result}")

    logger.info("Live Telegram alert delivered successfully!")
    return True


def main() -> int:
    try:
        start = time.perf_counter()
        rates = fetch_live_rates(RATE_API_URL)
        gbp_inr = rates["INR"]
        gbp_usd = rates["USD"]

        logger.info("Live Rates -> 1 GBP = Rs. %s | $%s USD", gbp_inr, gbp_usd)

        if gbp_inr >= ALERT_THRESHOLD_INR:
            status_tag = html.escape("TARGET HIT - HIGH RATE ALERT")
            msg = (
                f"<b>[{status_tag}]</b>\n"
                f"1 GBP = <b>Rs. {gbp_inr}</b> (Target: Rs. {ALERT_THRESHOLD_INR})\n"
                f"USD Cross Rate: ${gbp_usd}\n"
                f"<i>Status: Ready for invoice conversion!</i>"
            )
            send_telegram_alert(msg)
        else:
            logger.info(
                "Rate Rs. %s is below target Rs. %s. No alert triggered.",
                gbp_inr,
                ALERT_THRESHOLD_INR,
            )

        elapsed = time.perf_counter() - start
        logger.info("Audit cycle completed in %.2f sec.", elapsed)
        return 0

    except requests.exceptions.Timeout:
        logger.error("API Timeout: Server ne %d sec mein jawab nahi diya.", REQUEST_TIMEOUT)
    except requests.exceptions.RequestException as req_err:
        logger.error("Network/API Error: %s", req_err)
    except (ValueError, RuntimeError) as audit_err:
        logger.error("Security/Data Audit Failed: %s", audit_err)

    return 1


if __name__ == "__main__":
    sys.exit(main())