from __future__ import annotations

import argparse
import datetime as dt
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
import html
import json
import os
import re
import smtplib
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
    "AMD": "AMD",
    "AVGO": "브로드컴",
    "QCOM": "퀄컴",
    "MU": "마이크론",
    "INTC": "인텔",
    "TSM": "TSMC",
    "ASML": "ASML",
    "AMAT": "어플라이드 머티어리얼즈",
    "LRCX": "램리서치",
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
class MarketIndex:
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
    translated_title: str = ""


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
    config.setdefault("market_overview", {})
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


def normalize_market_indexes(config: dict[str, Any]) -> list[MarketIndex]:
    market_config = config.get("market_overview", {})
    if not market_config.get("enabled", True):
        return []

    indexes: list[MarketIndex] = []
    for item in market_config.get("indexes", []):
        symbol = str(item.get("symbol", "")).strip()
        name = str(item.get("name", symbol)).strip() or symbol
        query = str(item.get("query", f"{name} stock market")).strip()
        if not symbol:
            continue
        indexes.append(MarketIndex(symbol=symbol, name=name, query=query))
    return indexes


def fetch_market_snapshot(index: MarketIndex) -> ReportEntry:
    try:
        import yfinance as yf
    except ImportError:
        return ReportEntry(title=f"{index.name} 지수 정보를 수집하지 못했습니다.", url="", source="Yahoo Finance")

    configure_yfinance_cache(yf)

    try:
        ticker = yf.Ticker(index.symbol)
        history = ticker.history(period="5d")
    except Exception as exc:
        print(f"Could not fetch market snapshot for {index.symbol}: {exc}")
        return ReportEntry(title=f"{index.name} 지수 정보를 수집하지 못했습니다.", url="", source="Yahoo Finance")

    if history is None or getattr(history, "empty", True) or "Close" not in history:
        return ReportEntry(title=f"{index.name} 지수 데이터가 없습니다.", url="", source="Yahoo Finance")

    closes = history["Close"].dropna()
    if closes.empty:
        return ReportEntry(title=f"{index.name} 지수 종가 데이터가 없습니다.", url="", source="Yahoo Finance")

    last_close = float(closes.iloc[-1])
    previous_close = float(closes.iloc[-2]) if len(closes) >= 2 else last_close
    change = last_close - previous_close
    change_pct = (change / previous_close * 100) if previous_close else 0.0
    direction = "상승" if change > 0 else "하락" if change < 0 else "보합"
    latest_date = parse_date(closes.index[-1])
    title = f"{index.name}: {last_close:,.2f} ({change:+,.2f}, {change_pct:+.2f}%) {direction}"
    return ReportEntry(
        title=title,
        url=f"https://finance.yahoo.com/quote/{quote_plus(index.symbol)}",
        source="Yahoo Finance",
        published_at=(latest_date or dt.date.today()).isoformat(),
        summary=f"{index.name}의 최근 종가 기준 지수 동향입니다.",
    )


def fetch_market_overview(config: dict[str, Any]) -> list[tuple[MarketIndex, ReportEntry, list[ReportEntry]]]:
    market_config = config.get("market_overview", {})
    news_limit = int(market_config.get("news_limit", 3))
    overview: list[tuple[MarketIndex, ReportEntry, list[ReportEntry]]] = []

    for index in normalize_market_indexes(config):
        snapshot = fetch_market_snapshot(index)
        news = sort_report_entries(fetch_google_news_rss(index.query, news_limit))[:news_limit]
        overview.append((index, snapshot, news))
    return overview


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


def parse_report_datetime(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time.min, tzinfo=dt.timezone.utc)
    if isinstance(value, int):
        return dt.datetime.fromtimestamp(value, tz=dt.timezone.utc)

    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        if len(text) == 8:
            parsed_date = parse_date(text)
            if parsed_date:
                return dt.datetime.combine(parsed_date, dt.time.min, tzinfo=dt.timezone.utc)
        try:
            return dt.datetime.fromtimestamp(int(text), tz=dt.timezone.utc)
        except (OverflowError, ValueError):
            return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def sort_report_entries(entries: list[ReportEntry]) -> list[ReportEntry]:
    return sorted(
        entries,
        key=lambda entry: parse_report_datetime(entry.published_at) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
        reverse=True,
    )


def is_likely_english(text: str) -> bool:
    if re.search(r"[가-힣]", text):
        return False
    letters = re.findall(r"[A-Za-z]", text)
    return len(letters) >= 12


