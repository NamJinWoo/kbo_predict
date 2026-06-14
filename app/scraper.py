import re
import requests
from datetime import date, datetime, timedelta
from typing import Optional

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": "https://statiz.co.kr/",
}

KBO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": "https://www.koreabaseball.com/",
    "Content-Type": "application/x-www-form-urlencoded",
}

TEAM_CODE_MAP = {
    "1001": "삼성", "2002": "KIA", "3001": "롯데", "5002": "LG",
    "6002": "두산", "7002": "한화", "9002": "SSG", "10001": "키움",
    "11001": "NC",  "12001": "KT",
}

# KBO 공식 API 팀 ID (gameId 생성에 사용)
TEAM_KBO_ID = {
    "LG":   "LG",  "롯데": "LT",  "KIA":  "HT",  "두산": "OB",
    "삼성": "SS",  "한화": "HH",  "SSG":  "SK",  "KT":   "KT",
    "NC":   "NC",  "키움": "WO",
}

STADIUM_MAP = {
    "삼성": "대구 라이온즈파크",
    "KIA":  "광주 챔피언스필드",
    "롯데": "부산 사직구장",
    "LG":   "서울 잠실구장",
    "두산": "서울 잠실구장",
    "한화": "대전 한화생명이글스파크",
    "SSG":  "인천 SSG 랜더스필드",
    "키움": "서울 고척 스카이돔",
    "NC":   "창원 NC파크",
    "KT":   "수원 KT 위즈파크",
}


def _clean(html_str: str) -> str:
    return re.sub(r"<[^>]+>", "", html_str).strip()


def scrape_standings() -> list[dict]:
    """홈페이지에서 팀 순위표 파싱 (공개)"""
    resp = requests.get("https://statiz.co.kr/", headers=HEADERS, timeout=10)
    resp.raise_for_status()
    html = resp.text

    # 순위 테이블 tbody 추출
    m = re.search(r"<th>승률</th>.*?</thead>\s*<tbody>(.*?)</tbody>", html, re.DOTALL)
    if not m:
        return []

    rows = re.findall(r"<tr>(.*?)</tr>", m.group(1), re.DOTALL)
    standings = []
    for row in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
        if len(cells) < 10:
            continue
        team_m = re.search(r"t_code=(\d+)", cells[1])
        team_name = _clean(cells[1])
        team_name = re.sub(r"\s+", "", team_name)
        standings.append({
            "rank":       int(_clean(cells[0])),
            "team":       team_name,
            "team_code":  team_m.group(1) if team_m else "",
            "games":      int(_clean(cells[2])),
            "wins":       int(_clean(cells[3]).replace("\n", "")),
            "draws":      int(_clean(cells[4])),
            "losses":     int(_clean(cells[5])),
            "gb":         float(_clean(cells[6])),
            "win_pct":    float(_clean(cells[7])),
            "runs_scored":int(_clean(cells[8])),
            "runs_allowed":int(_clean(cells[9])),
        })
    return standings


