from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote_plus
from xml.etree import ElementTree


SCOPES = ["https://www.googleapis.com/auth/calendar"]
DEFAULT_CONFIG = "config.json"
DEFAULT_TIMEZONE = "Asia/Seoul"
US_TICKER_NAMES = {
    "AAPL": "애플",
    "MSFT": "마이크로소프트",
    "NVDA": "엔비디아",
    "TSLA": "테슬라",
}


@dataclass(frozen=True)
class StockEvent:
    summary: str
    description: str
    start_date: dt.date
    event_type: str
    source: str
    symbol: str | None = None
    external_id: str | None = None

    @property
    def uid(self) -> str:
        symbol = self.symbol or "market"
        key = self.external_id or self.start_date.isoformat()
        return f"stock-calendar-agent:{self.source}:{self.event_type}:{symbol}:{key}"


@dataclass(frozen=True)
class WatchlistItem:
    market: str
    symbol: str
    name: str
    query: str


@dataclass(frozen=True)
class ReportEntry:
    title: str
    url: str
    source: str
    published_at: str = ""
    summary: str = ""


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
    config.setdefault("korea_dart", {})
    config.setdefault("watchlist", build_default_watchlist(config))
    config.setdefault("reporting", {})
    config.setdefault("manual_events", [])
    return config


