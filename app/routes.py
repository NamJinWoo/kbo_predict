from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from app import db
from app.models import Prediction, TeamStat, TodayGame, RecentGame
from datetime import date, datetime, timedelta
from typing import Optional
import markdown
import json
import subprocess
import threading
from pathlib import Path
from collections import defaultdict

main = Blueprint("main", __name__)

FANDOM_TEAM = "삼성"
TEAMS_ORDER = ["삼성", "KIA", "롯데", "LG", "두산", "한화", "SSG", "키움", "NC", "KT"]


def _samsung_first(games, attr1="away_team", attr2="home_team"):
    """삼성 경기를 목록 맨 앞으로 정렬"""
    priority = [g for g in games if getattr(g, attr1) == FANDOM_TEAM or getattr(g, attr2) == FANDOM_TEAM]
    others   = [g for g in games if g not in priority]
    return priority + others


def _recent_rpg(team: str, before_date: date, n: int = 7, min_games: int = 4) -> Optional[float]:
    """최근 n경기 실제 득점 평균 (D: 최근폼 블렌딩용)
    - min_games 미만이면 None 반환 (이상값 방지)
    - 리그 평균의 ±40% 이내로 클램핑 (단일 폭발 게임 영향 억제)
    """
    games = RecentGame.query.filter(
        RecentGame.game_date < before_date,
        db.or_(RecentGame.away_team == team, RecentGame.home_team == team),
        RecentGame.away_score.isnot(None),
    ).order_by(RecentGame.game_date.desc()).limit(n).all()
    if len(games) < min_games:
        return None
    scores = [g.away_score if g.away_team == team else g.home_score for g in games]
    avg = sum(scores) / len(scores)
    from app.simulator import LEAGUE_AVG_RUNS
    return max(LEAGUE_AVG_RUNS * 0.6, min(LEAGUE_AVG_RUNS * 1.6, avg))


def _pitcher_days_since_last_start(pitcher_name: str, before_date: date) -> Optional[int]:
    """RecentGame에서 투수의 마지막 등판일을 조회해 간격(일) 반환"""
    if not pitcher_name:
        return None
    recent = RecentGame.query.filter(
        RecentGame.game_date < before_date,
        db.or_(
            RecentGame.win_pitcher  == pitcher_name,
            RecentGame.lose_pitcher == pitcher_name,
        ),
    ).order_by(RecentGame.game_date.desc()).first()
    if recent:
        return (before_date - recent.game_date).days
    return None


@main.route("/")
def index():
    # 팀 순위
    latest = TeamStat.query.order_by(TeamStat.scraped_at.desc()).first()
    standings = []
    scraped_at = None
    if latest:
        scraped_at = latest.scraped_at
        standings = (
            TeamStat.query
            .filter_by(scraped_at=latest.scraped_at)
            .order_by(TeamStat.rank)
            .all()
        )

    # 최근 완료 경기 (가장 최근 날짜)
    latest_result = RecentGame.query.order_by(RecentGame.game_date.desc()).first()
    recent_games = []
    if latest_result:
        recent_games = (
            RecentGame.query
            .filter_by(game_date=latest_result.game_date)
            .order_by(RecentGame.id)
            .all()
        )

    # 다음 예정 경기 (가장 빠른 날짜의 TodayGame, 중복 제거)
    upcoming_first = TodayGame.query.order_by(TodayGame.game_date.asc()).first()
    upcoming_games = []
    if upcoming_first:
        rows = (
            TodayGame.query
            .filter_by(game_date=upcoming_first.game_date)
            .order_by(TodayGame.scraped_at.desc())
            .all()
        )
        seen = set()
        for g in rows:
            key = (g.away_team, g.home_team)
            if key not in seen:
                seen.add(key)
                upcoming_games.append(g)

    recent_games   = _samsung_first(recent_games)
    upcoming_games = _samsung_first(upcoming_games)

    from app.scraper import STADIUM_MAP
    return render_template(
        "index.html",
        standings=standings,
        scraped_at=scraped_at,
        recent_games=recent_games,
        upcoming_games=upcoming_games,
        stadium_map=STADIUM_MAP,
        fandom_team=FANDOM_TEAM,
    )