def load_translation_cache() -> dict[str, str]:
    cache_path = Path(".cache") / "translations.json"
    if not cache_path.exists():
        return {}
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_translation_cache(cache: dict[str, str]) -> None:
    cache_path = Path(".cache") / "translations.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def translate_to_korean(text: str, cache: dict[str, str]) -> str:
    clean = clean_text(text)
    if not clean or not is_likely_english(clean):
        return clean
    if clean in cache:
        return cache[clean]

    try:
        import requests

        response = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "auto", "tl": "ko", "dt": "t", "q": clean},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        translated = "".join(part[0] for part in payload[0] if part and part[0])
        translated = clean_text(translated)
    except Exception as exc:
        print(f"Could not translate title: {exc}")
        translated = clean

    cache[clean] = translated or clean
    return cache[clean]


def title_for_report(entry: ReportEntry, translation_cache: dict[str, str]) -> str:
    if entry.translated_title:
        return entry.translated_title
    return translate_to_korean(entry.title, translation_cache)


def markdown_link(entry: ReportEntry) -> str:
    title = entry.translated_title or entry.title
    if entry.url:
        return f"{title} ([링크]({entry.url}))"
    return title


def markdown_link_with_translation(entry: ReportEntry, translation_cache: dict[str, str]) -> str:
    title = title_for_report(entry, translation_cache)
    if entry.url:
        return f"{title} ([링크]({entry.url}))"
    return title


def original_title_note(entry: ReportEntry, translation_cache: dict[str, str]) -> str:
    translated = title_for_report(entry, translation_cache)
    if translated and translated != entry.title:
        return f"  - 원문 제목: {entry.title}"
    return ""


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
    joined = " ".join(f"{entry.title} {entry.summary}".lower() for entry in entries)
    for keyword, note in keyword_map.items():
        if keyword.lower() in joined and note not in notes:
            notes.append(note)
    if not notes and entries:
        notes.append("최근 기사와 공시 제목 기준으로 주요 이슈를 확인하세요.")
    return notes


def build_major_issue_summary(
    sections: list[tuple[WatchlistItem, list[ReportEntry], list[ReportEntry], list[str]]],
    translation_cache: dict[str, str],
    max_items: int,
) -> list[str]:
    candidates: list[tuple[dt.datetime, WatchlistItem, str, ReportEntry]] = []
    for item, disclosures, news, _notes in sections:
        for entry in disclosures:
            published = parse_report_datetime(entry.published_at) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)
            candidates.append((published, item, "공시", entry))
        for entry in news:
            published = parse_report_datetime(entry.published_at) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)
            candidates.append((published, item, "뉴스", entry))

    lines = ["## 주요 이슈 요약", ""]
    if not candidates:
        lines.extend(["- 새로 수집된 공시나 뉴스가 없습니다.", ""])
        return lines

    for published, item, entry_type, entry in sorted(candidates, key=lambda row: row[0], reverse=True)[:max_items]:
        date_text = published.date().isoformat() if published.year > 1900 else "날짜 미상"
        source_text = f"{entry.source}, {date_text}" if entry.source else date_text
        lines.append(f"- **{item.name}({item.symbol})** [{entry_type}] {title_for_report(entry, translation_cache)} ({source_text})")
    lines.append("")
    return lines


