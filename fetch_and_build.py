# -*- coding: utf-8 -*-
"""
ShortsRadar — 미국 인기 숏츠 스크리너

YouTube Data API(mostPopular 차트)에서 미국·영화/애니메이션 인기 영상을 받아
숏츠(3분 이하)만 걸러 docs/index.html 리포트를 생성한다.
조회수 스냅샷을 docs/data/history.json에 누적해 '증가속도'를 계산한다.

사용: YT_API_KEY 환경변수 필요.  (테스트: python fetch_and_build.py --mock)
쿼터: 실행당 ~4유닛 (일일 무료 10,000유닛의 0.04%)
"""
import json
import os
import re
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── 설정 ─────────────────────────────────────────────────────────────────────
REGION      = "US"    # 국가
CATEGORY_ID = "1"     # 1 = Film & Animation (영화/애니메이션)
MAX_DUR_S   = 183     # 이 길이 이하만 숏츠로 판정 (쇼츠는 최대 3분)
PAGES       = 4       # mostPopular 최대 200개 (50×4)
KEEP_DAYS   = 14      # 기록 보관 일수
KST         = timezone(timedelta(hours=9))

# 검색 수집 (차트에 안 뜨는 인기 숏츠까지) — 검색 1페이지 = 100유닛
SEARCH_WEEK_PAGES = 3    # 최근 7일 · 조회수순 (이번 주 풀 확장)
SEARCH_NEW_PAGES  = 2    # 최근 48시간 · 조회수순 (오늘 탭 확장)
SEARCH_QUERY      = ""   # 검색어 필터 (예: "movie recap"). "" = 전체

ROOT     = Path(__file__).resolve().parent
DOCS     = ROOT / "docs"
HISTORY  = DOCS / "data" / "history.json"

API        = "https://www.googleapis.com/youtube/v3/videos"
SEARCH_API = "https://www.googleapis.com/youtube/v3/search"


def search_short_ids(key, published_after, pages):
    """search.list: 기간 내 조회수순 숏츠 후보 ID 수집 (videoDuration=short = 4분 미만)."""
    ids, token = [], None
    for _ in range(pages):
        q = {"part": "id", "type": "video", "videoDuration": "short",
             "videoCategoryId": CATEGORY_ID, "regionCode": REGION,
             "order": "viewCount", "publishedAfter": published_after,
             "maxResults": 50, "key": key}
        if SEARCH_QUERY:
            q["q"] = SEARCH_QUERY
        if token:
            q["pageToken"] = token
        url = SEARCH_API + "?" + urllib.parse.urlencode(q)
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.load(r)
        ids += [it["id"]["videoId"] for it in data.get("items", [])
                if it.get("id", {}).get("videoId")]
        token = data.get("nextPageToken")
        if not token:
            break
    return ids


def fetch_details(key, ids):
    """videos.list로 ID 목록의 상세(스니펫·길이·조회수) 조회. 50개씩 배치."""
    out = []
    for i in range(0, len(ids), 50):
        q = {"part": "snippet,contentDetails,statistics",
             "id": ",".join(ids[i:i + 50]), "key": key}
        url = API + "?" + urllib.parse.urlencode(q)
        with urllib.request.urlopen(url, timeout=30) as r:
            out += json.load(r).get("items", [])
    return out


def fetch_popular(key):
    """mostPopular 차트 (US, 카테고리) → 영상 dict 리스트 (차트 순서 유지)."""
    items, token = [], None
    for _ in range(PAGES):
        q = {"part": "snippet,contentDetails,statistics",
             "chart": "mostPopular", "regionCode": REGION,
             "videoCategoryId": CATEGORY_ID, "maxResults": 50, "key": key}
        if token:
            q["pageToken"] = token
        url = API + "?" + urllib.parse.urlencode(q)
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            # 일부 카테고리는 차트 미제공 → 전체 차트에서 카테고리 필터로 폴백
            if e.code == 400 and "chart" in body:
                print("카테고리 차트 미제공 → 전체 차트에서 필터링으로 폴백")
                return fetch_popular_all_then_filter(key)
            raise RuntimeError(f"API 오류 {e.code}: {body[:300]}") from e
        items += data.get("items", [])
        token = data.get("nextPageToken")
        if not token:
            break
    return items


