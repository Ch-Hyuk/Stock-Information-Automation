# 주식 캘린더 자동화 에이전트

Python과 GitHub Actions를 이용해 국내외 주식 관련 일정을 수집하고 Google Calendar에 자동 등록하는 프로젝트입니다.

## 주요 기능

- `config.json`에서 관심 종목과 수집 조건을 관리합니다.
- `yfinance`로 해외 주식의 실적 발표일과 배당 일정을 수집합니다.
- 무료 OpenDART API로 국내 상장사의 주요 공시를 수집합니다.
- 수동 일정도 `manual_events`에 직접 추가할 수 있습니다.
- Google Calendar에 종일 일정으로 등록합니다.
- 자체 UID로 중복 등록을 방지합니다.
- 코스피, 코스닥, 나스닥, S&P 500 등 주요 지수 브리핑을 리포트에 포함합니다.
- 관심 종목별 일일 Markdown 리포트를 `reports/` 폴더에 생성합니다.
- GitHub Actions로 매일 한국 시간 오전 6시에 자동 실행됩니다.

## 등록되는 일정

현재 기본 설정 기준으로 아래 일정이 캘린더에 들어갑니다.

- 해외 주식: `AAPL`, `MSFT`, `NVDA`, `TSLA` 및 주요 미국 반도체주의 실적 발표 및 배당 일정
- 국내 주식: 최근 7일간 KOSPI/KOSDAQ 공시 중 아래 키워드가 포함된 공시
  - `영업(잠정)실적`
  - `현금ㆍ현물배당`
  - `주주총회`
  - `증권신고`
  - `투자설명서`

해외 주식 일정은 캘린더에 한국어로 표시됩니다.

예시:

```text
[해외주식] 애플(AAPL) 실적 발표
[해외주식] 테슬라(TSLA) 배당
[국내공시] 삼성전자: 현금ㆍ현물배당결정
```

## 초기 설정

1. Google Cloud에서 Google Calendar API를 활성화합니다.
2. OAuth Desktop App 클라이언트를 만들고 JSON 파일을 `credentials.json`으로 저장합니다.
3. 의존성을 설치합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

4. 로컬에서 최초 1회 실행해 `token.json`을 생성합니다.

```powershell
python main.py --dry-run
python main.py
```

5. GitHub Actions 자동 실행을 위해 저장소 Secret에 `GOOGLE_TOKEN_JSON`을 추가하고 `token.json` 전체 내용을 붙여넣습니다.
6. 국내 공시 수집을 위해 OpenDART에서 무료 API 인증키를 발급받고 저장소 Secret에 `DART_API_KEY`로 추가합니다.

## 설정 파일

`config.json`에서 수집 대상을 바꿀 수 있습니다.