@main.route("/predictions")
def predictions():
    page = request.args.get("page", 1, type=int)
    query = Prediction.query.order_by(Prediction.game_date.desc())
    pagination = query.paginate(page=page, per_page=20)
    return render_template("predictions.html", pagination=pagination)


@main.route("/predictions/<int:pred_id>")
def prediction_detail(pred_id):
    pred = Prediction.query.get_or_404(pred_id)
    html_analysis = markdown.markdown(pred.analysis or "", extensions=["extra", "nl2br"])
    return render_template("prediction_detail.html", pred=pred, html_analysis=html_analysis)


@main.route("/new", methods=["GET", "POST"])
def new_prediction():
    if request.method == "POST":
        game_date = datetime.strptime(request.form["game_date"], "%Y-%m-%d").date()
        pred = Prediction(
            game_date=game_date,
            home_team=request.form["home_team"],
            away_team=request.form["away_team"],
            predicted_winner=request.form["predicted_winner"],
            confidence=request.form.get("confidence", type=int),
            analysis=request.form.get("analysis", ""),
        )
        db.session.add(pred)
        db.session.commit()
        return redirect(url_for("main.prediction_detail", pred_id=pred.id))

    # 오늘 경기 정보 (자동 초안용)
    today_games = (
        TodayGame.query
        .filter_by(game_date=date.today())
        .order_by(TodayGame.scraped_at.desc())
        .all()
    )
    # 팀별 최신 스탯 (JS에서 tojson 사용하므로 plain dict로 변환)
    latest = TeamStat.query.order_by(TeamStat.scraped_at.desc()).first()
    team_stats = {}
    if latest:
        for ts in TeamStat.query.filter_by(scraped_at=latest.scraped_at).all():
            team_stats[ts.team] = {
                "rank": ts.rank,
                "wins": ts.wins,
                "draws": ts.draws,
                "losses": ts.losses,
                "win_pct": ts.win_pct,
                "runs_per_game": ts.runs_per_game,
                "runs_allowed_per_game": ts.runs_allowed_per_game,
                "run_diff": ts.run_diff,
            }

    return render_template(
        "new_prediction.html",
        today=date.today().isoformat(),
        today_games=today_games,
        team_stats=team_stats,
    )


@main.route("/predictions/<int:pred_id>/result", methods=["POST"])
def update_result(pred_id):
    pred = Prediction.query.get_or_404(pred_id)
    pred.actual_winner = request.form["actual_winner"]
    db.session.commit()
    return redirect(url_for("main.prediction_detail", pred_id=pred_id))


@main.route("/simulate")
def simulate():
    away = request.args.get("away", "")
    home = request.args.get("home", "")
    game_date_str = request.args.get("date", date.today().isoformat())

    if not away or not home:
        return redirect(url_for("main.index"))

    latest = TeamStat.query.order_by(TeamStat.scraped_at.desc()).first()
    team_stats = {}
    if latest:
        for ts in TeamStat.query.filter_by(scraped_at=latest.scraped_at).all():
            team_stats[ts.team] = ts

    away_stat = team_stats.get(away)
    home_stat = team_stats.get(home)

    try:
        gd = datetime.strptime(game_date_str, "%Y-%m-%d").date()
    except ValueError:
        gd = date.today()

    game_info = (
        TodayGame.query
        .filter_by(game_date=gd, away_team=away, home_team=home)
        .order_by(TodayGame.scraped_at.desc())
        .first()
    )

    fatigue_away_days = fatigue_home_days = None
    if game_info:
        fatigue_away_days = _pitcher_days_since_last_start(game_info.away_pitcher, gd)
        fatigue_home_days = _pitcher_days_since_last_start(game_info.home_pitcher, gd)

    recent_away = _recent_rpg(away, gd)
    recent_home = _recent_rpg(home, gd)

    from app.simulator import run_simulation, generate_analysis
    sim = run_simulation(away, home, away_stat, home_stat, game_info,
                         fatigue_away_days=fatigue_away_days,
                         fatigue_home_days=fatigue_home_days,
                         recent_rpg_away=recent_away,
                         recent_rpg_home=recent_home)
    analysis = generate_analysis(sim, away_stat, home_stat)
    html_analysis = markdown.markdown(analysis, extensions=["extra", "nl2br"])

    return render_template(
        "simulation.html",
        sim=sim,
        away_stat=away_stat,
        home_stat=home_stat,
        game_info=game_info,
        html_analysis=html_analysis,
        game_date=game_date_str,
    )