def scrape_today_games() -> list[dict]:
    """prediction 페이지에서 오늘 경기 + 선발 투수 스탯 파싱"""
    resp = requests.get("https://statiz.co.kr/prediction/?m=main", headers=HEADERS, timeout=10)
    resp.raise_for_status()
    html = resp.text

    games = []

    # 오늘 날짜 추출 (day 클래스에서)
    date_m = re.search(r'class="day"[^>]*>\s*(\d{4}\.\d{2}\.\d{2})', html)
    game_date = date.today()
    if date_m:
        try:
            game_date = datetime.strptime(date_m.group(1), "%Y.%m.%d").date()
        except ValueError:
            pass

    # 각 경기 블록 파싱 (team_logo 두 개 + 선발 투수 비교)
    # 팀 코드에서 팀명 추출
    team_codes = re.findall(r"t_code=(\d+)&year=\d+", html)
    team_names_in_order = [TEAM_CODE_MAP.get(c, c) for c in team_codes]

    # g_info 블록 (경기 단위)
    game_blocks = re.findall(r'<div class="g_info">(.*?)</div>\s*</div>\s*</div>', html, re.DOTALL)

    # 선발 투수 이름 추출 (name div)
    pitcher_names = re.findall(r'<div class="name">(.*?)</div>', html)

    # 레코드 박스 파싱 (좌/우 선발 스탯)
    record_blocks = re.findall(
        r'<ul>\s*(<li class="value">.*?</ul>)\s*<ul>\s*(<li class="value">.*?</ul>)',
        html, re.DOTALL
    )

    # 경기 단위로 팀 쌍 추출
    matchup_blocks = re.findall(
        r'<div class="t_info">(.*?)</div>\s*<span class="vs"',
        html, re.DOTALL
    )

    # 팀 정보 블록 파싱
    team_blocks = re.findall(
        r'<div class="t_name">\s*<a href="[^"]*t_code=(\d+)[^"]*"[^>]*>(.*?)</a>',
        html, re.DOTALL
    )

    # 경기별로 팀 2개씩 묶기
    i = 0
    pitcher_idx = 0
    while i + 1 < len(team_blocks):
        away_code, away_raw = team_blocks[i]
        home_code, home_raw = team_blocks[i + 1]
        away_team = re.sub(r"\s+", "", _clean(away_raw))
        home_team = re.sub(r"\s+", "", _clean(home_raw))

        game = {
            "game_date": game_date,
            "away_team": away_team,
            "home_team": home_team,
            "away_pitcher": pitcher_names[pitcher_idx] if pitcher_idx < len(pitcher_names) else "",
            "home_pitcher": pitcher_names[pitcher_idx + 1] if pitcher_idx + 1 < len(pitcher_names) else "",
            "stats": {},
        }

        # 선발 투수 스탯 파싱
        if pitcher_idx // 2 < len(record_blocks):
            away_vals, home_vals = record_blocks[pitcher_idx // 2]
            game["stats"]["away"] = _parse_pitcher_stats(away_vals)
            game["stats"]["home"] = _parse_pitcher_stats(home_vals)

        games.append(game)
        i += 2
        pitcher_idx += 2

    return games


def _parse_pitcher_stats(ul_html: str) -> dict:
    """<ul> 내 li.value + li.label 쌍에서 스탯 딕셔너리 추출"""
    labels = re.findall(r'class="label[^"]*"[^>]*>(.*?)</li>', ul_html, re.DOTALL)
    values = re.findall(r'class="value[^"]*"[^>]*>(.*?)</li>', ul_html, re.DOTALL)
    stats = {}
    for label, value in zip(labels, values):
        k = _clean(label)
        v = _clean(value)
        if k and v:
            stats[k] = v
    return stats


def scrape_kbo_teamrank() -> list[dict]:
    """KBO 공식 사이트 팀 순위 페이지 파싱 (최근10경기, 연속, 홈/원정 전적 포함)"""
    resp = requests.get(
        "https://www.koreabaseball.com/Record/TeamRank/TeamRank.aspx",
        headers={**HEADERS, "Referer": "https://www.koreabaseball.com/"},
        timeout=10,
    )
    resp.raise_for_status()
    html = resp.text

    # 팀명이 있는 테이블 찾기
    idx = html.find("삼성")
    if idx < 0:
        return []

    table_start = html.rfind("<table", 0, idx)
    table_end = html.find("</table>", idx) + len("</table>")
    table = html[table_start:table_end]

    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.DOTALL)
    results = []
    for row in rows:
        cells = [
            re.sub(r"<[^>]+>", "", c).strip()
            for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
        ]
        cells = [c for c in cells if c]
        if len(cells) < 10:
            continue
        try:
            results.append({
                "rank":        int(cells[0]),
                "team":        cells[1],
                "games":       int(cells[2]),
                "wins":        int(cells[3]),
                "losses":      int(cells[4]),
                "draws":       int(cells[5]),
                "win_pct":     float(cells[6]),
                "gb":          float(cells[7]),
                "last10":      cells[8],   # "6승0무4패"
                "streak":      cells[9],   # "3승" / "7패"
                "home_record": cells[10] if len(cells) > 10 else "",
                "away_record": cells[11] if len(cells) > 11 else "",
            })
        except (ValueError, IndexError):
            continue
    return results


