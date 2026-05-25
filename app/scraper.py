import re
import requests
from datetime import date, datetime

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


def scrape_all() -> dict:
    """statiz + KBO 공식 데이터 통합 스크래핑"""
    statiz = scrape_standings()        # 득점/실점 포함
    kbo = scrape_kbo_teamrank()        # 연속/최근10/홈원정 포함

    # KBO 데이터를 팀명으로 인덱싱
    kbo_map = {r["team"]: r for r in kbo}

    # 두 소스 병합 (statiz 기본 + KBO 보완)
    merged = []
    for s in statiz:
        k = kbo_map.get(s["team"], {})
        merged.append({**s, **{
            "last10":      k.get("last10", ""),
            "streak":      k.get("streak", ""),
            "home_record": k.get("home_record", ""),
            "away_record": k.get("away_record", ""),
        }})

    return {
        "scraped_at": datetime.utcnow().isoformat(),
        "standings": merged,
        "today_games": scrape_today_games(),
    }