@main.route("/stats")
def stats():
    all_preds = Prediction.query.filter(Prediction.actual_winner.isnot(None)).all()
    total = len(all_preds)
    correct = sum(1 for p in all_preds if p.is_correct)
    accuracy = round(correct / total * 100, 1) if total else 0

    # RecentGame 기반 시뮬레이션 예측 정확도 트렌드
    # winner는 @property이므로 scores로 필터링
    sim_games = RecentGame.query.filter(
        RecentGame.predicted_winner.isnot(None),
        RecentGame.away_score.isnot(None),
        RecentGame.home_score.isnot(None),
    ).order_by(RecentGame.game_date.asc()).all()

    # 월별 집계
    monthly: dict[str, dict] = defaultdict(lambda: {"total": 0, "correct": 0})
    for g in sim_games:
        if g.game_date:
            key = g.game_date.strftime("%Y-%m")
            monthly[key]["total"] += 1
            if g.predicted_winner == g.winner:
                monthly[key]["correct"] += 1

    monthly_labels = sorted(monthly.keys())
    monthly_accuracy = [
        round(monthly[m]["correct"] / monthly[m]["total"] * 100, 1)
        if monthly[m]["total"] else 0
        for m in monthly_labels
    ]
    monthly_totals = [monthly[m]["total"] for m in monthly_labels]

    # 신뢰도 구간별 정확도
    confidence_buckets = [
        {"label": "50~59%", "min": 50, "max": 60},
        {"label": "60~69%", "min": 60, "max": 70},
        {"label": "70~79%", "min": 70, "max": 80},
        {"label": "80%+",   "min": 80, "max": 101},
    ]
    for b in confidence_buckets:
        bucket_games = [
            g for g in sim_games
            if g.sim_confidence and b["min"] <= g.sim_confidence < b["max"]
        ]
        b["total"] = len(bucket_games)
        b["correct"] = sum(1 for g in bucket_games if g.predicted_winner == g.winner)
        b["accuracy"] = round(b["correct"] / b["total"] * 100, 1) if b["total"] else None

    # 팀별 예측 적중률
    team_acc_map: dict = defaultdict(lambda: {"total": 0, "correct": 0, "home_total": 0, "home_correct": 0})
    for g in sim_games:
        for team, is_home in [(g.away_team, False), (g.home_team, True)]:
            team_acc_map[team]["total"] += 1
            if g.predicted_winner == g.winner:
                team_acc_map[team]["correct"] += 1
            if is_home:
                team_acc_map[team]["home_total"] += 1
                if g.predicted_winner == g.winner:
                    team_acc_map[team]["home_correct"] += 1

    team_accuracy_list = []
    for team in TEAMS_ORDER:
        if team not in team_acc_map:
            continue
        d = team_acc_map[team]
        away_total   = d["total"]   - d["home_total"]
        away_correct = d["correct"] - d["home_correct"]
        team_accuracy_list.append({
            "team":          team,
            "total":         d["total"],
            "correct":       d["correct"],
            "accuracy":      round(d["correct"] / d["total"] * 100, 1) if d["total"] else None,
            "home_total":    d["home_total"],
            "home_correct":  d["home_correct"],
            "home_accuracy": round(d["home_correct"] / d["home_total"] * 100, 1) if d["home_total"] else None,
            "away_total":    away_total,
            "away_correct":  away_correct,
            "away_accuracy": round(away_correct / away_total * 100, 1) if away_total else None,
        })

    # 팀 상성 히트맵 (RecentGame 전체 기록 기반)
    all_games = RecentGame.query.filter(
        RecentGame.away_score.isnot(None),
        RecentGame.home_score.isnot(None),
    ).all()
    TEAMS = TEAMS_ORDER
    h2h_matrix: dict[tuple, dict] = defaultdict(lambda: {"wins": 0, "total": 0})
    for g in all_games:
        away, home, winner = g.away_team, g.home_team, g.winner
        if away not in TEAMS or home not in TEAMS:
            continue
        h2h_matrix[(away, home)]["total"] += 1
        h2h_matrix[(home, away)]["total"] += 1
        if winner == away:
            h2h_matrix[(away, home)]["wins"] += 1
        else:
            h2h_matrix[(home, away)]["wins"] += 1

    heatmap = []
    for row_team in TEAMS:
        row = []
        for col_team in TEAMS:
            if row_team == col_team:
                row.append(None)
            else:
                d = h2h_matrix.get((row_team, col_team), {"wins": 0, "total": 0})
                if d["total"] >= 1:
                    row.append({
                        "wins": d["wins"],
                        "total": d["total"],
                        "pct": round(d["wins"] / d["total"] * 100),
                    })
                else:
                    row.append(None)
        heatmap.append({"team": row_team, "data": row})

    sim_total = len(sim_games)
    sim_correct = sum(1 for g in sim_games if g.predicted_winner == g.winner)
    sim_accuracy = round(sim_correct / sim_total * 100, 1) if sim_total else 0

    return render_template(
        "stats.html",
        total=total, correct=correct, accuracy=accuracy, predictions=all_preds,
        sim_total=sim_total, sim_correct=sim_correct, sim_accuracy=sim_accuracy,
        monthly_labels=json.dumps(monthly_labels),
        monthly_accuracy=json.dumps(monthly_accuracy),
        monthly_totals=json.dumps(monthly_totals),
        confidence_buckets=confidence_buckets,
        heatmap=heatmap,
        heatmap_teams=TEAMS,
        team_accuracy_list=team_accuracy_list,
        fandom_team=FANDOM_TEAM,
    )