def _parse_game_page() -> tuple:
    """
    statiz ?page=game 한 번 요청으로 완료경기 + 예정경기 동시 파싱.
    반환: (recent_list, upcoming_list)
    - 완료경기: span에서 실제 날짜(MM.DD) 추출
    - 예정경기: span에서 구장명 추출, 날짜는 완료경기 날 기준 추론
    """
    resp = requests.get("https://statiz.co.kr/?page=game", headers=HEADERS, timeout=10)
    resp.raise_for_status()
    html = resp.text

    blocks = re.findall(
        r'<div class="vsbox">(.*?)</div>\s*<div class="v_info">(.*?)</div>',
        html, re.DOTALL
    )

    today = date.today()
    recent = []
    upcoming = []
    completed_date = None  # 완료경기의 실제 날짜

    for vsbox, vinfo in blocks:
        vclean = re.sub(r'&nbsp;', ' ', re.sub(r'<[^>]+>', '', vinfo)).strip()
        span_m = re.search(r'<span[^>]*>(.*?)</span>', vsbox, re.DOTALL)
        span_text = re.sub(r'<[^>]+>', '', span_m.group(1)).strip() if span_m else ''

        # ── 완료경기: span = MM.DD, vinfo에 W 포함 ──
        if re.search(r'\bW\b', vclean):
            p_tags = re.findall(r'<p[^>]*>(.*?)</p>', vsbox, re.DOTALL)
            if len(p_tags) < 2:
                continue
            away_text = re.sub(r'<[^>]+>', '', p_tags[0]).strip()
            home_text = re.sub(r'<[^>]+>', '', p_tags[1]).strip()
            away_m = re.match(r'(.+?)\s+(\d+)$', away_text)
            home_m = re.match(r'^(\d+)\s+(.+)', home_text)
            if not away_m or not home_m:
                continue

            # span에서 실제 날짜 추출 (MM.DD)
            date_span = re.match(r'(\d{2})\.(\d{2})', span_text)
            if date_span:
                mm, dd = int(date_span.group(1)), int(date_span.group(2))
                game_date = date(today.year, mm, dd)
            else:
                game_date = today - timedelta(days=1)
            if completed_date is None or game_date > completed_date:
                completed_date = game_date

            def _pick(tag):
                m = re.search(rf'\b{tag}\b\s+(\S+)', vclean)
                return m.group(1) if m else ""

            recent.append({
                "game_date":    game_date,
                "away_team":    away_m.group(1).strip(),
                "home_team":    home_m.group(2).strip(),
                "away_score":   int(away_m.group(2)),
                "home_score":   int(home_m.group(1)),
                "win_pitcher":  _pick("W"),
                "lose_pitcher": _pick("L"),
                "save_pitcher": _pick("S"),
                "hold_pitcher": _pick("H"),
                "stadium":      STADIUM_MAP.get(home_m.group(2).strip(), ""),
            })

        # ── 예정경기: span = 구장명, vinfo에 시간 포함 ──
        elif re.search(r'\d{2}:\d{2}', vclean):
            team_links = re.findall(
                r'<a href="[^"]*t_code=(\d+)[^"]*"[^>]*>(.*?)</a>',
                vsbox, re.DOTALL
            )
            if len(team_links) < 2:
                continue
            away_team = re.sub(r'\s+', '', _clean(team_links[0][1]))
            home_team = re.sub(r'\s+', '', _clean(team_links[1][1]))
            stadium   = span_text if span_text else STADIUM_MAP.get(home_team, '')

            upcoming.append({
                "away_team":    away_team,
                "home_team":    home_team,
                "away_pitcher": "",
                "home_pitcher": "",
                "stadium":      stadium,
                "stats":        {},
            })

    # 예정경기 날짜 추론: 완료경기 날짜가 오늘이면 다음경기는 내일, 아니면 오늘
    if completed_date is not None:
        upcoming_date = today + timedelta(days=1) if completed_date >= today else today
    else:
        upcoming_date = today
    for g in upcoming:
        g["game_date"] = upcoming_date

    return recent, upcoming


