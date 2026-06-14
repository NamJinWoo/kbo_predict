"""
Monte Carlo 야구 시뮬레이션 엔진 v2
- 포아송 분포 기반 득점 시뮬레이션
- 선발 투수 ERA + 상대전적 ERA + 위험타자 OPS + WAR 비율 반영
- Claude API 분석 (ANTHROPIC_API_KEY 설정 시) 또는 규칙 기반 분석 fallback
"""
import re
import os
import numpy as np
from typing import Optional

LEAGUE_AVG_ERA   = 4.50
LEAGUE_AVG_RUNS  = 4.80
LEAGUE_AVG_OPS   = 0.750
HOME_ADVANTAGE    = 1.01  # 홈어드밴티지 축소 (과잉보정 방지)
N_SIM             = 10_000
CONFIDENCE_SHRINK = 0.75  # 과잉확신 억제 (50% 방향으로 수렴)

# KBO 구장별 파크팩터 (홈팀 기준, 1.0 = 리그 평균)
# 1.0 초과: 타자 유리 구장, 1.0 미만: 투수 유리 구장
PARK_FACTORS: dict[str, float] = {
    "LG":   1.05,   # 잠실 (타자 친화적)
    "두산": 1.05,   # 잠실
    "삼성": 0.97,   # 대구 (투수 친화적)
    "KIA":  1.01,   # 광주
    "롯데": 1.00,   # 부산
    "한화": 1.02,   # 대전
    "SSG":  0.97,   # 인천
    "키움": 0.96,   # 고척 (돔구장, 투수 유리)
    "NC":   0.95,   # 창원 (투수 유리)
    "KT":   0.93,   # 수원 (투수 유리)
}

LEAGUE_AVG_BULLPEN_WAR = 3.0  # KBO 시즌 팀 불펜 WAR 리그 평균 추정치
LEAGUE_AVG_K_BB_9      = 4.5  # KBO 리그 평균 K-BB/9

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


def _parse_era(stats_dict: dict) -> Optional[float]:
    for key in ("평균자책", "ERA", "era"):
        if key in stats_dict:
            try:
                return float(stats_dict[key])
            except (ValueError, TypeError):
                pass
    return None


def _parse_float(s: str) -> Optional[float]:
    m = re.search(r"[\d.]+", s or "")
    try:
        return float(m.group()) if m else None
    except ValueError:
        return None


def _parse_last10_wins(last10: str) -> int:
    m = re.match(r"(\d+)승", last10 or "")
    return int(m.group(1)) if m else 5


def _win_pct_from_record(record: str) -> Optional[float]:
    parts = record.split("-") if record else []
    if len(parts) == 3:
        try:
            w, d, l = int(parts[0]), int(parts[1]), int(parts[2])
            total = w + d + l
            return (w + d * 0.5) / total if total else None
        except ValueError:
            pass
    return None


def _parse_matchup_wins(record: str) -> int:
    """'3승 0무 0패' → 3"""
    m = re.search(r"(\d+)승", record or "")
    return int(m.group(1)) if m else 0


def _parse_h2h_record(record: str) -> Optional[tuple]:
    """'3승 0무 2패' or '3-0-2' → (wins, draws, losses)"""
    w = re.search(r"(\d+)승", record or "")
    l = re.search(r"(\d+)패", record or "")
    if w and l:
        d = re.search(r"(\d+)무", record or "")
        return int(w.group(1)), (int(d.group(1)) if d else 0), int(l.group(1))
    m = re.match(r"(\d+)-(\d+)-(\d+)", record or "")
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    return None


def _h2h_win_pct(record: str) -> Optional[float]:
    parsed = _parse_h2h_record(record)
    if not parsed:
        return None
    w, d, l = parsed
    total = w + d + l
    return (w + d * 0.5) / total if total > 0 else None


def _h2h_total_games(record: str) -> int:
    parsed = _parse_h2h_record(record)
    return sum(parsed) if parsed else 0


def _career_vs_factor(win_pct: Optional[float], total_games: int) -> float:
    """통산 특정팀 상대 승률 → 득점 조정 계수 (0.93 ~ 1.07)
    투수 승률 높음 → 상대팀 득점 낮아짐 → factor < 1.0
    3경기 미만은 샘플 부족으로 무시.
    """
    if win_pct is None or total_games < 3:
        return 1.0
    # 15경기 이상이면 full weight, 미만이면 비례 적용
    sample_weight = min(1.0, total_games / 15)
    raw = 1.0 - (win_pct - 0.5) * 0.20
    return 1.0 + (raw - 1.0) * sample_weight


def _parse_innings(innings_str: str) -> Optional[float]:
    """'80.2' or '80⅔' → 80.667 이닝 float. 1/3=0.333, 2/3=0.667"""
    if not innings_str:
        return None
    s = str(innings_str).strip()
    # 'X.1' → X + 1/3, 'X.2' → X + 2/3
    m = re.match(r"(\d+)\.(\d)$", s)
    if m:
        whole = int(m.group(1))
        frac  = int(m.group(2))
        return whole + (frac / 3.0)
    try:
        return float(s)
    except ValueError:
        return None


def _pitcher_ratios(stats: dict) -> dict:
    """탈삼진/볼넷/이닝에서 K/9, BB/9, K-BB% 계산"""
    ip  = _parse_innings(stats.get("경기이닝", ""))
    k   = _parse_float(stats.get("탈삼진", ""))
    bb  = _parse_float(stats.get("볼넷", ""))
    if ip is None or ip <= 0:
        return {}
    result = {}
    if k is not None:
        result["K/9"] = round(k / ip * 9, 2)
    if bb is not None:
        result["BB/9"] = round(bb / ip * 9, 2)
    if k is not None and bb is not None:
        # K-BB%는 (K - BB) / IP * 9 기반 표준화 (비율로 표현)
        result["K-BB/9"] = round((k - bb) / ip * 9, 2)
    return result


def _park_factor(home_team: str) -> float:
    """홈팀 구장 파크팩터 (0.90 ~ 1.10)"""
    return PARK_FACTORS.get(home_team, 1.0)


def _bullpen_war_factor(bullpen_war_str: str) -> float:
    """불펜WAR 문자열 → λ 조정 계수 (0.92 ~ 1.08)"""
    war = _parse_float(bullpen_war_str)
    if war is None:
        return 1.0
    delta = (war - LEAGUE_AVG_BULLPEN_WAR) / LEAGUE_AVG_BULLPEN_WAR
    return max(0.92, min(1.08, 1.0 - delta * 0.25))


def _bullpen_combined_factor(bullpen_war_str: str, team_era_str: str) -> float:
    """불펜WAR + 팀ERA 복합 계수 (WAR 60% + 팀ERA 40%)
    - WAR: 누적 기여도 기반 → 시즌 불펜 전력
    - 팀ERA: 실제 실점 반영 → 선발+불펜 전반 수준
    두 지표 교차 검증으로 단일 지표의 노이즈를 줄임
    """
    war_f = _bullpen_war_factor(bullpen_war_str)

    team_era = _parse_float(team_era_str)
    if team_era is None:
        era_f = 1.0
    else:
        delta = (team_era - LEAGUE_AVG_ERA) / LEAGUE_AVG_ERA
        era_f = max(0.93, min(1.07, 1.0 + delta * 0.18))

    return war_f * 0.6 + era_f * 0.4


def _kbb_form_factor(pitcher_ratios: dict) -> float:
    """K-BB/9 기반 투수 폼 계수 (0.93 ~ 1.07)
    K-BB/9 높을수록 투수 우세 → 상대 득점 감소 (factor < 1.0)
    """
    kbb9 = pitcher_ratios.get("K-BB/9")
    if kbb9 is None:
        return 1.0
    delta = (kbb9 - LEAGUE_AVG_K_BB_9) / LEAGUE_AVG_K_BB_9
    return max(0.93, min(1.07, 1.0 - delta * 0.15))


def _fatigue_factor(days_since_last: Optional[int]) -> float:
    """최근 등판 간격 기반 피로도 계수 (1.0 ~ 1.10)
    짧을수록 투수 체력 부담 → 상대 득점 증가
    """
    if days_since_last is None:
        return 1.0
    if days_since_last <= 2:
        return 1.10
    if days_since_last <= 3:
        return 1.05
    return 1.0


def _danger_factor(batters: list[dict]) -> float:
    """상위 3타자 평균 OPS → 런 프로덕션 조정 계수 (0.90 ~ 1.15)"""
    ops_list = [b["ops"] for b in (batters or [])[:5] if b.get("ops", 0) > 0]
    if not ops_list:
        return 1.0
    avg_ops = sum(ops_list) / len(ops_list)
    raw = 1.0 + (avg_ops / LEAGUE_AVG_OPS - 1.0) * 0.25
    return max(0.90, min(1.15, raw))