@main.route("/teams")
def teams():
    latest = TeamStat.query.order_by(TeamStat.scraped_at.desc()).first()
    standings = []
    scraped_at = None
    if latest:
        scraped_at = latest.scraped_at
        standings = (
            TeamStat.query
            .filter_by(scraped_at=latest.scraped_at)
            .order_by(TeamStat.rank)
            .all()
        )
    today_games = (
        TodayGame.query
        .filter_by(game_date=date.today())
        .order_by(TodayGame.scraped_at.desc())
        .all()
    )
    # 중복 제거 (같은 날 여러 번 스크래핑된 경우 최신 1경기만)
    seen = set()
    unique_games = []
    for g in today_games:
        key = (g.away_team, g.home_team)
        if key not in seen:
            seen.add(key)
            unique_games.append(g)
    return render_template(
        "teams.html",
        standings=standings,
        scraped_at=scraped_at,
        today_games=unique_games,
    )


@main.route("/game/<game_date>/<away_team>/<home_team>")
def game_detail(game_date: str, away_team: str, home_team: str):
    from app.scraper import scrape_game_boxscore
    try:
        gd = datetime.strptime(game_date, "%Y-%m-%d").date()
    except ValueError:
        return redirect(url_for("main.index"))

    recent = (
        RecentGame.query
        .filter_by(game_date=gd, away_team=away_team, home_team=home_team)
        .order_by(RecentGame.id.desc())
        .first()
    )

    boxscore = scrape_game_boxscore(gd, away_team, home_team)

    # 이닝 헤더: 타자 테이블의 숫자 컬럼만 추출
    inning_headers = []
    if boxscore.get("away_batters"):
        inning_headers = [
            h for h in boxscore["away_batters"]["headers"]
            if h.isdigit()
        ]

    result_analysis_html = ""
    if recent and recent.result_analysis:
        result_analysis_html = markdown.markdown(
            recent.result_analysis, extensions=["extra", "nl2br"]
        )

    return render_template(
        "game_detail.html",
        recent=recent,
        boxscore=boxscore,
        inning_headers=inning_headers,
        game_date=game_date,
        away_team=away_team,
        home_team=home_team,
        result_analysis_html=result_analysis_html,
    )