def scrape_recent_results() -> list[dict]:
    """최근 완료 경기 결과 반환"""
    recent, _ = _parse_game_page()
    return recent


def scrape_upcoming_games() -> list[dict]:
    """다음 예정 경기 목록 반환"""
    _, upcoming = _parse_game_page()
    return upcoming


def scrape_results_for_date(target_date: date) -> list[dict]:
    """KBO 공식 API로 특정 날짜 완료 경기 결과 조회.
    statiz 홈에서 보이지 않는 과거 날짜 백필에 사용.
    """
    date_str = target_date.strftime("%Y%m%d")
    try:
        resp = requests.post(
            "https://www.koreabaseball.com/ws/Main.asmx/GetKboGameList",
            headers=KBO_HEADERS, timeout=10,
            data=f"date={date_str}&leId=1&srId=0",
        )
        resp.raise_for_status()
        games = resp.json().get("game", [])
    except Exception:
        return []

    results = []
    for g in games:
        if str(g.get("G_DT", "")) != date_str:
            continue
        if str(g.get("GAME_RESULT_CK", "0")) != "1":
            continue  # 완료 경기만
        away_team = (g.get("AWAY_NM") or "").strip()
        home_team = (g.get("HOME_NM") or "").strip()
        if not away_team or not home_team:
            continue
        try:
            away_score = int(g.get("T_SCORE_CN") or 0)
            home_score = int(g.get("B_SCORE_CN") or 0)
        except (ValueError, TypeError):
            continue
        results.append({
            "game_date":    target_date,
            "away_team":    away_team,
            "home_team":    home_team,
            "away_score":   away_score,
            "home_score":   home_score,
            "win_pitcher":  (g.get("W_PIT_P_NM") or "").strip(),
            "lose_pitcher": (g.get("L_PIT_P_NM") or "").strip(),
            "save_pitcher": (g.get("SV_PIT_P_NM") or "").strip(),
            "hold_pitcher": "",
            "stadium":      (g.get("S_NM") or "").strip(),
        })
    return results


def backfill_missing_dates(existing_dates: set, lookback_days: int = 30) -> list[dict]:
    """최근 lookback_days일 중 existing_dates에 없는 날짜를 KBO API로 채움."""
    today = date.today()
    all_results = []
    for i in range(1, lookback_days + 1):
        d = today - timedelta(days=i)
        if d in existing_dates:
            continue
        day_results = scrape_results_for_date(d)
        if day_results:
            all_results.extend(day_results)
    return all_results


def _parse_ul_blocks(html: str) -> list[dict]:
    """Parse all <ul> blocks into structured label/value dicts."""
    ul_blocks = re.findall(r"<ul[^>]*>(.*?)</ul>", html, re.DOTALL)
    parsed = []
    for ul in ul_blocks:
        items = re.findall(r'<li[^>]*class="([^"]+)"[^>]*>(.*?)</li>', ul, re.DOTALL)
        block = {"values": [], "label": "", "away": None, "home": None}
        for cls, val in items:
            cls = cls.strip()
            clean = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", val).strip())
            if "label" in cls:
                block["label"] = clean
            elif "value" in cls:
                block["values"].append(clean)
            elif cls == "away":
                block["away"] = clean
            elif cls == "home":
                block["home"] = clean
        if block["label"] or block["values"] or block["away"]:
            parsed.append(block)
    return parsed


