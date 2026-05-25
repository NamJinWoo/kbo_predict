"""
Monte Carlo 야구 시뮬레이션 엔진
- 포아송 분포 기반 득점 시뮬레이션
- 선발 투수 ERA, 팀 득실, 최근 흐름, 홈 어드밴티지 반영
- Claude API 분석 (ANTHROPIC_API_KEY 설정 시) 또는 규칙 기반 분석 fallback
"""
import re
import os
import numpy as np
from typing import Optional

LEAGUE_AVG_ERA   = 4.50   # KBO 2026 시즌 리그 평균 ERA (추정)
LEAGUE_AVG_RUNS  = 4.80   # 경기당 평균 득점
HOME_ADVANTAGE   = 1.04   # 홈 구장 이점 (~4%)
N_SIM            = 10_000 # 시뮬레이션 횟수


# ── 팀 코드 → 팀명 ──────────────────────────────────────────
TEAM_CODE_MAP = {
    "1001": "삼성", "2002": "KIA", "3001": "롯데", "5002": "LG",
    "6002": "두산", "7002": "한화", "9002": "SSG", "10001": "키움",
    "11001": "NC",  "12001": "KT",
}

TEAM_COLORS = {
    "삼성": "#074CA1", "KIA": "#EA0029", "롯데": "#002955",
    "LG":   "#C30452", "두산": "#131230", "한화": "#FF6600",
    "SSG":  "#CE0E2D", "키움": "#570514", "NC":   "#071D38", "KT": "#000000",
}


# ── 내부 파서 ────────────────────────────────────────────────
def _parse_era(stats_dict: dict) -> Optional[float]:
    """스탯 딕셔너리에서 ERA 추출"""
    for key in ("평균자책", "ERA", "era"):
        if key in stats_dict:
            try:
                return float(stats_dict[key])
            except (ValueError, TypeError):
                pass
    return None


def _parse_last10_wins(last10: str) -> int:
    """'6승0무4패' → 6 반환"""
    m = re.match(r"(\d+)승", last10 or "")
    return int(m.group(1)) if m else 5


def _win_pct_from_record(record: str) -> Optional[float]:
    """'14-1-9' (승-무-패) → 승률 반환"""
    parts = record.split("-") if record else []
    if len(parts) == 3:
        try:
            w, d, l = int(parts[0]), int(parts[1]), int(parts[2])
            total = w + d + l
            return (w + d * 0.5) / total if total else None
        except ValueError:
            pass
    return None