def fetch_popular_all_then_filter(key):
    items, token = [], None
    for _ in range(PAGES):
        q = {"part": "snippet,contentDetails,statistics",
             "chart": "mostPopular", "regionCode": REGION,
             "maxResults": 50, "key": key}
        if token:
            q["pageToken"] = token
        url = API + "?" + urllib.parse.urlencode(q)
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.load(r)
        items += data.get("items", [])
        token = data.get("nextPageToken")
        if not token:
            break
    return [v for v in items if v["snippet"].get("categoryId") == CATEGORY_ID]


_DUR = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def dur_seconds(iso):
    m = _DUR.fullmatch(iso or "")
    if not m:
        return 10 ** 9
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def fmt_views(n):
    n = int(n)
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}억"
    if n >= 10_000:
        return f"{n / 10_000:.1f}만"
    return f"{n:,}"


def fmt_age(published, now):
    d = (now - published).total_seconds()
    if d < 3600 * 24:
        return f"{int(d // 3600)}시간 전"
    if d < 3600 * 24 * 30:
        return f"{int(d // 86400)}일 전"
    return f"{int(d // (86400 * 30))}달 전"


def load_history():
    if HISTORY.exists():
        return json.loads(HISTORY.read_text(encoding="utf-8"))
    return {}


def update_history(hist, shorts, now_iso):
    for rank, v in enumerate(shorts, 1):
        vid = v["id"]
        sn = v["snippet"]
        views = int(v["statistics"].get("viewCount", 0))
        rec = hist.get(vid)
        if rec is None:
            rec = hist[vid] = {
                "title": sn["title"], "channel": sn["channelTitle"],
                "publishedAt": sn["publishedAt"],
                "thumb": (sn["thumbnails"].get("high") or sn["thumbnails"]["default"])["url"],
                "first_seen": now_iso, "snapshots": [],
            }
        rec["last_seen"] = now_iso
        rec["last_rank"] = rank
        rec["title"] = sn["title"]          # 제목 변경 반영
        rec["snapshots"].append({"t": now_iso, "views": views})
        rec["snapshots"] = rec["snapshots"][-40:]
    # 오래 안 보인 영상 정리
    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat()
    for vid in [k for k, r in hist.items() if r.get("last_seen", "") < cutoff]:
        del hist[vid]
    return hist


def growth_per_hour(rec):
    """마지막 두 스냅샷 사이 시간당 조회수 증가. 스냅샷 1개면 None."""
    ss = rec["snapshots"]
    if len(ss) < 2:
        return None
    a, b = ss[-2], ss[-1]
    dt_h = (datetime.fromisoformat(b["t"]) - datetime.fromisoformat(a["t"])).total_seconds() / 3600
    if dt_h < 0.5:
        return None
    return (b["views"] - a["views"]) / dt_h


# ── HTML ─────────────────────────────────────────────────────────────────────

def card(vid, rec, now, badge=""):
    ss = rec["snapshots"]
    views = ss[-1]["views"] if ss else 0
    g = growth_per_hour(rec)
    g_txt = f"+{fmt_views(int(g))}/시간" if g and g > 0 else ("—" if g is None else "0/시간")
    g_cls = "hot" if (g or 0) >= 10000 else ""
    pub = datetime.fromisoformat(rec["publishedAt"].replace("Z", "+00:00"))
    new = '<span class="badge new">NEW</span>' if rec.get("first_seen", "") >= (now - timedelta(hours=26)).isoformat() else ""
    return f"""<a class="card" href="https://www.youtube.com/shorts/{vid}" target="_blank">
  <div class="th"><img src="{rec['thumb']}" loading="lazy" alt=""></div>
  <div class="body">
    <div class="badges">{badge}{new}</div>
    <div class="title">{rec['title'][:80]}</div>
    <div class="ch">{rec['channel']}</div>
    <div class="stats"><b>{fmt_views(views)}</b> 조회
      <span class="g {g_cls}">{g_txt}</span>
      <span class="age">{fmt_age(pub, now)}</span></div>
  </div>
</a>"""