```json
{
  "calendar_id": "primary",
  "timezone": "Asia/Seoul",
  "lookahead_days": 60,
  "us_tickers": ["AAPL", "MSFT", "NVDA", "TSLA", "AMD", "AVGO", "QCOM", "MU", "INTC", "TSM", "ASML", "AMAT", "LRCX"],
  "watchlist": [
    {
      "market": "US",
      "symbol": "AAPL",
      "name": "애플",
      "query": "AAPL Apple stock earnings"
    },
    {
      "market": "KR",
      "symbol": "005930",
      "name": "삼성전자",
      "query": "삼성전자 005930 실적 배당 공시"
    },
    {
      "market": "KR",
      "symbol": "042700",
      "name": "한미반도체",
      "query": "한미반도체 042700 HBM 반도체 장비 공시"
    }
  ],
  "reporting": {
    "enabled": true,
    "output_dir": "reports",
    "news_limit": 5,
    "disclosure_limit": 5,
    "dart_recent_days": 14,
    "major_issue_limit": 10
  },
  "market_overview": {
    "enabled": true,
    "news_limit": 3,
    "indexes": [
      {
        "symbol": "^KS11",
        "name": "코스피",
        "query": "코스피 증시 반도체 외국인 기관 수급"
      },
      {
        "symbol": "^IXIC",
        "name": "나스닥",
        "query": "Nasdaq market semiconductor AI stocks"
      },
      {
        "symbol": "^GSPC",
        "name": "S&P 500",
        "query": "S&P 500 market earnings technology stocks"
      }
    ]
  },
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

`stock_codes`를 비워두면 KOSPI/KOSDAQ 전체를 모니터링합니다. 특정 종목만 보고 싶으면 6자리 종목코드를 넣습니다.

예시:

```json
"stock_codes": ["005930", "000660"]
```

`watchlist`는 일일 리포트에 포함할 관심 종목입니다.

- `market`: `US` 또는 `KR`
- `symbol`: 미국 티커 또는 국내 6자리 종목코드
- `name`: 리포트에 표시할 종목명
- `query`: 뉴스 검색에 사용할 문구

현재 기본 관심 종목에는 미국 반도체주 `NVDA`, `AMD`, `AVGO`, `QCOM`, `MU`, `INTC`, `TSM`, `ASML`, `AMAT`, `LRCX`와 한국 반도체주 `삼성전자`, `SK하이닉스`, `한미반도체`, `DB하이텍`, `리노공업`, `HPSP`, `이오테크닉스`, `원익IPS`가 포함되어 있습니다.

`market_overview`는 리포트 상단에 표시할 주요 시장 지수입니다. 기본값에는 `코스피`, `코스닥`, `나스닥`, `S&P 500`, `다우존스`, `러셀2000`, `필라델피아 반도체`가 포함되어 있습니다. 각 지수는 Yahoo Finance 심볼로 최근 지수 동향을 가져오고, `query`로 관련 최신 뉴스를 검색합니다.

## 로컬 실행

캘린더에 등록하지 않고 미리보기:

```powershell
python main.py --dry-run
```

실제 캘린더 등록:

```powershell
python main.py
```

리포트만 생성:

```powershell
python main.py --report-only
```

캘린더 등록 없이 리포트 생성:

```powershell
python main.py --skip-calendar
```

캘린더만 갱신하고 리포트는 생략:

```powershell
python main.py --skip-report
```

## 일일 리포트

실행하면 아래 형식의 Markdown 파일이 생성됩니다.

```text
reports/2026-06-22.md
```

리포트에는 종목별로 다음 내용이 들어갑니다.

- 주요 시장 브리핑
- 전체 주요 이슈 요약
- 확인 포인트
- 최근 DART 공시
- 최신순으로 정렬된 최근 뉴스 링크
- 기사 또는 공시 원문 링크
- 영어 기사 제목과 요약의 한국어 번역

해외 종목은 Yahoo Finance 뉴스와 Google News RSS를 활용합니다. 국내 종목은 OpenDART 공시와 Google News RSS를 활용합니다.

GitHub Actions 실행 시 새 리포트가 생성되면 `reports/` 폴더에 자동 커밋됩니다.

영어 기사 제목은 무료 번역 엔드포인트를 통해 한국어 번역을 시도합니다. 번역에 실패하면 원문 제목을 그대로 표시합니다.

## GitHub Actions

`.github/workflows/run_agent.yml`이 매일 한국 시간 오전 6시에 실행됩니다. GitHub 저장소의 `Actions` 탭에서 `Run workflow` 버튼으로 수동 실행할 수도 있습니다.

필요한 GitHub Secrets:

- `GOOGLE_TOKEN_JSON`: Google Calendar 인증 토큰
- `DART_API_KEY`: OpenDART 무료 API 인증키
- `SMTP_HOST`: SMTP 서버 주소. Gmail 예시는 `smtp.gmail.com`
- `SMTP_PORT`: SMTP 포트. Gmail STARTTLS 예시는 `587`
- `SMTP_USER`: SMTP 로그인 이메일 주소
- `SMTP_PASSWORD`: SMTP 비밀번호 또는 앱 비밀번호
- `REPORT_EMAIL_TO`: 리포트를 받을 이메일 주소
- `REPORT_EMAIL_FROM`: 보내는 이메일 주소. 생략하면 `SMTP_USER`를 사용합니다.

## 이메일 발송

리포트가 생성되면 GitHub Actions에서 이메일 발송을 시도합니다. SMTP 관련 Secret이 모두 설정되어 있지 않으면 이메일 발송만 건너뛰고, 캘린더 갱신과 리포트 생성은 그대로 진행됩니다.

Gmail을 사용할 경우 일반 로그인 비밀번호 대신 Google 계정의 **앱 비밀번호**를 발급받아 `SMTP_PASSWORD`에 넣는 방식을 권장합니다.

Gmail 예시:

```text
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=Google 앱 비밀번호
REPORT_EMAIL_TO=받을 이메일 주소
REPORT_EMAIL_FROM=your-email@gmail.com
```

로컬에서 이메일 없이 실행하려면:

```powershell
python main.py --skip-email
```

## 문제 해결

GitHub Actions에서 아래 오류가 나오면 Google Calendar 인증 토큰이 만료되었거나 철회된 상태입니다.

```text
invalid_grant: Token has been expired or revoked.
```

해결 방법:

1. 로컬에서 기존 `token.json`을 삭제합니다.
2. `credentials.json`이 있는 상태에서 다시 실행합니다.

```powershell
python main.py --skip-email
```

3. 브라우저에서 Google 계정 권한을 다시 승인합니다.
4. 새로 생성된 `token.json` 전체 내용을 복사합니다.
5. GitHub 저장소의 `GOOGLE_TOKEN_JSON` Secret 값을 새 `token.json` 내용으로 업데이트합니다.

캘린더 토큰 문제가 생겨도 리포트 생성과 이메일 발송은 계속 시도되도록 되어 있습니다. 다만 Google Calendar 일정 갱신은 새 토큰으로 Secret을 교체해야 다시 정상 동작합니다.

## 보안 주의

아래 파일은 절대 GitHub에 올리면 안 됩니다.

- `credentials.json`
- `token.json`

두 파일은 `.gitignore`에 포함되어 있습니다.