def build_market_overview_section(
    overview: list[tuple[MarketIndex, ReportEntry, list[ReportEntry]]],
    translation_cache: dict[str, str],
) -> list[str]:
    lines = ["## 주요 시장 브리핑", ""]
    if not overview:
        lines.extend(["- 시장 브리핑 수집 대상이 설정되어 있지 않습니다.", ""])
        return lines

    for index, snapshot, news in overview:
        lines.extend([f"### {index.name}({index.symbol})", ""])
        lines.append(f"- {markdown_link_with_translation(snapshot, translation_cache)}")
        if snapshot.summary:
            lines.append(f"  - {snapshot.summary}")
        if news:
            lines.append("- 관련 최신 뉴스")
            for entry in sort_report_entries(news):
                meta = " / ".join(part for part in [entry.source, entry.published_at] if part)
                meta_text = f" ({meta})" if meta else ""
                lines.append(f"  - {markdown_link_with_translation(entry, translation_cache)}{meta_text}")
                original_note = original_title_note(entry, translation_cache)
                if original_note:
                    lines.append(f"    - {original_note.strip()}")
        else:
            lines.append("- 관련 뉴스가 수집되지 않았습니다.")
        lines.append("")

    return lines


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
    major_issue_limit = int(reporting.get("major_issue_limit", 10))
    watchlist = normalize_watchlist(config)
    translation_cache = load_translation_cache()
    market_overview = fetch_market_overview(config)

    lines = [
        f"# {report_date.isoformat()} 주식 이벤트 리포트",
        "",
        "이 리포트는 관심 종목의 최근 공시와 뉴스 링크를 자동으로 모은 것입니다.",
        "영어 뉴스 제목은 가능한 경우 한국어로 번역해 표시합니다.",
        "투자 판단을 대신하지 않으며, 중요한 내용은 원문을 확인하세요.",
        "",
    ]

    if not watchlist:
        lines.extend(["관심 종목이 설정되어 있지 않습니다.", ""])

    lines.extend(build_market_overview_section(market_overview, translation_cache))

    sections: list[tuple[WatchlistItem, list[ReportEntry], list[ReportEntry], list[str]]] = []
    for item in watchlist:

        if item.market == "KR":
            disclosures = fetch_dart_report_entries(config, item.symbol, disclosure_limit)
            news = fetch_google_news_rss(item.query or f"{item.name} {item.symbol}", news_limit)
        else:
            disclosures = []
            news = fetch_yahoo_news(item.symbol, news_limit)
            if not news:
                news = fetch_google_news_rss(item.query or f"{item.name} {item.symbol} stock", news_limit)

        disclosures = sort_report_entries(disclosures)[:disclosure_limit]
        news = sort_report_entries(news)[:news_limit]
        notes = infer_report_notes(disclosures + news)
        sections.append((item, disclosures, news, notes))

    if watchlist:
        lines.extend(build_major_issue_summary(sections, translation_cache, major_issue_limit))

    for item, disclosures, news, notes in sections:
        lines.extend([f"## {item.name}({item.symbol})", ""])

        lines.append("### 확인 포인트")
        if notes:
            lines.extend(f"- {note}" for note in notes)
        else:
            lines.append("- 새로 수집된 공시나 뉴스가 없습니다.")
        lines.append("")

        lines.append("### 공시")
        if disclosures:
            for entry in disclosures:
                date_text = f" ({entry.published_at})" if entry.published_at else ""
                lines.append(f"- {markdown_link_with_translation(entry, translation_cache)}{date_text}")
                original_note = original_title_note(entry, translation_cache)
                if original_note:
                    lines.append(original_note)
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
                lines.append(f"- {markdown_link_with_translation(entry, translation_cache)}{meta_text}")
                original_note = original_title_note(entry, translation_cache)
                if original_note:
                    lines.append(original_note)
                if entry.summary:
                    translated_summary = translate_to_korean(entry.summary, translation_cache)
                    lines.append(f"  - {translated_summary}")
                    if translated_summary != entry.summary:
                        lines.append(f"  - 원문 요약: {entry.summary}")
        else:
            lines.append("- 수집된 뉴스가 없습니다.")
        lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    save_translation_cache(translation_cache)
    print(f"Report generated: {report_path}")
    return report_path


def smtp_config_from_env() -> dict[str, str]:
    return {
        "host": os.environ.get("SMTP_HOST", ""),
        "port": os.environ.get("SMTP_PORT", "587"),
        "user": os.environ.get("SMTP_USER", ""),
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "to": os.environ.get("REPORT_EMAIL_TO", ""),
        "from": os.environ.get("REPORT_EMAIL_FROM", "") or os.environ.get("SMTP_USER", ""),
    }


def should_send_email() -> bool:
    config = smtp_config_from_env()
    required = ["host", "port", "user", "password", "to", "from"]
    return all(config.get(key) for key in required)


def render_inline_markdown_for_email(text: str) -> str:
    link_pattern = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
    parts: list[str] = []
    position = 0

    def render_text(value: str) -> str:
        escaped = html.escape(value)
        return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)

    for match in link_pattern.finditer(text):
        parts.append(render_text(text[position : match.start()]))
        label = match.group(1).strip()
        href = html.escape(match.group(2), quote=True)
        if label == "링크":
            parts.append(f'<a href="{href}">링크</a>')
        else:
            parts.append(f'{render_text(label)} (<a href="{href}">링크</a>)')
        position = match.end()

    parts.append(render_text(text[position:]))
    return "".join(parts)