def build_html(hist, today_ids, now):
    # 오늘의 인기: 오늘 차트 순서
    today_cards = "".join(
        card(vid, hist[vid], now, f'<span class="badge rank">#{i + 1}</span>')
        for i, vid in enumerate(today_ids) if vid in hist)
    # 급상승: 시간당 증가속도 순 (스냅샷 2개 이상)
    risers = sorted(((growth_per_hour(r), vid) for vid, r in hist.items()
                     if growth_per_hour(r) is not None),
                    key=lambda x: -x[0])[:24]
    riser_cards = "".join(
        card(vid, hist[vid], now, f'<span class="badge up">🔥 #{i + 1}</span>')
        for i, (g, vid) in enumerate(risers) if g > 0)
    # 이번 주: 최근 7일 등장 영상, 조회수 순
    week_cut = (now - timedelta(days=7)).isoformat()
    weekly = sorted(((r["snapshots"][-1]["views"], vid) for vid, r in hist.items()
                     if r.get("last_seen", "") >= week_cut and r["snapshots"]),
                    key=lambda x: -x[0])[:48]
    week_cards = "".join(
        card(vid, hist[vid], now, f'<span class="badge rank">#{i + 1}</span>')
        for i, (_, vid) in enumerate(weekly))

    stamp = now.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ShortsRadar — 미국 인기 숏츠</title><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0e0e14;color:#e8e8f0;font-family:'Segoe UI','Malgun Gothic',sans-serif}}