def run_simulation(
    away_team: str,
    home_team: str,
    away_stat,
    home_stat,
    game_info=None,
    fatigue_away_days: Optional[int] = None,
    fatigue_home_days: Optional[int] = None,
    recent_rpg_away: Optional[float] = None,
    recent_rpg_home: Optional[float] = None,
) -> dict:
    np.random.seed(None)

    # ── D: 최근 5경기 득점 블렌딩 (시즌 70% + 최근 30%) ──
    base_away_rpg = (away_stat.runs_per_game if away_stat else LEAGUE_AVG_RUNS)
    base_home_rpg = (home_stat.runs_per_game if home_stat else LEAGUE_AVG_RUNS)
    away_rpg = base_away_rpg * 0.80 + recent_rpg_away * 0.20 if recent_rpg_away is not None else base_away_rpg
    home_rpg = base_home_rpg * 0.80 + recent_rpg_home * 0.20 if recent_rpg_home is not None else base_home_rpg

    away_pitcher = home_pitcher = ""
    away_pitcher_era = home_pitcher_era = None
    pitcher_stats_away = pitcher_stats_home = {}
    stats = {}
    h2h = {}
    team_comp = {}
    dangerous = {}

    if game_info:
        away_pitcher = game_info.away_pitcher or ""
        home_pitcher = game_info.home_pitcher or ""
        stats = game_info.stats
        if stats.get("away"):
            pitcher_stats_away = stats["away"]
            away_pitcher_era = _parse_era(stats["away"])
        if stats.get("home"):
            pitcher_stats_home = stats["home"]
            home_pitcher_era = _parse_era(stats["home"])

        h2h = stats.get("h2h", {})
        team_comp = stats.get("team_comp", {})
        dangerous = stats.get("dangerous_batters", {})

    away_pitcher_ratios = _pitcher_ratios(pitcher_stats_away)
    home_pitcher_ratios = _pitcher_ratios(pitcher_stats_home)

    # ── K/BB 폼 계수 (투수 → 상대팀 λ에 반영) ──
    # away pitcher's K-BB/9 높으면 → lambda_home ↓
    # home pitcher's K-BB/9 높으면 → lambda_away ↓
    away_kbb_factor = _kbb_form_factor(away_pitcher_ratios)
    home_kbb_factor = _kbb_form_factor(home_pitcher_ratios)

    # ── 피로도 계수 (최근 등판 간격) ──
    # away pitcher 피로 → lambda_home ↑ (홈팀이 득점 유리)
    # home pitcher 피로 → lambda_away ↑ (원정팀이 득점 유리)
    away_fatigue = _fatigue_factor(fatigue_away_days)
    home_fatigue = _fatigue_factor(fatigue_home_days)

    # ── ERA factor (season) ──
    def era_factor(era: Optional[float]) -> float:
        if era is None:
            return 1.0
        return max(0.55, min(1.75, era / LEAGUE_AVG_ERA))

    # ── Head-to-head ERA factor + 통산 승률 ──
    # away pitcher's h2h ERA/record vs home team batters → affects lambda_home
    # home pitcher's h2h ERA/record vs away team batters → affects lambda_away
    h2h_away_era    = _parse_float((h2h.get("away") or {}).get("상대 평균자책", ""))
    h2h_home_era    = _parse_float((h2h.get("home") or {}).get("상대 평균자책", ""))
    h2h_away_record = (h2h.get("away") or {}).get("상대전적", "")
    h2h_home_record = (h2h.get("home") or {}).get("상대전적", "")

    h2h_away_win_pct = _h2h_win_pct(h2h_away_record)
    h2h_home_win_pct = _h2h_win_pct(h2h_home_record)
    h2h_away_games   = _h2h_total_games(h2h_away_record)
    h2h_home_games   = _h2h_total_games(h2h_home_record)

    # Blend: season ERA + career ERA (가중치는 통산 경기 수에 비례)
    def blended_era_factor(season_era, h2h_era, h2h_games=0):
        sf = era_factor(season_era)
        hf = era_factor(h2h_era)
        if h2h_era and h2h_era > 0:
            h2h_w = 0.35 if h2h_games >= 5 else 0.25
            return sf * (1 - h2h_w) + hf * h2h_w
        return sf

    # 통산 상대 승률 보정 (ERA 블렌딩과 별개로 소폭 추가 반영)
    # away pitcher high win_pct vs home team → home team scores LESS → lambda_home ↓
    away_career_factor = _career_vs_factor(h2h_away_win_pct, h2h_away_games)  # affects lambda_home
    home_career_factor = _career_vs_factor(h2h_home_win_pct, h2h_home_games)  # affects lambda_away

    away_era_factor = blended_era_factor(home_pitcher_era, h2h_home_era, h2h_home_games)
    home_era_factor = blended_era_factor(away_pitcher_era, h2h_away_era, h2h_away_games)

    # ── 최근 흐름 ──
    away_streak = (away_stat.streak_num if away_stat else 0)
    home_streak = (home_stat.streak_num if home_stat else 0)
    away_form = 1.0 + min(abs(away_streak), 5) * 0.015 * (1 if away_streak > 0 else -1)
    home_form = 1.0 + min(abs(home_streak), 5) * 0.015 * (1 if home_streak > 0 else -1)

    # ── 홈/원정 전적 ──
    away_road_wpct = _win_pct_from_record(away_stat.away_record if away_stat else "")
    home_home_wpct = _win_pct_from_record(home_stat.home_record if home_stat else "")
    road_factor       = max(0.75, min(1.20, (away_road_wpct / 0.5) if away_road_wpct else 1.0))
    home_field_factor = max(0.75, min(1.20, (home_home_wpct / 0.5) if home_home_wpct else 1.0))

    # ── 위험 타자 OPS 기반 보정 ──
    # vs_home_pitcher = away team's batters who hit well vs home pitcher → lambda_away up
    # vs_away_pitcher = home team's batters who hit well vs away pitcher → lambda_home up
    danger_away = _danger_factor(dangerous.get("vs_home_pitcher", []))
    danger_home = _danger_factor(dangerous.get("vs_away_pitcher", []))

    # ── 파크팩터 ──
    park_f = _park_factor(home_team)

    # ── 불펜 WAR + 팀ERA 복합 보정 (E) ──
    # 원정팀 불펜 강함 → 홈팀 후반 득점 감소 → lambda_home ↓
    # 홈팀 불펜 강함 → 원정팀 후반 득점 감소 → lambda_away ↓
    tc_away = team_comp.get("away", {})
    tc_home = team_comp.get("home", {})
    away_bullpen_factor = _bullpen_combined_factor(tc_away.get("불펜WAR", ""), tc_away.get("팀 평균자책", ""))
    home_bullpen_factor = _bullpen_combined_factor(tc_home.get("불펜WAR", ""), tc_home.get("팀 평균자책", ""))

    # ── 최근 맞대결 보정 ──
    recent_matchup_away = team_comp.get("away", {}).get("최근 맞대결", "")
    recent_matchup_home = team_comp.get("home", {}).get("최근 맞대결", "")
    if not recent_matchup_away:
        recent_matchup_away = (h2h.get("away") or {}).get("최근 맞대결", "")
    rm_away_wins = _parse_matchup_wins(recent_matchup_away)
    rm_home_wins = _parse_matchup_wins(recent_matchup_home)
    if rm_away_wins + rm_home_wins > 0:
        total_rm = rm_away_wins + rm_home_wins
        rm_away_factor = max(0.92, min(1.08, (rm_away_wins / total_rm) / 0.5))
        rm_home_factor = max(0.92, min(1.08, (rm_home_wins / total_rm) / 0.5))
    else:
        rm_away_factor = rm_home_factor = 1.0

    # ── λ 계산 ──
    # away_career_factor: away pitcher's career dominance vs home team → reduces lambda_home
    # home_career_factor: home pitcher's career dominance vs away team → reduces lambda_away
    # park_f: 홈구장 파크팩터 → 양 팀 득점에 반영
    # home_bullpen_factor: 홈팀 불펜 WAR → away 득점 감소
    # away_bullpen_factor: 원정팀 불펜 WAR → home 득점 감소
    lambda_away = (
        away_rpg * away_era_factor * away_form * road_factor * danger_away * rm_away_factor
        * home_career_factor * park_f * home_bullpen_factor
        * home_kbb_factor * home_fatigue
    )
    lambda_home = (
        home_rpg * home_era_factor * home_form * home_field_factor
        * HOME_ADVANTAGE * danger_home * rm_home_factor
        * away_career_factor * park_f * away_bullpen_factor
        * away_kbb_factor * away_fatigue
    )

    lambda_away = max(2.5, min(10.0, lambda_away))
    lambda_home = max(2.5, min(10.0, lambda_home))

    # ── 포아송 시뮬레이션 ──
    away_scores = np.random.poisson(lambda_away, N_SIM)
    home_scores = np.random.poisson(lambda_home, N_SIM)

    # 스코어 예측 범위 (25~75 백분위수)
    away_p25, away_median, away_p75 = [int(x) for x in np.percentile(away_scores, [25, 50, 75])]
    home_p25, home_median, home_p75 = [int(x) for x in np.percentile(home_scores, [25, 50, 75])]

    ties      = np.sum(away_scores == home_scores)
    away_wins = int(np.sum(away_scores > home_scores) + ties * 0.5)
    home_wins = int(np.sum(home_scores > away_scores) + ties * 0.5)

    sim_away_pct = away_wins / N_SIM * 100
    sim_home_pct = home_wins / N_SIM * 100

    # ── 최근 10경기 보정 (30%) ──
    away_r10 = _parse_last10_wins(away_stat.last10 if away_stat else "")
    home_r10 = _parse_last10_wins(home_stat.last10 if home_stat else "")
    r10_total = away_r10 + home_r10 or 10
    r10_away  = away_r10 / r10_total * 100
    r10_home  = home_r10 / r10_total * 100

    final_away = sim_away_pct * 0.70 + r10_away * 0.30
    final_home = sim_home_pct * 0.70 + r10_home * 0.30

    total = final_away + final_home
    final_away = final_away / total * 100
    final_home = 100 - final_away

    # ── C: Shrinkage — 과잉확신 억제 (50%로 수렴) ──
    final_away = round(50 + (final_away - 50) * CONFIDENCE_SHRINK, 1)
    final_home = round(100 - final_away, 1)

    return {
        "away_team":           away_team,
        "home_team":           home_team,
        "away_win_pct":        final_away,
        "home_win_pct":        final_home,
        "predicted_winner":    home_team if final_home > final_away else away_team,
        "confidence":          round(max(final_away, final_home), 1),
        "lambda_away":         round(float(lambda_away), 2),
        "lambda_home":         round(float(lambda_home), 2),
        # 스코어 예측 범위 (25~75 백분위수)
        "away_score_range":    (away_p25, away_median, away_p75),
        "home_score_range":    (home_p25, home_median, home_p75),
        # 파크팩터 / 불펜 WAR 정보
        "park_factor":         round(park_f, 3),
        "away_bullpen_factor": round(away_bullpen_factor, 3),
        "home_bullpen_factor": round(home_bullpen_factor, 3),
        "recent_rpg_away":     round(recent_rpg_away, 2) if recent_rpg_away is not None else None,
        "recent_rpg_home":     round(recent_rpg_home, 2) if recent_rpg_home is not None else None,
        "blended_rpg_away":    round(away_rpg, 2),
        "blended_rpg_home":    round(home_rpg, 2),
        "away_kbb_factor":     round(away_kbb_factor, 3),
        "home_kbb_factor":     round(home_kbb_factor, 3),
        "away_fatigue_factor": round(away_fatigue, 3),
        "home_fatigue_factor": round(home_fatigue, 3),
        "fatigue_away_days":   fatigue_away_days,
        "fatigue_home_days":   fatigue_home_days,
        "away_pitcher":         away_pitcher,
        "home_pitcher":         home_pitcher,
        "away_pitcher_era":     away_pitcher_era,
        "home_pitcher_era":     home_pitcher_era,
        "pitcher_stats_away":   pitcher_stats_away,
        "pitcher_stats_home":   pitcher_stats_home,
        "away_pitcher_ratios":  away_pitcher_ratios,
        "home_pitcher_ratios":  home_pitcher_ratios,
        "h2h":                 h2h,
        "team_comp":           team_comp,
        "dangerous_batters":   dangerous,
        "away_r10_wins":       away_r10,
        "home_r10_wins":       home_r10,
        "away_streak":         away_streak,
        "home_streak":         home_streak,
        "n_sim":               N_SIM,
        "h2h_away_win_pct":    h2h_away_win_pct,
        "h2h_home_win_pct":    h2h_home_win_pct,
        "h2h_away_games":      h2h_away_games,
        "h2h_home_games":      h2h_home_games,
        "h2h_away_record":     h2h_away_record,
        "h2h_home_record":     h2h_home_record,
        "factors":             _build_factors(
            away_team, home_team, away_stat, home_stat,
            away_pitcher, home_pitcher,
            away_pitcher_era, home_pitcher_era,
            lambda_away, lambda_home, final_away, final_home,
            h2h, team_comp, dangerous,
            h2h_away_win_pct, h2h_home_win_pct,
            h2h_away_games, h2h_home_games,
            h2h_away_record, h2h_home_record,
            park_f,
        ),
    }


