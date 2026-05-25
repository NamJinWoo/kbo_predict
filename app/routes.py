from flask import Blueprint, render_template, request, redirect, url_for, abort
from app import db
from app.models import Prediction
from datetime import date, datetime
import markdown

main = Blueprint("main", __name__)


@main.route("/")
def index():
    recent = (
        Prediction.query
        .order_by(Prediction.game_date.desc())
        .limit(10)
        .all()
    )
    return render_template("index.html", predictions=recent)


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
    return render_template("new_prediction.html", today=date.today().isoformat())


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
