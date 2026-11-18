import html
import logging
import os
import re
import sys
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import List, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from fpdf import FPDF
from fpdf.errors import FPDFException

# --- Security & Pipeline Configuration ---
BASE_URL_TEMPLATE = "https://books.toscrape.com/catalogue/page-{page}.html"
ALLOWED_HOSTS = {"books.toscrape.com", "api.telegram.org"}
REPORT_PDF = Path("Market_Audit_Executive_Report.pdf")
REQUEST_TIMEOUT = 10
PAGES_TO_AUDIT = 2
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB Telegram upload safety cap

def load_local_env(env_path: Path = Path(".env")) -> None:
    """Loads secrets safely from local .env file without extra libraries."""
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        clean_key = key.strip()
        clean_val = value.strip().strip('"').strip("'")
        if clean_key and clean_key not in os.environ:
            os.environ[clean_key] = clean_val


load_local_env()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MasterAuditPipeline/1.0",
    "Accept-Language": "en-US,en;q=0.9",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("MasterFusion")

ProductItem = Tuple[str, Decimal, str]


def validate_url(url: str) -> None:
    """Enforces HTTPS and strict domain allowlisting against SSRF."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc not in ALLOWED_HOSTS:
        raise ValueError(f"Blocked unauthorized endpoint: {url}")


def sanitize_pdf_text(raw_text: str, max_len: int = 52) -> str:
    """Neutralizes formula prefixes, excess whitespace, and encoding crashes."""
    cleaned = " ".join(str(raw_text).strip().split())
    if cleaned.startswith(("=", "+", "-", "@")):
        cleaned = f"'{cleaned}"
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 3] + "..."
    return cleaned.encode("latin-1", errors="replace").decode("latin-1")


def parse_price(raw_price: str) -> Decimal:
    """Converts currency string into exact 2-decimal Decimal."""
    digits = re.sub(r"[^\d.]", "", raw_price)
    if not digits:
        raise ValueError(f"Unparseable price: {raw_price!r}")
    try:
        return Decimal(digits).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid Decimal price: {raw_price!r}") from exc


def step1_scrape_market(pages: int) -> List[ProductItem]:
    """Stage 1: Scrapes and sanitizes live product catalog data."""
    items: List[ProductItem] = []
    rating_map = {"One": "1/5", "Two": "2/5", "Three": "3/5", "Four": "4/5", "Five": "5/5"}

    with requests.Session() as session:
        session.headers.update(HEADERS)
        for page in range(1, pages + 1):
            url = BASE_URL_TEMPLATE.format(page=page)
            validate_url(url)
            logger.info("[Stage 1/3] Scanning live market page %d/%d...", page, pages)

            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            resp.encoding = "utf-8"

            soup = BeautifulSoup(resp.text, "html.parser")
            for card in soup.select("article.product_pod"):
                title_tag = card.select_one("h3 a")
                price_tag = card.select_one("p.price_color")
                rating_tag = card.select_one("p.star-rating")

                if not (title_tag and price_tag):
                    continue

                try:
                    raw_title = title_tag.get("title") or title_tag.get_text()
                    title = sanitize_pdf_text(raw_title)
                    price = parse_price(price_tag.get_text())

                    classes = rating_tag.get("class", []) if rating_tag else []
                    word = next((c for c in classes if c in rating_map), "N/A")
                    rating = rating_map.get(word, "N/A")

                    items.append((title, price, rating))
                except ValueError:
                    continue

            if page < pages:
                time.sleep(0.6)

    if not items:
        raise ValueError("Stage 1 failed: No valid market items extracted.")
    return items


def step2_build_executive_pdf(items: List[ProductItem], pdf_path: Path) -> dict:
    """Stage 2: Computes exact Decimal KPIs and builds an audited PDF report."""
    logger.info("[Stage 2/3] Generating audited executive PDF report...")

    total_count = len(items)
    total_value = sum((price for _, price, _ in items), Decimal("0.00"))
    avg_price = (total_value / Decimal(total_count)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    top_deals = sorted(items, key=lambda x: x[1])[:10]

    pdf = FPDF()
    pdf.add_page()

    # Title Banner
    pdf.set_font("helvetica", "B", 16)
    pdf.cell(0, 12, "AUTOMATED MARKET AUDIT REPORT", border=0, align="C")
    pdf.ln(12)

    pdf.set_font("helvetica", "", 10)
    stamp = datetime.now().strftime("%d-%b-%Y %H:%M")
    pdf.cell(100, 8, f"Items Audited: {total_count} | Avg Price: GBP {avg_price:,.2f}", border=0)
    pdf.cell(0, 8, f"Generated: {stamp}", border=0, align="R")
    pdf.ln(12)

    # Top 10 Lowest-Priced Deals Table
    pdf.set_font("helvetica", "B", 11)
    pdf.cell(0, 8, "Top 10 Best-Value Market Listings:", border=0)
    pdf.ln(9)

    pdf.set_fill_color(220, 230, 241)
    pdf.cell(115, 9, "Product Title", border=1, fill=True)
    pdf.cell(35, 9, "Rating", border=1, align="C", fill=True)
    pdf.cell(40, 9, "Price (GBP)", border=1, align="R", fill=True)
    pdf.ln()

    pdf.set_font("helvetica", "", 10)
    for title, price, rating in top_deals:
        pdf.cell(115, 8, title, border=1)
        pdf.cell(35, 8, rating, border=1, align="C")
        pdf.cell(40, 8, f"{price:,.2f}", border=1, align="R")
        pdf.ln()

    pdf.set_font("helvetica", "B", 10)
    pdf.cell(150, 9, "TOTAL CATALOG VALUE (ALL AUDITED ITEMS)", border=1, align="R")
    pdf.cell(40, 9, f"GBP {total_value:,.2f}", border=1, align="R")
    pdf.ln()

    pdf.output(str(pdf_path))
    return {
        "count": total_count,
        "avg_price": avg_price,
        "total_value": total_value,
        "best_title": top_deals[0][0],
        "best_price": top_deals[0][1],
    }


def step3_deliver_to_telegram(pdf_path: Path, summary: dict) -> None:
    """Stage 3: Sends summary alert and attaches the generated PDF via Telegram."""
    if not pdf_path.exists() or pdf_path.stat().st_size > MAX_UPLOAD_BYTES:
        raise ValueError("PDF report missing or exceeds safe upload limit.")

    caption = (
        f"<b>[MASTER PIPELINE REPORT READY]</b>\n"
        f"Audited Items: <b>{summary['count']}</b>\n"
        f"Average Price: <b>GBP {summary['avg_price']:,.2f}</b>\n"
        f"Best Deal: {html.escape(summary['best_title'])} (<b>GBP {summary['best_price']:,.2f}</b>)\n"
        f"<i>Full audited PDF attached below!</i>"
    )

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("[Stage 3/3] Telegram env vars missing -> Dry-Run complete. PDF saved at '%s'.", pdf_path)
        return

    doc_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    validate_url(doc_url)
    logger.info("[Stage 3/3] Dispatching PDF report directly to Telegram...")

    with open(pdf_path, "rb") as pdf_file:
        resp = requests.post(
            doc_url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption,
                "parse_mode": "HTML",
            },
            files={"document": (pdf_path.name, pdf_file, "application/pdf")},
            timeout=REQUEST_TIMEOUT,
        )
    resp.raise_for_status()
    if not resp.json().get("ok"):
        raise RuntimeError(f"Telegram rejected document upload: {resp.text}")

    logger.info("Master Fusion complete! PDF delivered straight to your Telegram!")


def main() -> int:
    try:
        start = time.perf_counter()
        scraped_items = step1_scrape_market(PAGES_TO_AUDIT)
        kpis = step2_build_executive_pdf(scraped_items, REPORT_PDF)
        step3_deliver_to_telegram(REPORT_PDF, kpis)
        elapsed = time.perf_counter() - start
        logger.info("Full End-to-End Pipeline executed in %.2f sec!", elapsed)
        return 0

    except requests.exceptions.RequestException as net_err:
        logger.error("Network/API Error: %s", net_err)
    except (ValueError, RuntimeError, FPDFException, OSError) as err:
        logger.error("Pipeline Audit Error: %s", err)

    return 1


if __name__ == "__main__":
    sys.exit(main())