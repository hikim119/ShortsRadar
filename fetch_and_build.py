# -*- coding: utf-8 -*-
"""
ShortsRadar — 미국 인기 숏츠 스크리너 (Playboard 스타일)

수집: YouTube 인기 차트 + 조회수순 검색(1시간/48시간/7일/30일 창)
표시: 기간 × 조회수 구간 × 정렬 필터를 클라이언트(JS)에서 즉시 적용
기록: docs/data/history.json에 조회수 스냅샷 누적 → 증가속도 계산

사용: YT_API_KEY 환경변수 필요.  (테스트: python fetch_and_build.py --mock)
쿼터: 실행당 ~1,915유닛 × 4회/일(6시간 간격) ≈ 7,700 (일일 무료 10,000 이내)
      SEARCH_PLAN 추가 시 한도 초과 주의: 1페이지 = +100유닛 × 4회 = +400/일
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
MAX_DUR_S   = 300     # 이 길이(초) 이하만 표시 — 5분 미만
PAGES       = 4       # mostPopular 최대 200개 (50×4)
KEEP_DAYS   = 35      # 기록 보관 일수 (30일 필터 지원)
KST         = timezone(timedelta(hours=9))

# ── 검색 수집 계획 ───────────────────────────────────────────────────────────
# (검색어 or None, 카테고리ID or None, 기간, 페이지수, duration)
#   기간: "1h"=1시간, "2d"=48시간, "7d"=7일, "30d"=30일
#   duration: "short"=4분 미만 / "medium"=4~20분 (4~5분짜리 보완)
#   비용: 1페이지 = 100유닛 = 최대 50개.  전체 유닛 합계가 하루 한도(10,000)를
#   넘지 않게 조절 (현재 계획: 13페이지 ≈ 1,315유닛/회 × 6회 ≈ 7,900/일)
SEARCH_PLAN = [
    # 영화/애니메이션 카테고리 (기본)
    (None, "1", "1h", 1, "short"),
    (None, "1", "1d", 2, "short"),                                  # 24시간 전용
    (None, "1", "2d", 1, "short"), (None, "1", "2d", 1, "medium"),
    (None, "1", "7d", 2, "short"), (None, "1", "7d", 1, "medium"),
    (None, "1", "30d", 1, "short"), (None, "1", "30d", 1, "medium"),
    # 영화 리캡류는 엔터테인먼트(24)로 올라오는 경우가 많음 → 키워드로 보강
    ("movie recap", None, "1d", 1, "short"),
    ("movie recap", None, "7d", 1, "short"), ("movie recap", None, "30d", 1, "short"),
    ("movie", "24", "1d", 1, "short"),
    ("movie", "24", "7d", 1, "short"), ("movie", "24", "30d", 1, "short"),
    ("film", None, "1d", 1, "short"),
    ("film", None, "7d", 1, "short"), ("film", None, "30d", 1, "short"),
]

ROOT     = Path(__file__).resolve().parent
DOCS     = ROOT / "docs"
HISTORY  = DOCS / "data" / "history.json"

API        = "https://www.googleapis.com/youtube/v3/videos"
SEARCH_API = "https://www.googleapis.com/youtube/v3/search"


# ── 수집 ─────────────────────────────────────────────────────────────────────

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


def search_short_ids(key, published_after, pages, duration="short",
                     query=None, category=None):
    """search.list: 기간 내 조회수순 후보 ID 수집.
    query/category 중 최소 하나로 범위를 좁힌다."""
    ids, token = [], None
    for _ in range(pages):
        q = {"part": "id", "type": "video", "videoDuration": duration,
             "regionCode": REGION, "order": "viewCount",
             "publishedAfter": published_after, "maxResults": 50, "key": key}
        if category:
            q["videoCategoryId"] = category
        if query:
            q["q"] = query
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


_WIN = {"1h": {"hours": 1}, "1d": {"days": 1}, "2d": {"days": 2},
        "7d": {"days": 7}, "30d": {"days": 30}}


CHANNELS_FILE = ROOT / "channels.txt"
CHANNELS_API  = "https://www.googleapis.com/youtube/v3/channels"
PLAYLIST_API  = "https://www.googleapis.com/youtube/v3/playlistItems"


def load_channel_refs():
    """channels.txt의 관심 채널 목록 — 한 줄에 하나(또는 띄어쓰기 구분),
    '#' 주석·빈 줄 무시. 없으면 []."""
    try:
        refs = []
        for line in CHANNELS_FILE.read_text(encoding="utf-8-sig").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                refs += line.split()
        return refs
    except OSError:
        return []


def resolve_channel_id(key, ref):
    """URL/@핸들/UC아이디 → 채널 ID (UC...). 실패 시 None."""
    r = ref.strip().rstrip("/")
    if "/channel/" in r:
        r = r.split("/channel/")[-1].split("/")[0].split("?")[0]
    elif "youtube.com" in r:
        r = r.split("youtube.com/")[-1].split("/")[0].split("?")[0]   # @handle
    if r.startswith("UC") and len(r) == 24:
        return r
    handle = r if r.startswith("@") else "@" + r
    q = {"part": "id", "forHandle": handle, "key": key}
    url = CHANNELS_API + "?" + urllib.parse.urlencode(q)
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            items = json.load(resp).get("items", [])
        return items[0]["id"] if items else None
    except (urllib.error.URLError, KeyError, IndexError):
        return None


def fetch_channel_video_ids(key, channel_id, pages=1):
    """채널 업로드 재생목록(UU...)에서 최신 영상 ID (페이지당 50개, 1유닛)."""
    playlist = "UU" + channel_id[2:]
    ids, token = [], None
    for _ in range(pages):
        q = {"part": "contentDetails", "playlistId": playlist,
             "maxResults": 50, "key": key}
        if token:
            q["pageToken"] = token
        url = PLAYLIST_API + "?" + urllib.parse.urlencode(q)
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                data = json.load(r)
        except urllib.error.HTTPError:
            return ids   # 비공개/삭제 채널 등
        ids += [it["contentDetails"]["videoId"] for it in data.get("items", [])]
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


# ── 유틸 ─────────────────────────────────────────────────────────────────────

_DUR = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def dur_seconds(iso):
    m = _DUR.fullmatch(iso or "")
    if not m:
        return 10 ** 9
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


# ── 기록 ─────────────────────────────────────────────────────────────────────

def load_history():
    if HISTORY.exists():
        return json.loads(HISTORY.read_text(encoding="utf-8"))
    return {}


def update_history(hist, shorts, now_iso):
    for v in shorts:
        vid = v["id"]
        sn = v["snippet"]
        views = int(v["statistics"].get("viewCount", 0))
        rec = hist.get(vid)
        if rec is None:
            rec = hist[vid] = {
                "title": sn["title"], "channel": sn["channelTitle"],
                "publishedAt": sn["publishedAt"],
                "first_seen": now_iso, "snapshots": [],
            }
        rec["last_seen"] = now_iso
        rec["title"] = sn["title"]
        rec["dur"] = dur_seconds(v["contentDetails"]["duration"])
        lk = v["statistics"].get("likeCount")
        if lk is not None:
            rec["likes"] = int(lk)
        rec["snapshots"].append({"t": now_iso, "views": views})
        rec["snapshots"] = rec["snapshots"][-60:]
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


def views_7d(rec, now):
    """최근 7일 조회수 (Playboard '7일 조회' 근사).

    - 게시 7일 미만 영상: 전체 조회수 = 7일 조회
    - 그 외: 7일 전에 가장 가까운 스냅샷 대비 증가분
      (추적 기간이 7일 미만이면 추적 시작 대비 — 하한 근사)"""
    ss = rec["snapshots"]
    if not ss:
        return None
    v_now = ss[-1]["views"]
    try:
        pub = datetime.fromisoformat(rec["publishedAt"].replace("Z", "+00:00"))
    except ValueError:
        return None
    target = now - timedelta(days=7)
    if pub >= target:
        return v_now
    older = [s for s in ss if datetime.fromisoformat(s["t"]) <= target]
    base = older[-1] if older else ss[0]
    if base is ss[-1]:
        return None
    return max(0, v_now - base["views"])


# ── HTML (클라이언트 필터링) ─────────────────────────────────────────────────

TEMPLATE = r"""<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ShortsRadar — 미국 인기 숏츠</title><style>
:root{--bg:#0b0b11;--panel:#14141d;--line:#23232f;--txt:#eceef6;--mut:#8b8fa3;
--acc:#7c85f0;--acc2:#a78bfa;--green:#4ade80;--hot:#fb923c}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font-family:'Segoe UI','Malgun Gothic',system-ui,sans-serif}
.wrap{max-width:1240px;margin:0 auto;padding:0 20px}
header{padding:22px 0 14px;display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
h1{font-size:22px;font-weight:800;background:linear-gradient(90deg,var(--acc),var(--acc2));
   -webkit-background-clip:text;background-clip:text;color:transparent}
.sub{color:var(--mut);font-size:12px}
.filters{position:sticky;top:0;z-index:5;background:linear-gradient(var(--bg) 88%,transparent);
  padding:6px 0 14px;display:flex;flex-direction:column;gap:9px}
.frow{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.flabel{color:var(--mut);font-size:11px;font-weight:700;min-width:44px}
.pill{background:var(--panel);border:1px solid var(--line);color:var(--mut);
  padding:6px 13px;border-radius:999px;cursor:pointer;font-size:12px;font-weight:600;
  transition:.12s}
.pill:hover{color:var(--txt);border-color:#3a3a4d}
.pill.on{background:linear-gradient(90deg,var(--acc),var(--acc2));color:#fff;border-color:transparent}
.pill.edit{border-style:dashed;color:var(--acc);text-decoration:none;display:inline-block}
.pill.edit:hover{border-color:var(--acc)}
.count{color:var(--mut);font-size:12px;margin-left:auto}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(225px,1fr));gap:15px;padding-bottom:40px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden;
  text-decoration:none;color:inherit;display:flex;flex-direction:column;
  transition:transform .12s, box-shadow .12s, border-color .12s}
.card:hover{transform:translateY(-4px);border-color:var(--acc);box-shadow:0 8px 24px rgba(124,133,240,.15)}
.th{position:relative;aspect-ratio:16/9;background:#000}
.th img{width:100%;height:100%;object-fit:cover;display:block}
.rank{position:absolute;top:8px;left:10px;font-size:20px;font-weight:900;color:#fff;
  text-shadow:0 1px 6px rgba(0,0,0,.9)}
.dur{position:absolute;bottom:7px;right:8px;background:rgba(0,0,0,.75);color:#fff;
  font-size:10px;font-weight:700;padding:2px 6px;border-radius:5px}
.body{padding:11px 13px 13px;display:flex;flex-direction:column;gap:6px}
.title{font-size:13px;line-height:1.45;height:2.9em;overflow:hidden;font-weight:600}
.ch{color:var(--mut);font-size:11px}
.stats{display:flex;align-items:center;gap:8px;font-size:12px}
.views{font-weight:800;font-size:14px}
.chip{font-size:10px;font-weight:700;padding:2px 7px;border-radius:5px}
.chip.g{background:rgba(74,222,128,.12);color:var(--green)}
.chip.hot{background:rgba(251,146,60,.14);color:var(--hot)}
.chip.new{background:rgba(124,133,240,.14);color:var(--acc)}
.age{color:#565a6e;font-size:11px;margin-left:auto}
.meta{display:flex;gap:10px;color:var(--mut);font-size:11px;border-top:1px solid var(--line);
  padding-top:7px;margin-top:2px}
.meta b{color:#c6c9d8;font-weight:700}
.empty{color:var(--mut);padding:60px 0;text-align:center;font-size:13px}
/* 오른쪽 재생 독: 목록을 보면서 재생, 카드 클릭으로 즉시 전환 */
.dock{position:fixed;top:0;right:0;height:100vh;width:400px;background:#101018;
  border-left:1px solid var(--line);z-index:20;display:none;flex-direction:column;
  padding:14px;gap:10px;box-shadow:-14px 0 44px rgba(0,0,0,.5)}
.dock.on{display:flex}
body.dopen .wrap{margin-right:410px;max-width:none}
/* dclip = 고정 틀(클리핑) · dframe = 슬라이드 전환되는 내용물 */
.dclip{align-self:center;aspect-ratio:9/16;background:#000;border-radius:12px;
  overflow:hidden;width:min(100%,calc((100vh - 210px)*9/16));position:relative}
.dframe{width:100%;height:100%;will-change:transform}
.dframe iframe,.dframe #pframe{width:100%;height:100%;border:0}
.dhint{color:#565a6e;font-size:10px;text-align:center}
/* 모바일 스와이프 레일: 플레이어(iframe)가 터치를 가로채므로
   오른쪽 가장자리에 우리 소유의 터치 띠를 올린다 */
.rail{position:absolute;right:0;top:0;bottom:0;width:56px;z-index:2;display:none;
  flex-direction:column;justify-content:space-between;align-items:center;
  padding:14px 0;color:rgba(255,255,255,.55);font-size:20px;touch-action:none;
  background:linear-gradient(to left,rgba(0,0,0,.28),transparent)}
@media(max-width:900px){
  .rail{display:flex}
  .mbtn{padding:11px 16px;font-size:14px}
}
.dtitle{font-size:13px;font-weight:700;line-height:1.45;max-height:2.9em;overflow:hidden}
.dmeta{color:var(--mut);font-size:12px}
.mrow{display:flex;gap:8px}
.mbtn{background:var(--panel);border:1px solid var(--line);color:var(--txt);
  padding:7px 14px;border-radius:8px;cursor:pointer;font-size:12px;text-decoration:none}
.mbtn:hover{border-color:var(--acc)}
.card.sel{border-color:var(--acc);box-shadow:0 0 0 2px rgba(124,133,240,.35)}
@media(max-width:900px){.dock{width:100vw}body.dopen .wrap{margin-right:0}}
footer{color:#4a4d5e;font-size:11px;padding:0 0 30px}
@media(max-width:600px){.grid{grid-template-columns:repeat(2,1fr);gap:10px}}
</style></head><body><div class="wrap">
<header><h1>📡 ShortsRadar</h1>
<span class="sub">미국 · 영화 숏츠 · 5분 미만 · 갱신 __STAMP__ (6시간 간격)</span></header>

<div class="filters">
  <div class="frow"><span class="flabel">기간</span><span id="wins"></span>
    <span class="count" id="count"></span></div>
  <div class="frow"><span class="flabel">조회수</span><span id="buckets"></span></div>
  <div class="frow"><span class="flabel">정렬</span><span id="sorts"></span></div>
  <div class="frow"><span class="flabel">채널</span><span id="chans"></span>
    <a class="pill edit" href="https://github.com/hikim119/ShortsRadar/edit/main/channels.txt"
       target="_blank">✏️ 관심 채널 편집</a></div>
</div>

<div class="grid" id="grid"></div>
<div class="empty" id="empty" style="display:none">조건에 맞는 숏츠가 없습니다 — 기간을 늘리거나 조회수 구간을 바꿔보세요.</div>

<aside class="dock" id="dock">
  <div class="dclip">
    <div class="dframe" id="dframe"><div id="pframe"></div></div>
    <div class="rail" id="rail"><span>⌃</span><span>⌄</span></div>
  </div>
  <div class="dtitle" id="dtitle"></div>
  <div class="dmeta" id="dmeta"></div>
  <div class="mrow">
    <button class="mbtn" onclick="nav(-1)">▲ 이전</button>
    <button class="mbtn" onclick="nav(1)">▼ 다음</button>
    <a class="mbtn" id="mopen" href="#" target="_blank">YouTube ↗</a>
    <button class="mbtn" onclick="closeM()">닫기</button>
  </div>
  <div class="dhint">휠 · ↑↓ 키 · 모바일은 영상 오른쪽 가장자리 스와이프 &nbsp;|&nbsp; 끝나면 자동 다음 ▶</div>
</aside>
<footer>YouTube Data API · 인기 차트 + 조회수순 검색 (US · Film&nbsp;&amp;&nbsp;Animation) · 증가속도는 수집 간(4h) 조회수 변화 기준</footer>
</div><script>
const DATA=__DATA__, NOW=__NOW__;
const WINS=[[3600,"1시간"],[86400,"24시간"],[604800,"7일"],[1296000,"15일"],[2592000,"30일"]];
const BUCKETS=[[0,Infinity,"전체"],[1e5,5e5,"10만-50만"],[5e5,1e6,"50만-1백만"],
  [1e6,5e6,"1백만-5백만"],[5e6,1e7,"5백만-1천만"],[1e7,Infinity,"1천만+"],
  [1e8,Infinity,"1억+"],[1e9,Infinity,"10억+"]];
const SORTS=[["v","조회수"],["g","🔥 증가속도"],["p","최신"]];
const CHANS=[["전체"],["📌 관심채널"]];
let win=1, bkt=0, srt="v", chn=0;   // 기본: 24시간 · 전체 · 조회수순 · 전체채널

function fmt(n){if(n>=1e8)return (n/1e8).toFixed(1)+"억";
  if(n>=1e4)return (n/1e4).toFixed(1)+"만";return n.toLocaleString();}
function age(p){const d=NOW-p;if(d<3600)return Math.max(1,d/60|0)+"분 전";
  if(d<86400)return (d/3600|0)+"시간 전";if(d<2592000)return (d/86400|0)+"일 전";
  return (d/2592000|0)+"달 전";}
function durTxt(s){if(!s)return"";return (s/60|0)+":"+String(s%60).padStart(2,"0");}

function pills(elId,arr,cur,fn){
  document.getElementById(elId).innerHTML=arr.map((a,i)=>
    `<button class="pill${i===cur?" on":""}" onclick="${fn}(${i})">${a[a.length-1]}</button>`).join("");}

function setWin(i){win=i;render();} function setBkt(i){bkt=i;render();}
function setSrt(i){srt=SORTS[i][0];render();} function setChn(i){chn=i;render();}

function render(){
  pills("wins",WINS,win,"setWin");
  pills("buckets",BUCKETS,bkt,"setBkt");
  pills("sorts",SORTS,SORTS.findIndex(s=>s[0]===srt),"setSrt");
  pills("chans",CHANS,chn,"setChn");
  const [lo,hi]=BUCKETS[bkt];
  let rows=DATA.filter(d=>NOW-d.p<=WINS[win][0]&&d.v>=lo&&d.v<hi);
  if(chn===1)rows=rows.filter(d=>d.s);
  if(srt==="v")rows.sort((a,b)=>b.v-a.v);
  else if(srt==="p")rows.sort((a,b)=>b.p-a.p);
  else rows.sort((a,b)=>(b.g||-1)-(a.g||-1));
  rows=rows.slice(0,120);
  RCUR=rows;   // 재생 독의 이전/다음 순서 = 현재 필터·정렬 결과
  document.getElementById("count").textContent=rows.length+"개";
  document.getElementById("empty").style.display=rows.length?"none":"block";
  document.getElementById("grid").innerHTML=rows.map((d,i)=>{
    const g=d.g==null?"":`<span class="chip ${d.g>=10000?"hot":"g"}">+${fmt(d.g)}/h</span>`;
    const nw=(NOW-d.n)<93600?'<span class="chip new">NEW</span>':"";
    const st=d.s?'<span class="chip new">📌</span>':"";
    const meta=[];
    if(d.w!=null)meta.push(`7일 조회 <b>${fmt(d.w)}</b>`);
    if(d.l!=null)meta.push(`좋아요 <b>${d.l}%</b>`);
    const metaHtml=meta.length?`<div class="meta">${meta.join("<span>·</span>")}</div>`:"";
    return `<a class="card" id="c-${d.i}" href="https://www.youtube.com/shorts/${d.i}" target="_blank"
        onclick="return play(event,'${d.i}')">
      <div class="th"><img loading="lazy" src="https://i.ytimg.com/vi/${d.i}/hqdefault.jpg">
        <span class="rank">${i+1}</span><span class="dur">${durTxt(d.d)}</span></div>
      <div class="body"><div class="title">${d.t.replace(/</g,"&lt;")}</div>
        <div class="ch">${d.c.replace(/</g,"&lt;")}</div>
        <div class="stats"><span class="views">${fmt(d.v)}</span>${g}${nw}${st}
          <span class="age">${age(d.p)}</span></div>
        ${metaHtml}</div></a>`;}).join("");
}
// ── 오른쪽 재생 독 (숏츠식 연속 재생) ──
// 클릭 = 독에서 재생 · 휠/↑↓/버튼 = 이전·다음 · 영상 끝 = 자동 다음
// Ctrl·휠클릭 = 유튜브 새 탭
let RCUR=[], ytp=null, pendingId=null, curIdx=-1, navT=0;
// file://(로컬 미리보기)나 API 로드 실패(광고차단 등) 시 → 단순 iframe 폴백
let apiDead=(location.protocol==="file:");

function plainEmbed(id){
  document.getElementById("pframe").innerHTML=
    '<iframe src="https://www.youtube.com/embed/'+id+
    '?autoplay=1&rel=0&playsinline=1" allow="autoplay; encrypted-media" '+
    'allowfullscreen style="width:100%;height:100%;border:0"></iframe>';
}
function loadYT(){
  if(loadYT.called)return; loadYT.called=true;
  if(window.YT&&YT.Player){onYTReady();return;}
  const s=document.createElement("script");
  s.src="https://www.youtube.com/iframe_api";
  s.onerror=()=>{apiDead=true;if(pendingId)plainEmbed(pendingId);};
  document.head.appendChild(s);
  setTimeout(()=>{   // 2초 내 API 안 뜨면 폴백 (자동다음만 비활성, 재생은 보장)
    if(!ytp&&!apiDead){apiDead=true;if(pendingId)plainEmbed(pendingId);}
  },2000);
}
window.onYouTubeIframeAPIReady=function(){onYTReady();};
function onYTReady(){
  if(ytp||apiDead)return;
  ytp=new YT.Player("pframe",{width:"100%",height:"100%",videoId:pendingId,
    playerVars:{autoplay:1,rel:0,playsinline:1,origin:location.origin},
    events:{onReady:e=>e.target.playVideo(),               // 자동재생 확실히
            onStateChange:e=>{if(e.data===0)nav(1);},      // 끝나면 자동 다음
            onError:e=>{                                    // 오류 153 등 → 자동 폴백
              if(e.data===101||e.data===150){nav(1);return;} // 임베드 금지 영상 → 스킵
              apiDead=true;
              try{ytp.destroy();}catch(_){}
              ytp=null;
              if(pendingId)plainEmbed(pendingId);
            }}});
}
function play(e,id){
  if(e&&(e.ctrlKey||e.metaKey||e.button===1))return true;
  if(e)e.preventDefault();
  curIdx=RCUR.findIndex(x=>x.i===id);
  openDock(id);
  return false;
}
function openDock(id){
  const d=RCUR[curIdx]&&RCUR[curIdx].i===id?RCUR[curIdx]:DATA.find(x=>x.i===id);
  document.getElementById("dtitle").textContent=d?d.t:"";
  document.getElementById("dmeta").textContent=
    d?`${curIdx+1}/${RCUR.length} · ${d.c} · ${fmt(d.v)} 조회 · ${age(d.p)}`:"";
  document.getElementById("mopen").href="https://www.youtube.com/shorts/"+id;
  document.getElementById("dock").classList.add("on");
  document.body.classList.add("dopen");
  document.querySelectorAll(".card.sel").forEach(c=>c.classList.remove("sel"));
  const el=document.getElementById("c-"+id);
  if(el){el.classList.add("sel");el.scrollIntoView({block:"nearest",behavior:"smooth"});}
  pendingId=id;
  if(apiDead)plainEmbed(id);
  else if(ytp)ytp.loadVideoById(id);
  else loadYT();
}
function nav(dir){
  const t=Date.now(); if(t-navT<420)return; navT=t;   // 과속 방지(애니메이션 길이만큼)
  if(!RCUR.length||curIdx<0)return;
  const n=curIdx+dir;
  if(n<0||n>=RCUR.length){bounce(dir);return;}        // 끝이면 살짝 튕김
  curIdx=n;
  // 숏츠식 슬라이드: 현재 영상이 위(다음)/아래(이전)로 밀려나가고 새 영상이 반대편에서 진입
  const f=document.getElementById("dframe");
  f.style.transition="transform .16s ease-in,opacity .16s";
  f.style.transform=`translateY(${-dir*106}%)`;
  f.style.opacity=".3";
  setTimeout(()=>{
    openDock(RCUR[n].i);
    f.style.transition="none";
    f.style.transform=`translateY(${dir*106}%)`;
    void f.offsetHeight;   // reflow — 순간이동을 확정한 뒤 슬라이드 인
    f.style.transition="transform .22s ease-out,opacity .22s";
    f.style.transform="translateY(0)";
    f.style.opacity="1";
  },165);
}
function bounce(dir){   // 목록 끝 피드백
  const f=document.getElementById("dframe");
  f.style.transition="transform .12s ease-out";
  f.style.transform=`translateY(${-dir*4}%)`;
  setTimeout(()=>{f.style.transform="translateY(0)";},120);
}
function closeM(){
  document.getElementById("dock").classList.remove("on");
  document.body.classList.remove("dopen");
  if(ytp)ytp.stopVideo();
  else document.getElementById("pframe").innerHTML="";   // 폴백 모드: iframe 제거로 정지
  document.querySelectorAll(".card.sel").forEach(c=>c.classList.remove("sel"));
}
document.addEventListener("keydown",e=>{
  if(e.key==="Escape"){closeM();return;}
  if(!document.getElementById("dock").classList.contains("on"))return;
  if(e.key==="ArrowDown"){e.preventDefault();nav(1);}
  if(e.key==="ArrowUp"){e.preventDefault();nav(-1);}
});
// 독 위에서 휠 = 이전/다음 (플레이어 화면 밖 영역)
document.getElementById("dock").addEventListener("wheel",e=>{
  e.preventDefault();nav(e.deltaY>0?1:-1);},{passive:false});
// 터치 스와이프 (모바일)
let tY=null;
document.getElementById("dock").addEventListener("touchstart",e=>{tY=e.touches[0].clientY;});
document.getElementById("dock").addEventListener("touchend",e=>{
  if(tY===null)return;
  const dy=tY-e.changedTouches[0].clientY;
  if(Math.abs(dy)>60)nav(dy>0?1:-1);
  tY=null;});
document.getElementById("rail").addEventListener("touchmove",
  e=>e.preventDefault(),{passive:false});   // iOS: 레일 위 스와이프가 스크롤로 새는 것 방지
render();
</script></body></html>"""


def build_html(hist, now):
    data = []
    for vid, r in hist.items():
        if not r.get("snapshots"):
            continue
        g = growth_per_hour(r)
        try:
            p = int(datetime.fromisoformat(r["publishedAt"].replace("Z", "+00:00")).timestamp())
            n = int(datetime.fromisoformat(r["first_seen"]).timestamp())
        except (ValueError, KeyError):
            continue
        v_now = r["snapshots"][-1]["views"]
        likes = r.get("likes")
        w7 = views_7d(r, now)
        data.append({"i": vid, "t": r["title"], "c": r["channel"],
                     "p": p, "n": n, "v": v_now,
                     "g": int(g) if g is not None else None,
                     "d": r.get("dur"),
                     "w": int(w7) if w7 is not None else None,
                     "l": round(likes / v_now * 100, 1) if (likes and v_now) else None,
                     "s": 1 if r.get("ch") else 0})
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    stamp = now.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")
    return (TEMPLATE.replace("__DATA__", payload)
                    .replace("__NOW__", str(int(now.timestamp())))
                    .replace("__STAMP__", stamp))


# ── mock ─────────────────────────────────────────────────────────────────────

def mock_items():
    """--mock: API 없이 렌더 테스트용 가짜 데이터 (기간·조회수 버킷 골고루)."""
    now = datetime.now(timezone.utc)
    views = [2_100_000_000, 150_000_000, 42_000_000, 9_500_000, 7_200_000,
             3_800_000, 1_400_000, 820_000, 640_000, 310_000, 150_000, 98_000,
             12_000_000, 25_000_000, 480_000, 5_600_000]
    ago_h = [2000, 700, 300, 200, 100, 60, 40, 30, 20, 10, 5, 0.5, 400, 24, 3, 48]
    out = []
    for i, (v, h) in enumerate(zip(views, ago_h)):
        out.append({
            "id": f"MOCK{i:03d}xxxxx",
            "snippet": {
                "title": f"[MOCK #{i + 1}] Epic Movie Scene — unbelievable ending ({fmt_num(v)})",
                "channelTitle": f"MockStudio{i % 5}",
                "publishedAt": (now - timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "categoryId": CATEGORY_ID,
                "thumbnails": {"high": {"url": ""}},
            },
            "contentDetails": {"duration": f"PT{1 + i % 4}M{(i * 13) % 60}S"},
            "statistics": {"viewCount": str(v), "likeCount": str(int(v * (0.008 + i * 0.002)))},
        })
    return out


def fmt_num(n):
    return f"{n:,}"


# ── 메인 ─────────────────────────────────────────────────────────────────────

def collect(key, now):
    """API 수집 전체. 반환: (items, tracked_ids)."""
    chart_items = fetch_popular(key)
    print(f"인기 차트 {len(chart_items)}개")
    # SEARCH_PLAN에 따라 검색 수집 (5분 초과는 아래 필터로 탈락)
    pool = []
    for query, cat, win, pages, dur in SEARCH_PLAN:
        after = (now - timedelta(**_WIN[win])).strftime("%Y-%m-%dT%H:%M:%SZ")
        got = search_short_ids(key, after, pages, dur, query=query, category=cat)
        label = query or f"cat{cat}"
        print(f"  검색[{label} · {win} · {dur}] {len(got)}개")
        pool += got
    # 📌 관심 채널: channels.txt의 채널은 최근 업로드를 무조건 수집
    tracked_ids = set()
    for ref in load_channel_refs():
        cid = resolve_channel_id(key, ref)
        if not cid:
            print(f"  ⚠ 채널 못 찾음: {ref}")
            continue
        vids = fetch_channel_video_ids(key, cid)
        tracked_ids.update(vids)
        pool += vids
        print(f"  📌 관심채널 [{ref}] 최신 {len(vids)}개")
    known = {v["id"] for v in chart_items}
    extra_ids = [i for i in dict.fromkeys(pool) if i not in known]
    extra_items = fetch_details(key, extra_ids)
    print(f"검색 수집 합계 +{len(extra_items)}개 (중복 제거 후)")
    return chart_items + extra_items, tracked_ids


def main():
    mock = "--mock" in sys.argv
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    if mock:
        items = mock_items()
        tracked_ids = {items[0]["id"], items[3]["id"]}   # 관심채널 배지 렌더 테스트
        print("MOCK 모드 (API 호출 없음)")
    else:
        key = os.environ.get("YT_API_KEY", "").strip()
        if not key:
            print("환경변수 YT_API_KEY가 없습니다.", file=sys.stderr)
            sys.exit(1)
        try:
            items, tracked_ids = collect(key, now)
        except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError) as e:
            # 쿼터 초과 등 — 수집은 포기하되 기존 기록으로 페이지는 재생성
            # (UI 수정사항이 데이터 수집 실패에 막혀 배포 안 되는 일 방지)
            print(f"⚠ 수집 실패({e}) — 기존 기록으로 페이지만 재생성합니다")
            items, tracked_ids = [], set()

    shorts = [v for v in items if dur_seconds(v["contentDetails"]["duration"]) <= MAX_DUR_S]
    print(f"숏츠({MAX_DUR_S}s 이하) 총 {len(shorts)}개")

    hist = load_history()
    hist = update_history(hist, shorts, now_iso)
    for vid in tracked_ids:
        if vid in hist:
            hist[vid]["ch"] = True    # 📌 관심채널 표시
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(json.dumps(hist, ensure_ascii=False), encoding="utf-8")

    (DOCS / "index.html").write_text(build_html(hist, now), encoding="utf-8")
    print(f"완료: docs/index.html (풀 {len(hist)}개 영상)")


if __name__ == "__main__":
    main()