# ── 시뮬레이션 코어 ──────────────────────────────────────────
def run_simulation(
    away_team: str,
    home_team: str,
    away_stat,      # TeamStat ORM 객체 or None
    home_stat,      # TeamStat ORM 객체 or None
    game_info=None, # TodayGame ORM 객체 or None
) -> dict:
    """
    Monte Carlo 시뮬레이션 실행.
    Returns dict with win_pct, expected_runs, key_factors, ...
    """
    np.random.seed(None)  # 매 실행마다 다른 결과

    # ── 기본값 ──
    away_rpg  = (away_stat.runs_per_game         if away_stat else LEAGUE_AVG_RUNS)
    home_rpg  = (home_stat.runs_per_game         if home_stat else LEAGUE_AVG_RUNS)
    away_rapg = (away_stat.runs_allowed_per_game if away_stat else LEAGUE_AVG_RUNS)
    home_rapg = (home_stat.runs_allowed_per_game if home_stat else LEAGUE_AVG_RUNS)

    # ── 선발 투수 ERA ──
    away_pitcher_era = home_pitcher_era = None
    away_pitcher = home_pitcher = ""
    pitcher_stats_away = pitcher_stats_home = {}

    if game_info:
        away_pitcher = game_info.away_pitcher or ""
        home_pitcher = game_info.home_pitcher or ""
        s = game_info.stats
        if s.get("away"):
            pitcher_stats_away = s["away"]
            away_pitcher_era = _parse_era(s["away"])
        if s.get("home"):
            pitcher_stats_home = s["home"]
            home_pitcher_era = _parse_era(s["home"])

    # ── ERA 기반 실점 조정 계수 ──
    # 홈 선발이 강하면 → 원정팀 득점 감소
    def era_factor(era):
        if era is None:
            return 1.0
        f = era / LEAGUE_AVG_ERA
        return max(0.55, min(1.75, f))

    away_era_factor = era_factor(home_pitcher_era)  # 원정 득점에 홈 선발 영향
    home_era_factor = era_factor(away_pitcher_era)  # 홈 득점에 원정 선발 영향

    # ── 최근 흐름 보정 ──
    away_streak = (away_stat.streak_num if away_stat else 0)
    home_streak = (home_stat.streak_num if home_stat else 0)
    away_form   = 1.0 + min(abs(away_streak), 5) * 0.015 * (1 if away_streak > 0 else -1)
    home_form   = 1.0 + min(abs(home_streak), 5) * 0.015 * (1 if home_streak > 0 else -1)

    # ── 홈/원정 실제 전적 기반 추가 보정 ──
    away_road_wpct = _win_pct_from_record(away_stat.away_record if away_stat else "")
    home_home_wpct = _win_pct_from_record(home_stat.home_record if home_stat else "")

    road_factor = (away_road_wpct / 0.5) if away_road_wpct else 1.0
    home_field_factor = (home_home_wpct / 0.5) if home_home_wpct else 1.0

    road_factor       = max(0.7, min(1.3, road_factor))
    home_field_factor = max(0.7, min(1.3, home_field_factor))

    # ── 람다 계산 ──
    lambda_away = away_rpg * away_era_factor * away_form * road_factor
    lambda_home = home_rpg * home_era_factor * home_form * home_field_factor * HOME_ADVANTAGE

    lambda_away = max(2.0, min(10.0, lambda_away))
    lambda_home = max(2.0, min(10.0, lambda_home))

    # ── 포아송 시뮬레이션 ──
    away_scores = np.random.poisson(lambda_away, N_SIM)
    home_scores = np.random.poisson(lambda_home, N_SIM)

    ties       = np.sum(away_scores == home_scores)
    away_wins  = int(np.sum(away_scores > home_scores) + ties * 0.5)
    home_wins  = int(np.sum(home_scores > away_scores) + ties * 0.5)

    sim_away_pct = away_wins / N_SIM * 100
    sim_home_pct = home_wins / N_SIM * 100

    # ── 최근 10경기 보정 (30% 가중) ──
    away_r10 = _parse_last10_wins(away_stat.last10 if away_stat else "")
    home_r10 = _parse_last10_wins(home_stat.last10 if home_stat else "")
    r10_total = away_r10 + home_r10 or 10
    r10_away  = away_r10 / r10_total * 100
    r10_home  = home_r10 / r10_total * 100

    final_away = sim_away_pct * 0.70 + r10_away * 0.30
    final_home = sim_home_pct * 0.70 + r10_home * 0.30

    total = final_away + final_home
    final_away = round(final_away / total * 100, 1)
    final_home = round(100 - final_away, 1)

    return {
        "away_team":         away_team,
        "home_team":         home_team,
        "away_win_pct":      final_away,
        "home_win_pct":      final_home,
        "predicted_winner":  home_team if final_home > final_away else away_team,
        "confidence":        round(max(final_away, final_home), 1),
        "lambda_away":       round(float(lambda_away), 2),
        "lambda_home":       round(float(lambda_home), 2),
        "away_pitcher":      away_pitcher,
        "home_pitcher":      home_pitcher,
        "away_pitcher_era":  away_pitcher_era,
        "home_pitcher_era":  home_pitcher_era,
        "pitcher_stats_away": pitcher_stats_away,
        "pitcher_stats_home": pitcher_stats_home,
        "away_r10_wins":     away_r10,
        "home_r10_wins":     home_r10,
        "away_streak":       away_streak,
        "home_streak":       home_streak,
        "n_sim":             N_SIM,
        "factors":           _build_factors(
            away_team, home_team, away_stat, home_stat,
            away_pitcher, home_pitcher,
            away_pitcher_era, home_pitcher_era,
            lambda_away, lambda_home, final_away, final_home,
        ),
    }


