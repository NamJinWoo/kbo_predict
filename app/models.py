from app import db
from datetime import datetime
import json


class TeamStat(db.Model):
    """statiz + KBO 공식 사이트에서 스크래핑한 팀 순위/스탯 스냅샷"""
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
    # statiz 제공
    runs_scored = db.Column(db.Integer)
    runs_allowed = db.Column(db.Integer)
    # KBO 공식 제공
    last10 = db.Column(db.String(20))    # 최근 10경기: "6승0무4패"
    streak = db.Column(db.String(10))    # 연속: "3승", "7패"
    home_record = db.Column(db.String(20))   # "14-1-9"
    away_record = db.Column(db.String(20))   # "14-0-9"

    @property
    def run_diff(self):
        return (self.runs_scored or 0) - (self.runs_allowed or 0)

    @property
    def runs_per_game(self):
        return round(self.runs_scored / self.games, 2) if self.games else 0

    @property
    def runs_allowed_per_game(self):
        return round(self.runs_allowed / self.games, 2) if self.games else 0

    @property
    def streak_num(self):
        """연속 숫자 추출 (양수=승, 음수=패)"""
        if not self.streak:
            return 0
        import re
        m = re.match(r'(\d+)(승|패)', self.streak)
        if not m:
            return 0
        n = int(m.group(1))
        return n if m.group(2) == '승' else -n

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


class RecentGame(db.Model):
    """완료된 경기 결과 (승리투수, 세이브 포함)"""
    id = db.Column(db.Integer, primary_key=True)
    scraped_at = db.Column(db.DateTime, default=datetime.utcnow)
    game_date = db.Column(db.Date, nullable=False)
    away_team = db.Column(db.String(20))
    home_team = db.Column(db.String(20))
    away_score = db.Column(db.Integer)
    home_score = db.Column(db.Integer)
    win_pitcher = db.Column(db.String(30))
    lose_pitcher = db.Column(db.String(30))
    save_pitcher = db.Column(db.String(30))
    hold_pitcher = db.Column(db.String(30))
    stadium = db.Column(db.String(40))

    @property
    def winner(self):
        if self.away_score is None or self.home_score is None:
            return None
        return self.away_team if self.away_score > self.home_score else self.home_team

    def __repr__(self):
        return f"<RecentGame {self.game_date} {self.away_team}{self.away_score}-{self.home_score}{self.home_team}>"


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
