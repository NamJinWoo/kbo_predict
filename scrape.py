#!/usr/bin/env python3
"""Daily KBO data scraping script."""
import sys, os, json
from pathlib import Path
from datetime import datetime, date, timedelta

sys.path.insert(0, str(Path(__file__).parent))
os.chdir(Path(__file__).parent)

from app import create_app, db
from app.models import TeamStat, TodayGame, RecentGame
from app.scraper import scrape_all, backfill_missing_dates
from app.simulator import run_simulation, generate_result_analysis
from sqlalchemy import cast, Date as SaDate

RETENTION_DAYS = 30


def _recent_rpg(team: str, before_date: date, n: int = 7, min_games: int = 4):
    from app.simulator import LEAGUE_AVG_RUNS
    games = RecentGame.query.filter(
        RecentGame.game_date < before_date,
        db.or_(RecentGame.away_team == team, RecentGame.home_team == team),
        RecentGame.away_score.isnot(None),
    ).order_by(RecentGame.game_date.desc()).limit(n).all()
    if len(games) < min_games:
        return None
    scores = [g.away_score if g.away_team == team else g.home_score for g in games]
    avg = sum(scores) / len(scores)
    return max(LEAGUE_AVG_RUNS * 0.6, min(LEAGUE_AVG_RUNS * 1.6, avg))


def _attach_analysis(rg: RecentGame, r: dict) -> None:
    away_score = r.get("away_score") or 0
    home_score = r.get("home_score") or 0
    if away_score == 0 and home_score == 0:
        return

    actual_winner = r["away_team"] if away_score > home_score else r["home_team"]

    today_game = (
        TodayGame.query
        .filter_by(game_date=r["game_date"], away_team=r["away_team"], home_team=r["home_team"])
        .order_by(TodayGame.scraped_at.desc())
        .first()
    )
    if not today_game:
        return

    away_ts = (
        TeamStat.query
        .filter(TeamStat.team == r["away_team"], cast(TeamStat.scraped_at, SaDate) <= r["game_date"])
        .order_by(TeamStat.scraped_at.desc()).first()
    )
    home_ts = (
        TeamStat.query
        .filter(TeamStat.team == r["home_team"], cast(TeamStat.scraped_at, SaDate) <= r["game_date"])
        .order_by(TeamStat.scraped_at.desc()).first()
    )

    sim = run_simulation(
        r["away_team"], r["home_team"], away_ts, home_ts, today_game,
        recent_rpg_away=_recent_rpg(r["away_team"], r["game_date"]),
        recent_rpg_home=_recent_rpg(r["home_team"], r["game_date"]),
    )

    rg.predicted_winner = sim["predicted_winner"]
    rg.sim_confidence   = sim["confidence"]
    rg.sim_json = json.dumps({
        k: sim[k] for k in (
            "away_team", "home_team", "predicted_winner", "confidence",
            "away_win_pct", "home_win_pct", "lambda_away", "lambda_home",
            "away_pitcher", "home_pitcher", "away_pitcher_era", "home_pitcher_era",
            "h2h_away_win_pct", "h2h_home_win_pct",
            "h2h_away_games",   "h2h_home_games",
            "h2h_away_record",  "h2h_home_record",
            "factors",
        ) if k in sim
    }, ensure_ascii=False)
    rg.result_analysis = generate_result_analysis(
        sim, actual_winner, away_score, home_score,
        win_pitcher=r.get("win_pitcher", ""),
        lose_pitcher=r.get("lose_pitcher", ""),
    )

    hit = "✅" if sim["predicted_winner"] == actual_winner else "❌"
    print(f"    {hit} {r['game_date']} {r['away_team']} {away_score}-{home_score} {r['home_team']}")