def build_default_watchlist(config: dict[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for symbol in config.get("us_tickers", []):
        clean_symbol = str(symbol).strip().upper()
        if not clean_symbol:
            continue
        items.append(
            {
                "market": "US",
                "symbol": clean_symbol,
                "name": US_TICKER_NAMES.get(clean_symbol, clean_symbol),
                "query": f"{clean_symbol} stock earnings dividend",
            }
        )

    for symbol in config.get("korea_dart", {}).get("stock_codes", []):
        clean_symbol = str(symbol).strip().zfill(6)
        if not clean_symbol:
            continue
        items.append({"market": "KR", "symbol": clean_symbol, "name": clean_symbol, "query": clean_symbol})
    return items


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
        if len(clean) == 8 and clean.isdigit():
            try:
                return dt.date(int(clean[:4]), int(clean[4:6]), int(clean[6:8]))
            except ValueError:
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


def configure_yfinance_cache(yf: Any) -> None:
    cache_dir = Path(".cache") / "yfinance"
    cache_dir.mkdir(parents=True, exist_ok=True)
    yf.set_tz_cache_location(str(cache_dir))


def fetch_us_events(tickers: Iterable[str], lookahead_days: int) -> list[StockEvent]:
    try:
        import yfinance as yf
    except ImportError:
        print("yfinance is not installed; skipping US market events.")
        return []

    configure_yfinance_cache(yf)

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
            company_name = US_TICKER_NAMES.get(symbol, symbol)
            events.append(
                StockEvent(
                    summary=f"[해외주식] {company_name}({symbol}) 실적 발표",
                    description=f"{company_name}({symbol})의 실적 발표 예정일입니다.\n출처: Yahoo Finance",
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
                summary=f"[해외주식] {US_TICKER_NAMES.get(symbol, symbol)}({symbol}) 배당",
                description=(
                    f"{US_TICKER_NAMES.get(symbol, symbol)}({symbol})의 배당 일정입니다.\n"
                    f"배당 금액: {amount}\n"
                    "출처: Yahoo Finance"
                ),
                start_date=dividend_date,
                event_type="dividend",
                source="yfinance",
                symbol=symbol,
            )
        )
    return events


def fetch_korea_dart_events(config: dict[str, Any]) -> list[StockEvent]:
    dart_config = config.get("korea_dart", {})
    if not dart_config.get("enabled", False):
        return []

    api_key = os.environ.get("DART_API_KEY") or dart_config.get("api_key")
    if not api_key:
        print("DART_API_KEY is not set; skipping Korean DART events.")
        return []

    try:
        import requests
    except ImportError:
        print("requests is not installed; skipping Korean DART events.")
        return []

    recent_days = int(dart_config.get("recent_days", 7))
    page_count = int(dart_config.get("page_count", 100))
    include_keywords = dart_config.get("include_keywords", [])
    stock_codes = {str(code).zfill(6) for code in dart_config.get("stock_codes", [])}
    corp_cls_values = dart_config.get("corp_cls", ["Y", "K"])

    today = dt.date.today()
    begin = today - dt.timedelta(days=max(recent_days - 1, 0))
    events: list[StockEvent] = []

    for corp_cls in corp_cls_values:
        params = {
            "crtfc_key": api_key,
            "bgn_de": begin.strftime("%Y%m%d"),
            "end_de": today.strftime("%Y%m%d"),
            "corp_cls": corp_cls,
            "sort": "date",
            "sort_mth": "desc",
            "page_no": 1,
            "page_count": page_count,
        }

        try:
            response = requests.get("https://opendart.fss.or.kr/api/list.json", params=params, timeout=20)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            print(f"Could not fetch DART disclosures for corp_cls={corp_cls}: {exc}")
            continue

        status = payload.get("status")
        if status == "013":
            continue
        if status != "000":
            print(f"DART API returned status={status}: {payload.get('message')}")
            continue

        for item in payload.get("list", []):
            stock_code = item.get("stock_code", "")
            report_name = item.get("report_nm", "")
            if stock_codes and stock_code not in stock_codes:
                continue
            if include_keywords and not any(keyword in report_name for keyword in include_keywords):
                continue

            received_date = parse_date(item.get("rcept_dt"))
            if not received_date:
                continue

            corp_name = item.get("corp_name", "Unknown")
            receipt_no = item.get("rcept_no", "")
            dart_url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt_no}" if receipt_no else ""
            description = "\n".join(
                line
                for line in [
                    f"공시명: {report_name}",
                    f"회사명: {corp_name}",
                    f"종목코드: {stock_code}" if stock_code else "",
                    f"DART 접수번호: {receipt_no}" if receipt_no else "",
                    dart_url,
                ]
                if line
            )

            events.append(
                StockEvent(
                    summary=f"[국내공시] {corp_name}: {report_name}",
                    description=description,
                    start_date=received_date,
                    event_type=classify_dart_event(report_name),
                    source="opendart",
                    symbol=stock_code or item.get("corp_code"),
                    external_id=receipt_no,
                )
            )

    return events


def classify_dart_event(report_name: str) -> str:
    if "배당" in report_name:
        return "korea-dividend-disclosure"
    if "영업(잠정)실적" in report_name or "잠정실적" in report_name:
        return "korea-earnings-disclosure"
    if "주주총회" in report_name:
        return "korea-shareholder-meeting-disclosure"
    if "증권신고" in report_name or "투자설명서" in report_name:
        return "korea-offering-disclosure"
    return "korea-disclosure"


def normalize_watchlist(config: dict[str, Any]) -> list[WatchlistItem]:
    items: list[WatchlistItem] = []
    for item in config.get("watchlist", []):
        market = str(item.get("market", "")).strip().upper()
        symbol = str(item.get("symbol", "")).strip().upper()
        name = str(item.get("name", symbol)).strip() or symbol
        query = str(item.get("query", f"{name} {symbol}")).strip()
        if not market or not symbol:
            continue
        if market == "KR":
            symbol = symbol.zfill(6)
        items.append(WatchlistItem(market=market, symbol=symbol, name=name, query=query))
    return items


def fetch_yahoo_news(symbol: str, limit: int) -> list[ReportEntry]:
    try:
        import yfinance as yf
    except ImportError:
        return []

    configure_yfinance_cache(yf)

    try:
        ticker = yf.Ticker(symbol)
        raw_news = ticker.news or []
    except Exception as exc:
        print(f"Could not fetch Yahoo news for {symbol}: {exc}")
        return []

    entries: list[ReportEntry] = []
    for item in raw_news[:limit]:
        content = item.get("content", item) if isinstance(item, dict) else {}
        title = content.get("title") or item.get("title", "")
        url = content.get("canonicalUrl", {}).get("url") or content.get("clickThroughUrl", {}).get("url") or item.get("link", "")
        provider = content.get("provider", {}).get("displayName") or item.get("publisher", "Yahoo Finance")
        published = content.get("pubDate") or item.get("providerPublishTime", "")
        summary = content.get("summary") or item.get("summary", "")
        if isinstance(published, int):
            published = dt.datetime.fromtimestamp(published, tz=dt.timezone.utc).date().isoformat()
        entries.append(
            ReportEntry(
                title=clean_text(title),
                url=url,
                source=clean_text(provider),
                published_at=str(published),
                summary=clean_text(summary),
            )
        )
    return entries


def fetch_google_news_rss(query: str, limit: int) -> list[ReportEntry]:
    try:
        import requests
    except ImportError:
        return []

    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=ko&gl=KR&ceid=KR:ko"
    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
    except Exception as exc:
        print(f"Could not fetch Google News RSS for {query}: {exc}")
        return []

    entries: list[ReportEntry] = []
    for item in root.findall("./channel/item")[:limit]:
        title = item.findtext("title", default="")
        link = item.findtext("link", default="")
        published = item.findtext("pubDate", default="")
        source = item.findtext("source", default="Google News")
        entries.append(
            ReportEntry(
                title=clean_text(title),
                url=link,
                source=clean_text(source),
                published_at=clean_text(published),
            )
        )
    return entries


def fetch_dart_report_entries(config: dict[str, Any], stock_code: str, limit: int) -> list[ReportEntry]:
    dart_config = config.get("korea_dart", {})
    api_key = os.environ.get("DART_API_KEY") or dart_config.get("api_key")
    if not api_key:
        return []

    try:
        import requests
    except ImportError:
        return []

    reporting = config.get("reporting", {})
    recent_days = int(reporting.get("dart_recent_days", dart_config.get("recent_days", 14)))
    page_count = int(reporting.get("dart_page_count", 100))
    include_keywords = reporting.get("dart_keywords", dart_config.get("include_keywords", []))
    today = dt.date.today()
    begin = today - dt.timedelta(days=max(recent_days - 1, 0))
    entries: list[ReportEntry] = []

    for corp_cls in dart_config.get("corp_cls", ["Y", "K"]):
        params = {
            "crtfc_key": api_key,
            "bgn_de": begin.strftime("%Y%m%d"),
            "end_de": today.strftime("%Y%m%d"),
            "corp_cls": corp_cls,
            "sort": "date",
            "sort_mth": "desc",
            "page_no": 1,
            "page_count": page_count,
        }
        try:
            response = requests.get("https://opendart.fss.or.kr/api/list.json", params=params, timeout=20)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            print(f"Could not fetch DART report entries for {stock_code}: {exc}")
            continue

        if payload.get("status") != "000":
            continue

        for item in payload.get("list", []):
            if item.get("stock_code") != stock_code:
                continue
            report_name = item.get("report_nm", "")
            if include_keywords and not any(keyword in report_name for keyword in include_keywords):
                continue
            receipt_no = item.get("rcept_no", "")
            url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt_no}" if receipt_no else ""
            entries.append(
                ReportEntry(
                    title=clean_text(report_name),
                    url=url,
                    source="OpenDART",
                    published_at=str(parse_date(item.get("rcept_dt")) or item.get("rcept_dt", "")),
                    summary=f"회사명: {item.get('corp_name', '')} / 종목코드: {stock_code}",
                )
            )

    return entries[:limit]


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def markdown_link(entry: ReportEntry) -> str:
    if entry.url:
        return f"[{entry.title}]({entry.url})"
    return entry.title