def _extract_float(s: str) -> Optional[float]:
    m = re.search(r"[\d.]+", s or "")
    return float(m.group()) if m else None


def _parse_prediction_full_stats(html: str) -> dict:
    """
    Extract comprehensive stats from a prediction game page.
    Returns dict stored as stats_json in TodayGame.
    """
    blocks = _parse_ul_blocks(html)

    # ── 선발 투수 시즌 스탯 (UL 12~19, label/value pairs) ──
    pitcher_stat_labels = {"경기이닝", "승패", "평균자책", "WHIP", "피안타율", "탈삼진", "볼넷", "WAR"}
    away_stats: dict = {}
    home_stats: dict = {}
    h2h_labels = {"상대전적", "상대 평균자책", "상대 OOPS", "상대 WHIP"}
    away_h2h: dict = {}
    home_h2h: dict = {}
    team_comp_away: dict = {}
    team_comp_home: dict = {}

    for b in blocks:
        lbl = b["label"]
        vals = b["values"]
        if not lbl:
            continue

        if lbl in pitcher_stat_labels and len(vals) >= 2:
            away_stats[lbl] = vals[0]
            home_stats[lbl] = vals[1]
        elif lbl in h2h_labels and len(vals) >= 2:
            away_h2h[lbl] = vals[0]
            home_h2h[lbl] = vals[1]
        elif b["away"] is not None and b["home"] is not None:
            # Team comparison blocks (away/home class)
            av = re.sub(r"\d+위\s*", "", b["away"]).strip()
            hv = re.sub(r"\d+위\s*", "", b["home"]).strip()
            team_comp_away[lbl] = av
            team_comp_home[lbl] = hv

    # ── 위험 타자 (player_box hitter) ──
    # Block 0 = away team batters vs home pitcher (원정 타자 vs 홈 선발)
    # Block 1 = home team batters vs away pitcher (홈 타자 vs 원정 선발)
    pb_hitter_blocks = re.findall(
        r'<div class="player_box hitter">(.*?)(?=<div class="player_box hitter"|</section>)',
        html, re.DOTALL
    )

    def _parse_batters(section: str) -> list[dict]:
        batters = []
        name_blocks = re.findall(
            r'</a>\s*<div class="name">(.*?)</div>\s*<div class="name">OPS\s*:\s*([\d.]+)</div>',
            section, re.DOTALL
        )
        for name_raw, ops_str in name_blocks:
            name = re.sub(r"<[^>]+>", "", name_raw).strip()
            try:
                ops = float(ops_str)
            except ValueError:
                ops = 0.0
            if name:
                batters.append({"name": name, "ops": ops})
        return batters

    vs_home_pitcher = _parse_batters(pb_hitter_blocks[0]) if len(pb_hitter_blocks) > 0 else []
    vs_away_pitcher = _parse_batters(pb_hitter_blocks[1]) if len(pb_hitter_blocks) > 1 else []

    return {
        "away": away_stats,
        "home": home_stats,
        "h2h": {"away": away_h2h, "home": home_h2h},
        "team_comp": {"away": team_comp_away, "home": team_comp_home},
        "dangerous_batters": {
            "vs_away_pitcher": vs_away_pitcher,
            "vs_home_pitcher": vs_home_pitcher,
        },
    }