def _build_factors(
    away_team, home_team, away_stat, home_stat,
    away_pitcher, home_pitcher, away_era, home_era,
    lambda_away, lambda_home, away_pct, home_pct,
    h2h, team_comp, dangerous,
    h2h_away_win_pct=None, h2h_home_win_pct=None,
    h2h_away_games=0, h2h_home_games=0,
    h2h_away_record="", h2h_home_record="",
    park_factor=1.0,
) -> list[dict]:
    factors = []

    # 1. 선발 투수
    if away_era and home_era:
        diff = home_era - away_era
        if abs(diff) >= 0.3:
            fav = away_team if diff > 0 else home_team
            factors.append({
                "icon": "⚾", "title": "선발 투수 ERA",
                "desc": f"{away_pitcher} ERA {away_era:.2f} vs {home_pitcher} ERA {home_era:.2f} — {fav} 우세",
                "side": "away" if fav == away_team else "home",
            })
    elif away_era:
        factors.append({"icon": "⚾", "title": "선발 투수", "desc": f"{away_pitcher} ERA {away_era:.2f}", "side": "away"})
    elif home_era:
        factors.append({"icon": "⚾", "title": "선발 투수", "desc": f"{home_pitcher} ERA {home_era:.2f}", "side": "home"})

    # 2. 상대전적 ERA
    h2h_home_era = _parse_float((h2h.get("home") or {}).get("상대 평균자책", ""))
    h2h_away_era = _parse_float((h2h.get("away") or {}).get("상대 평균자책", ""))
    if h2h_home_era and h2h_home_era > 0:
        fav = away_team if h2h_home_era > (LEAGUE_AVG_ERA + 0.5) else home_team
        factors.append({
            "icon": "📋", "title": "상대전적 ERA",
            "desc": f"{home_pitcher} 상대(원정팀) ERA {h2h_home_era:.2f} — {'불안' if h2h_home_era > 4.5 else '준수'}",
            "side": "away" if h2h_home_era > 4.5 else "home",
        })
    if h2h_away_era and h2h_away_era > 0:
        factors.append({
            "icon": "📋", "title": "상대전적 ERA",
            "desc": f"{away_pitcher} 상대(홈팀) ERA {h2h_away_era:.2f} — {'불안' if h2h_away_era > 4.5 else '준수'}",
            "side": "home" if h2h_away_era > 4.5 else "away",
        })

    # 2-b. 통산 상대 승률 (3경기 이상일 때만)
    for pitcher, record, games, win_pct, opp_team, side in [
        (away_pitcher, h2h_away_record, h2h_away_games, h2h_away_win_pct, home_team, "away"),
        (home_pitcher, h2h_home_record, h2h_home_games, h2h_home_win_pct, away_team, "home"),
    ]:
        if pitcher and games >= 3 and win_pct is not None:
            parsed = _parse_h2h_record(record)
            w, d, l = parsed if parsed else (0, 0, 0)
            pct_str = f"{win_pct*100:.0f}%"
            if win_pct >= 0.65:
                label = "상성 우위"
                fav_side = side
            elif win_pct <= 0.35:
                label = "상성 열세"
                fav_side = "home" if side == "away" else "away"
            else:
                continue  # 보통이면 팩터 표시 생략
            factors.append({
                "icon": "🗂️", "title": f"{pitcher} 통산 {opp_team} 상대",
                "desc": f"{w}승{d}무{l}패 (승률 {pct_str}) — {label}",
                "side": fav_side,
            })

    # 3. 위험 타자
    vs_hp = dangerous.get("vs_home_pitcher", [])
    vs_ap = dangerous.get("vs_away_pitcher", [])
    if vs_hp and vs_hp[0].get("ops", 0) > 0.900:
        factors.append({
            "icon": "🔥", "title": f"{away_team} 위험 타자",
            "desc": f"{vs_hp[0]['name']} OPS {vs_hp[0]['ops']:.3f} vs {home_pitcher} — 핵심 위협",
            "side": "away",
        })
    if vs_ap and vs_ap[0].get("ops", 0) > 0.900:
        factors.append({
            "icon": "🔥", "title": f"{home_team} 위험 타자",
            "desc": f"{vs_ap[0]['name']} OPS {vs_ap[0]['ops']:.3f} vs {away_pitcher} — 핵심 위협",
            "side": "home",
        })

    # 4. 최근 맞대결
    tc = team_comp.get("away", {})
    rm_text = tc.get("최근 맞대결", "")
    rm_home_text = (team_comp.get("home") or {}).get("최근 맞대결", "")
    if rm_text:
        away_rm = _parse_matchup_wins(rm_text)
        home_rm = _parse_matchup_wins(rm_home_text)
        if abs(away_rm - home_rm) >= 2:
            fav = away_team if away_rm > home_rm else home_team
            factors.append({
                "icon": "📊", "title": "최근 맞대결",
                "desc": f"{fav} 최근 상대전적 우세 ({away_team} {rm_text} vs {home_team} {rm_home_text})",
                "side": "away" if fav == away_team else "home",
            })

    # 5. 선발WAR 비교
    tc_a = team_comp.get("away", {})
    tc_h = team_comp.get("home", {})
    starter_war_away = _parse_float(tc_a.get("선발WAR", ""))
    starter_war_home = _parse_float(tc_h.get("선발WAR", ""))
    if starter_war_away and starter_war_home and abs(starter_war_away - starter_war_home) >= 1.0:
        fav = away_team if starter_war_away > starter_war_home else home_team
        factors.append({
            "icon": "📈", "title": "선발 로테이션 WAR",
            "desc": f"{fav} 선발WAR 우세 ({away_team} {starter_war_away} vs {home_team} {starter_war_home})",
            "side": "away" if fav == away_team else "home",
        })

    # 6. 최근 흐름
    if away_stat and home_stat:
        as_ = away_stat.streak_num
        hs_ = home_stat.streak_num
        if abs(as_) >= 3 or abs(hs_) >= 3:
            if abs(as_) >= abs(hs_) and abs(as_) >= 3:
                mood = "상승세" if as_ > 0 else "침체"
                factors.append({
                    "icon": "📈" if as_ > 0 else "📉",
                    "title": f"{away_team} 최근 흐름",
                    "desc": f"{away_stat.streak} → {mood}",
                    "side": "away" if as_ > 0 else "home",
                })
            elif abs(hs_) >= 3:
                mood = "상승세" if hs_ > 0 else "침체"
                factors.append({
                    "icon": "📈" if hs_ > 0 else "📉",
                    "title": f"{home_team} 최근 흐름",
                    "desc": f"{home_stat.streak} → {mood}",
                    "side": "home" if hs_ > 0 else "away",
                })

    # 7. 파크팩터 (유의미한 경우만)
    if abs(park_factor - 1.0) >= 0.03:
        pf_side = "away" if park_factor > 1.0 else "home"
        pf_label = "타자 유리 구장" if park_factor > 1.0 else "투수 유리 구장"
        factors.append({
            "icon": "🏟️", "title": f"{home_team} 홈구장 파크팩터",
            "desc": f"파크팩터 {park_factor:.2f} — {pf_label}",
            "side": pf_side,
        })

    return factors[:6]