RETENTION_DAYS = 30  # RecentGame 보관 기간


@main.route("/admin/scrape", methods=["POST"])
def admin_scrape():
    now = datetime.utcnow()
    scraped = 0

    # ── 사전 정리 ──────────────────────────────────────────────
    # TeamStat: 이전 스크래핑 전체 삭제 (최신 1회분만 유지)
    TeamStat.query.delete()
    # RecentGame: 30일 초과분 삭제
    cutoff = date.today() - timedelta(days=RETENTION_DAYS)
    RecentGame.query.filter(RecentGame.game_date < cutoff).delete()
    # TodayGame: 오늘 이전 날짜 삭제
    TodayGame.query.filter(TodayGame.game_date < date.today()).delete()
    db.session.flush()

    try:
        from app.scraper import scrape_all
        data = scrape_all()

        standings = data["standings"]
        for s in standings:
            ts = TeamStat(
                scraped_at=now,
                team=s["team"],
                team_code=s.get("team_code", ""),
                rank=s.get("rank"),
                games=s.get("games"),
                wins=s.get("wins"),
                draws=s.get("draws"),
                losses=s.get("losses"),
                gb=s.get("gb"),
                win_pct=s.get("win_pct"),
                runs_scored=s.get("runs_scored"),
                runs_allowed=s.get("runs_allowed"),
                last10=s.get("last10", ""),
                streak=s.get("streak", ""),
                home_record=s.get("home_record", ""),
                away_record=s.get("away_record", ""),
            )
            db.session.add(ts)
        scraped += len(standings)
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": f"standings: {e}"}), 500

    try:
        games = data["today_games"]
        # 스크레이프된 날짜만 교체 (다른 날짜 TodayGame 보존)
        new_dates = {g["game_date"] for g in games}
        for d in new_dates:
            TodayGame.query.filter_by(game_date=d).delete()
        for g in games:
            tg = TodayGame(
                scraped_at=now,
                game_date=g["game_date"],
                away_team=g["away_team"],
                home_team=g["home_team"],
                away_pitcher=g.get("away_pitcher", ""),
                home_pitcher=g.get("home_pitcher", ""),
                stats_json=json.dumps(g.get("stats", {}), ensure_ascii=False),
            )
            db.session.add(tg)
        scraped += len(games)
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": f"today_games: {e}"}), 500

    try:
        recent_list = data.get("recent_results", [])
        added = 0
        for r in recent_list:
            # 같은 날짜+경기 조합이 이미 있으면 스킵 (중복 방지)
            exists = RecentGame.query.filter_by(
                game_date=r["game_date"],
                away_team=r["away_team"],
                home_team=r["home_team"],
            ).first()
            if exists:
                continue
            rg = RecentGame(
                scraped_at=now,
                game_date=r["game_date"],
                away_team=r["away_team"],
                home_team=r["home_team"],
                away_score=r["away_score"],
                home_score=r["home_score"],
                win_pitcher=r.get("win_pitcher", ""),
                lose_pitcher=r.get("lose_pitcher", ""),
                save_pitcher=r.get("save_pitcher", ""),
                hold_pitcher=r.get("hold_pitcher", ""),
                stadium=r.get("stadium", ""),
            )
            db.session.add(rg)
            added += 1

            # 경기 결과 vs 시뮬레이션 예측 분석 자동 생성
            try:
                _attach_result_analysis(rg, r)
            except Exception:
                pass  # 분석 실패해도 스크래핑은 계속

        scraped += added
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": f"recent_results: {e}"}), 500

    # ── 누락 날짜 백필 (KBO API) ───────────────────────────────────
    try:
        from app.scraper import backfill_missing_dates
        existing_dates = {r.game_date for r in RecentGame.query.with_entities(RecentGame.game_date).all()}
        backfill_list = backfill_missing_dates(existing_dates, lookback_days=RETENTION_DAYS)
        backfilled = 0
        for r in backfill_list:
            exists = RecentGame.query.filter_by(
                game_date=r["game_date"], away_team=r["away_team"], home_team=r["home_team"]
            ).first()
            if exists:
                continue
            rg = RecentGame(
                scraped_at=now,
                game_date=r["game_date"],
                away_team=r["away_team"],
                home_team=r["home_team"],
                away_score=r["away_score"],
                home_score=r["home_score"],
                win_pitcher=r.get("win_pitcher", ""),
                lose_pitcher=r.get("lose_pitcher", ""),
                save_pitcher=r.get("save_pitcher", ""),
                hold_pitcher=r.get("hold_pitcher", ""),
                stadium=r.get("stadium", ""),
            )
            db.session.add(rg)
            backfilled += 1
        scraped += backfilled
    except Exception:
        pass  # 백필 실패해도 메인 스크래핑 결과는 유지

    db.session.commit()

    # 블로그 빌드를 백그라운드로 실행 (완료 안 기다림)
    build_script = Path(__file__).parent.parent / "build_blog.py"
    if build_script.exists():
        # Use venv Python to ensure Flask/deps are available
        venv_python = Path(__file__).parent.parent / ".venv" / "bin" / "python"
        python_bin = str(venv_python) if venv_python.exists() else "python3"
        def _build():
            subprocess.run(
                [python_bin, str(build_script)],
                cwd=str(build_script.parent),
                capture_output=True,
            )
        threading.Thread(target=_build, daemon=True).start()

    return jsonify({
        "ok": True,
        "scraped": scraped,
        "standings": len(standings),
        "games": len(games),
        "recent": added,
        "blog": "building",
    })