def main():
    print("🔄 KBO 데이터 스크래핑 시작...")
    app = create_app()

    with app.app_context():
        now = datetime.utcnow()

        # 1. 오래된 데이터 정리
        TeamStat.query.delete()
        cutoff = date.today() - timedelta(days=RETENTION_DAYS)
        RecentGame.query.filter(RecentGame.game_date < cutoff).delete()
        TodayGame.query.filter(TodayGame.game_date < date.today()).delete()
        db.session.flush()

        # 2. 전체 스크래핑
        data = scrape_all()

        # 3. 팀 순위 저장
        for s in data["standings"]:
            db.session.add(TeamStat(
                scraped_at=now,
                team=s["team"],           team_code=s.get("team_code", ""),
                rank=s.get("rank"),       games=s.get("games"),
                wins=s.get("wins"),       draws=s.get("draws"),       losses=s.get("losses"),
                gb=s.get("gb"),           win_pct=s.get("win_pct"),
                runs_scored=s.get("runs_scored"),  runs_allowed=s.get("runs_allowed"),
                last10=s.get("last10", ""),        streak=s.get("streak", ""),
                home_record=s.get("home_record", ""), away_record=s.get("away_record", ""),
            ))
        db.session.flush()
        print(f"  ✓ 팀 순위 {len(data['standings'])}개")

        # 4. 예정 경기 저장 (선발 투수 정보 — 결과 분석에 필요하므로 먼저 저장)
        upcoming = data.get("today_games", [])
        new_dates = {g["game_date"] for g in upcoming}
        for d in new_dates:
            TodayGame.query.filter_by(game_date=d).delete()
        for g in upcoming:
            db.session.add(TodayGame(
                scraped_at=now,
                game_date=g["game_date"],
                away_team=g["away_team"],   home_team=g["home_team"],
                away_pitcher=g.get("away_pitcher", ""),
                home_pitcher=g.get("home_pitcher", ""),
                stats_json=json.dumps(g.get("stats", {}), ensure_ascii=False),
            ))
        db.session.flush()
        print(f"  ✓ 예정 경기 {len(upcoming)}개 ({sorted(new_dates)})")

        # 5. 최근 경기 결과 저장 + 결과 분석 첨부
        recent = data.get("recent_results", [])
        new_games = []
        for r in recent:
            exist = RecentGame.query.filter_by(
                game_date=r["game_date"], away_team=r["away_team"], home_team=r["home_team"]
            ).first()
            if not exist:
                rg = RecentGame(
                    game_date=r["game_date"],
                    away_team=r["away_team"],     home_team=r["home_team"],
                    away_score=r.get("away_score"), home_score=r.get("home_score"),
                    win_pitcher=r.get("win_pitcher", ""),
                    lose_pitcher=r.get("lose_pitcher", ""),
                    save_pitcher=r.get("save_pitcher", ""),
                )
                db.session.add(rg)
                new_games.append((rg, r))
        db.session.flush()
        print(f"  ✓ 최근 경기 {len(new_games)}개 신규")

        if new_games:
            print(f"  🤖 결과 분석 생성 ({len(new_games)}경기)...")
            for rg, r in new_games:
                _attach_analysis(rg, r)

        db.session.commit()

        # 6. 누락 날짜 백필 (최근 7일)
        existing = {rg.game_date for rg in RecentGame.query.filter(RecentGame.away_score.isnot(None)).all()}
        try:
            backfilled = backfill_missing_dates(existing, lookback_days=7)
            added = 0
            for r in backfilled:
                if not RecentGame.query.filter_by(
                    game_date=r["game_date"], away_team=r["away_team"], home_team=r["home_team"]
                ).first():
                    rg = RecentGame(
                        game_date=r["game_date"],
                        away_team=r["away_team"],       home_team=r["home_team"],
                        away_score=r.get("away_score"), home_score=r.get("home_score"),
                        win_pitcher=r.get("win_pitcher", ""),
                        lose_pitcher=r.get("lose_pitcher", ""),
                        save_pitcher=r.get("save_pitcher", ""),
                    )
                    db.session.add(rg)
                    added += 1
            if added:
                db.session.commit()
                print(f"  ✓ 백필 {added}개 추가")
        except Exception as e:
            print(f"  ⚠️  백필 실패: {e}")

    print("✅ 스크래핑 완료!")


if __name__ == "__main__":
    main()
