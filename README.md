# ShortsRadar 📡

**미국 인기 숏츠(영화/애니메이션) 랭킹**을 매일 자동 수집해서
GitHub Pages(github.io)로 보여주는 스크리너.

- **오늘의 인기** — YouTube 인기 차트 순위 그대로
- **🔥 급상승** — 시간당 조회수 증가속도 순 (이틀째부터 표시)
- **이번 주** — 최근 7일 등장 영상 누적 조회수 순

서버 없음 · 전부 무료 (GitHub Actions가 매일 아침 6시(KST) 자동 실행, API 쿼터 하루 ~4유닛)

---

## 최초 설정 (1회, 약 10분)

### 1. YouTube API 키 발급 (무료)

1. https://console.cloud.google.com 접속 → 구글 로그인
2. 상단 프로젝트 선택 → **새 프로젝트** (이름 아무거나, 예: shorts-radar)
3. 왼쪽 메뉴 **API 및 서비스 → 라이브러리** → `YouTube Data API v3` 검색 → **사용 설정**
4. **API 및 서비스 → 사용자 인증 정보 → + 사용자 인증 정보 만들기 → API 키** → 키 복사
5. (권장) 만든 키 클릭 → **API 제한사항 → 키 제한 → YouTube Data API v3**만 체크 → 저장

### 2. GitHub 저장소 만들기

1. https://github.com/new → 이름 `ShortsRadar` → **Public** (Pages 무료는 Public 필요) → Create
2. 이 폴더를 push (아래 명령 또는 Claude에게 요청)

### 3. API 키를 GitHub Secret으로 등록

1. 저장소 → **Settings → Secrets and variables → Actions**
2. **New repository secret** → Name: `YT_API_KEY` / Secret: 1번에서 복사한 키 → Add

> ⚠️ 키를 코드나 커밋에 직접 넣지 마세요. Secret에만.

### 4. GitHub Pages 켜기

1. 저장소 → **Settings → Pages**
2. Source: **Deploy from a branch** → Branch: `main`, 폴더: **/docs** → Save
3. 몇 분 후 사이트 주소: `https://<아이디>.github.io/ShortsRadar/`

### 5. 첫 실행

- 저장소 → **Actions** 탭 → `daily-update` → **Run workflow** (수동 1회)
- 이후 매일 아침 6시(KST)에 자동 갱신됩니다

---

## 로컬 테스트

```bat
:: API 키로 실제 수집
set YT_API_KEY=발급받은키
python fetch_and_build.py

:: API 없이 화면만 확인
python fetch_and_build.py --mock
```

결과: `docs/index.html` 을 브라우저로 열기.

## 설정 바꾸기

[fetch_and_build.py](fetch_and_build.py) 상단:

| 변수 | 기본값 | 의미 |
|---|---|---|
| `REGION` | `US` | 국가 코드 (KR, JP, ...) |
| `CATEGORY_ID` | `1` | 1=영화/애니메이션, 20=게임, 24=엔터테인먼트, 10=음악 |
| `MAX_DUR_S` | `183` | 이 길이(초) 이하만 숏츠로 판정 |
| `KEEP_DAYS` | `14` | 조회수 기록 보관 일수 |

갱신 시각 변경: [.github/workflows/daily.yml](.github/workflows/daily.yml)의 `cron` (UTC 기준, KST−9시간).
하루 2회로 늘리면 급상승 감지가 더 빨라집니다 (예: `"0 9,21 * * *"`).
