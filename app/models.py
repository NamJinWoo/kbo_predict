from app import db
from datetime import datetime
import json


class TeamStat(db.Model):
    """statiz 홈에서 스크래핑한 팀 순위/스탯 스냅샷"""
    id = db.Column(db.Integer, primary_key=True)
    scraped_at = db.Column(db.DateTime, default=datetime.utcnow)
    team = db.Column(db.String(20), nullable=False)
    team_code = db.Column(db.String(10))
    rank = db.Column(db.Integer)
    games = db.Column(db.Integer)
    wins = db.Column(db.Integer)
    draws = db.Column(db.Integer)
    losses = db.Column(db.Integer)
    gb = db.Column(db.Float)
    win_pct = db.Column(db.Float)
    runs_scored = db.Column(db.Integer)
    runs_allowed = db.Column(db.Integer)

    @property
    def run_diff(self):
        return (self.runs_scored or 0) - (self.runs_allowed or 0)

    @property
    def runs_per_game(self):
        return round(self.runs_scored / self.games, 2) if self.games else 0

    @property
    def runs_allowed_per_game(self):
        return round(self.runs_allowed / self.games, 2) if self.games else 0

    def __repr__(self):
        return f"<TeamStat {self.team} {self.scraped_at.date()}>"


class TodayGame(db.Model):
    """오늘 경기 + 선발 투수 스탯 스냅샷"""
    id = db.Column(db.Integer, primary_key=True)
    scraped_at = db.Column(db.DateTime, default=datetime.utcnow)
    game_date = db.Column(db.Date, nullable=False)
    away_team = db.Column(db.String(20))
    home_team = db.Column(db.String(20))
    away_pitcher = db.Column(db.String(30))
    home_pitcher = db.Column(db.String(30))
    stats_json = db.Column(db.Text)   # {"away": {...}, "home": {...}}

    @property
    def stats(self):
        return json.loads(self.stats_json) if self.stats_json else {}

    def __repr__(self):
        return f"<TodayGame {self.game_date} {self.away_team}@{self.home_team}>"


class Prediction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    game_date = db.Column(db.Date, nullable=False)
    home_team = db.Column(db.String(20), nullable=False)
    away_team = db.Column(db.String(20), nullable=False)
    predicted_winner = db.Column(db.String(20), nullable=False)
    confidence = db.Column(db.Integer)          # 0~100 확신도
    analysis = db.Column(db.Text)               # 마크다운 분석 본문
    actual_winner = db.Column(db.String(20))    # 실제 결과 (경기 후 입력)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def is_correct(self):
        if self.actual_winner is None:
            return None
        return self.predicted_winner == self.actual_winner

    def __repr__(self):
        return f"<Prediction {self.game_date} {self.away_team}@{self.home_team}>"