# ── 분석 텍스트 생성 ──────────────────────────────────────────
def generate_analysis(sim: dict, away_stat, home_stat) -> str:
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
            f"  경기당 득점: {stat.runs_per_game} | 실점: {stat.runs_allowed_per_game}"
        )

    pitcher_block = ""
    for side, pitcher, era, pstats in [
        ("원정", sim["away_pitcher"], sim["away_pitcher_era"], sim["pitcher_stats_away"]),
        ("홈",   sim["home_pitcher"], sim["home_pitcher_era"], sim["pitcher_stats_home"]),
    ]:
        team = away if side == "원정" else home
        if not pitcher:
            continue
        pitcher_block += f"\n{team} 선발({side}): {pitcher}"
        if era:
            pitcher_block += f" (시즌 ERA {era:.2f})"
        key_fields = ("경기이닝", "승패", "평균자책", "WHIP", "피안타율", "탈삼진", "볼넷", "WAR")
        for k in key_fields:
            if k in pstats:
                pitcher_block += f"\n  {k}: {pstats[k]}"

    h2h = sim.get("h2h", {})
    h2h_block = ""
    ah2h = h2h.get("away", {})
    hh2h = h2h.get("home", {})
    if ah2h or hh2h:
        h2h_block = f"\n[선발투수 통산 상대팀 성적]\n"
        if ah2h.get("상대 평균자책"):
            record_a = sim.get("h2h_away_record", "")
            pct_a = sim.get("h2h_away_win_pct")
            games_a = sim.get("h2h_away_games", 0)
            pct_str_a = f"승률 {pct_a*100:.0f}% ({games_a}경기)" if pct_a is not None and games_a > 0 else ""
            h2h_block += f"  {sim['away_pitcher']} vs {home}타선: ERA {ah2h.get('상대 평균자책','?')}, OOPS {ah2h.get('상대 OOPS','?')}"
            if record_a:
                h2h_block += f", 전적 {record_a}"
            if pct_str_a:
                h2h_block += f" ({pct_str_a})"
            h2h_block += "\n"
        if hh2h.get("상대 평균자책"):
            record_h = sim.get("h2h_home_record", "")
            pct_h = sim.get("h2h_home_win_pct")
            games_h = sim.get("h2h_home_games", 0)
            pct_str_h = f"승률 {pct_h*100:.0f}% ({games_h}경기)" if pct_h is not None and games_h > 0 else ""
            h2h_block += f"  {sim['home_pitcher']} vs {away}타선: ERA {hh2h.get('상대 평균자책','?')}, OOPS {hh2h.get('상대 OOPS','?')}"
            if record_h:
                h2h_block += f", 전적 {record_h}"
            if pct_str_h:
                h2h_block += f" ({pct_str_h})"
            h2h_block += "\n"

    danger = sim.get("dangerous_batters", {})
    danger_block = ""
    if danger.get("vs_home_pitcher"):
        top3 = danger["vs_home_pitcher"][:3]
        danger_block += f"\n{away} 위협 타자 vs {sim['home_pitcher']}: " + ", ".join(f"{b['name']}(OPS {b['ops']:.3f})" for b in top3)
    if danger.get("vs_away_pitcher"):
        top3 = danger["vs_away_pitcher"][:3]
        danger_block += f"\n{home} 위협 타자 vs {sim['away_pitcher']}: " + ", ".join(f"{b['name']}(OPS {b['ops']:.3f})" for b in top3)

    tc = sim.get("team_comp", {})
    tc_block = ""
    tc_a = tc.get("away", {})
    tc_h = tc.get("home", {})
    if tc_a:
        tc_block = f"\n[팀 전력]\n"
        for key in ("팀 타율", "팀 OPS", "팀 평균자책", "팀 WHIP", "선발WAR", "불펜WAR", "최근 맞대결"):
            av = tc_a.get(key, "")
            hv = tc_h.get(key, "")
            if av or hv:
                tc_block += f"  {key}: {away} {av} vs {home} {hv}\n"

    system_text = """당신은 KBO 프로야구 전문 분석가입니다.
제공되는 몬테카를로 시뮬레이션 데이터와 팀/투수 스탯을 바탕으로 아래 형식으로 분석하세요.

## 핵심 포인트
(3가지 불릿 — 이 경기의 핵심 관전 포인트)

## 선발 투수 분석
(시즌 스탯 + 통산 상대팀 ERA/OOPS/승률 포함, 상성(특정 팀에 유독 강하거나 약한 패턴) 분석, 오늘 예상 퍼포먼스)

## 위협 타자 분석
(각 팀에서 상대 선발투수를 상대로 강한 타자, 그 타자들의 기록이 경기에 미칠 영향)

## 팀 흐름 및 전력 비교
(최근 흐름, WAR 비교, 최근 맞대결 성적)

## 승부 포인트
(이 경기 승패를 가를 결정적 요인 2~3가지)

## 최종 예측
(승팀 예측, 예상 스코어, 확신도와 이유)

전문적이고 날카롭게, 구체적 수치를 인용하며 작성하세요."""

    user_content = f"""경기: {away}(원정) @ {home}(홈)

[몬테카를로 시뮬레이션 — {sim['n_sim']:,}회]
- {away} 승리 확률: {sim['away_win_pct']}%
- {home} 승리 확률: {sim['home_win_pct']}%
- 예상 득점: {away} {sim['lambda_away']}점 / {home} {sim['lambda_home']}점

[선발 투수]{pitcher_block if pitcher_block else ' 미정'}
{h2h_block}
[팀 성적]
{stat_block(away, away_stat, '원정')}
{stat_block(home, home_stat, '홈')}
{tc_block}
[위험 타자]{danger_block if danger_block else ' 데이터 없음'}

[주요 요인]
{chr(10).join(f'- {f["icon"]} {f["title"]}: {f["desc"]}' for f in sim['factors'])}"""

    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        system=[{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_content}],
    )
    return resp.content[0].text