def infer_report_notes(entries: list[ReportEntry]) -> list[str]:
    notes: list[str] = []
    keyword_map = {
        "실적": "실적 관련 내용이 포함되어 있습니다. 매출, 영업이익, 가이던스 변화를 확인하세요.",
        "배당": "배당 관련 내용이 포함되어 있습니다. 기준일, 지급일, 배당 규모를 확인하세요.",
        "주주총회": "주주총회 관련 내용이 포함되어 있습니다. 안건과 기준일을 확인하세요.",
        "증권신고": "자금조달 또는 공모 관련 내용이 포함되어 있습니다. 발행 규모와 희석 가능성을 확인하세요.",
        "투자설명서": "공모 또는 증권 발행 관련 세부 내용이 포함되어 있습니다.",
        "earnings": "실적 발표 관련 기사일 수 있습니다. 시장 예상치와 다음 분기 전망을 확인하세요.",
        "dividend": "배당 관련 기사일 수 있습니다. 배당락일과 배당 수익률을 확인하세요.",
        "guidance": "가이던스 관련 기사일 수 있습니다. 향후 실적 전망 변화에 주목하세요.",
    }
    joined = " ".join(entry.title.lower() for entry in entries)
    for keyword, note in keyword_map.items():
        if keyword.lower() in joined and note not in notes:
            notes.append(note)
    if not notes and entries:
        notes.append("최근 기사와 공시 제목 기준으로 주요 이슈를 확인하세요.")
    return notes


