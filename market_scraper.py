import argparse
import logging
import re
import sys
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import List, Tuple
from urllib.parse import urlparse

import openpyxl
from openpyxl.styles import Font
import requests
from bs4 import BeautifulSoup

# --- Security & Scraper Configuration ---
BASE_URL_TEMPLATE = "https://books.toscrape.com/catalogue/page-{page}.html"
ALLOWED_DOMAIN = "books.toscrape.com"
OUTPUT_EXCEL = Path("scraped_market_data.xlsx")
REQUEST_TIMEOUT = 10
MAX_PAGES_LIMIT = 15      # Auditor DoS safety cap
POLITE_DELAY_SEC = 0.8    # Rate limiting between requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MarketAuditBot/2.0",
    "Accept-Language": "en-US,en;q=0.9",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ScraperAuditor")

ProductRecord = Tuple[int, str, Decimal, str, str]


def validate_target_url(url: str) -> None:
    """Blocks SSRF or untrusted domain redirects."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != ALLOWED_DOMAIN:
        raise ValueError(f"Blocked untrusted URL target: {url}")


def sanitize_excel_cell(raw_text: str, max_len: int = 120) -> str:
    """Prevents Excel Formula Injection (=, +, -, @) and excess whitespace."""
    cleaned = " ".join(str(raw_text).strip().split())
    if cleaned.startswith(("=", "+", "-", "@")):
        cleaned = f"'{cleaned}"
    return cleaned[:max_len]


def parse_price(raw_price: str) -> Decimal:
    """Parses currency string into exact 2-decimal Decimal."""
    numeric_only = re.sub(r"[^\d.]", "", raw_price)
    if not numeric_only:
        raise ValueError(f"Price parse failed: {raw_price!r}")
    try:
        return Decimal(numeric_only).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise ValueError(f"Invalid price format: {raw_price!r}") from exc


def scrape_multiple_pages(total_pages: int) -> List[ProductRecord]:
    """Crawls multiple catalog pages safely using a persistent HTTP session."""
    if not (1 <= total_pages <= MAX_PAGES_LIMIT):
        raise ValueError(f"Pages 1 se {MAX_PAGES_LIMIT} ke beech hi allowed hain (mila: {total_pages}).")

    all_records: List[ProductRecord] = []
    rating_map = {"One": "1/5", "Two": "2/5", "Three": "3/5", "Four": "4/5", "Five": "5/5"}

    with requests.Session() as session:
        session.headers.update(HEADERS)

        for page_num in range(1, total_pages + 1):
            url = BASE_URL_TEMPLATE.format(page=page_num)
            validate_target_url(url)
            logger.info("Scraping Page %d/%d -> %s", page_num, total_pages, url)

            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 404:
                logger.warning("Page %d nahi mila (404). Crawl yahin rok rahe hain.", page_num)
                break
            resp.raise_for_status()
            resp.encoding = "utf-8"

            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.select("article.product_pod")

            for idx, card in enumerate(cards, start=1):
                try:
                    title_tag = card.select_one("h3 a")
                    price_tag = card.select_one("p.price_color")
                    stock_tag = card.select_one("p.instock.availability")
                    rating_tag = card.select_one("p.star-rating")

                    if not (title_tag and price_tag and stock_tag):
                        continue

                    raw_title = title_tag.get("title") or title_tag.get_text()
                    title = sanitize_excel_cell(raw_title)
                    price = parse_price(price_tag.get_text())
                    stock = sanitize_excel_cell(stock_tag.get_text())

                    rating_classes = rating_tag.get("class", []) if rating_tag else []
                    rating_word = next((c for c in rating_classes if c in rating_map), "N/A")
                    rating = rating_map.get(rating_word, "N/A")

                    all_records.append((page_num, title, price, rating, stock))
                except ValueError as err:
                    logger.warning("Page %d Card #%d rejected -> %s", page_num, idx, err)

            # Polite delay before next page request
            if page_num < total_pages:
                time.sleep(POLITE_DELAY_SEC)

    if not all_records:
        raise ValueError("Koi bhi valid record extract nahi ho paya.")

    return all_records


def save_to_excel(records: List[ProductRecord], output_path: Path) -> None:
    """Exports multi-page scraped data with strict currency formatting."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Scraped_Products"

    headers = ["Page", "Product Title", "Price (GBP)", "Customer Rating", "Availability"]
    ws.append(headers)

    for cell in ws[1]:
        cell.font = Font(bold=True)

    for row_idx, (page, title, price, rating, stock) in enumerate(records, start=2):
        ws.append([page, title, float(price), rating, stock])
        # Lock 2-decimal currency display (fixes 50.1 -> 50.10)
        ws.cell(row=row_idx, column=3).number_format = "0.00"

    ws.column_dimensions["A"].width = 8
    ws.column_dimensions["B"].width = 48
    ws.column_dimensions["C"].width = 15
    ws.column_dimensions["D"].width = 18
    ws.column_dimensions["E"].width = 18

    wb.save(output_path)
    logger.info("Total %d records saved -> '%s'", len(records), output_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audited Multi-Page Web Scraper")
    parser.add_argument("-p", "--pages", type=int, default=3, help="Kitne pages scrape karne hain (1-15)")
    args = parser.parse_args()

    try:
        start_time = time.perf_counter()
        data = scrape_multiple_pages(args.pages)
        save_to_excel(data, OUTPUT_EXCEL)
        elapsed = time.perf_counter() - start_time
        logger.info("Done! %d products extracted in %.2f sec.", len(data), elapsed)
        return 0

    except requests.exceptions.Timeout:
        logger.error("Network Timeout: Server ne %d sec mein jawab nahi diya.", REQUEST_TIMEOUT)
    except requests.exceptions.RequestException as req_err:
        logger.error("Network/HTTP Error: %s", req_err)
    except PermissionError:
        logger.error("File locked hai! Pehle Excel tab band karo phir chalao.")
    except ValueError as val_err:
        logger.error("Audit Error: %s", val_err)

    return 1


if __name__ == "__main__":
    sys.exit(main())