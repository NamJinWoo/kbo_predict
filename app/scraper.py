import re
import requests
from datetime import date, datetime, timedelta

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": "https://statiz.co.kr/",
}

TEAM_CODE_MAP = {
    "1001": "삼성", "2002": "KIA", "3001": "롯데", "5002": "LG",
    "6002": "두산", "7002": "한화", "9002": "SSG", "10001": "키움",
    "11001": "NC",  "12001": "KT",
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


def scrape_recent_results(target_date: date) -> list[dict]:
    """statiz ?page=game 페이지에서 완료된 경기 결과 파싱 (W/L/S)"""
    date_str = target_date.strftime("%Y%m%d")
    resp = requests.get(
        f"https://statiz.co.kr/?page=game&date={date_str}",
        headers=HEADERS, timeout=10
    )
    resp.raise_for_status()
    html = resp.text

    blocks = re.findall(
        r'<div class="vsbox">(.*?)</div>\s*<div class="v_info">(.*?)</div>',
        html, re.DOTALL
    )
    results = []
    for vsbox, vinfo in blocks:
        vclean = re.sub(r'&nbsp;', ' ', re.sub(r'<[^>]+>', '', vinfo)).strip()
        # 완료된 경기만 (W 가 있는 경우)
        if not re.search(r'\bW\b', vclean):
            continue

        p_tags = re.findall(r'<p[^>]*>(.*?)</p>', vsbox, re.DOTALL)
        if len(p_tags) < 2:
            continue
        away_text = re.sub(r'<[^>]+>', '', p_tags[0]).strip()
        home_text = re.sub(r'<[^>]+>', '', p_tags[1]).strip()

        # away_text: "팀명 점수"  home_text: "점수 팀명"
        away_m = re.match(r'(.+?)\s+(\d+)$', away_text)
        home_m = re.match(r'^(\d+)\s+(.+)', home_text)
        if not away_m or not home_m:
            continue

        away_team  = away_m.group(1).strip()
        away_score = int(away_m.group(2))
        home_score = int(home_m.group(1))
        home_team  = home_m.group(2).strip()

        def _pick(tag):
            m = re.search(rf'\b{tag}\b\s+([^\s정보]+)', vclean)
            return m.group(1) if m else ""

        results.append({
            "game_date":    target_date,
            "away_team":    away_team,
            "home_team":    home_team,
            "away_score":   away_score,
            "home_score":   home_score,
            "win_pitcher":  _pick("W"),
            "lose_pitcher": _pick("L"),
            "save_pitcher": _pick("S"),
            "hold_pitcher": _pick("H"),
            "stadium":      STADIUM_MAP.get(home_team, ""),
        })
    return results


def scrape_upcoming_by_date(target_date: date) -> list[dict]:
    """statiz prediction 페이지에서 특정 날짜 예정 경기 + 선발 투수 파싱"""
    date_str = target_date.strftime("%Y%m%d")
    resp = requests.get(
        f"https://statiz.co.kr/prediction/?m=main&date={date_str}",
        headers=HEADERS, timeout=10
    )
    resp.raise_for_status()
    html = resp.text

    # game_date 파싱
    date_m = re.search(r'class="day"[^>]*>\s*(\d{4}\.\d{2}\.\d{2})', html)
    game_date = target_date
    if date_m:
        try:
            game_date = datetime.strptime(date_m.group(1), "%Y.%m.%d").date()
        except ValueError:
            pass

    pitcher_names = re.findall(r'<div class="name">(.*?)</div>', html)
    record_blocks = re.findall(
        r'<ul>\s*(<li class="value">.*?</ul>)\s*<ul>\s*(<li class="value">.*?</ul>)',
        html, re.DOTALL
    )
    team_blocks = re.findall(
        r'<div class="t_name">\s*<a href="[^"]*t_code=(\d+)[^"]*"[^>]*>(.*?)</a>',
        html, re.DOTALL
    )

    games = []
    i = 0
    pitcher_idx = 0
    while i + 1 < len(team_blocks):
        away_code, away_raw = team_blocks[i]
        home_code, home_raw = team_blocks[i + 1]
        away_team = re.sub(r"\s+", "", _clean(away_raw))
        home_team = re.sub(r"\s+", "", _clean(home_raw))

        game = {
            "game_date":    game_date,
            "away_team":    away_team,
            "home_team":    home_team,
            "away_pitcher": pitcher_names[pitcher_idx]     if pitcher_idx     < len(pitcher_names) else "",
            "home_pitcher": pitcher_names[pitcher_idx + 1] if pitcher_idx + 1 < len(pitcher_names) else "",
            "stadium":      STADIUM_MAP.get(home_team, ""),
            "stats":        {},
        }
        if pitcher_idx // 2 < len(record_blocks):
            aw_vals, hm_vals = record_blocks[pitcher_idx // 2]
            game["stats"]["away"] = _parse_pitcher_stats(aw_vals)
            game["stats"]["home"] = _parse_pitcher_stats(hm_vals)

        games.append(game)
        i += 2
        pitcher_idx += 2

    return games


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

    # 최근 완료 경기 결과: 오늘부터 최대 3일 전까지 역순 탐색
    recent_results = []
    recent_date = None
    for delta in range(0, 4):
        check = date.today() - timedelta(days=delta)
        try:
            found = scrape_recent_results(check)
        except Exception:
            found = []
        if found:
            recent_results = found
            recent_date = check
            break

    # 다음 예정 경기: 오늘부터 최대 3일 후까지 탐색
    upcoming = []
    upcoming_date = None
    for delta in range(0, 4):
        check = date.today() + timedelta(days=delta)
        try:
            found = scrape_upcoming_by_date(check)
        except Exception:
            found = []
        if found:
            upcoming = found
            upcoming_date = check
            break

    return {
        "scraped_at":     datetime.utcnow().isoformat(),
        "standings":      merged,
        "today_games":    upcoming,       # TodayGame 저장용 (기존 키 유지)
        "recent_results": recent_results,
        "recent_date":    recent_date,
        "upcoming_date":  upcoming_date,
    }