def _rule_based_analysis(sim: dict, away_stat, home_stat) -> str:
    away  = sim["away_team"]
    home  = sim["home_team"]
    fav   = sim["predicted_winner"]
    und   = home if fav == away else away
    fav_pct = sim["confidence"]
    h2h   = sim.get("h2h", {})
    tc    = sim.get("team_comp", {})
    danger = sim.get("dangerous_batters", {})

    lines = []

    # ── 핵심 포인트 ──
    lines.append("## 핵심 포인트\n")
    points = []

    if sim["away_pitcher"] and sim["home_pitcher"] and sim["away_pitcher_era"] and sim["home_pitcher_era"]:
        diff = sim["home_pitcher_era"] - sim["away_pitcher_era"]
        better = away if diff > 0 else home
        pts = f"**선발 투수**: {away} {sim['away_pitcher']}(ERA {sim['away_pitcher_era']:.2f}) vs {home} {sim['home_pitcher']}(ERA {sim['home_pitcher_era']:.2f}) — {better} 선발 ERA 우위"
        points.append(pts)

    h2h_home_era = _parse_float((h2h.get("home") or {}).get("상대 평균자책", ""))
    if h2h_home_era and h2h_home_era > 5.0:
        points.append(f"**{home_pitcher_trap(sim)} 상대전적**: {sim['home_pitcher']} vs {away}타선 통산 ERA {h2h_home_era:.2f} — 상성 불리")

    # 통산 승률 기반 상성 핵심 포인트
    for pitcher, win_pct, games, opp_team, record in [
        (sim["away_pitcher"], sim.get("h2h_away_win_pct"), sim.get("h2h_away_games", 0), home, sim.get("h2h_away_record", "")),
        (sim["home_pitcher"], sim.get("h2h_home_win_pct"), sim.get("h2h_home_games", 0), away, sim.get("h2h_home_record", "")),
    ]:
        if pitcher and win_pct is not None and games >= 3:
            parsed = _parse_h2h_record(record)
            w, d, l = parsed if parsed else (0, 0, 0)
            if win_pct >= 0.65:
                points.append(f"**{pitcher} vs {opp_team} 상성**: {w}승{d}무{l}패 (승률 {win_pct*100:.0f}%) — 역대 상성 우위")
            elif win_pct <= 0.35:
                points.append(f"**{pitcher} vs {opp_team} 상성**: {w}승{d}무{l}패 (승률 {win_pct*100:.0f}%) — 역대 상성 열세")

    vs_hp = danger.get("vs_home_pitcher", [])
    if vs_hp and vs_hp[0].get("ops", 0) >= 0.900:
        top = vs_hp[0]
        points.append(f"**{away} 위협 타자**: {top['name']} OPS {top['ops']:.3f} vs {sim['home_pitcher']} — 주목 포인트")

    if away_stat and home_stat and abs(away_stat.runs_per_game - home_stat.runs_per_game) >= 0.3:
        off_fav = away if away_stat.runs_per_game > home_stat.runs_per_game else home
        points.append(f"**공격력**: {off_fav} 경기당 {max(away_stat.runs_per_game, home_stat.runs_per_game):.2f}득점 우세")

    if not points:
        points.append(f"**시뮬레이션**: {fav} {fav_pct}% 우세, 전력 차이 {abs(sim['away_win_pct']-sim['home_win_pct']):.1f}%p")

    for p in points[:3]:
        lines.append(f"- {p}")

    # ── 선발 투수 분석 ──
    lines.append("\n## 선발 투수 분석\n")
    for side, pitcher, era, pstats in [
        ("원정", sim["away_pitcher"], sim["away_pitcher_era"], sim["pitcher_stats_away"]),
        ("홈",   sim["home_pitcher"], sim["home_pitcher_era"], sim["pitcher_stats_home"]),
    ]:
        team = away if side == "원정" else home
        if not pitcher:
            continue
        era_str = f" (ERA {era:.2f})" if era else ""
        lines.append(f"**{team} — {pitcher}{era_str}**")
        if pstats:
            key_fields = [k for k in ("경기이닝", "승패", "평균자책", "WHIP", "탈삼진", "볼넷", "WAR") if k in pstats]
            if key_fields:
                lines.append("  " + " | ".join(f"{k}: {pstats[k]}" for k in key_fields))
        # 통산 상대팀 성적 (ERA + 승률)
        h2h_key = "home" if side == "홈" else "away"
        h2h_era = _parse_float((h2h.get(h2h_key) or {}).get("상대 평균자책", ""))
        h2h_oops = (h2h.get(h2h_key) or {}).get("상대 OOPS", "")
        win_pct_key = "h2h_home_win_pct" if side == "홈" else "h2h_away_win_pct"
        games_key   = "h2h_home_games"   if side == "홈" else "h2h_away_games"
        record_key  = "h2h_home_record"  if side == "홈" else "h2h_away_record"
        h2h_wpc   = sim.get(win_pct_key)
        h2h_games = sim.get(games_key, 0)
        h2h_rec   = sim.get(record_key, "")
        opp_label = away if side == "홈" else home
        if h2h_era and h2h_era > 0:
            extra = ""
            if h2h_wpc is not None and h2h_games >= 3:
                parsed = _parse_h2h_record(h2h_rec)
                if parsed:
                    w, d, l = parsed
                    trend = "상성 우위" if h2h_wpc >= 0.6 else ("상성 열세" if h2h_wpc <= 0.4 else "")
                    trend_str = f" — {trend}" if trend else ""
                    extra = f" | 전적 {w}승{d}무{l}패 (승률 {h2h_wpc*100:.0f}%){trend_str}"
            lines.append(f"  → {opp_label}타선 통산: ERA {h2h_era:.2f}, OOPS {h2h_oops}{extra}")
        if era:
            comment = "ERA 3.5 미만 — 리그 에이스급" if era < 3.5 else ("ERA 4.5 미만 — 평균 이상" if era < 4.5 else "ERA 4.5 초과 — 불안정")
            lines.append(f"  ▸ {comment}")

    # ── 위협 타자 ──
    lines.append("\n## 위협 타자\n")
    has_danger = False
    for batter_key, pitcher_name, batting_team in [
        ("vs_home_pitcher", sim["home_pitcher"], away),
        ("vs_away_pitcher", sim["away_pitcher"], home),
    ]:
        batters = danger.get(batter_key, [])[:4]
        if batters and batters[0].get("ops", 0) > 0:
            has_danger = True
            lines.append(f"**{batting_team} — {pitcher_name} 상대 강타자**")
            for b in batters:
                if b.get("ops", 0) > 0:
                    lines.append(f"  • {b['name']}: OPS {b['ops']:.3f}")
    if not has_danger:
        lines.append("위협 타자 데이터 없음")

    # ── 팀 흐름 ──
    lines.append("\n## 팀 흐름 분석\n")
    tc_a = tc.get("away", {})
    tc_h = tc.get("home", {})

    def streak_comment(sn):
        if sn >= 4: return f"**{sn}연승 상승세**"
        if sn <= -4: return f"**{abs(sn)}연패 침체**"
        if sn >= 2: return f"{sn}연승"
        if sn <= -2: return f"{abs(sn)}연패"
        return ""

    if away_stat:
        sc = streak_comment(away_stat.streak_num)
        lines.append(
            f"**{away} (원정)**: {away_stat.wins}승{away_stat.draws}무{away_stat.losses}패 (승률 {away_stat.win_pct}) | "
            f"최근10경기 {away_stat.last10 or '-'} | 연속 {away_stat.streak or '-'}" + (f" — {sc}" if sc else "")
        )
        if tc_a.get("선발WAR") or tc_a.get("불펜WAR"):
            lines.append(f"  WAR: 선발 {tc_a.get('선발WAR', '-')} / 불펜 {tc_a.get('불펜WAR', '-')}")

    if home_stat:
        sc = streak_comment(home_stat.streak_num)
        lines.append(
            f"\n**{home} (홈)**: {home_stat.wins}승{home_stat.draws}무{home_stat.losses}패 (승률 {home_stat.win_pct}) | "
            f"최근10경기 {home_stat.last10 or '-'} | 연속 {home_stat.streak or '-'}" + (f" — {sc}" if sc else "")
        )
        if tc_h.get("선발WAR") or tc_h.get("불펜WAR"):
            lines.append(f"  WAR: 선발 {tc_h.get('선발WAR', '-')} / 불펜 {tc_h.get('불펜WAR', '-')}")

    rm = tc_a.get("최근 맞대결", "")
    rm_h = tc_h.get("최근 맞대결", "")
    if rm:
        lines.append(f"\n최근 맞대결: {away} {rm} vs {home} {rm_h}")

    # ── 승부 포인트 ──
    lines.append("\n## 승부 포인트\n")
    bp = []
    if sim["away_pitcher_era"] and sim["home_pitcher_era"] and abs(sim["away_pitcher_era"] - sim["home_pitcher_era"]) >= 0.5:
        bp.append(f"**선발 투수 지배력**: ERA 차이 {abs(sim['away_pitcher_era'] - sim['home_pitcher_era']):.2f}가 경기 흐름 결정")
    if h2h_home_era and h2h_home_era > 5.5:
        bp.append(f"**{sim['home_pitcher']} 상성**: 통산 {away}타선 상대 ERA {h2h_home_era:.2f} — 조기 교체 가능성")
    # 통산 승률 기반 승부 포인트
    for pitcher, win_pct, games, opp_team, record in [
        (sim["away_pitcher"], sim.get("h2h_away_win_pct"), sim.get("h2h_away_games", 0), home, sim.get("h2h_away_record", "")),
        (sim["home_pitcher"], sim.get("h2h_home_win_pct"), sim.get("h2h_home_games", 0), away, sim.get("h2h_home_record", "")),
    ]:
        if pitcher and win_pct is not None and games >= 5:
            parsed = _parse_h2h_record(record)
            if parsed:
                w, d, l = parsed
                if win_pct >= 0.70:
                    bp.append(f"**{pitcher} 상성 강점**: {opp_team} 상대 {w}승{l}패 (승률 {win_pct*100:.0f}%) — 역대 지배력 발휘 기대")
                elif win_pct <= 0.30:
                    bp.append(f"**{pitcher} 상성 취약**: {opp_team} 상대 {w}승{l}패 (승률 {win_pct*100:.0f}%) — 상성 변수")
    vs_hp2 = danger.get("vs_home_pitcher", [])
    if vs_hp2 and vs_hp2[0].get("ops", 0) >= 0.950:
        bp.append(f"**{vs_hp2[0]['name']}의 활약**: OPS {vs_hp2[0]['ops']:.3f} vs {sim['home_pitcher']} — 경기 판도 변수")
    if not bp:
        bp.append(f"**선취점 선점**: {away} λ={sim['lambda_away']} vs {home} λ={sim['lambda_home']} — 초반 흐름이 승부 갈림")
        bp.append(f"**불펜 안정성**: 선발 교체 이후 중간계투 운영이 후반 열쇠")
    for b in bp[:3]:
        lines.append(f"- {b}")

    # ── 최종 예측 ──
    lines.append("\n## 최종 예측\n")
    margin = abs(sim["away_win_pct"] - sim["home_win_pct"])
    lines.append(f"**예측 승팀: {fav}** ({fav_pct}% 우세)\n")
    reasons = []
    if sim["away_pitcher_era"] and sim["home_pitcher_era"]:
        better_p = away if sim["home_pitcher_era"] > sim["away_pitcher_era"] else home
        if better_p == fav:
            reasons.append("선발 ERA 우위")
    if h2h_home_era and h2h_home_era > 5.0 and fav == away:
        reasons.append(f"상대팀 선발 상성 불리(h2h ERA {h2h_home_era:.2f})")
    # 통산 승률 우위 근거
    for pitcher, win_pct, games, pitcher_team in [
        (sim["away_pitcher"], sim.get("h2h_away_win_pct"), sim.get("h2h_away_games", 0), away),
        (sim["home_pitcher"], sim.get("h2h_home_win_pct"), sim.get("h2h_home_games", 0), home),
    ]:
        if pitcher and win_pct is not None and games >= 3 and fav == pitcher_team and win_pct >= 0.65:
            reasons.append(f"{pitcher} 통산 상대 상성 우위({win_pct*100:.0f}%)")
    if away_stat and home_stat:
        fav_s = away_stat if fav == away else home_stat
        if fav_s.streak_num >= 2:
            reasons.append(f"{fav_s.streak} 상승세")
        if fav == home:
            reasons.append("홈 어드밴티지")
    if not reasons:
        reasons.append("종합 전력 우위")
    lines.append(f"**근거**: {' / '.join(reasons)}")

    pred_away = round(sim["lambda_away"] * (0.9 if fav == home else 1.1))
    pred_home = round(sim["lambda_home"] * (1.1 if fav == home else 0.9))
    if fav == home:
        lines.append(f"**예상 스코어**: {home} {max(pred_home, pred_away+1)} - {away} {min(pred_away, pred_home-1)}")
    else:
        lines.append(f"**예상 스코어**: {away} {max(pred_away, pred_home+1)} - {home} {min(pred_home, pred_away-1)}")

    if margin < 8:
        lines.append(f"\n> ⚠️ 전력 차이 {margin:.1f}%p — 박빙 승부")

    return "\n".join(lines)