def _build_factors(
    away_team, home_team, away_stat, home_stat,
    away_pitcher, home_pitcher, away_era, home_era,
    lambda_away, lambda_home, away_pct, home_pct,
) -> list[dict]:
    """시뮬레이션 주요 요인 목록 생성"""
    factors = []

    # 1. 선발 투수
    if away_era and home_era:
        diff = home_era - away_era
        if abs(diff) >= 0.5:
            fav = away_team if diff > 0 else home_team
            factors.append({
                "icon": "⚾",
                "title": "선발 투수 우세",
                "desc": f"{fav} 선발 ERA 유리 ({away_pitcher} {away_era:.2f} vs {home_pitcher} {home_era:.2f})",
                "side": "away" if fav == away_team else "home",
            })
    elif away_era and not home_era:
        factors.append({"icon": "⚾", "title": "선발 투수 데이터", "desc": f"{away_pitcher} ERA {away_era:.2f} 확인됨", "side": "away"})
    elif home_era and not away_era:
        factors.append({"icon": "⚾", "title": "선발 투수 데이터", "desc": f"{home_pitcher} ERA {home_era:.2f} 확인됨", "side": "home"})

    # 2. 득점력
    if away_stat and home_stat:
        diff = away_stat.runs_per_game - home_stat.runs_per_game
        if abs(diff) >= 0.3:
            fav = away_team if diff > 0 else home_team
            factors.append({
                "icon": "🔥",
                "title": "공격력",
                "desc": f"{fav} 경기당 득점 우세 ({away_stat.runs_per_game} vs {home_stat.runs_per_game})",
                "side": "away" if fav == away_team else "home",
            })

    # 3. 투구/수비
    if away_stat and home_stat:
        diff = away_stat.runs_allowed_per_game - home_stat.runs_allowed_per_game
        if abs(diff) >= 0.3:
            fav = away_team if diff < 0 else home_team
            factors.append({
                "icon": "🛡",
                "title": "수비/불펜",
                "desc": f"{fav} 경기당 실점 우세 ({away_stat.runs_allowed_per_game} vs {home_stat.runs_allowed_per_game})",
                "side": "away" if fav == away_team else "home",
            })

    # 4. 최근 흐름
    if away_stat and home_stat:
        as_ = away_stat.streak_num
        hs_ = home_stat.streak_num
        if abs(as_) >= 3 or abs(hs_) >= 3:
            if abs(as_) > abs(hs_):
                mood = "상승세" if as_ > 0 else "침체"
                factors.append({
                    "icon": "📈" if as_ > 0 else "📉",
                    "title": f"{away_team} 최근 흐름",
                    "desc": f"{away_team} {away_stat.streak} 중 → {mood}",
                    "side": "away" if as_ > 0 else "home",
                })
            else:
                mood = "상승세" if hs_ > 0 else "침체"
                factors.append({
                    "icon": "📈" if hs_ > 0 else "📉",
                    "title": f"{home_team} 최근 흐름",
                    "desc": f"{home_team} {home_stat.streak} 중 → {mood}",
                    "side": "home" if hs_ > 0 else "away",
                })

    # 5. 홈 어드밴티지
    if home_stat and home_stat.home_record:
        wpct = _win_pct_from_record(home_stat.home_record)
        if wpct and wpct >= 0.55:
            factors.append({
                "icon": "🏟",
                "title": "홈 어드밴티지",
                "desc": f"{home_team} 홈 전적 {home_stat.home_record} (홈 강팀)",
                "side": "home",
            })

    # 6. 최근 10경기
    if away_stat and home_stat:
        aw10 = _parse_last10_wins(away_stat.last10)
        hw10 = _parse_last10_wins(home_stat.last10)
        if abs(aw10 - hw10) >= 3:
            fav = away_team if aw10 > hw10 else home_team
            factors.append({
                "icon": "📊",
                "title": "최근 10경기",
                "desc": f"{fav} 최근 흐름 우세 ({away_team} {away_stat.last10} vs {home_team} {home_stat.last10})",
                "side": "away" if fav == away_team else "home",
            })

    return factors[:6]


