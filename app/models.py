from app import db
from datetime import datetime


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