def _kbo_pitchers_for_date(target_date: date) -> dict:
    """KBO 공식 API에서 선발투수 이름 조회.
    Returns {(away_team, home_team): (away_pitcher, home_pitcher)}.
    """
    date_str = target_date.strftime("%Y%m%d")
    try:
        resp = requests.post(
            "https://www.koreabaseball.com/ws/Main.asmx/GetKboGameList",
            headers=KBO_HEADERS, timeout=10,
            data=f"date={date_str}&leId=1&srId=0",
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return {}
    result = {}
    for g in data.get("game", []):
        if str(g.get("G_DT", "")) != date_str:
            continue
        away_team = (g.get("AWAY_NM") or "").strip()
        home_team = (g.get("HOME_NM") or "").strip()
        away_p = (g.get("T_PIT_P_NM") or "").strip()
        home_p = (g.get("B_PIT_P_NM") or "").strip()
        if away_team and home_team:
            result[(away_team, home_team)] = (away_p, home_p)
    return result


def _scrape_pitchers_for_date(target_date: date) -> dict:
    """
    Fetch starting pitchers + full game stats for all games on target_date.
    Returns {(away_team, home_team): {"away_pitcher": str, "home_pitcher": str, "stats": dict}}.
    Pitcher names from KBO official API; stats from statiz individual prediction pages.
    """
    # Pitcher names: KBO API (reliable for both today and upcoming games)
    kbo_pitchers = _kbo_pitchers_for_date(target_date)

    date_str = target_date.strftime("%Y%m%d")
    resp = requests.get(
        f"https://statiz.co.kr/prediction/?m=main&g_date={date_str}",
        headers=HEADERS, timeout=10,
    )
    resp.raise_for_status()
    html = resp.text

    s_nos = re.findall(r"s_no=(\d+)", html)
    result = {}
    for s_no in s_nos:
        pos = html.find(f"s_no={s_no}")
        if pos < 0:
            continue
        snippet = html[pos : pos + 800]
        codes = re.findall(r"/(\d{4,5})\.(?:svg|png)", snippet)
        if len(codes) < 2:
            continue
        away_team = TEAM_CODE_MAP.get(codes[0], "")
        home_team = TEAM_CODE_MAP.get(codes[1], "")
        if not away_team or not home_team:
            continue
        kbo_names = kbo_pitchers.get((away_team, home_team), ("", ""))
        try:
            p_resp = requests.get(
                f"https://statiz.co.kr/prediction/?s_no={s_no}",
                headers=HEADERS, timeout=10,
            )
            p_resp.raise_for_status()
            p_html = p_resp.text
            full_stats = _parse_prediction_full_stats(p_html)
            # Pitcher names: prefer statiz page (has stats), fallback to KBO API
            statiz_names = [
                n.strip() for n in re.findall(r'<div class="name">(.*?)</div>', p_html)
                if 'OPS' not in n and ':' not in n and not re.search(r'^\s*[\d.]+\s*$', n)
            ]
            # Only trust statiz names if pitcher stats are present (확인된 선발)
            has_pitcher_stats = bool(full_stats.get("away") and full_stats["away"].get("평균자책"))
            if has_pitcher_stats and len(statiz_names) >= 2:
                away_p = statiz_names[0]
                home_p = statiz_names[1]
            else:
                away_p = kbo_names[0]
                home_p = kbo_names[1]
            result[(away_team, home_team)] = {
                "away_pitcher": away_p,
                "home_pitcher": home_p,
                "stats": full_stats,
            }
        except Exception:
            result[(away_team, home_team)] = {
                "away_pitcher": kbo_names[0],
                "home_pitcher": kbo_names[1],
                "stats": {},
            }
    # Add any KBO games not found via statiz
    for (away_team, home_team), (away_p, home_p) in kbo_pitchers.items():
        if (away_team, home_team) not in result:
            result[(away_team, home_team)] = {
                "away_pitcher": away_p,
                "home_pitcher": home_p,
                "stats": {},
            }
    return result


def scrape_all() -> dict:
    """statiz + KBO 공식 데이터 통합 스크래핑"""
    statiz = scrape_standings()
    kbo    = scrape_kbo_teamrank()
    kbo_map = {r["team"]: r for r in kbo}
    merged = []
    for s in statiz:
        k = kbo_map.get(s["team"], {})
        merged.append({**s, **{
            "last10":      k.get("last10", ""),
            "streak":      k.get("streak", ""),
            "home_record": k.get("home_record", ""),
            "away_record": k.get("away_record", ""),
        }})

    # 최근 완료 경기 + 다음 예정 경기 (한 번에)
    try:
        recent_results, upcoming = _parse_game_page()
    except Exception:
        recent_results, upcoming = [], []

    # 선발 투수 + 전체 예측 스탯 보강
    if upcoming:
        try:
            enriched = _scrape_pitchers_for_date(upcoming[0]["game_date"])
            for g in upcoming:
                key = (g["away_team"], g["home_team"])
                if key in enriched:
                    d = enriched[key]
                    g["away_pitcher"] = d["away_pitcher"]
                    g["home_pitcher"] = d["home_pitcher"]
                    g["stats"] = d["stats"]
        except Exception:
            pass

    return {
        "scraped_at":     datetime.utcnow().isoformat(),
        "standings":      merged,
        "today_games":    upcoming,
        "recent_results": recent_results,
        "backfill_needed": True,  # routes에서 누락 날짜 백필 실행
    }


# ── KBO 공식 박스스코어 ─────────────────────────────────────────

def _kbo_game_id(game_date: date, away_team: str, home_team: str) -> str:
    away_id = TEAM_KBO_ID.get(away_team, "")
    home_id = TEAM_KBO_ID.get(home_team, "")
    return f"{game_date.strftime('%Y%m%d')}{away_id}{home_id}0"


def _parse_box_table(table: dict) -> tuple[list, list]:
    """KBO GetBoxScore 테이블 → (headers, rows) 텍스트 리스트"""
    headers = [
        c.get("Text", "").replace("&nbsp;", "").strip()
        for row in table.get("headers", [])
        for c in row.get("row", [])
    ]
    rows = []
    for row in table.get("rows", []):
        cells = [c.get("Text", "").replace("&nbsp;", "").strip() for c in row.get("row", [])]
        rows.append(cells)
    tfoot = []
    for row in table.get("tfoot", []):
        cells = [c.get("Text", "").replace("&nbsp;", "").strip() for c in row.get("row", [])]
        tfoot.append(cells)
    return headers, rows, tfoot


_pid_name_cache: dict = {}


def _resolve_player_name(pid: str) -> str:
    """Resolve KBO player ID to name via detail page, with in-memory cache."""
    if pid in _pid_name_cache:
        return _pid_name_cache[pid]
    try:
        resp = requests.get(
            f"https://www.koreabaseball.com/Record/Player/PitcherDetail/Basic.aspx?playerId={pid}",
            headers={**KBO_HEADERS, "Content-Type": "text/html"},
            timeout=5,
        )
        resp.encoding = "utf-8"
        m = re.search(r"lblName[^>]*>([^<]+)", resp.text)
        if m:
            name = m.group(1).strip()
            _pid_name_cache[pid] = name
            return name
    except Exception:
        pass
    return pid


def scrape_game_boxscore(game_date: date, away_team: str, home_team: str) -> dict:
    """
    KBO 공식 API에서 경기 박스스코어를 가져온다.
    반환:
      game_id, cancel, special_plays,
      away_batters / home_batters  (헤더 + 행 + tfoot),
      away_pitchers / home_pitchers,
      away_name / home_name,
      game_info (W/L/S 투수, 구장, 시간 등)
    """
    import json

    game_id = _kbo_game_id(game_date, away_team, home_team)

    # ── 1. 게임 기본 정보 (투수 이름 ID 매핑용) ──
    game_info: dict = {}
    pid_to_name: dict = {}
    try:
        resp = requests.post(
            "https://www.koreabaseball.com/ws/Main.asmx/GetKboGameList",
            headers=KBO_HEADERS, timeout=10,
            data="date={}&leId=1&srId=0".format(game_date.strftime("%Y%m%d")),
        )
        resp.raise_for_status()
        data = json.loads(resp.text)
        for g in data.get("game", []):
            if g.get("G_ID") == game_id:
                game_info = g
                # 투수 이름 ID 매핑
                for pid_key, pnm_key in [
                    ("T_PIT_P_ID", "T_PIT_P_NM"),  # 원정 선발
                    ("B_PIT_P_ID", "B_PIT_P_NM"),  # 홈 선발
                    ("W_PIT_P_ID", "W_PIT_P_NM"),
                    ("L_PIT_P_ID", "L_PIT_P_NM"),
                    ("SV_PIT_P_ID", "SV_PIT_P_NM"),
                    ("T_D_PIT_P_ID", "T_D_PIT_P_NM"),
                    ("B_D_PIT_P_ID", "B_D_PIT_P_NM"),
                ]:
                    pid = g.get(pid_key)
                    pnm = (g.get(pnm_key) or "").strip()
                    if pid and pnm:
                        pid_to_name[str(pid)] = pnm
                        _pid_name_cache[str(pid)] = pnm
                break
    except Exception:
        pass

    if game_info.get("CANCEL_SC_ID") == "1":
        return {
            "game_id": game_id,
            "cancel": game_info.get("CANCEL_SC_NM", "취소"),
            "game_info": game_info,
        }

    if not game_info.get("GAME_RESULT_CK"):
        return {
            "game_id": game_id,
            "cancel": "경기 미완료",
            "game_info": game_info,
        }

    # ── 2. 박스스코어 ──
    try:
        resp = requests.post(
            "https://www.koreabaseball.com/ws/Schedule.asmx/GetBoxScore",
            headers=KBO_HEADERS, timeout=10,
            data=f"gameId={game_id}&leId=1&srId=0&seasonId={game_date.year}",
        )
        resp.raise_for_status()
        data = json.loads(resp.text)
    except Exception as e:
        return {"game_id": game_id, "error": str(e), "game_info": game_info}

    tables = data.get("tables", [])
    if len(tables) < 5:
        return {"game_id": game_id, "error": "박스스코어 데이터 없음", "game_info": game_info}

    # 테이블 0: 특이사항 (결승타, 홈런, 2루타, ...)
    _, special_rows, _ = _parse_box_table(tables[0])
    special_plays = {row[0]: row[1].strip() for row in special_rows if len(row) >= 2}

    # 테이블 1: 원정 타자 / 테이블 2: 홈 타자
    away_bat_hdrs, away_bat_rows, away_bat_foot = _parse_box_table(tables[1])
    home_bat_hdrs, home_bat_rows, home_bat_foot = _parse_box_table(tables[2])

    # 테이블 3: 원정 투수 / 테이블 4: 홈 투수 (선수명 자리에 ID 올 수 있음)
    away_pit_hdrs, away_pit_rows, away_pit_foot = _parse_box_table(tables[3])
    home_pit_hdrs, home_pit_rows, home_pit_foot = _parse_box_table(tables[4])

    # 투수 이름 ID → 이름 치환 (KBO API가 홈팀 투수를 ID로 반환하는 경우)
    for row in away_pit_rows + home_pit_rows:
        if row and row[0].isdigit():
            row[0] = pid_to_name.get(row[0]) or _resolve_player_name(row[0])

    # 결과/홀드 태그 정규화
    for row in away_pit_rows + home_pit_rows:
        if len(row) > 2 and row[2] == "&nbsp;":
            row[2] = ""

    return {
        "game_id":         game_id,
        "cancel":          None,
        "game_info":       game_info,
        "special_plays":   special_plays,
        "away_name":       away_team,
        "home_name":       home_team,
        "away_batters":    {"headers": away_bat_hdrs, "rows": away_bat_rows, "tfoot": away_bat_foot},
        "home_batters":    {"headers": home_bat_hdrs, "rows": home_bat_rows, "tfoot": home_bat_foot},
        "away_pitchers":   {"headers": away_pit_hdrs, "rows": away_pit_rows, "tfoot": away_pit_foot},
        "home_pitchers":   {"headers": home_pit_hdrs, "rows": home_pit_rows, "tfoot": home_pit_foot},
    }
