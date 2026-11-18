# 🛡️ Audited Python Automation & Alert Suite

A production-hardened Python automation toolkit built with a **Security Auditor mindset**—featuring strict input validation, exact financial arithmetic, formula-injection defense, and zero-secret environment management.

## 🚀 Core Modules

1. **`auto_quotation.py` — Audited Excel-to-PDF Billing Engine**
   - Converts client Excel workbooks into formatted PDF quotations via CLI flags (`--file`, `--client`) or interactive prompts.
   - Rejects corrupted rows, negative quantities, and oversized files before PDF generation.

2. **`market_scraper.py` — Multi-Page Sanitized Web Scraper**
   - Crawls paginated e-commerce listings using persistent HTTP sessions with polite rate limiting and strict domain allowlisting.
   - Exports formatted `.xlsx` reports with locked 2-decimal currency formatting.

3. **`alert_bot.py` — Live Currency & Threshold Telegram Bot**
   - Monitors live exchange rates via HTTPS APIs using exact `Decimal` comparisons and dispatches instant HTML alerts to Telegram.
   - Includes a safe `AUDIT DRY-RUN` fallback when credentials are absent.

4. **`master_pipeline.py` — End-to-End Market Audit Pipeline**
   - Fuses live scraping, `Decimal` KPI aggregation, executive PDF report generation, and automated Telegram document delivery into a single workflow.

## 🔒 Security & Engineering Standards

- **Financial Precision:** Uses Python's `decimal.Decimal` (`ROUND_HALF_UP`) across all billing and price aggregations to eliminate floating-point drift.
- **Injection & Encoding Defense:** Neutralizes Excel/PDF formula prefixes (`=`, `+`, `-`, `@`) and enforces safe `UTF-8` / `Latin-1` text sanitization.
- **SSRF & DoS Guardrails:** Enforces HTTPS scheme validation, strict host allowlisting, request timeouts, and file-size / pagination caps.
- **Zero Hardcoded Secrets:** Loads API tokens strictly from local `.env` / environment variables excluded via `.gitignore`.

## ⚙️ Quick Setup

```bash
pip install -r requirements.txt
python master_pipeline.py