def home_pitcher_trap(sim: dict) -> str:
    return sim.get("home_team", "")


# ── 배트맨 배팅 분석 ────────────────────────────────────────────

def _poisson_pmf(k: int, lam: float) -> float:
    from math import exp, factorial
    if k < 0 or lam <= 0:
        return 0.0
    return (lam ** k * exp(-lam)) / factorial(min(k, 170))

def _poisson_cdf(k: int, lam: float) -> float:
    return sum(_poisson_pmf(i, lam) for i in range(k + 1))

def calc_betman_picks(sim: dict) -> dict:
    """시뮬레이션 결과에서 배트맨 베팅 종목별 확률 및 추천 반환."""
    from math import exp

    away      = sim["away_team"]
    home      = sim["home_team"]
    away_pct  = sim["away_win_pct"]   # shrinkage 적용 최종 확률
    home_pct  = sim["home_win_pct"]
    la        = sim.get("lambda_away", LEAGUE_AVG_RUNS)
    lh        = sim.get("lambda_home", LEAGUE_AVG_RUNS)
    lt        = la + lh                # 예상 합산 득점

    # ── 승패 ──
    win_fav    = away if away_pct >= home_pct else home
    win_conf   = max(away_pct, home_pct)

    # ── 홀짝 (Poisson 합의 홀수 확률) ──
    p_odd  = 0.5 * (1 - exp(-2 * lt))
    p_even = 1 - p_odd
    oe_pick = "홀" if p_odd > p_even else "짝"
    oe_conf = max(p_odd, p_even) * 100

    # ── 언더오버 (기준선 8.5 — KBO 리그 평균 근사) ──
    line = 8.5
    p_over  = (1 - _poisson_cdf(int(line), lt)) * 100
    p_under = 100 - p_over
    uo_pick = "오버" if p_over > p_under else "언더"
    uo_conf = max(p_over, p_under)

    # ── 종목별 최고 신뢰도 → 추천 픽 결정 ──
    candidates = [
        {"type": "승패",    "pick": win_fav, "conf": win_conf,  "detail": f"{win_fav} 승리"},
        {"type": "언더오버","pick": uo_pick, "conf": uo_conf,   "detail": f"합산 {line} {uo_pick} (예상 {lt:.1f}점)"},
        {"type": "홀짝",    "pick": oe_pick, "conf": oe_conf,   "detail": f"합산 득점 {oe_pick} (P={max(p_odd,p_even)*100:.1f}%)"},
    ]
    best = max(candidates, key=lambda x: x["conf"])

    # ── 애매함 판단 (두 종목 이상 신뢰도 차이 5%p 미만) ──
    confs = sorted([c["conf"] for c in candidates], reverse=True)
    is_ambiguous = (confs[0] - confs[1]) < 5.0 or confs[0] < 57.0

    return {
        "away": away, "home": home,
        "win":  {"pick": win_fav, "conf": round(win_conf, 1),
                 "away_pct": round(away_pct, 1), "home_pct": round(home_pct, 1)},
        "uo":   {"pick": uo_pick, "conf": round(uo_conf, 1),
                 "line": line, "lambda_total": round(lt, 2),
                 "p_over": round(p_over, 1), "p_under": round(p_under, 1)},
        "oe":   {"pick": oe_pick, "conf": round(oe_conf, 1),
                 "p_odd": round(p_odd * 100, 1), "p_even": round(p_even * 100, 1)},
        "best": best,
        "is_ambiguous": is_ambiguous,
        "lambda_away": round(la, 2),
        "lambda_home": round(lh, 2),
        "no_pitcher": not sim.get("away_pitcher") or not sim.get("home_pitcher"),
    }