header{{padding:18px 22px 10px;display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}}
h1{{font-size:20px}} .sub{{color:#777;font-size:12px}}
nav{{padding:0 22px 14px;display:flex;gap:8px}}
nav button{{background:#1c1c28;color:#999;border:0;padding:8px 16px;border-radius:8px;cursor:pointer;font-size:13px;font-weight:600}}
nav button.on{{background:#7c85f0;color:#fff}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:14px;padding:0 22px 30px}}
.card{{background:#16161f;border:1px solid #24242f;border-radius:10px;overflow:hidden;text-decoration:none;color:inherit;display:flex;flex-direction:column;transition:transform .1s}}
.card:hover{{transform:translateY(-3px);border-color:#7c85f0}}
.th{{aspect-ratio:16/9;background:#000;overflow:hidden}}
.th img{{width:100%;height:100%;object-fit:cover;display:block}}
.body{{padding:10px 12px 12px}}
.badges{{min-height:20px;margin-bottom:4px}}
.badge{{font-size:10px;font-weight:700;padding:2px 7px;border-radius:5px;margin-right:5px}}
.badge.rank{{background:#26263a;color:#aab}} .badge.up{{background:#3a2318;color:#ff9f6b}}
.badge.new{{background:#183a20;color:#5cd67e}}
.title{{font-size:13px;line-height:1.45;height:2.9em;overflow:hidden;font-weight:600}}
.ch{{color:#888;font-size:11px;margin:5px 0}}
.stats{{font-size:12px;color:#aaa}} .stats b{{color:#fff}}
.g{{margin-left:8px;color:#5cd67e}} .g.hot{{color:#ff9f6b;font-weight:700}}
.age{{float:right;color:#666}}
.empty{{color:#666;padding:30px 22px;font-size:13px}}
footer{{color:#555;font-size:11px;padding:0 22px 26px}}
section{{display:none}} section.on{{display:block}}
</style></head><body>
<header><h1>📡 ShortsRadar</h1>
<span class="sub">미국 · 영화/애니메이션 · 숏츠 &nbsp;|&nbsp; 갱신 {stamp} (매일 자동)</span></header>
<nav>
<button id="b0" class="on" onclick="tab(0)">오늘의 인기</button>
<button id="b1" onclick="tab(1)">🔥 급상승</button>
<button id="b2" onclick="tab(2)">이번 주</button>
</nav>
<section id="s0" class="on"><div class="grid">{today_cards or ''}</div>
{'' if today_cards else '<div class="empty">오늘 수집된 숏츠가 없습니다.</div>'}</section>
<section id="s1"><div class="grid">{riser_cards or ''}</div>
{'' if riser_cards else '<div class="empty">증가속도는 이틀째 실행부터 계산됩니다 (조회수 변화 비교가 필요).</div>'}</section>
<section id="s2"><div class="grid">{week_cards or ''}</div>
{'' if week_cards else '<div class="empty">아직 기록이 없습니다.</div>'}</section>
<footer>YouTube Data API · 인기 차트 + 조회수순 검색 (region {REGION}, category {CATEGORY_ID}) · {MAX_DUR_S}초 이하만 숏츠로 표시 · 기록 {KEEP_DAYS}일 보관</footer>
<script>
function tab(i){{for(let k=0;k<3;k++){{
document.getElementById('s'+k).classList.toggle('on',k===i);
document.getElementById('b'+k).classList.toggle('on',k===i);}}}}
</script></body></html>"""


# ── 메인 ─────────────────────────────────────────────────────────────────────

def mock_items():
    """--mock: API 없이 렌더 테스트용 가짜 데이터."""
    now = datetime.now(timezone.utc)
    out = []
    for i in range(12):
        out.append({
            "id": f"MOCK{i:03d}xxxxx",
            "snippet": {
                "title": f"[MOCK] Epic Movie Scene #{i + 1} — you won't believe what happens",
                "channelTitle": f"MockChannel{i % 4}",
                "publishedAt": (now - timedelta(days=i % 5, hours=i)).isoformat().replace("+00:00", "Z"),
                "categoryId": CATEGORY_ID,
                "thumbnails": {"high": {"url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"}},
            },
            "contentDetails": {"duration": f"PT{30 + i * 9}S"},
            "statistics": {"viewCount": str(1_000_000 * (12 - i) + i * 7777)},
        })
    return out


def main():
    mock = "--mock" in sys.argv
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    if mock:
        chart_items, fresh_ids = mock_items(), []
        items = chart_items
        print("MOCK 모드 (API 호출 없음)")
    else:
        key = os.environ.get("YT_API_KEY", "").strip()
        if not key:
            print("환경변수 YT_API_KEY가 없습니다.", file=sys.stderr)
            sys.exit(1)
        chart_items = fetch_popular(key)
        print(f"인기 차트 {len(chart_items)}개")
        # 검색으로 풀 확장: 차트에 안 뜨는 인기 숏츠까지
        ts = lambda d: (now - timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%SZ")
        week_ids  = search_short_ids(key, ts(7), SEARCH_WEEK_PAGES)
        fresh_ids = search_short_ids(key, ts(2), SEARCH_NEW_PAGES)
        known = {v["id"] for v in chart_items}
        extra_ids = [i for i in dict.fromkeys(week_ids + fresh_ids) if i not in known]
        extra_items = fetch_details(key, extra_ids)
        print(f"검색 수집 +{len(extra_items)}개 (주간 {len(week_ids)} · 48시간 {len(fresh_ids)})")
        items = chart_items + extra_items

    shorts = [v for v in items if dur_seconds(v["contentDetails"]["duration"]) <= MAX_DUR_S]
    print(f"숏츠({MAX_DUR_S}s 이하) 총 {len(shorts)}개")

    hist = load_history()
    hist = update_history(hist, shorts, now_iso)
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(json.dumps(hist, ensure_ascii=False), encoding="utf-8")

    # 오늘 탭 = 차트 순위 숏츠 + 최근 48시간 조회수순 검색 숏츠 (중복 제거, 60개 상한)
    short_ids = {v["id"] for v in shorts}
    chart_short_ids = [v["id"] for v in chart_items if v["id"] in short_ids]
    today_ids = list(dict.fromkeys(
        chart_short_ids + [i for i in fresh_ids if i in short_ids]))[:60]

    html = build_html(hist, today_ids, now)
    (DOCS / "index.html").write_text(html, encoding="utf-8")
    print(f"완료: docs/index.html (오늘 {len(today_ids)}개 / 기록 {len(hist)}개 영상)")


if __name__ == "__main__":
    main()
