# 주식 캘린더 자동화 에이전트

Python과 GitHub Actions를 이용해 국내외 주식 관련 일정을 수집하고 Google Calendar에 자동 등록하는 프로젝트입니다.

## 주요 기능

- `config.json`에서 관심 종목과 수집 조건을 관리합니다.
- `yfinance`로 해외 주식의 실적 발표일과 배당 일정을 수집합니다.
- 무료 OpenDART API로 국내 상장사의 주요 공시를 수집합니다.
- 수동 일정도 `manual_events`에 직접 추가할 수 있습니다.
- Google Calendar에 종일 일정으로 등록합니다.
- 자체 UID로 중복 등록을 방지합니다.
- GitHub Actions로 매일 한국 시간 오전 6시에 자동 실행됩니다.

## 등록되는 일정

현재 기본 설정 기준으로 아래 일정이 캘린더에 들어갑니다.

- 해외 주식: `AAPL`, `MSFT`, `NVDA`, `TSLA`의 실적 발표 및 배당 일정
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

`stock_codes`를 비워두면 KOSPI/KOSDAQ 전체를 모니터링합니다. 특정 종목만 보고 싶으면 6자리 종목코드를 넣습니다.

예시:

```json
"stock_codes": ["005930", "000660"]
```

## 로컬 실행

캘린더에 등록하지 않고 미리보기:

```powershell
python main.py --dry-run
```

실제 캘린더 등록:

```powershell
python main.py
```

## GitHub Actions

`.github/workflows/run_agent.yml`이 매일 한국 시간 오전 6시에 실행됩니다. GitHub 저장소의 `Actions` 탭에서 `Run workflow` 버튼으로 수동 실행할 수도 있습니다.

필요한 GitHub Secrets:

- `GOOGLE_TOKEN_JSON`: Google Calendar 인증 토큰
- `DART_API_KEY`: OpenDART 무료 API 인증키

## 보안 주의

아래 파일은 절대 GitHub에 올리면 안 됩니다.

- `credentials.json`
- `token.json`

두 파일은 `.gitignore`에 포함되어 있습니다.