# ── 경기 결과 분석 ──────────────────────────────────────────────

def generate_result_analysis(
    sim: dict,
    actual_winner: str,
    away_score: int,
    home_score: int,
    win_pitcher: str = "",
    lose_pitcher: str = "",
) -> str:
    """시뮬레이션 예측 대비 실제 경기 결과 분석 생성 (Claude or 규칙 기반)"""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        try:
            return _claude_result_analysis(
                sim, actual_winner, away_score, home_score,
                win_pitcher, lose_pitcher, api_key,
            )
        except Exception:
            pass
    return _rule_result_analysis(sim, actual_winner, away_score, home_score, win_pitcher, lose_pitcher)


def _claude_result_analysis(
    sim, actual_winner, away_score, home_score, win_pitcher, lose_pitcher, api_key
) -> str:
    import anthropic

    away = sim["away_team"]
    home = sim["home_team"]
    predicted = sim["predicted_winner"]
    is_correct = predicted == actual_winner
    factors = sim.get("factors", [])
    actual_side = "away" if actual_winner == away else "home"

    factor_lines = "\n".join(f"  [{f['side']}] {f['icon']} {f['title']}: {f['desc']}" for f in factors)

    career_block = ""
    for pitcher, win_pct, games, record, opp in [
        (sim.get("away_pitcher",""), sim.get("h2h_away_win_pct"), sim.get("h2h_away_games",0),
         sim.get("h2h_away_record",""), home),
        (sim.get("home_pitcher",""), sim.get("h2h_home_win_pct"), sim.get("h2h_home_games",0),
         sim.get("h2h_home_record",""), away),
    ]:
        if pitcher and win_pct is not None and games >= 3:
            career_block += f"\n  {pitcher} vs {opp}: {record} (승률 {win_pct*100:.0f}%, {games}경기)"

    result_label = "✅ 적중" if is_correct else "❌ 빗나감"
    header_label = "✅ 예측 적중" if is_correct else "❌ 예측 빗나감"
    factor_section = "적중 핵심 요인" if is_correct else "빗나간 원인 분석"
    factor_hint = "(어떤 요인이 가장 결정적이었는지, 3줄 이내)" if is_correct else "(예상과 달랐던 구체적 이유, 3줄 이내)"
    mvp_section = "오늘의 MVP 요인" if is_correct else "다음 시뮬레이션 주목 포인트"
    mvp_hint = "(가장 예측력이 높았던 지표 한 줄 강조)" if is_correct else "(앞으로 이 매치업에서 더 주목해야 할 변수 2~3가지, 불릿)"
    improvement_block = "" if is_correct else (
        "\n\n### 시뮬레이션 개선 제안\n"
        "(이번 오류에서 학습할 수 있는 기준 추가/가중치 조정 제안 1~2가지)"
    )
    lambda_away_str = f"{sim.get('lambda_away', 0):.1f}" if sim.get("lambda_away") else "-"
    lambda_home_str = f"{sim.get('lambda_home', 0):.1f}" if sim.get("lambda_home") else "-"
    era_away = sim.get("away_pitcher_era") or "-"
    era_home = sim.get("home_pitcher_era") or "-"

    system_text = (
        "당신은 KBO 프로야구 예측 분석 전문가입니다.\n"
        "시뮬레이션 예측과 실제 경기 결과를 비교하여 아래 형식으로 분석하세요.\n\n"
        f"## {header_label}\n\n"
        f"**예측**: {predicted} 승 ({sim['confidence']}%) → **실제**: {actual_winner} 승 ({away_score}:{home_score})\n\n"
        "### 예측 득점 정확도\n"
        "(예측 λ vs 실제 스코어 비교, 어느 팀 득점이 더 잘/못 맞았는지)\n\n"
        f"### {factor_section}\n"
        f"{factor_hint}\n\n"
        f"### {mvp_section}\n"
        f"{mvp_hint}"
        f"{improvement_block}\n\n"
        "간결하고 날카롭게, 구체적 수치를 인용하여 작성하세요."
    )

    user_content = (
        f"경기: {away}(원정) {away_score} - {home_score} {home}(홈)\n"
        f"실제 승리: {actual_winner}\n\n"
        "[시뮬레이션 예측]\n"
        f"- 예측 승팀: {predicted} ({sim['confidence']}% 우세)\n"
        f"- 예측 득점: {away} {lambda_away_str}점 / {home} {lambda_home_str}점\n"
        f"- 결과: {result_label}\n\n"
        "[예측 요인 카드]\n"
        f"{factor_lines if factor_lines else '요인 없음'}\n\n"
        "[선발 투수]\n"
        f"- {away}: {sim.get('away_pitcher','-')} (ERA {era_away})\n"
        f"- {home}: {sim.get('home_pitcher','-')} (ERA {era_home})\n\n"
        f"[투수 통산 상대 성적]{career_block if career_block else ' 없음'}\n\n"
        "[실제 투수 결과]\n"
        f"- 승리투수: {win_pitcher or '-'}\n"
        f"- 패전투수: {lose_pitcher or '-'}"
    )

    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        system=[{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_content}],
    )
    return resp.content[0].text


def _rule_result_analysis(
    sim, actual_winner, away_score, home_score, win_pitcher, lose_pitcher
) -> str:
    away = sim["away_team"]
    home = sim["home_team"]
    predicted = sim["predicted_winner"]
    is_correct = predicted == actual_winner
    confidence = sim.get("confidence", 50)
    lambda_away = sim.get("lambda_away", 0) or 0
    lambda_home = sim.get("lambda_home", 0) or 0
    factors = sim.get("factors", [])
    actual_side = "away" if actual_winner == away else "home"

    icon = "✅" if is_correct else "❌"
    label = "예측 적중" if is_correct else "예측 빗나감"

    lines = [f"## {icon} {label}", ""]
    lines.append(f"**예측**: {predicted} 승 ({confidence}% 우세) → **실제**: {actual_winner} 승 ({away} {away_score} - {home_score} {home})")
    lines.append("")

    # 득점 예측 정확도
    away_err = abs(away_score - lambda_away)
    home_err = abs(home_score - lambda_home)
    lines.append("### 예측 득점 정확도")
    for team, predicted_runs, actual_runs, err in [
        (away, lambda_away, away_score, away_err),
        (home, lambda_home, home_score, home_err),
    ]:
        accuracy = "근접" if err <= 1.5 else ("소폭 차이" if err <= 3.0 else "큰 차이")
        lines.append(f"- {team}: 예측 {predicted_runs:.1f}점 → 실제 {actual_runs}점 (오차 {err:.1f}, {accuracy})")
    lines.append("")

    # 요인 검증
    supporting = [f for f in factors if f.get("side") == actual_side]
    opposing   = [f for f in factors if f.get("side") and f.get("side") != actual_side]
    total_f = len([f for f in factors if f.get("side")])

    if is_correct:
        lines.append(f"### 적중 기여 요인 ({len(supporting)}/{total_f}개 일치)")
        for f in supporting:
            lines.append(f"- {f['icon']} **{f['title']}**: {f['desc']}")
        if opposing:
            lines.append(f"\n반대 신호 ({len(opposing)}개는 이번엔 맞지 않음):")
            for f in opposing:
                lines.append(f"- {f['icon']} {f['title']}: {f['desc']}")
        lines.append("")

        # Career win rate vindicated?
        career_notes = _check_career_accuracy(sim, actual_winner, win_pitcher, lose_pitcher)
        if career_notes:
            lines.append("### 통산 상성 적중")
            for note in career_notes:
                lines.append(f"- {note}")
            lines.append("")

        lines.append("### 핵심 적중 포인트")
        if supporting:
            hero = supporting[0]
            lines.append(f"**{hero['title']}**이 가장 결정적: {hero['desc']}")
        else:
            lines.append("시뮬레이션 종합 전력 우위가 경기 결과로 이어졌습니다.")

    else:
        # Wrong prediction
        lines.append(f"### 빗나간 원인 분석")
        reasons = _find_failure_reasons(sim, actual_winner, away_score, home_score, win_pitcher, lose_pitcher)
        for r in reasons:
            lines.append(f"- {r}")
        lines.append("")

        if opposing:
            lines.append(f"*실제로는 {actual_winner}를 지지했던 요인 ({len(opposing)}개):*")
            for f in opposing:
                lines.append(f"  - {f['icon']} {f['title']}: {f['desc']}")
            lines.append("")

        lines.append("### 다음 시뮬레이션 주목 포인트")
        suggestions = _build_suggestions(sim, actual_winner, away_score, home_score, win_pitcher, lose_pitcher)
        for s in suggestions:
            lines.append(f"- {s}")
        lines.append("")

        lines.append("### 시뮬레이션 개선 제안")
        improvements = _build_improvements(sim, actual_winner, away_score, home_score)
        for imp in improvements:
            lines.append(f"- {imp}")

    return "\n".join(lines)


