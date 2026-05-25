from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from app import db
from app.models import Prediction, TeamStat, TodayGame
from datetime import date, datetime
from typing import Optional
import markdown
import json

main = Blueprint("main", __name__)


@main.route("/")
def index():
    recent = (
        Prediction.query
        .order_by(Prediction.game_date.desc())
        .limit(10)
        .all()
    )
    # 최신 팀 순위 (스크래핑된 것 중 가장 최신)
    latest_scrape = (
        TeamStat.query
        .order_by(TeamStat.scraped_at.desc())
        .first()
    )
    standings = []
    if latest_scrape:
        standings = (
            TeamStat.query
            .filter_by(scraped_at=latest_scrape.scraped_at)
            .order_by(TeamStat.rank)
            .all()
        )
    return render_template("index.html", predictions=recent, standings=standings)


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
    # 팀별 최신 스탯
    latest = TeamStat.query.order_by(TeamStat.scraped_at.desc()).first()
    team_stats = {}
    if latest:
        for ts in TeamStat.query.filter_by(scraped_at=latest.scraped_at).all():
            team_stats[ts.team] = ts

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


@main.route("/stats")
def stats():
    all_preds = Prediction.query.filter(Prediction.actual_winner.isnot(None)).all()
    total = len(all_preds)
    correct = sum(1 for p in all_preds if p.is_correct)
    accuracy = round(correct / total * 100, 1) if total else 0
    return render_template("stats.html", total=total, correct=correct, accuracy=accuracy, predictions=all_preds)


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


@main.route("/admin/scrape", methods=["POST"])
def admin_scrape():
    from app.scraper import scrape_standings, scrape_today_games

    now = datetime.utcnow()
    scraped = 0

    try:
        standings = scrape_standings()
        for s in standings:
            ts = TeamStat(scraped_at=now, **s)
            db.session.add(ts)
        scraped += len(standings)
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": f"standings: {e}"}), 500

    try:
        games = scrape_today_games()
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

    db.session.commit()
    return jsonify({"ok": True, "scraped": scraped, "standings": len(standings), "games": len(games)})


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
        rows = [
            ("순위", away_stat.rank if away_stat else "-", home_stat.rank if home_stat else "-"),
            ("승률", away_stat.win_pct if away_stat else "-", home_stat.win_pct if home_stat else "-"),
            ("경기수", away_stat.games if away_stat else "-", home_stat.games if home_stat else "-"),
            ("승-무-패",
             f"{away_stat.wins}-{away_stat.draws}-{away_stat.losses}" if away_stat else "-",
             f"{home_stat.wins}-{home_stat.draws}-{home_stat.losses}" if home_stat else "-"),
            ("경기당 득점", away_stat.runs_per_game if away_stat else "-", home_stat.runs_per_game if home_stat else "-"),
            ("경기당 실점", away_stat.runs_allowed_per_game if away_stat else "-", home_stat.runs_allowed_per_game if home_stat else "-"),
            ("득실차", away_stat.run_diff if away_stat else "-", home_stat.run_diff if home_stat else "-"),
        ]
        for label, av, hv in rows:
            lines.append(f"| {label} | {av} | {hv} |")

    # 강점/약점 분석
    if away_stat and home_stat:
        lines.append("\n## 강점 / 약점 분석")
        lines.append(f"\n### {away} (원정)")
        if away_stat.runs_per_game > home_stat.runs_per_game:
            lines.append(f"- 공격력 우세: 경기당 {away_stat.runs_per_game}득점 vs {home_stat.runs_per_game}득점")
        else:
            lines.append(f"- 공격력 열세: 경기당 {away_stat.runs_per_game}득점 vs {home_stat.runs_per_game}득점")
        if away_stat.runs_allowed_per_game < home_stat.runs_allowed_per_game:
            lines.append(f"- 수비/투구 우세: 경기당 {away_stat.runs_allowed_per_game}실점 vs {home_stat.runs_allowed_per_game}실점")
        else:
            lines.append(f"- 수비/투구 열세: 경기당 {away_stat.runs_allowed_per_game}실점 vs {home_stat.runs_allowed_per_game}실점")

        lines.append(f"\n### {home} (홈)")
        if home_stat.runs_per_game > away_stat.runs_per_game:
            lines.append(f"- 공격력 우세: 경기당 {home_stat.runs_per_game}득점 vs {away_stat.runs_per_game}득점")
        else:
            lines.append(f"- 공격력 열세: 경기당 {home_stat.runs_per_game}득점 vs {away_stat.runs_per_game}득점")
        if home_stat.runs_allowed_per_game < away_stat.runs_allowed_per_game:
            lines.append(f"- 수비/투구 우세: 경기당 {home_stat.runs_allowed_per_game}실점 vs {away_stat.runs_allowed_per_game}실점")
        else:
            lines.append(f"- 수비/투구 열세: 경기당 {home_stat.runs_allowed_per_game}실점 vs {away_stat.runs_allowed_per_game}실점")

    lines.append("\n## 예측 근거\n\n<!-- 여기에 예측 근거를 직접 작성하세요 -->")
    lines.append("\n## 결론\n\n**예측: **  \n**확신도: **%")

    return "\n".join(lines)
