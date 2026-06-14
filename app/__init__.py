from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
from pathlib import Path
import os

load_dotenv(override=True)

db = SQLAlchemy()

BASE_DIR = Path(__file__).parent.parent


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev")
    default_db = f"sqlite:///{BASE_DIR / 'data' / 'kbo.db'}"
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", default_db)
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    from app.routes import main
    app.register_blueprint(main)

    with app.app_context():
        db.create_all()
        _migrate_db(db)

    return app


def _migrate_db(db):
    """기존 DB에 새 컬럼이 없으면 ALTER TABLE로 추가 (SQLite 호환)"""
    new_cols = [
        ("recent_game", "predicted_winner", "VARCHAR(20)"),
        ("recent_game", "sim_confidence",   "FLOAT"),
        ("recent_game", "sim_json",         "TEXT"),
        ("recent_game", "result_analysis",  "TEXT"),
    ]
    with db.engine.connect() as conn:
        for table, col, dtype in new_cols:
            try:
                conn.execute(db.text(f"ALTER TABLE {table} ADD COLUMN {col} {dtype}"))
                conn.commit()
            except Exception:
                pass  # 이미 존재하면 무시