def _check_career_accuracy(sim, actual_winner, win_pitcher, lose_pitcher) -> list:
    away = sim["away_team"]
    notes = []
    for pitcher, win_pct, games, record, opp, pitcher_team in [
        (sim.get("away_pitcher",""), sim.get("h2h_away_win_pct"), sim.get("h2h_away_games",0),
         sim.get("h2h_away_record",""), sim["home_team"], away),
        (sim.get("home_pitcher",""), sim.get("h2h_home_win_pct"), sim.get("h2h_home_games",0),
         sim.get("h2h_home_record",""), away, sim["home_team"]),
    ]:
        if not pitcher or win_pct is None or games < 3:
            continue
        dominated = win_pct >= 0.60
        struggled = win_pct <= 0.40
        if dominated and actual_winner == pitcher_team and win_pitcher == pitcher:
            notes.append(f"{pitcher} vs {opp} 통산 {record} (승률 {win_pct*100:.0f}%) — 상성 우위가 이번에도 유효")
        elif struggled and actual_winner != pitcher_team and lose_pitcher == pitcher:
            notes.append(f"{pitcher} vs {opp} 통산 {record} (승률 {win_pct*100:.0f}%) — 취약 상성이 이번에도 확인")
    return notes


def _find_failure_reasons(sim, actual_winner, away_score, home_score, win_pitcher, lose_pitcher) -> list:
    away = sim["away_team"]
    home = sim["home_team"]
    lambda_away = sim.get("lambda_away", 0) or 0
    lambda_home = sim.get("lambda_home", 0) or 0
    reasons = []

    actual_side = "away" if actual_winner == away else "home"
    predicted_side = "home" if actual_side == "away" else "away"

    # 스코어 이상치
    actual_runs = away_score if actual_winner == away else home_score
    predicted_runs = lambda_away if actual_winner == away else lambda_home
    loser_actual = home_score if actual_winner == away else away_score
    loser_predicted = lambda_home if actual_winner == away else lambda_away

    if actual_runs > predicted_runs * 1.4:
        reasons.append(f"{actual_winner} 타선 폭발 — 예측({predicted_runs:.1f}점) 대비 실제 {actual_runs}점, 타선이 시뮬레이션을 초과")
    if loser_actual < loser_predicted * 0.6:
        loser = home if actual_winner == away else away
        reasons.append(f"{loser} 타선 침묵 — 예측({loser_predicted:.1f}점)보다 훨씬 저조한 {loser_actual}점")

    # 패전 투수 분석
    for pitcher_name, pitcher_era, pitcher_team in [
        (sim.get("away_pitcher",""), sim.get("away_pitcher_era"), away),
        (sim.get("home_pitcher",""), sim.get("home_pitcher_era"), home),
    ]:
        if pitcher_name and pitcher_name == lose_pitcher and pitcher_era and pitcher_era < 4.0:
            reasons.append(
                f"{pitcher_name}(시즌 ERA {pitcher_era:.2f}) 이날 부진 — "
                f"시즌 누적 ERA가 당일 컨디션을 보장하지 않음"
            )

    # 상성 역전
    for pitcher, win_pct, games, record, opp, pitcher_team in [
        (sim.get("away_pitcher",""), sim.get("h2h_away_win_pct"), sim.get("h2h_away_games",0),
         sim.get("h2h_away_record",""), home, away),
        (sim.get("home_pitcher",""), sim.get("h2h_home_win_pct"), sim.get("h2h_home_games",0),
         sim.get("h2h_home_record",""), away, home),
    ]:
        if pitcher and win_pct is not None and games >= 3:
            if win_pct >= 0.65 and actual_winner == opp:
                reasons.append(
                    f"{pitcher} 통산 {opp} 상대 승률 {win_pct*100:.0f}%({record})이었지만 이번엔 상성 역전"
                )
            elif win_pct <= 0.35 and actual_winner == pitcher_team:
                reasons.append(
                    f"{pitcher} 통산 {opp} 상대 취약(승률 {win_pct*100:.0f}%)했으나 이번엔 극복"
                )

    if not reasons:
        margin = abs(sim["away_win_pct"] - sim["home_win_pct"])
        if margin < 10:
            reasons.append(f"전력 차이 {margin:.1f}%p — 시뮬레이션도 박빙으로 본 경기, 변수 발생")
        else:
            reasons.append("주요 변수(부상, 갑작스러운 라인업 변경 등) 시뮬레이션 외 요인")

    return reasons


def _build_suggestions(sim, actual_winner, away_score, home_score, win_pitcher, lose_pitcher) -> list:
    away = sim["away_team"]
    home = sim["home_team"]
    lambda_away = sim.get("lambda_away", 0) or 0
    lambda_home = sim.get("lambda_home", 0) or 0
    suggestions = []

    # 투수 컨디션
    for pitcher_name, pitcher_era, pitcher_team in [
        (sim.get("away_pitcher",""), sim.get("away_pitcher_era"), away),
        (sim.get("home_pitcher",""), sim.get("home_pitcher_era"), home),
    ]:
        if pitcher_name and pitcher_name == lose_pitcher and pitcher_era and pitcher_era < 4.5:
            suggestions.append(
                f"{pitcher_name} 최근 5경기 ERA 추세 확인 — 시즌 누적 ERA({pitcher_era:.2f})보다 "
                f"최근 흐름이 당일 성적을 더 잘 예측"
            )

    # 대폭 득점 초과
    actual_winner_runs = away_score if actual_winner == away else home_score
    predicted_winner_runs = lambda_away if actual_winner == away else lambda_home
    if actual_winner_runs > predicted_winner_runs * 1.3:
        suggestions.append(
            f"{actual_winner} 불펜 vs 타선 매치업 — 선발 이후 불펜 ERA/WHIP을 포함한 "
            f"전체 투수력으로 보정 필요"
        )

    # 상성 역전
    for pitcher, win_pct, games, opp, pitcher_team in [
        (sim.get("away_pitcher",""), sim.get("h2h_away_win_pct"), sim.get("h2h_away_games",0), home, away),
        (sim.get("home_pitcher",""), sim.get("h2h_home_win_pct"), sim.get("h2h_home_games",0), away, home),
    ]:
        if pitcher and win_pct is not None and games >= 5 and win_pct >= 0.65 and actual_winner == opp:
            suggestions.append(
                f"통산 상성 역전 발생 → {opp} 라인업 변화(좌/우 타자 비율) 확인 필요"
            )

    if not suggestions:
        suggestions.append("선발 교체 타이밍과 불펜 운영 상황 — 이닝 수 / 구수 기록 체크")
        suggestions.append("팀 최근 3경기 득점 추세 — 연속 경기 피로도 반영 고려")

    return suggestions[:4]


def _build_improvements(sim, actual_winner, away_score, home_score) -> list:
    away = sim["away_team"]
    home = sim["home_team"]
    lambda_away = sim.get("lambda_away", 0) or 0
    lambda_home = sim.get("lambda_home", 0) or 0
    improvements = []

    # ERA는 좋은데 실점이 많은 경우 → 불펜 가중치 상향 제안
    actual_side_runs = away_score if actual_winner == away else home_score
    predicted_side_runs = lambda_away if actual_winner == away else lambda_home
    if actual_side_runs > predicted_side_runs * 1.4:
        improvements.append(
            "불펜 ERA/WAR 가중치 상향 검토 — 선발 이후 이닝에서 실점이 예측을 초과함"
        )

    # 상성 역전이 잦으면 → 시즌 초반/후반 분리 제안
    for pitcher, win_pct, games in [
        (sim.get("away_pitcher",""), sim.get("h2h_away_win_pct"), sim.get("h2h_away_games",0)),
        (sim.get("home_pitcher",""), sim.get("h2h_home_win_pct"), sim.get("h2h_home_games",0)),
    ]:
        if pitcher and win_pct is not None and games >= 5:
            if (win_pct >= 0.65 and actual_winner != (away if pitcher == sim.get("away_pitcher") else home)):
                improvements.append(
                    f"통산 상성 데이터를 시즌 단위로 분리(최근 1년 vs 전체 통산) — "
                    f"로스터/코칭스태프 변화 후 상성이 달라질 수 있음"
                )
                break

    if not improvements:
        improvements.append(
            "선발 투수 최근 5경기 ERA를 별도 변수로 추가 — 시즌 ERA보다 현재 컨디션 반영"
        )

    return improvements[:2]
