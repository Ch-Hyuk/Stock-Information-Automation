# Stock Calendar Agent

Python and GitHub Actions based automation for collecting stock calendar events and registering them in Google Calendar.

## What It Does

- Reads ticker and calendar settings from `config.json`
- Collects US ticker earnings and dividend data through `yfinance`
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

## Configuration

Edit `config.json`:

```json
{
  "calendar_id": "primary",
  "timezone": "Asia/Seoul",
  "lookahead_days": 60,
  "us_tickers": ["AAPL", "MSFT", "NVDA", "TSLA"],
  "manual_events": []
}
```

Use `manual_events` for Korean market schedules until you connect a Korean market API or crawler.

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
