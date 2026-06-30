# 주식 유튜버 인사이트 트래커 (Stock YouTuber Insight Tracker)

국내 주식 유튜버들의 새 영상을 매일 자동 수집하여, **Claude API로 종목별 언급 감성을 분석**하고,
유튜버의 **입장 변화를 추적**해 **Gmail 데일리 브리핑** 및 **Obsidian 아카이브**로 제공하는
개인 설치형 Python 자동화 도구입니다.

> 여러 채널을 구독하지만 모든 영상을 볼 시간이 없는 개인 투자자를 위한 도구입니다.
> 공개 유튜브 자막만 활용하며, 본 도구의 출력은 **투자 권유가 아닌 참고 자료**입니다.

---

## 주요 기능

| 단계 | 내용 |
|------|------|
| 채널/종목 등록 | `config.yaml`에 추적할 유튜버 채널·관심 종목·별칭을 직접 등록 |
| 자동 수집 | 등록 채널의 24시간 내 신규 영상 자막 수집 (`youtube-transcript-api`) |
| 자막 품질 검증 | 자막 없음/너무 짧음 → 스킵하고 "분석 제외 N건"으로 표시 |
| 별칭 매칭 | "삼전→삼성전자" 등 내장 약어 사전 1차 매칭 후 Claude 보조 정규화 |
| AI 분석 | Claude API로 종목·감성(긍정/부정/중립)·신뢰도(상/중/하) 추출 |
| 여론 집계 | "삼성전자: 긍정 3 / 중립 1 / 부정 1 (60% 긍정)" 형식 집계 |
| 입장 변화 감지 | 같은 유튜버의 입장이 바뀌면(긍정→부정 등) 경고 표시 |
| Gmail + Obsidian | 데일리 브리핑 이메일 발송 + 날짜별 md 아카이브 저장 |

---

## 설치

```bash
git clone <this-repo-url>
cd stock-youtube-tracker

# (권장) 가상환경
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 의존성 설치 (한 줄)
pip install -r requirements.txt
```

요구 환경: **Python 3.10 이상**

---

## 설정

1. 예시 파일을 복사합니다.

   ```bash
   cp config.example.yaml config.yaml      # Windows: copy config.example.yaml config.yaml
   ```

2. `config.yaml`을 열어 아래 값을 채웁니다. (이 파일은 `.gitignore`에 포함되어 커밋되지 않습니다.)

   | 항목 | 발급처 / 설명 |
   |------|----------------|
   | `api_keys.youtube` | [Google Cloud Console](https://console.cloud.google.com) → YouTube Data API v3 키 (무료 일 10,000 units) |
   | `api_keys.claude` | [Anthropic Console](https://console.anthropic.com) → API 키 (유료) |
   | `channels` | 추적할 유튜버 **@핸들** 목록 (예: `"@StockChannelA"`). 채널ID(`UC…`)도 허용 |
   | `stocks` | 관심 종목명 + `aliases`(줄임말·종목코드) 목록 |
   | `gmail_sender` / `gmail_app_password` | Gmail 주소 + [앱 비밀번호](https://myaccount.google.com/apppasswords) (2단계 인증 필요) |
   | `gmail_to` | 브리핑 수신자(복수 가능) |
   | `vault_path` | Obsidian vault 경로 (선택 — 비우면 Gmail만 발송) |

---

## 실행

```bash
python main.py                       # config.yaml 사용
python main.py --config myconf.yaml  # 다른 설정 파일 지정
```

실행하면 다음 순서로 동작합니다:

```
config.yaml → collector → analyzer → aggregator → notifier(Gmail + Obsidian)
```

- 실행 로그: `logs/YYYY-MM-DD.log`
- 처리한 영상 ID: `data/processed_ids.json` (중복 분석 방지)
- 유튜버별 입장 이력: `data/history.json` (입장 변화 비교용)
- Obsidian 아카이브: `{vault}/주식인사이트/YYYY-MM-DD.md`

---

## 매일 자동 실행

### Linux / macOS (cron)

`crontab -e` 후 아래 줄 추가 — 매일 오전 8시 실행 예시:

```cron
0 8 * * * cd /path/to/stock-youtube-tracker && /path/to/.venv/bin/python main.py >> logs/cron.log 2>&1
```

### Windows (작업 스케줄러 / Task Scheduler)

PowerShell에서 매일 08:00 실행 작업 등록 예시:

```powershell
schtasks /Create /SC DAILY /TN "StockTracker" /ST 08:00 ^
  /TR "cmd /c cd /d C:\path\to\stock-youtube-tracker && .venv\Scripts\python.exe main.py"
```

---

## 프로젝트 구조

```
stock-youtube-tracker/
├── main.py                 # 파이프라인 진입점
├── config.yaml             # 사용자 설정 (.gitignore 대상, 직접 생성)
├── config.example.yaml     # 설정 예시 (공개용)
├── requirements.txt
├── README.md
├── .gitignore
├── src/
│   ├── config.py           # 설정 로더 + 별칭 맵
│   ├── logger.py           # 일자별 로깅
│   ├── abbreviations.py    # 한국 주식 공통 약어 내장 사전 (1차 매칭)
│   ├── collector.py        # 신규 영상 + 자막 수집 / 품질 검증
│   ├── analyzer.py         # Claude API 종목·감성 분석
│   ├── aggregator.py       # 여론 집계 + 입장 변화 감지
│   └── notifier.py         # Gmail 브리핑 + Obsidian 아카이브
├── data/
│   ├── processed_ids.json  # 처리 완료 영상 ID
│   └── history.json        # 유튜버별 종목 감성 이력
└── logs/                   # 실행 로그 (YYYY-MM-DD.log)
```

---

## 분석 신뢰성 원칙

본 프로젝트의 핵심 리스크는 유튜브 자동 자막의 누락·부정확입니다.
부정확한 자막을 억지로 분석해 틀린 정보를 사실처럼 전달하는 것이 가장 위험하므로,
**"확실한 것만 분석하고, 애매하면 신뢰도 '하'로 솔직히 표시한다"**를 원칙으로 합니다.

- 자막이 최소 길이(기본 100자) 미만이면 분석에서 제외
- 판단이 불확실하면 신뢰도 **"하"** + 브리핑에 **"참고용"** 표기
- 신뢰도 "하" 항목은 종목별 여론 비율 집계에서 **제외**(건수만 별도 표기)

---

## 라이선스 / 면책

- 공개 데이터(유튜브 자막)만 활용하며 개인정보를 수집하지 않습니다.
- 본 도구의 모든 출력은 정보 제공·참고 목적이며 투자 권유가 아닙니다.
  투자 판단과 그 결과에 대한 책임은 전적으로 사용자 본인에게 있습니다.

---

## v1 범위 / 향후 확장

- **현재(v1):** 국내(KOSPI/KOSDAQ) 한국어 채널·종목 대상 (ETF 포함)
- **향후:** 해외(미국 등) 주식·영어권 유튜버 지원 (종목 매칭 모듈 확장 가능 구조)
