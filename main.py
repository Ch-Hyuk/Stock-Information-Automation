from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SCOPES = ["https://www.googleapis.com/auth/calendar"]
DEFAULT_CONFIG = "config.json"
DEFAULT_TIMEZONE = "Asia/Seoul"


@dataclass(frozen=True)
class StockEvent:
    summary: str
    description: str
    start_date: dt.date
    event_type: str
    source: str
    symbol: str | None = None

    @property
    def uid(self) -> str:
        symbol = self.symbol or "market"
        return f"stock-calendar-agent:{self.source}:{self.event_type}:{symbol}:{self.start_date.isoformat()}"


def load_config(path: str) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)

    config.setdefault("calendar_id", "primary")
    config.setdefault("timezone", DEFAULT_TIMEZONE)
    config.setdefault("lookahead_days", 60)
    config.setdefault("us_tickers", [])
    config.setdefault("manual_events", [])
    return config


def parse_date(value: Any) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        clean = value.strip()
        if not clean:
            return None
        try:
            return dt.date.fromisoformat(clean[:10])
        except ValueError:
            return None
    return None


def get_calendar_service(token_path: str = "token.json"):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    token_json = os.environ.get("GOOGLE_TOKEN_JSON")
    token_file = Path(token_path)
    if token_json and not token_file.exists():
        token_file.write_text(token_json, encoding="utf-8")

    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json(), encoding="utf-8")

    return build("calendar", "v3", credentials=creds)


def fetch_us_events(tickers: Iterable[str], lookahead_days: int) -> list[StockEvent]:
    try:
        import yfinance as yf
    except ImportError:
        print("yfinance is not installed; skipping US market events.")
        return []

    today = dt.date.today()
    until = today + dt.timedelta(days=lookahead_days)
    events: list[StockEvent] = []

    for symbol in tickers:
        symbol = symbol.strip().upper()
        if not symbol:
            continue

        try:
            ticker = yf.Ticker(symbol)
            calendar = ticker.calendar
        except Exception as exc:
            print(f"Could not fetch {symbol} calendar: {exc}")
            continue

        earnings_date = extract_earnings_date(calendar)
        if earnings_date and today <= earnings_date <= until:
            events.append(
                StockEvent(
                    summary=f"{symbol} earnings",
                    description=f"Earnings date collected from Yahoo Finance for {symbol}.",
                    start_date=earnings_date,
                    event_type="earnings",
                    source="yfinance",
                    symbol=symbol,
                )
            )

        events.extend(fetch_recent_dividend_events(ticker, symbol, today, until))

    return events


def extract_earnings_date(calendar: Any) -> dt.date | None:
    if calendar is None:
        return None

    if isinstance(calendar, dict):
        candidates = calendar.get("Earnings Date") or calendar.get("EarningsDate")
        if isinstance(candidates, (list, tuple)) and candidates:
            return parse_date(candidates[0])
        return parse_date(candidates)

    try:
        if "Earnings Date" in calendar.index:
            value = calendar.loc["Earnings Date"][0]
            return parse_date(value)
    except Exception:
        return None

    return None


def fetch_recent_dividend_events(ticker: Any, symbol: str, today: dt.date, until: dt.date) -> list[StockEvent]:
    try:
        dividends = ticker.dividends
    except Exception as exc:
        print(f"Could not fetch {symbol} dividends: {exc}")
        return []

    if dividends is None or getattr(dividends, "empty", True):
        return []

    events: list[StockEvent] = []
    for index, amount in dividends.tail(8).items():
        dividend_date = parse_date(index)
        if not dividend_date or not (today <= dividend_date <= until):
            continue
        events.append(
            StockEvent(
                summary=f"{symbol} dividend",
                description=f"Dividend event collected from Yahoo Finance. Amount: {amount}",
                start_date=dividend_date,
                event_type="dividend",
                source="yfinance",
                symbol=symbol,
            )
        )
    return events


def fetch_manual_events(config: dict[str, Any]) -> list[StockEvent]:
    events: list[StockEvent] = []
    for item in config.get("manual_events", []):
        start_date = parse_date(item.get("date"))
        if not start_date:
            print(f"Skipping manual event with invalid date: {item}")
            continue
        events.append(
            StockEvent(
                summary=item["summary"],
                description=item.get("description", ""),
                start_date=start_date,
                event_type=item.get("type", "manual"),
                source="manual",
                symbol=item.get("symbol"),
            )
        )
    return events


def fetch_stock_events(config: dict[str, Any]) -> list[StockEvent]:
    events = []
    events.extend(fetch_us_events(config["us_tickers"], int(config["lookahead_days"])))
    events.extend(fetch_manual_events(config))
    return dedupe_events(events)


def dedupe_events(events: Iterable[StockEvent]) -> list[StockEvent]:
    seen: set[str] = set()
    unique: list[StockEvent] = []
    for event in sorted(events, key=lambda item: (item.start_date, item.summary)):
        if event.uid in seen:
            continue
        seen.add(event.uid)
        unique.append(event)
    return unique


def get_existing_event_uids(service: Any, calendar_id: str, lookahead_days: int) -> set[str]:
    now = dt.datetime.now(dt.timezone.utc)
    time_max = now + dt.timedelta(days=lookahead_days)
    existing: set[str] = set()
    page_token = None

    while True:
        response = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=now.isoformat().replace("+00:00", "Z"),
                timeMax=time_max.isoformat().replace("+00:00", "Z"),
                singleEvents=True,
                orderBy="startTime",
                pageToken=page_token,
                maxResults=250,
            )
            .execute()
        )
        for item in response.get("items", []):
            private_props = item.get("extendedProperties", {}).get("private", {})
            uid = private_props.get("stockCalendarAgentUid")
            if uid:
                existing.add(uid)
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return existing


def to_google_event(event: StockEvent, timezone: str) -> dict[str, Any]:
    end_date = event.start_date + dt.timedelta(days=1)
    return {
        "summary": event.summary,
        "description": event.description,
        "start": {"date": event.start_date.isoformat(), "timeZone": timezone},
        "end": {"date": end_date.isoformat(), "timeZone": timezone},
        "extendedProperties": {"private": {"stockCalendarAgentUid": event.uid}},
    }


def register_to_calendar(config: dict[str, Any], dry_run: bool = False) -> int:
    events = fetch_stock_events(config)
    if not events:
        print("No stock events found.")
        return 0

    if dry_run:
        for event in events:
            print(f"[DRY RUN] {event.start_date.isoformat()} - {event.summary}")
        return 0

    service = get_calendar_service()
    calendar_id = config["calendar_id"]
    timezone = config["timezone"]
    existing_uids = get_existing_event_uids(service, calendar_id, int(config["lookahead_days"]))

    created_count = 0
    for event in events:
        if event.uid in existing_uids:
            print(f"Skipping existing event: {event.summary}")
            continue

        created = (
            service.events()
            .insert(calendarId=calendar_id, body=to_google_event(event, timezone))
            .execute()
        )
        created_count += 1
        print(f"Created event: {event.summary} - {created.get('htmlLink')}")

    print(f"Done. Created {created_count} event(s).")
    return created_count


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect stock events and add them to Google Calendar.")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="Path to config JSON file.")
    parser.add_argument("--dry-run", action="store_true", help="Print events without writing to Google Calendar.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config)
    register_to_calendar(config, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