def markdown_report_to_email_html(markdown_body: str) -> str:
    body_parts: list[str] = []
    open_lists = 0

    def close_lists(target_level: int = 0) -> None:
        nonlocal open_lists
        while open_lists > target_level:
            body_parts.append("</ul>")
            open_lists -= 1

    for raw_line in markdown_body.splitlines():
        if not raw_line.strip():
            close_lists()
            continue

        heading = re.match(r"^(#{1,3})\s+(.+)$", raw_line)
        if heading:
            close_lists()
            level = len(heading.group(1))
            body_parts.append(f"<h{level}>{render_inline_markdown_for_email(heading.group(2))}</h{level}>")
            continue

        bullet = re.match(r"^(\s*)-\s+(.+)$", raw_line)
        if bullet:
            indent = len(bullet.group(1).replace("\t", "  "))
            level = min(indent // 2 + 1, 3)
            while open_lists < level:
                body_parts.append("<ul>")
                open_lists += 1
            close_lists(level)
            body_parts.append(f"<li>{render_inline_markdown_for_email(bullet.group(2))}</li>")
            continue

        close_lists()
        body_parts.append(f"<p>{render_inline_markdown_for_email(raw_line)}</p>")

    close_lists()
    content = "\n".join(body_parts)
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{
      margin: 0;
      padding: 24px;
      background: #f6f7f9;
      color: #1f2933;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
      line-height: 1.58;
    }}
    .report {{
      max-width: 760px;
      margin: 0 auto;
      padding: 28px;
      background: #ffffff;
      border: 1px solid #e4e7eb;
      border-radius: 8px;
    }}
    h1, h2, h3 {{
      margin: 24px 0 10px;
      line-height: 1.25;
      color: #102a43;
    }}
    h1 {{
      margin-top: 0;
      font-size: 24px;
    }}
    h2 {{
      padding-top: 14px;
      border-top: 1px solid #e4e7eb;
      font-size: 19px;
    }}
    h3 {{
      font-size: 16px;
    }}
    p {{
      margin: 8px 0;
    }}
    ul {{
      margin: 8px 0 12px;
      padding-left: 22px;
    }}
    li {{
      margin: 6px 0;
    }}
    a {{
      color: #1d4ed8;
      font-weight: 600;
      text-decoration: none;
    }}
    strong {{
      color: #111827;
    }}
  </style>
</head>
<body>
  <div class="report">
{content}
  </div>
</body>
</html>"""


def send_report_email(report_path: Path | None) -> bool:
    if report_path is None:
        print("No report file was generated; skipping email.")
        return False
    if not report_path.exists():
        print(f"Report file does not exist: {report_path}; skipping email.")
        return False
    if not should_send_email():
        print("SMTP email secrets are not fully configured; skipping email.")
        return False

    config = smtp_config_from_env()
    report_date = dt.date.today().isoformat()
    body = report_path.read_text(encoding="utf-8")
    message = EmailMessage()
    message["Subject"] = f"[주식 리포트] {report_date} 일일 시장/종목 요약"
    message["From"] = config["from"]
    message["To"] = config["to"]
    message.set_content(body)
    message.add_alternative(markdown_report_to_email_html(body), subtype="html")
    message.add_attachment(
        body.encode("utf-8"),
        maintype="text",
        subtype="markdown",
        filename=report_path.name,
    )

    port = int(config["port"])
    if port == 465:
        with smtplib.SMTP_SSL(config["host"], port, timeout=30) as smtp:
            smtp.login(config["user"], config["password"])
            smtp.send_message(message)
    else:
        with smtplib.SMTP(config["host"], port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(config["user"], config["password"])
            smtp.send_message(message)

    print(f"Report email sent to {config['to']}.")
    return True


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
    parser.add_argument("--skip-email", action="store_true", help="Do not email the generated report.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config)
    report_path = None
    if not args.skip_calendar and not args.report_only:
        try:
            register_to_calendar(config, dry_run=args.dry_run)
        except Exception as exc:
            print(f"Calendar update failed: {exc}")
            print("Continuing with report generation. Refresh GOOGLE_TOKEN_JSON to restore calendar updates.")
    if not args.skip_report or args.report_only:
        report_path = generate_report(config)
    if not args.skip_email and not args.dry_run and not args.skip_report:
        send_report_email(report_path)


if __name__ == "__main__":
    main()