@main.route("/api/draft")
def api_draft():
    """선택한 두 팀의 스탯 기반 분석 초안 텍스트 반환"""
    away = request.args.get("away", "")
    home = request.args.get("home", "")
    game_date = request.args.get("date", date.today().isoformat())

    latest = TeamStat.query.order_by(TeamStat.scraped_at.desc()).first()
    team_stats = {}
    if latest:
        for ts in TeamStat.query.filter_by(scraped_at=latest.scraped_at).all():
            team_stats[ts.team] = ts

    today_game = (
        TodayGame.query
        .filter_by(game_date=datetime.strptime(game_date, "%Y-%m-%d").date(),
                   away_team=away, home_team=home)
        .order_by(TodayGame.scraped_at.desc())
        .first()
    )

    draft = _build_draft(away, home, team_stats, today_game)
    return jsonify({"draft": draft})


def _build_draft(away: str, home: str, team_stats: dict, game: Optional[TodayGame]) -> str:
    lines = []

    # 선발 투수 섹션
    if game:
        lines.append("## 선발 투수")
        lines.append(f"- **{away}**: {game.away_pitcher or '미정'}")
        lines.append(f"- **{home}**: {game.home_pitcher or '미정'}")
        stats = game.stats
        if stats.get("away"):
            lines.append(f"\n### {game.away_pitcher} 시즌 스탯")
            for k, v in stats["away"].items():
                lines.append(f"- {k}: {v}")
        if stats.get("home"):
            lines.append(f"\n### {game.home_pitcher} 시즌 스탯")
            for k, v in stats["home"].items():
                lines.append(f"- {k}: {v}")

    # 팀 성적 비교 섹션
    away_stat = team_stats.get(away)
    home_stat = team_stats.get(home)
    if away_stat or home_stat:
        lines.append("\n## 팀 성적 비교")
        lines.append("| 항목 | {} | {} |".format(away, home))
        lines.append("|------|------|------|")

        def v(stat, attr, default="-"):
            return getattr(stat, attr) if stat else default

        table_rows = [
            ("순위",          v(away_stat,"rank"),      v(home_stat,"rank")),
            ("승률",          v(away_stat,"win_pct"),   v(home_stat,"win_pct")),
            ("승-무-패",
             f"{away_stat.wins}-{away_stat.draws}-{away_stat.losses}" if away_stat else "-",
             f"{home_stat.wins}-{home_stat.draws}-{home_stat.losses}" if home_stat else "-"),
            ("최근 10경기",   v(away_stat,"last10"),    v(home_stat,"last10")),
            ("현재 연속",     v(away_stat,"streak"),    v(home_stat,"streak")),
            ("홈 전적",       v(away_stat,"home_record"), v(home_stat,"home_record")),
            ("원정 전적",     v(away_stat,"away_record"), v(home_stat,"away_record")),
            ("경기당 득점",   v(away_stat,"runs_per_game"),         v(home_stat,"runs_per_game")),
            ("경기당 실점",   v(away_stat,"runs_allowed_per_game"), v(home_stat,"runs_allowed_per_game")),
            ("득실차",        v(away_stat,"run_diff"),  v(home_stat,"run_diff")),
        ]
        for label, av, hv in table_rows:
            lines.append(f"| {label} | {av} | {hv} |")

    # 강점/약점 분석
    if away_stat and home_stat:
        lines.append("\n## 강점 / 약점 분석")

        lines.append(f"\n### {away} (원정)")
        if away_stat.runs_per_game > home_stat.runs_per_game:
            lines.append(f"- 공격력 우세: 경기당 {away_stat.runs_per_game}득점 (상대 {home_stat.runs_per_game})")
        else:
            lines.append(f"- 공격력 열세: 경기당 {away_stat.runs_per_game}득점 (상대 {home_stat.runs_per_game})")
        if away_stat.runs_allowed_per_game < home_stat.runs_allowed_per_game:
            lines.append(f"- 투구/수비 우세: 경기당 {away_stat.runs_allowed_per_game}실점 (상대 {home_stat.runs_allowed_per_game})")
        else:
            lines.append(f"- 투구/수비 열세: 경기당 {away_stat.runs_allowed_per_game}실점 (상대 {home_stat.runs_allowed_per_game})")
        if away_stat.away_record:
            lines.append(f"- 원정 전적: {away_stat.away_record}")
        if away_stat.streak:
            sn = away_stat.streak_num
            if sn <= -3:
                lines.append(f"- 현재 {abs(sn)}연패 중 → 흐름 나쁨 주의")
            elif sn >= 3:
                lines.append(f"- 현재 {sn}연승 중 → 상승세")

        lines.append(f"\n### {home} (홈)")
        if home_stat.runs_per_game > away_stat.runs_per_game:
            lines.append(f"- 공격력 우세: 경기당 {home_stat.runs_per_game}득점 (상대 {away_stat.runs_per_game})")
        else:
            lines.append(f"- 공격력 열세: 경기당 {home_stat.runs_per_game}득점 (상대 {away_stat.runs_per_game})")
        if home_stat.runs_allowed_per_game < away_stat.runs_allowed_per_game:
            lines.append(f"- 투구/수비 우세: 경기당 {home_stat.runs_allowed_per_game}실점 (상대 {away_stat.runs_allowed_per_game})")
        else:
            lines.append(f"- 투구/수비 열세: 경기당 {home_stat.runs_allowed_per_game}실점 (상대 {away_stat.runs_allowed_per_game})")
        if home_stat.home_record:
            lines.append(f"- 홈 전적: {home_stat.home_record}")
        if home_stat.streak:
            sn = home_stat.streak_num
            if sn <= -3:
                lines.append(f"- 현재 {abs(sn)}연패 중 → 흐름 나쁨 주의")
            elif sn >= 3:
                lines.append(f"- 현재 {sn}연승 중 → 상승세")

    lines.append("\n## 예측 근거\n\n<!-- 여기에 예측 근거를 직접 작성하세요 -->")
    lines.append("\n## 결론\n\n**예측: **  \n**확신도: **%")

    return "\n".join(lines)