# ── 분석 텍스트 생성 ────────────────────────────────────────
def generate_analysis(sim: dict, away_stat, home_stat) -> str:
    """Claude API (우선) → 규칙 기반 fallback"""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        try:
            return _claude_analysis(sim, away_stat, home_stat, api_key)
        except Exception:
            pass
    return _rule_based_analysis(sim, away_stat, home_stat)


def _claude_analysis(sim: dict, away_stat, home_stat, api_key: str) -> str:
    import anthropic

    away = sim["away_team"]
    home = sim["home_team"]

    def stat_block(name, stat, role):
        if not stat:
            return f"{name} ({role}): 데이터 없음"
        return (
            f"{name} ({role})\n"
            f"  순위: {stat.rank}위 | 승률: {stat.win_pct} | {stat.wins}승{stat.draws}무{stat.losses}패\n"
            f"  최근10경기: {stat.last10 or '-'} | 연속: {stat.streak or '-'}\n"
            f"  홈전적: {stat.home_record or '-'} | 원정전적: {stat.away_record or '-'}\n"
            f"  경기당 득점: {stat.runs_per_game} | 실점: {stat.runs_allowed_per_game} | 득실차: {stat.run_diff}"
        )

    pitcher_block = ""
    if sim["away_pitcher"]:
        pitcher_block += f"\n{away} 선발: {sim['away_pitcher']}"
        if sim["away_pitcher_era"]:
            pitcher_block += f" (ERA {sim['away_pitcher_era']:.2f})"
        if sim["pitcher_stats_away"]:
            for k, v in list(sim["pitcher_stats_away"].items())[:6]:
                pitcher_block += f"\n  {k}: {v}"
    if sim["home_pitcher"]:
        pitcher_block += f"\n{home} 선발: {sim['home_pitcher']}"
        if sim["home_pitcher_era"]:
            pitcher_block += f" (ERA {sim['home_pitcher_era']:.2f})"
        if sim["pitcher_stats_home"]:
            for k, v in list(sim["pitcher_stats_home"].items())[:6]:
                pitcher_block += f"\n  {k}: {v}"

    prompt = f"""당신은 KBO 프로야구 전문 분석가입니다. 다음 데이터를 바탕으로 오늘 경기 예측 분석을 작성해주세요.

경기: {away}(원정) @ {home}(홈)

[몬테카를로 시뮬레이션 결과 — {sim['n_sim']:,}회]
- {away} 승리 확률: {sim['away_win_pct']}%
- {home} 승리 확률: {sim['home_win_pct']}%
- 예상 득점: {away} {sim['lambda_away']}점 / {home} {sim['lambda_home']}점

[선발 투수]{pitcher_block if pitcher_block else ' 정보 없음'}

[팀 성적]
{stat_block(away, away_stat, '원정')}
{stat_block(home, home_stat, '홈')}

[주요 요인]
{chr(10).join(f'- {f["icon"]} {f["title"]}: {f["desc"]}' for f in sim['factors'])}

위 데이터를 분석하여 다음 형식으로 작성하세요:

## 핵심 포인트
(3가지 불릿으로 이 경기의 핵심 관전 포인트)

## 선발 투수 분석
(양 팀 선발 투수 비교, 오늘 경기에서의 예상 퍼포먼스)

## 팀 흐름 분석
(양 팀 최근 흐름, 홈/원정 성적, 연속 승패 의미 분석)

## 승부 포인트
(이 경기의 승패를 가를 결정적 요인 2~3가지)

## 최종 예측
(어떤 팀이 왜 이길 가능성이 높은지, 예상 스코어, 확신도)

전문적이고 날카롭게, 구체적인 수치를 인용하며 작성해주세요."""

    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1800,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def _rule_based_analysis(sim: dict, away_stat, home_stat) -> str:
    """Claude API 없을 때 규칙 기반 분석 생성"""
    away  = sim["away_team"]
    home  = sim["home_team"]
    fav   = sim["predicted_winner"]
    und   = home if fav == away else away
    fav_pct = sim["confidence"]

    lines = []

    # ── 핵심 포인트 ──
    lines.append("## 핵심 포인트\n")
    points = []

    if sim["away_pitcher"] and sim["home_pitcher"]:
        if sim["away_pitcher_era"] and sim["home_pitcher_era"]:
            diff = sim["home_pitcher_era"] - sim["away_pitcher_era"]
            better = away if diff > 0 else home
            worse_era = max(sim["away_pitcher_era"], sim["home_pitcher_era"])
            better_era = min(sim["away_pitcher_era"], sim["home_pitcher_era"])
            points.append(
                f"**선발 투수 구도**: {away} {sim['away_pitcher']}(ERA {sim['away_pitcher_era']:.2f}) vs "
                f"{home} {sim['home_pitcher']}(ERA {sim['home_pitcher_era']:.2f}) — "
                f"{better} 선발이 ERA {better_era:.2f}로 우위"
            )
        else:
            points.append(
                f"**선발 투수**: {away} {sim['away_pitcher']} vs {home} {sim['home_pitcher']} "
                f"— 상세 ERA 데이터 미확인, 직접 관전 포인트"
            )

    if away_stat and home_stat:
        rpg_diff = away_stat.runs_per_game - home_stat.runs_per_game
        if abs(rpg_diff) >= 0.3:
            off_fav = away if rpg_diff > 0 else home
            points.append(
                f"**공격력 우세**: {off_fav}이 경기당 {max(away_stat.runs_per_game, home_stat.runs_per_game):.2f}득점으로 "
                f"상대({min(away_stat.runs_per_game, home_stat.runs_per_game):.2f}) 대비 공격 지표 앞서"
            )

        if away_stat.last10 and home_stat.last10:
            a10 = _parse_last10_wins(away_stat.last10)
            h10 = _parse_last10_wins(home_stat.last10)
            if abs(a10 - h10) >= 2:
                form_fav = away if a10 > h10 else home
                form_bad = home if a10 > h10 else away
                points.append(
                    f"**최근 10경기 흐름**: {form_fav}({(away_stat if form_fav == away else home_stat).last10})이 "
                    f"{form_bad}({(home_stat if form_bad == home else away_stat).last10}) 대비 최근 우세"
                )

    if away_stat and home_stat and not points:
        points.append(f"**시뮬레이션 우위**: {fav}가 {fav_pct}% 확률로 유력, 전력 차이 {abs(sim['away_win_pct']-sim['home_win_pct']):.1f}%p")

    for p in points[:3]:
        lines.append(f"- {p}")

    # ── 선발 투수 분석 ──
    lines.append("\n## 선발 투수 분석\n")
    if sim["away_pitcher"] or sim["home_pitcher"]:
        if sim["away_pitcher"]:
            era_str = f" (ERA {sim['away_pitcher_era']:.2f})" if sim["away_pitcher_era"] else ""
            lines.append(f"**{away} — {sim['away_pitcher']}{era_str}**")
            if sim["pitcher_stats_away"]:
                key_stats = {k: v for k, v in sim["pitcher_stats_away"].items()
                             if k in ("승패", "평균자책", "WHIP", "탈삼진", "WAR", "경기이닝")}
                if key_stats:
                    lines.append("  " + " | ".join(f"{k}: {v}" for k, v in key_stats.items()))
            if sim["away_pitcher_era"]:
                if sim["away_pitcher_era"] < 3.5:
                    lines.append(f"  → ERA {sim['away_pitcher_era']:.2f}로 리그 상위권 선발. 오늘 경기 핵심 변수.")
                elif sim["away_pitcher_era"] < 4.5:
                    lines.append(f"  → ERA {sim['away_pitcher_era']:.2f}로 평균 수준. 상대 타선 봉쇄가 관건.")
                else:
                    lines.append(f"  → ERA {sim['away_pitcher_era']:.2f}로 최근 불안정. 조기 교체 가능성 있음.")

        if sim["home_pitcher"]:
            era_str = f" (ERA {sim['home_pitcher_era']:.2f})" if sim["home_pitcher_era"] else ""
            lines.append(f"\n**{home} — {sim['home_pitcher']}{era_str}**")
            if sim["pitcher_stats_home"]:
                key_stats = {k: v for k, v in sim["pitcher_stats_home"].items()
                             if k in ("승패", "평균자책", "WHIP", "탈삼진", "WAR", "경기이닝")}
                if key_stats:
                    lines.append("  " + " | ".join(f"{k}: {v}" for k, v in key_stats.items()))
            if sim["home_pitcher_era"]:
                if sim["home_pitcher_era"] < 3.5:
                    lines.append(f"  → ERA {sim['home_pitcher_era']:.2f}로 홈에서 강력한 투구 기대.")
                elif sim["home_pitcher_era"] < 4.5:
                    lines.append(f"  → ERA {sim['home_pitcher_era']:.2f}, 홈 어드밴티지와 결합해 안정적 등판 예상.")
                else:
                    lines.append(f"  → ERA {sim['home_pitcher_era']:.2f}로 다소 불안. 중반 불펜 의존도 높을 것.")
    else:
        lines.append("선발 투수 정보가 아직 확정되지 않았습니다. 실제 경기 전 공식 발표 확인 권장.")

    # ── 팀 흐름 분석 ──
    lines.append("\n## 팀 흐름 분석\n")
    if away_stat:
        streak_comment = ""
        sn = away_stat.streak_num
        if sn >= 4:
            streak_comment = f" → **{sn}연승 중, 강한 상승세**"
        elif sn <= -4:
            streak_comment = f" → **{abs(sn)}연패 중, 팀 분위기 침체**"
        elif sn >= 2:
            streak_comment = f" → {sn}연승 흐름"
        elif sn <= -2:
            streak_comment = f" → {abs(sn)}연패, 반등 필요"
        lines.append(
            f"**{away} (원정)**: 시즌 {away_stat.wins}승{away_stat.draws}무{away_stat.losses}패 (승률 {away_stat.win_pct}) | "
            f"최근10경기 {away_stat.last10 or '-'} | 연속 {away_stat.streak or '-'}{streak_comment}\n"
            f"  원정 전적: {away_stat.away_record or '-'} | 경기당 득점/실점: {away_stat.runs_per_game}/{away_stat.runs_allowed_per_game}"
        )
    if home_stat:
        streak_comment = ""
        sn = home_stat.streak_num
        if sn >= 4:
            streak_comment = f" → **{sn}연승 중, 강한 상승세**"
        elif sn <= -4:
            streak_comment = f" → **{abs(sn)}연패 중, 팀 분위기 침체**"
        elif sn >= 2:
            streak_comment = f" → {sn}연승 흐름"
        elif sn <= -2:
            streak_comment = f" → {abs(sn)}연패, 반등 필요"
        lines.append(
            f"\n**{home} (홈)**: 시즌 {home_stat.wins}승{home_stat.draws}무{home_stat.losses}패 (승률 {home_stat.win_pct}) | "
            f"최근10경기 {home_stat.last10 or '-'} | 연속 {home_stat.streak or '-'}{streak_comment}\n"
            f"  홈 전적: {home_stat.home_record or '-'} | 경기당 득점/실점: {home_stat.runs_per_game}/{home_stat.runs_allowed_per_game}"
        )

    # ── 승부 포인트 ──
    lines.append("\n## 승부 포인트\n")
    bp = []

    if sim["away_pitcher_era"] and sim["home_pitcher_era"] and abs(sim["away_pitcher_era"] - sim["home_pitcher_era"]) >= 0.5:
        bp.append(f"**선발 투수의 초반 지배력**: ERA 차이 {abs(sim['away_pitcher_era'] - sim['home_pitcher_era']):.2f}가 경기 흐름을 결정할 가능성 높음")

    if away_stat and home_stat:
        run_diff_diff = abs(away_stat.run_diff - home_stat.run_diff)
        if run_diff_diff >= 20:
            better_team = away if away_stat.run_diff > home_stat.run_diff else home
            bp.append(f"**득실차 격차**: {better_team}의 득실차({(away_stat if better_team == away else home_stat).run_diff:+d})가 현저히 우세 — 전체적인 전력 차이 반영")

        if away_stat.streak_num <= -3 or home_stat.streak_num <= -3:
            slump_team = away if away_stat.streak_num <= -3 else home
            bp.append(f"**{slump_team} 연패 탈출 여부**: {(away_stat if slump_team == away else home_stat).streak} 중인 팀이 반등하느냐가 승부 가름")

    if not bp:
        bp.append(f"**초반 선취점**: 예상 득점 {away} {sim['lambda_away']}점 vs {home} {sim['lambda_home']}점 — 선취점 팀이 흐름 주도")
        bp.append(f"**불펜 운용**: 선발 이탈 후 중간계투 안정성이 후반 승부 관건")

    for b in bp[:3]:
        lines.append(f"- {b}")

    # ── 최종 예측 ──
    lines.append("\n## 최종 예측\n")
    margin = abs(sim["away_win_pct"] - sim["home_win_pct"])

    conf_word = "강하게" if margin >= 15 else ("어느 정도" if margin >= 8 else "소폭")
    lines.append(f"**예측 승팀: {fav}** ({fav_pct}% 우세)\n")

    reasons = []
    if sim["away_pitcher_era"] and sim["home_pitcher_era"]:
        better_p = away if sim["home_pitcher_era"] > sim["away_pitcher_era"] else home
        if better_p == fav:
            reasons.append(f"선발 투수 ERA 우위")
    if away_stat and home_stat:
        if fav == away and away_stat.runs_per_game > home_stat.runs_per_game:
            reasons.append(f"공격력 우세(경기당 {away_stat.runs_per_game}득점)")
        elif fav == home and home_stat.runs_per_game > away_stat.runs_per_game:
            reasons.append(f"공격력 우세(경기당 {home_stat.runs_per_game}득점) + 홈 어드밴티지")
        fav_s = away_stat if fav == away else home_stat
        if fav_s.streak_num >= 2:
            reasons.append(f"{fav_s.streak} 상승세")
    if not reasons:
        reasons.append(f"시즌 전체 전력 지표 종합")

    lines.append(f"**근거**: {' / '.join(reasons)}")

    pred_away = round(sim["lambda_away"] * (0.9 if fav == home else 1.1))
    pred_home = round(sim["lambda_home"] * (1.1 if fav == home else 0.9))
    if fav == home:
        lines.append(f"**예상 스코어**: {home} {max(pred_home, pred_away+1)} - {away} {min(pred_away, pred_home-1)}")
    else:
        lines.append(f"**예상 스코어**: {away} {max(pred_away, pred_home+1)} - {home} {min(pred_home, pred_away-1)}")

    if margin < 8:
        lines.append(f"\n> ⚠️ 전력 차이가 {margin:.1f}%p로 박빙 — 변수(날씨, 수비 실책, 상대 타자 컨디션)에 따라 결과가 뒤바뀔 수 있음")

    return "\n".join(lines)
