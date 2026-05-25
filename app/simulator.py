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
HOME_ADVANTAGE   = 1.04
N_SIM            = 10_000

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
) -> dict:
    np.random.seed(None)

    away_rpg  = (away_stat.runs_per_game         if away_stat else LEAGUE_AVG_RUNS)
    home_rpg  = (home_stat.runs_per_game         if home_stat else LEAGUE_AVG_RUNS)

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

    # ── ERA factor (season) ──
    def era_factor(era: Optional[float]) -> float:
        if era is None:
            return 1.0
        return max(0.55, min(1.75, era / LEAGUE_AVG_ERA))

    # ── Head-to-head ERA factor ──
    # away pitcher's h2h ERA vs home team batters → affects lambda_home
    # home pitcher's h2h ERA vs away team batters → affects lambda_away
    h2h_away_era = _parse_float((h2h.get("away") or {}).get("상대 평균자책", ""))
    h2h_home_era = _parse_float((h2h.get("home") or {}).get("상대 평균자책", ""))

    # Blend: season ERA 75% + h2h ERA 25% (h2h has small sample, don't over-weight)
    def blended_era_factor(season_era, h2h_era):
        sf = era_factor(season_era)
        hf = era_factor(h2h_era)
        if h2h_era and h2h_era > 0:
            return sf * 0.75 + hf * 0.25
        return sf

    away_era_factor = blended_era_factor(home_pitcher_era, h2h_home_era)  # home pitcher faces away bats
    home_era_factor = blended_era_factor(away_pitcher_era, h2h_away_era)  # away pitcher faces home bats

    # ── 최근 흐름 ──
    away_streak = (away_stat.streak_num if away_stat else 0)
    home_streak = (home_stat.streak_num if home_stat else 0)
    away_form = 1.0 + min(abs(away_streak), 5) * 0.015 * (1 if away_streak > 0 else -1)
    home_form = 1.0 + min(abs(home_streak), 5) * 0.015 * (1 if home_streak > 0 else -1)

    # ── 홈/원정 전적 ──
    away_road_wpct = _win_pct_from_record(away_stat.away_record if away_stat else "")
    home_home_wpct = _win_pct_from_record(home_stat.home_record if home_stat else "")
    road_factor       = max(0.7, min(1.3, (away_road_wpct / 0.5) if away_road_wpct else 1.0))
    home_field_factor = max(0.7, min(1.3, (home_home_wpct / 0.5) if home_home_wpct else 1.0))

    # ── 위험 타자 OPS 기반 보정 ──
    # vs_home_pitcher = away team's batters who hit well vs home pitcher → lambda_away up
    # vs_away_pitcher = home team's batters who hit well vs away pitcher → lambda_home up
    danger_away = _danger_factor(dangerous.get("vs_home_pitcher", []))
    danger_home = _danger_factor(dangerous.get("vs_away_pitcher", []))

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
    lambda_away = (
        away_rpg * away_era_factor * away_form * road_factor * danger_away * rm_away_factor
    )
    lambda_home = (
        home_rpg * home_era_factor * home_form * home_field_factor
        * HOME_ADVANTAGE * danger_home * rm_home_factor
    )

    lambda_away = max(2.0, min(10.0, lambda_away))
    lambda_home = max(2.0, min(10.0, lambda_home))

    # ── 포아송 시뮬레이션 ──
    away_scores = np.random.poisson(lambda_away, N_SIM)
    home_scores = np.random.poisson(lambda_home, N_SIM)

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
    final_away = round(final_away / total * 100, 1)
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
        "away_pitcher":        away_pitcher,
        "home_pitcher":        home_pitcher,
        "away_pitcher_era":    away_pitcher_era,
        "home_pitcher_era":    home_pitcher_era,
        "pitcher_stats_away":  pitcher_stats_away,
        "pitcher_stats_home":  pitcher_stats_home,
        "h2h":                 h2h,
        "team_comp":           team_comp,
        "dangerous_batters":   dangerous,
        "away_r10_wins":       away_r10,
        "home_r10_wins":       home_r10,
        "away_streak":         away_streak,
        "home_streak":         home_streak,
        "n_sim":               N_SIM,
        "factors":             _build_factors(
            away_team, home_team, away_stat, home_stat,
            away_pitcher, home_pitcher,
            away_pitcher_era, home_pitcher_era,
            lambda_away, lambda_home, final_away, final_home,
            h2h, team_comp, dangerous,
        ),
    }


def _build_factors(
    away_team, home_team, away_stat, home_stat,
    away_pitcher, home_pitcher, away_era, home_era,
    lambda_away, lambda_home, away_pct, home_pct,
    h2h, team_comp, dangerous,
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
        h2h_block = f"\n[선발투수 상대전적]\n"
        if ah2h.get("상대 평균자책"):
            h2h_block += f"  {sim['away_pitcher']} vs {home}타선: ERA {ah2h.get('상대 평균자책','?')}, OOPS {ah2h.get('상대 OOPS','?')}\n"
        if hh2h.get("상대 평균자책"):
            h2h_block += f"  {sim['home_pitcher']} vs {away}타선: ERA {hh2h.get('상대 평균자책','?')}, OOPS {hh2h.get('상대 OOPS','?')}\n"

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

    prompt = f"""당신은 KBO 프로야구 전문 분석가입니다.

경기: {away}(원정) @ {home}(홈)

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
{chr(10).join(f'- {f["icon"]} {f["title"]}: {f["desc"]}' for f in sim['factors'])}

위 모든 데이터를 종합하여 분석해주세요:

## 핵심 포인트
(3가지 불릿 — 이 경기의 핵심 관전 포인트)

## 선발 투수 분석
(시즌 스탯 + 상대팀 상대전적 ERA/OOPS 포함, 오늘 예상 퍼포먼스)

## 위협 타자 분석
(각 팀에서 상대 선발투수를 상대로 강한 타자, 그 타자들의 기록이 경기에 미칠 영향)

## 팀 흐름 및 전력 비교
(최근 흐름, WAR 비교, 최근 맞대결 성적)

## 승부 포인트
(이 경기 승패를 가를 결정적 요인 2~3가지)

## 최종 예측
(승팀 예측, 예상 스코어, 확신도와 이유)

전문적이고 날카롭게, 구체적 수치를 인용하며 작성하세요."""

    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
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
        # 상대전적 ERA
        h2h_key = "home" if side == "홈" else "away"
        h2h_era = _parse_float((h2h.get(h2h_key) or {}).get("상대 평균자책", ""))
        h2h_oops = (h2h.get(h2h_key) or {}).get("상대 OOPS", "")
        if h2h_era and h2h_era > 0:
            h2h_label = f"{away if side == '홈' else home}"
            lines.append(f"  → {h2h_label}타선 통산: ERA {h2h_era:.2f}, OOPS {h2h_oops}")
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