def _attach_result_analysis(rg: "RecentGame", r: dict) -> None:
    """새로 저장되는 RecentGame에 시뮬레이션 예측 vs 결과 분석을 첨부한다."""
    from app.simulator import run_simulation, generate_result_analysis
    from sqlalchemy import cast, Date as SaDate

    away_score = r.get("away_score") or 0
    home_score = r.get("home_score") or 0
    if away_score == 0 and home_score == 0:
        return  # 취소/무효 경기

    actual_winner = r["away_team"] if away_score > home_score else r["home_team"]

    # 해당 날짜의 TodayGame 찾기 (예측 데이터)
    today_game = (
        TodayGame.query
        .filter_by(game_date=r["game_date"], away_team=r["away_team"], home_team=r["home_team"])
        .order_by(TodayGame.scraped_at.desc())
        .first()
    )
    if not today_game:
        return  # 선발 정보 없으면 분석 불가

    # 경기 날짜 이전의 가장 최신 TeamStat 스냅샷
    away_ts = (
        TeamStat.query
        .filter(TeamStat.team == r["away_team"])
        .filter(cast(TeamStat.scraped_at, SaDate) <= r["game_date"])
        .order_by(TeamStat.scraped_at.desc())
        .first()
    )
    home_ts = (
        TeamStat.query
        .filter(TeamStat.team == r["home_team"])
        .filter(cast(TeamStat.scraped_at, SaDate) <= r["game_date"])
        .order_by(TeamStat.scraped_at.desc())
        .first()
    )

    away_recent = _recent_rpg(r["away_team"], r["game_date"])
    home_recent = _recent_rpg(r["home_team"], r["game_date"])
    sim = run_simulation(r["away_team"], r["home_team"], away_ts, home_ts, today_game,
                         recent_rpg_away=away_recent, recent_rpg_home=home_recent)

    rg.predicted_winner = sim["predicted_winner"]
    rg.sim_confidence   = sim["confidence"]

    # 분석에 필요한 핵심 데이터만 저장
    compact = {
        k: sim[k] for k in (
            "away_team", "home_team", "predicted_winner", "confidence",
            "away_win_pct", "home_win_pct", "lambda_away", "lambda_home",
            "away_pitcher", "home_pitcher", "away_pitcher_era", "home_pitcher_era",
            "h2h_away_win_pct", "h2h_home_win_pct",
            "h2h_away_games",   "h2h_home_games",
            "h2h_away_record",  "h2h_home_record",
            "factors",
        ) if k in sim
    }
    rg.sim_json = json.dumps(compact, ensure_ascii=False)

    rg.result_analysis = generate_result_analysis(
        sim,
        actual_winner,
        away_score,
        home_score,
        win_pitcher=r.get("win_pitcher", ""),
        lose_pitcher=r.get("lose_pitcher", ""),
    )