def generate_report(config: dict[str, Any]) -> Path | None:
    reporting = config.get("reporting", {})
    if not reporting.get("enabled", True):
        print("Reporting is disabled.")
        return None

    output_dir = Path(reporting.get("output_dir", "reports"))
    output_dir.mkdir(parents=True, exist_ok=True)
    report_date = dt.date.today()
    report_path = output_dir / f"{report_date.isoformat()}.md"
    news_limit = int(reporting.get("news_limit", 5))
    disclosure_limit = int(reporting.get("disclosure_limit", 5))
    watchlist = normalize_watchlist(config)

    lines = [
        f"# {report_date.isoformat()} 주식 이벤트 리포트",
        "",
        "이 리포트는 관심 종목의 최근 공시와 뉴스 링크를 자동으로 모은 것입니다.",
        "투자 판단을 대신하지 않으며, 중요한 내용은 원문을 확인하세요.",
        "",
    ]

    if not watchlist:
        lines.extend(["관심 종목이 설정되어 있지 않습니다.", ""])
    for item in watchlist:
        lines.extend([f"## {item.name}({item.symbol})", ""])

        if item.market == "KR":
            disclosures = fetch_dart_report_entries(config, item.symbol, disclosure_limit)
            news = fetch_google_news_rss(item.query or f"{item.name} {item.symbol}", news_limit)
        else:
            disclosures = []
            news = fetch_yahoo_news(item.symbol, news_limit)
            if not news:
                news = fetch_google_news_rss(item.query or f"{item.name} {item.symbol} stock", news_limit)

        lines.append("### 확인 포인트")
        notes = infer_report_notes(disclosures + news)
        if notes:
            lines.extend(f"- {note}" for note in notes)
        else:
            lines.append("- 새로 수집된 공시나 뉴스가 없습니다.")
        lines.append("")

        lines.append("### 공시")
        if disclosures:
            for entry in disclosures:
                date_text = f" ({entry.published_at})" if entry.published_at else ""
                lines.append(f"- {markdown_link(entry)}{date_text}")
                if entry.summary:
                    lines.append(f"  - {entry.summary}")
        else:
            lines.append("- 수집된 공시가 없습니다.")
        lines.append("")

        lines.append("### 뉴스")
        if news:
            for entry in news:
                meta = " / ".join(part for part in [entry.source, entry.published_at] if part)
                meta_text = f" ({meta})" if meta else ""
                lines.append(f"- {markdown_link(entry)}{meta_text}")
                if entry.summary:
                    lines.append(f"  - {entry.summary}")
        else:
            lines.append("- 수집된 뉴스가 없습니다.")
        lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report generated: {report_path}")
    return report_path


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
    events.extend(fetch_korea_dart_events(config))
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


def get_existing_events_by_uid(service: Any, calendar_id: str, events: list[StockEvent]) -> dict[str, dict[str, Any]]:
    event_dates = [event.start_date for event in events]
    if not event_dates:
        return {}

    time_min = dt.datetime.combine(min(event_dates) - dt.timedelta(days=1), dt.time.min, tzinfo=dt.timezone.utc)
    time_max = dt.datetime.combine(max(event_dates) + dt.timedelta(days=2), dt.time.min, tzinfo=dt.timezone.utc)
    existing: dict[str, dict[str, Any]] = {}
    page_token = None

    while True:
        response = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=time_min.isoformat().replace("+00:00", "Z"),
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
                existing[uid] = item
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return existing


def event_needs_update(existing_event: dict[str, Any], new_event: dict[str, Any]) -> bool:
    return (
        existing_event.get("summary") != new_event.get("summary")
        or existing_event.get("description") != new_event.get("description")
        or existing_event.get("start", {}).get("date") != new_event.get("start", {}).get("date")
        or existing_event.get("end", {}).get("date") != new_event.get("end", {}).get("date")
    )


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
    existing_events = get_existing_events_by_uid(service, calendar_id, events)

    created_count = 0
    updated_count = 0
    for event in events:
        event_body = to_google_event(event, timezone)
        existing_event = existing_events.get(event.uid)
        if existing_event:
            if event_needs_update(existing_event, event_body):
                updated = (
                    service.events()
                    .update(calendarId=calendar_id, eventId=existing_event["id"], body={**existing_event, **event_body})
                    .execute()
                )
                updated_count += 1
                print(f"Updated event: {event.summary} - {updated.get('htmlLink')}")
            else:
                print(f"Skipping existing event: {event.summary}")
            continue

        created = (
            service.events()
            .insert(calendarId=calendar_id, body=event_body)
            .execute()
        )
        created_count += 1
        print(f"Created event: {event.summary} - {created.get('htmlLink')}")

    print(f"Done. Created {created_count} event(s), updated {updated_count} event(s).")
    return created_count


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect stock events and add them to Google Calendar.")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="Path to config JSON file.")
    parser.add_argument("--dry-run", action="store_true", help="Print events without writing to Google Calendar.")
    parser.add_argument("--skip-calendar", action="store_true", help="Generate reports without writing calendar events.")
    parser.add_argument("--skip-report", action="store_true", help="Update calendar events without generating a report.")
    parser.add_argument("--report-only", action="store_true", help="Only generate the daily stock report.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config)
    if not args.skip_calendar and not args.report_only:
        register_to_calendar(config, dry_run=args.dry_run)
    if not args.skip_report or args.report_only:
        generate_report(config)


if __name__ == "__main__":
    main()
