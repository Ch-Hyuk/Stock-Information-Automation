# Stock Calendar Agent

Python and GitHub Actions based automation for collecting stock calendar events and registering them in Google Calendar.

## What It Does

- Reads ticker and calendar settings from `config.json`
- Collects US ticker earnings and dividend data through `yfinance`
- Collects Korean stock disclosures through the free OpenDART API
- Supports manually curated events for Korean IPOs, earnings, dividends, or any event source you want to add later
- Creates all-day Google Calendar events
- Prevents duplicate events with a private Google Calendar event UID
- Runs locally or daily through GitHub Actions

## Setup

1. Create a Google Cloud project and enable Google Calendar API.
2. Create an OAuth Desktop App client and download it as `credentials.json`.
3. Install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

4. Run once locally to create `token.json`:

```powershell
python main.py --dry-run
python main.py
```

5. For GitHub Actions, create a private repository secret named `GOOGLE_TOKEN_JSON` and paste the full contents of `token.json`.
6. To collect Korean disclosures, request a free OpenDART API key and save it as a repository secret named `DART_API_KEY`.

## Configuration

Edit `config.json`:

```json
{
  "calendar_id": "primary",
  "timezone": "Asia/Seoul",
  "lookahead_days": 60,
  "us_tickers": ["AAPL", "MSFT", "NVDA", "TSLA"],
  "korea_dart": {
    "enabled": true,
    "recent_days": 7,
    "corp_cls": ["Y", "K"],
    "stock_codes": [],
    "include_keywords": [
      "영업(잠정)실적",
      "현금ㆍ현물배당",
      "주주총회",
      "증권신고",
      "투자설명서"
    ]
  },
  "manual_events": []
}
```

`korea_dart` uses OpenDART disclosure receipt dates as all-day calendar events. Leave `stock_codes` empty to monitor all KOSPI and KOSDAQ companies, or add six-digit Korean stock codes such as `"005930"` to limit the feed.

OpenDART API keys are free, but they should stay private. For local testing, set `DART_API_KEY` in your shell. For GitHub Actions, add it under repository Settings -> Secrets and variables -> Actions.

## Run

Preview without writing to Google Calendar:

```powershell
python main.py --dry-run
```

Register events:

```powershell
python main.py
```

## GitHub Actions

`.github/workflows/run_agent.yml` runs every day at 06:00 KST and can also be started manually from the GitHub Actions tab.
