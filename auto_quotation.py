import argparse
import logging
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import List, Tuple
from zipfile import BadZipFile

import openpyxl
from fpdf import FPDF
from fpdf.errors import FPDFException
from openpyxl.utils.exceptions import InvalidFileException

# --- Security & Operational Constants ---
MAX_EXCEL_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB limit
ALLOWED_EXTENSION = ".xlsx"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("QuotationAuditor")

LineItem = Tuple[str, Decimal, Decimal, Decimal]


def format_clean_number(value: Decimal) -> str:
    """Formats Decimal cleanly without scientific notation (fixes 4.5E+2 bug)."""
    text = f"{value:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def sanitize_pdf_text(raw_text: str, max_len: int = 60) -> str:
    """Sanitizes text against encoding crashes, formula injection, and overflow."""
    cleaned = " ".join(str(raw_text).strip().split())
    if cleaned.startswith(("=", "+", "-", "@")):
        cleaned = f"'{cleaned}"
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 3] + "..."
    return cleaned.encode("latin-1", errors="replace").decode("latin-1")


def validate_excel_path(file_path: Path) -> None:
    """Validates file existence, extension, and size safely."""
    resolved = file_path.resolve()
    if not resolved.exists() or not resolved.is_file():
        raise FileNotFoundError(f"Excel file nahi mili: '{file_path}'")

    if resolved.suffix.lower() != ALLOWED_EXTENSION:
        raise ValueError(f"Sirf '{ALLOWED_EXTENSION}' file allowed hai (mila: '{resolved.suffix}')")

    file_size = resolved.stat().st_size
    if file_size == 0:
        raise ValueError("Excel file bilkul khali hai (0 bytes).")
    if file_size > MAX_EXCEL_SIZE_BYTES:
        raise OverflowError(f"File {MAX_EXCEL_SIZE_BYTES // (1024 * 1024)}MB safety limit se badi hai.")


def parse_positive_decimal(value: object, field_name: str, row_num: int) -> Decimal:
    """Converts cell values into exact, finite, positive Decimals."""
    if value is None or isinstance(value, bool):
        raise ValueError(f"Row {row_num}: '{field_name}' missing ya invalid hai.")

    try:
        dec_val = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError(f"Row {row_num}: '{field_name}' number nahi hai ({value!r}).") from exc

    if not dec_val.is_finite() or dec_val <= Decimal("0"):
        raise ValueError(f"Row {row_num}: '{field_name}' 0 se bada hona chahiye (mila: {dec_val}).")

    return dec_val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def read_and_audit_excel(file_path: Path) -> List[LineItem]:
    """Reads and audits Excel rows with strict financial validation."""
    validate_excel_path(file_path)
    cleaned_items: List[LineItem] = []

    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    try:
        ws = wb.active
        if ws is None:
            raise ValueError("Workbook mein koi active sheet nahi hai.")

        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            padded = list(row) + [None] * (3 - len(row))
            raw_desc, raw_qty, raw_rate = padded[0], padded[1], padded[2]

            if all(cell is None or str(cell).strip() == "" for cell in (raw_desc, raw_qty, raw_rate)):
                continue

            if not raw_desc or not str(raw_desc).strip():
                logger.warning("Skipping Row %d: Work description khali hai.", row_idx)
                continue

            try:
                desc = sanitize_pdf_text(str(raw_desc))
                qty = parse_positive_decimal(raw_qty, "Quantity", row_idx)
                rate = parse_positive_decimal(raw_rate, "Rate", row_idx)
                line_total = (qty * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                cleaned_items.append((desc, qty, rate, line_total))
            except ValueError as err:
                logger.warning("Audit rejected -> %s", err)
                continue
    finally:
        wb.close()

    if not cleaned_items:
        raise ValueError("Audit ke baad koi valid billable row nahi bachi.")

    return cleaned_items


def generate_pdf(items: List[LineItem], output_path: Path, client_name: str) -> None:
    """Generates a clean, bordered PDF quotation."""
    safe_client = sanitize_pdf_text(client_name, max_len=40)
    pdf = FPDF()
    pdf.add_page()

    # Header
    pdf.set_font("helvetica", "B", 18)
    pdf.cell(0, 12, "WORK QUOTATION & ESTIMATE", border=0, align="C")
    pdf.ln(14)

    # Metadata
    pdf.set_font("helvetica", "", 11)
    today = datetime.now().strftime("%d-%b-%Y")
    pdf.cell(100, 8, f"Client Name: {safe_client}", border=0)
    pdf.cell(0, 8, f"Date: {today}", border=0, align="R")
    pdf.ln(12)

    # Table Header
    pdf.set_font("helvetica", "B", 11)
    pdf.set_fill_color(220, 230, 241)
    pdf.cell(85, 10, "Work Description", border=1, fill=True)
    pdf.cell(30, 10, "Qty", border=1, align="C", fill=True)
    pdf.cell(35, 10, "Rate (Rs.)", border=1, align="R", fill=True)
    pdf.cell(40, 10, "Amount (Rs.)", border=1, align="R", fill=True)
    pdf.ln()

    # Table Rows
    pdf.set_font("helvetica", "", 10)
    grand_total = Decimal("0.00")

    for desc, qty, rate, total in items:
        grand_total += total
        pdf.cell(85, 9, desc, border=1)
        pdf.cell(30, 9, format_clean_number(qty), border=1, align="C")
        pdf.cell(35, 9, f"{rate:,.2f}", border=1, align="R")
        pdf.cell(40, 9, f"{total:,.2f}", border=1, align="R")
        pdf.ln()

    # Grand Total
    pdf.set_font("helvetica", "B", 11)
    pdf.cell(150, 10, "GRAND TOTAL", border=1, align="R")
    pdf.cell(40, 10, f"Rs. {grand_total:,.2f}", border=1, align="R")
    pdf.ln()

    pdf.output(str(output_path))
    logger.info("Audited PDF ready -> '%s'", output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audited Excel-to-PDF Quotation Generator")
    parser.add_argument("-f", "--file", type=str, help="Input Excel file path (e.g. site_work.xlsx)")
    parser.add_argument("-c", "--client", type=str, help="Client ka naam (e.g. 'Mr. Verma')")
    parser.add_argument("-o", "--out", type=str, help="Output PDF file path (optional)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    # Agar terminal flag nahi diya toh user se interactively pooch lo
    excel_input = args.file or input("Excel file ka naam (Enter = site_work.xlsx): ").strip() or "site_work.xlsx"
    client_input = args.client or input("Client ka naam (Enter = Valued Client): ").strip() or "Valued Client"

    excel_path = Path(excel_input)
    if args.out:
        pdf_path = Path(args.out)
    else:
        safe_stem = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in client_input)
        pdf_path = Path(f"Quotation_{safe_stem}.pdf")

    try:
        audited_items = read_and_audit_excel(excel_path)
        generate_pdf(audited_items, pdf_path, client_name=client_input)
        return 0

    except PermissionError:
        logger.error("File locked hai! Pehle Excel/PDF window band karo aur phir chalao.")
    except FileNotFoundError as fnf_err:
        logger.error("%s", fnf_err)
    except (InvalidFileException, BadZipFile) as corrupt_err:
        logger.error("Corrupted Excel file: %s", corrupt_err)
    except (ValueError, OverflowError) as val_err:
        logger.error("Audit Failed: %s", val_err)
    except (FPDFException, OSError) as pdf_err:
        logger.error("PDF Error: %s", pdf_err)

    return 1


if __name__ == "__main__":
    sys.exit(main())