@main.route("/calendar")
@main.route("/calendar/<int:year>/<int:month>")
def calendar_view(year=None, month=None):
    import calendar as cal_module

    today = date.today()
    if year is None:
        year = today.year
    if month is None:
        month = today.month

    year  = max(2020, min(2030, year))
    month = max(1,    min(12,   month))

    from_date = date(year, month, 1)
    if month == 12:
        to_date = date(year + 1, 1, 1)
    else:
        to_date = date(year, month + 1, 1)

    games = RecentGame.query.filter(
        RecentGame.game_date >= from_date,
        RecentGame.game_date < to_date,
        RecentGame.away_score.isnot(None),
    ).order_by(RecentGame.game_date).all()

    games_by_day: dict[int, list] = defaultdict(list)
    for g in games:
        games_by_day[g.game_date.day].append(g)

    # 삼성 경기를 각 날짜 내에서 맨 앞으로
    for day in games_by_day:
        lst = games_by_day[day]
        samsung = [g for g in lst if g.away_team == FANDOM_TEAM or g.home_team == FANDOM_TEAM]
        others  = [g for g in lst if g not in samsung]
        games_by_day[day] = samsung + others

    prev_month = month - 1 if month > 1 else 12
    prev_year  = year      if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year  = year      if month < 12 else year + 1

    return render_template(
        "calendar.html",
        year=year, month=month,
        cal=cal_module.monthcalendar(year, month),
        games_by_day=games_by_day,
        prev_year=prev_year, prev_month=prev_month,
        next_year=next_year, next_month=next_month,
        month_name=f"{year}년 {month}월",
        fandom_team=FANDOM_TEAM,
    )
