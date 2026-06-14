#!/usr/bin/env python3
"""
KBO 블로그 빌드 스크립트
실행: python build_blog.py
→ HTML 생성 후 ~/githubBlog/kbo/ 에 쓰고 GitHub Pages push
"""
import os, sys, json, subprocess
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.chdir(Path(__file__).parent)

from app import create_app
from app.models import RecentGame, TodayGame, TeamStat
from app.simulator import run_simulation, generate_analysis
import markdown as md_lib

BLOG_ROOT = Path(os.environ.get("BLOG_ROOT", "/Users/jinwoo/githubBlog"))
OUT_DIR   = BLOG_ROOT / "kbo"


# ── CSS / 공통 스타일 ────────────────────────────────────────────

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: #0f172a; color: #e2e8f0; min-height: 100vh;
}
a { color: inherit; text-decoration: none; }

/* ── 헤더 ── */
.site-header {
  background: linear-gradient(135deg, #1e3a5f 0%, #0f2744 100%);
  padding: 20px 16px 16px;
  border-bottom: 2px solid #3b82f6;
}
.site-header h1 {
  font-size: 1.4rem; font-weight: 800; color: #fff; letter-spacing: -0.5px;
}
.site-header .subtitle { font-size: 0.78rem; color: #94a3b8; margin-top: 4px; }

/* ── 탭 바 ── */
.tab-bar {
  display: flex; position: sticky; top: 0; z-index: 10;
  background: #0f172a; border-bottom: 1px solid #1e293b;
}
.tab-btn {
  flex: 1; padding: 14px 8px; text-align: center; font-size: 0.88rem;
  font-weight: 600; color: #64748b; cursor: pointer;
  border-bottom: 3px solid transparent; transition: all .2s;
  background: none; border-top: none; border-left: none; border-right: none;
}
.tab-btn.active { color: #3b82f6; border-bottom-color: #3b82f6; }

/* ── 탭 패널 ── */
.tab-panel { display: none; padding: 16px; }
.tab-panel.active { display: block; }

/* ── 날짜 카드 (인덱스) ── */
.date-list { display: flex; flex-direction: column; gap: 10px; }
.date-card {
  background: #1e293b; border-radius: 12px; padding: 16px 18px;
  display: flex; justify-content: space-between; align-items: center;
  border: 1px solid #334155; transition: border-color .2s;
}
.date-card:active { border-color: #3b82f6; }
.date-card .dc-date { font-size: 1rem; font-weight: 700; }
.date-card .dc-meta { font-size: 0.78rem; color: #64748b; margin-top: 3px; }
.date-card .dc-arrow { color: #3b82f6; font-size: 1.2rem; }

/* ── 페이지 헤더 (날짜 페이지) ── */
.page-header {
  padding: 16px;
  background: #0f172a;
  border-bottom: 1px solid #1e293b;
  display: flex; align-items: center; gap: 12px;
}
.back-btn {
  color: #3b82f6; font-size: 0.9rem; font-weight: 600;
  display: flex; align-items: center; gap: 4px;
}
.page-title { font-size: 1.05rem; font-weight: 700; }
.page-subtitle { font-size: 0.78rem; color: #64748b; }

/* ── 경기 카드 ── */
.games-list { padding: 12px 16px; display: flex; flex-direction: column; gap: 14px; }
.game-card {
  background: #1e293b; border-radius: 14px;
  border: 1px solid #334155; overflow: hidden;
}

/* 매치업 헤더 */
.matchup {
  padding: 16px;
  display: flex; align-items: center; justify-content: space-between;
  background: #162032;
}
.team-block { text-align: center; flex: 1; }
.team-name { font-size: 1.05rem; font-weight: 800; }
.team-role { font-size: 0.68rem; color: #64748b; margin-bottom: 4px; }
.team-score { font-size: 2rem; font-weight: 900; line-height: 1; margin-top: 4px; }
.team-score.winner { color: #22c55e; }
.vs-col { text-align: center; padding: 0 8px; }
.vs-text { font-size: 0.8rem; color: #64748b; font-weight: 600; }

/* 예측 결과 뱃지 */
.pred-badge {
  display: inline-block; font-size: 0.7rem; font-weight: 700;
  padding: 3px 8px; border-radius: 99px; margin-top: 6px;
}
.pred-hit  { background: #14532d; color: #4ade80; }
.pred-miss { background: #450a0a; color: #f87171; }
.pred-none { background: #1e293b; color: #64748b; }
.pred-warn { background: #422006; color: #fb923c; }

/* 승패 투수 */
.pitchers {
  padding: 10px 16px;
  display: flex; gap: 8px; flex-wrap: wrap;
  border-top: 1px solid #334155;
}
.pit-tag {
  font-size: 0.72rem; padding: 3px 10px; border-radius: 99px; font-weight: 600;
}
.pit-win  { background: #14532d; color: #4ade80; }
.pit-lose { background: #450a0a; color: #f87171; }
.pit-save { background: #1e3a5f; color: #60a5fa; }

/* 승률 바 */
.prob-section { padding: 14px 16px; border-top: 1px solid #334155; }
.prob-label { font-size: 0.72rem; color: #64748b; margin-bottom: 8px; font-weight: 600; }
.prob-bar-wrap {
  height: 28px; border-radius: 6px; overflow: hidden;
  display: flex; background: #0f172a;
}
.prob-away {
  display: flex; align-items: center; justify-content: flex-end;
  padding-right: 8px; font-size: 0.8rem; font-weight: 800; color: #fff;
  background: #1d4ed8; transition: width .6s ease;
}
.prob-home {
  display: flex; align-items: center; justify-content: flex-start;
  padding-left: 8px; font-size: 0.8rem; font-weight: 800; color: #fff;
  background: #dc2626; transition: width .6s ease;
}
.prob-teams {
  display: flex; justify-content: space-between;
  font-size: 0.72rem; color: #94a3b8; margin-top: 6px;
}

/* λ 지표 */
.lambda-row {
  display: flex; gap: 8px; padding: 10px 16px 0;
}
.lambda-chip {
  background: #0f172a; border-radius: 8px; padding: 8px 12px; flex: 1;
  text-align: center;
}
.lambda-chip .lc-label { font-size: 0.65rem; color: #64748b; }
.lambda-chip .lc-val   { font-size: 1.1rem; font-weight: 800; color: #f1f5f9; margin-top: 2px; }

/* 선발 투수 박스 */
.pitchers-box {
  padding: 12px 16px; border-top: 1px solid #334155;
  display: flex; gap: 8px;
}
.pitcher-chip {
  flex: 1; background: #0f172a; border-radius: 8px; padding: 10px 12px;
}
.pc-role  { font-size: 0.65rem; color: #64748b; }
.pc-name  { font-size: 0.95rem; font-weight: 700; margin-top: 2px; }
.pc-era   { font-size: 0.72rem; color: #94a3b8; margin-top: 2px; }

/* 분석 아코디언 */
.analysis-toggle {
  width: 100%; padding: 12px 16px;
  background: none; border: none; border-top: 1px solid #334155;
  color: #3b82f6; font-size: 0.82rem; font-weight: 600;
  text-align: left; cursor: pointer;
  display: flex; justify-content: space-between; align-items: center;
}
.analysis-body {
  display: none; padding: 0 16px 16px;
  font-size: 0.82rem; line-height: 1.7; color: #cbd5e1;
}
.analysis-body.open { display: block; }
.analysis-body h2 { font-size: 0.88rem; color: #93c5fd; margin: 12px 0 6px; }
.analysis-body h3 { font-size: 0.82rem; color: #7dd3fc; margin: 10px 0 4px; }
.analysis-body ul { padding-left: 16px; }
.analysis-body li { margin-bottom: 4px; }
.analysis-body p  { margin-bottom: 8px; }
.analysis-body strong { color: #e2e8f0; }
/* 완료경기 결과 분석 — 항상 노출 */
.result-analysis {
  padding: 12px 16px 16px;
  font-size: 0.82rem; line-height: 1.7; color: #cbd5e1;
  border-top: 1px solid #1e293b;
}
.result-analysis h2 { font-size: 0.88rem; color: #93c5fd; margin: 12px 0 6px; }
.result-analysis h3 { font-size: 0.82rem; color: #7dd3fc; margin: 10px 0 4px; }
.result-analysis ul { padding-left: 16px; }
.result-analysis li { margin-bottom: 4px; }
.result-analysis p  { margin-bottom: 8px; }
.result-analysis strong { color: #e2e8f0; }

/* 위협 타자 섹션 */
.danger-section { padding: 0 16px 12px; }
.danger-title { font-size: 0.72rem; color: #64748b; font-weight: 600; margin-bottom: 8px; }
.danger-group { margin-bottom: 10px; }
.danger-group-header {
  display: flex; align-items: center; gap: 6px; margin-bottom: 6px;
}
.danger-team-tag {
  font-size: 0.68rem; font-weight: 700; padding: 2px 8px; border-radius: 99px;
}
.away-tag { background: #1e3a5f; color: #60a5fa; }
.home-tag { background: #3b0764; color: #c084fc; }
.danger-sub { font-size: 0.72rem; color: #64748b; }
.danger-batter-row {
  display: flex; justify-content: space-between; align-items: center;
  padding: 5px 10px; border-radius: 6px; margin-bottom: 3px;
  background: #0f172a;
}
.db-name { font-size: 0.8rem; font-weight: 600; }
.db-ops  { font-size: 0.75rem; font-weight: 700; }
.ops-hot  { color: #f87171; }
.ops-warm { color: #fb923c; }
.ops-cool { color: #94a3b8; }

/* 빈 상태 */
.empty-state {
  text-align: center; padding: 48px 24px; color: #475569;
}
.empty-state .es-icon { font-size: 2.5rem; margin-bottom: 12px; }
.empty-state .es-text { font-size: 0.9rem; }

/* 업데이트 시각 */
.update-info {
  text-align: center; padding: 16px; font-size: 0.72rem; color: #334155;
}

/* ── 박스스코어 ── */
.bs-toggle {
  width: 100%; padding: 12px 16px;
  background: none; border: none; border-top: 1px solid #334155;
  color: #94a3b8; font-size: 0.82rem; font-weight: 600;
  text-align: left; cursor: pointer;
  display: flex; justify-content: space-between; align-items: center;
}
.bs-body { display: none; }
.bs-body.open { display: block; }

.bs-sp {
  padding: 10px 16px; border-top: 1px solid #1e293b;
  font-size: 0.72rem; color: #94a3b8; line-height: 1.8;
}
.bs-sp strong { color: #cbd5e1; }

.bs-team-header {
  padding: 8px 16px 4px; font-size: 0.75rem; font-weight: 700; color: #64748b;
  border-top: 1px solid #1e293b; margin-top: 4px;
}
.bs-table-wrap { overflow-x: auto; padding: 0 16px 12px; }
.bs-table {
  border-collapse: collapse; width: 100%; font-size: 0.72rem;
  white-space: nowrap;
}
.bs-table th {
  background: #0f172a; color: #64748b; font-weight: 600;
  padding: 5px 8px; text-align: center; border-bottom: 1px solid #334155;
  position: sticky; top: 0;
}
.bs-table td {
  padding: 4px 8px; text-align: center; border-bottom: 1px solid #1e293b;
  color: #cbd5e1;
}
.bs-table .td-name { text-align: left; font-weight: 600; color: #e2e8f0; min-width: 60px; }
.bs-table .td-pos  { color: #64748b; }
.bs-table .td-hi   { color: #fbbf24; font-weight: 700; }
.bs-table .td-win  { color: #4ade80; font-weight: 700; }
.bs-table .td-lose { color: #f87171; font-weight: 700; }
.bs-table .td-save { color: #60a5fa; font-weight: 700; }
.bs-table .td-hold { color: #c084fc; font-weight: 700; }
.bs-table tr.sub-row td { color: #475569; }
.bs-table tfoot td  { background: #0f172a; color: #94a3b8; font-weight: 600; }

/* ── 소개 탭 ── */
.intro-body {
  padding: 16px; font-size: 0.84rem; line-height: 1.8; color: #cbd5e1;
}
.intro-body h1 { font-size: 1.1rem; font-weight: 800; color: #f1f5f9; margin: 16px 0 8px; }
.intro-body h2 { font-size: 0.95rem; font-weight: 700; color: #93c5fd; margin: 16px 0 6px; border-bottom: 1px solid #1e293b; padding-bottom: 4px; }
.intro-body h3 { font-size: 0.85rem; font-weight: 700; color: #7dd3fc; margin: 12px 0 4px; }
.intro-body h4 { font-size: 0.82rem; font-weight: 700; color: #94a3b8; margin: 10px 0 4px; }
.intro-body p  { margin-bottom: 8px; }
.intro-body ul, .intro-body ol { padding-left: 18px; margin-bottom: 8px; }
.intro-body li { margin-bottom: 3px; }
.intro-body code {
  background: #0f172a; padding: 1px 5px; border-radius: 4px;
  font-size: 0.78rem; color: #7dd3fc; font-family: monospace;
}
.intro-body pre {
  background: #0f172a; border-radius: 8px; padding: 12px 14px;
  overflow-x: auto; margin: 8px 0; border: 1px solid #1e293b;
}
.intro-body pre code { background: none; padding: 0; color: #a5f3fc; }
.intro-body table { width: 100%; border-collapse: collapse; margin: 8px 0; font-size: 0.78rem; }
.intro-body th { background: #1e293b; color: #94a3b8; padding: 6px 10px; text-align: left; border: 1px solid #334155; }
.intro-body td { padding: 5px 10px; border: 1px solid #1e293b; }
.intro-body hr { border: none; border-top: 1px solid #1e293b; margin: 16px 0; }
.intro-body strong { color: #e2e8f0; }
.intro-body a { color: #60a5fa; text-decoration: underline; }
"""


def _html_page(title: str, body: str, *, back_url: str = "", back_label: str = "") -> str:
    back_html = ""
    if back_url:
        back_html = f'<a href="{back_url}" class="back-btn">← {back_label}</a>'
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>{CSS}</style>
</head>
<body>
{body}
</body>
</html>"""


# ── 경기 카드 HTML ────────────────────────────────────────────────

def _prob_bar(away_pct: float, away_team: str, home_team: str) -> str:
    home_pct = 100 - away_pct
    return f"""
<div class="prob-section">
  <div class="prob-label">승리 확률</div>
  <div class="prob-bar-wrap">
    <div class="prob-away" style="width:{away_pct:.1f}%">{away_pct:.1f}%</div>
    <div class="prob-home" style="width:{home_pct:.1f}%">{home_pct:.1f}%</div>
  </div>
  <div class="prob-teams">
    <span>{away_team} (원정)</span>
    <span>{home_team} (홈)</span>
  </div>
</div>"""


def _danger_html(sim: dict) -> str:
    db = sim.get("dangerous_batters", {})
    vs_hp = db.get("vs_home_pitcher", [])
    vs_ap = db.get("vs_away_pitcher", [])
    if not vs_hp and not vs_ap:
        return ""

    rows = '<div class="danger-section"><div class="danger-title">위협 타자</div>'

    for batters, team_name, tag_cls, vs_pitcher in [
        (vs_hp, sim.get("away_team",""), "away-tag", sim.get("home_pitcher","홈 선발")),
        (vs_ap, sim.get("home_team",""), "home-tag", sim.get("away_pitcher","원정 선발")),
    ]:
        if not batters:
            continue
        rows += f'''<div class="danger-group">
  <div class="danger-group-header">
    <span class="danger-team-tag {tag_cls}">{team_name}</span>
    <span class="danger-sub">vs {vs_pitcher}</span>
  </div>'''
        for b in batters[:4]:
            ops = b.get("ops", 0)
            cls = "ops-hot" if ops >= 0.900 else ("ops-warm" if ops >= 0.750 else "ops-cool")
            rows += f'''<div class="danger-batter-row">
    <span class="db-name">{b["name"]}</span>
    <span class="db-ops {cls}">OPS {ops:.3f}</span>
  </div>'''
        rows += '</div>'
    rows += '</div>'
    return rows


def _analysis_accordion(analysis_html: str, uid: str, label: str = "경기 분석 보기") -> str:
    if not analysis_html:
        return ""
    return f"""
<button class="analysis-toggle" onclick="toggleAcc('{uid}')">
  {label} <span id="arr-{uid}">▼</span>
</button>
<div class="analysis-body" id="ab-{uid}">
  {analysis_html}
</div>
<script>
function toggleAcc(id) {{
  var el = document.getElementById('ab-'+id);
  var arr = document.getElementById('arr-'+id);
  if (el.classList.contains('open')) {{
    el.classList.remove('open'); arr.textContent = '▼';
  }} else {{
    el.classList.add('open'); arr.textContent = '▲';
  }}
}}
</script>"""


def _boxscore_html(bs: dict, away_team: str, home_team: str, idx: int) -> str:
    """박스스코어 아코디언 HTML 생성"""
    if not bs or bs.get("cancel"):
        return ""

    def _result_cls(val: str) -> str:
        return {"승": "td-win", "패": "td-lose", "세": "td-save", "홀": "td-hold"}.get(val, "")

    def _bat_table(team: str, bat: dict) -> str:
        if not bat or not bat.get("rows"):
            return ""
        hdrs = bat.get("headers", [])

        STAT_COLS = {"타수", "안타", "타점", "득점", "타율"}
        HI_COLS   = {"안타", "타점", "득점"}

        # 컬럼 그룹 분류: 접두(타순·포지션·선수명) → 스탯 → 이닝
        prefix_idx = [i for i, h in enumerate(hdrs) if not h.isdigit() and h not in STAT_COLS]
        stat_idx   = [i for i, h in enumerate(hdrs) if h in STAT_COLS]
        inn_idx    = [i for i, h in enumerate(hdrs) if h.isdigit()]
        col_order  = prefix_idx + stat_idx + inn_idx
        new_hdrs   = [hdrs[i] for i in col_order]

        prev_order = ""
        rows_html  = ""
        for row in bat.get("rows", []):
            is_sub = prev_order and len(row) > 0 and row[0] == prev_order
            tr_cls = ' class="sub-row"' if is_sub else ""
            if row:
                prev_order = row[0]
            cells = ""
            for new_ci, orig_ci in enumerate(col_order):
                cell = row[orig_ci] if orig_ci < len(row) else ""
                hdr  = hdrs[orig_ci] if orig_ci < len(hdrs) else ""
                hi_cls   = " td-hi"   if hdr in HI_COLS and cell and cell != "0" else ""
                name_cls = " td-name" if hdr == "선수명" else ""
                pos_cls  = " td-pos"  if orig_ci == 1 and hdr == "" else ""
                cells += f'<td class="{(hi_cls+name_cls+pos_cls).strip()}">{cell or ""}</td>'
            rows_html += f"<tr{tr_cls}>{cells}</tr>"

        tfoot_html = ""
        for row in bat.get("tfoot", []):
            # tfoot도 같은 col_order로 재배열
            reordered = [row[i] if i < len(row) else "" for i in col_order]
            tfoot_html += "<tr>" + "".join(f"<td>{c}</td>" for c in reordered) + "</tr>"

        thead = "".join(f"<th>{h}</th>" for h in new_hdrs)
        return f"""
<div class="bs-team-header">{team} 타자</div>
<div class="bs-table-wrap">
  <table class="bs-table">
    <thead><tr>{thead}</tr></thead>
    <tbody>{rows_html}</tbody>
    {'<tfoot>'+tfoot_html+'</tfoot>' if tfoot_html else ''}
  </table>
</div>"""

    def _pit_table(team: str, pit: dict) -> str:
        if not pit or not pit.get("rows"):
            return ""
        hdrs = pit.get("headers", [])
        rows_html = ""
        for row in pit.get("rows", []):
            cells = ""
            for ci, cell in enumerate(row):
                hdr = hdrs[ci] if ci < len(hdrs) else ""
                cls = _result_cls(cell) if hdr == "결과" else ("td-name" if ci == 0 else "")
                cells += f'<td class="{cls}">{cell or "—"}</td>'
            rows_html += f"<tr>{cells}</tr>"
        tfoot_html = ""
        for row in pit.get("tfoot", []):
            tfoot_html += "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>"
        thead = "".join(f"<th>{h}</th>" for h in hdrs)
        return f"""
<div class="bs-team-header">{team} 투수</div>
<div class="bs-table-wrap">
  <table class="bs-table">
    <thead><tr>{thead}</tr></thead>
    <tbody>{rows_html}</tbody>
    {'<tfoot>'+tfoot_html+'</tfoot>' if tfoot_html else ''}
  </table>
</div>"""

    # 특이사항
    sp = bs.get("special_plays", {})
    sp_html = ""
    if sp:
        sp_rows = " &nbsp;|&nbsp; ".join(f"<strong>{k}</strong> {v}" for k, v in sp.items())
        sp_html = f'<div class="bs-sp">{sp_rows}</div>'

    inner = (
        sp_html
        + _bat_table(away_team, bs.get("away_batters"))
        + _bat_table(home_team, bs.get("home_batters"))
        + _pit_table(away_team, bs.get("away_pitchers"))
        + _pit_table(home_team, bs.get("home_pitchers"))
    )
    if not inner.strip():
        return ""

    bid = f"bs-{idx}"
    return f"""
<button class="bs-toggle" onclick="toggleBS('{bid}')">
  경기 결과 상세 <span id="{bid}-arr">▼</span>
</button>
<div class="bs-body" id="{bid}">
  {inner}
</div>
<script>
function toggleBS(id) {{
  var el=document.getElementById(id), arr=document.getElementById(id+'-arr');
  if(el.classList.contains('open')){{el.classList.remove('open');arr.textContent='▼';}}
  else{{el.classList.add('open');arr.textContent='▲';}}
}}
</script>"""


def build_completed_card(rg: "RecentGame", sim: dict, result_html: str,
                         pregame_html: str, boxscore_html: str, idx: int) -> str:
    away_score = rg.away_score or 0
    home_score = rg.home_score or 0
    away_win_cls = " winner" if away_score > home_score else ""
    home_win_cls = " winner" if home_score > away_score else ""

    # 예측 적중 여부
    predicted = sim.get("predicted_winner", "")
    actual = rg.away_team if away_score > home_score else (rg.home_team if home_score > away_score else "")
    if predicted and actual:
        badge = '<span class="pred-badge pred-hit">예측 적중</span>' if predicted == actual else '<span class="pred-badge pred-miss">예측 빗나감</span>'
    else:
        badge = '<span class="pred-badge pred-none">무승부</span>'

    # 투수 태그
    pit_html = ""
    tags = []
    if rg.win_pitcher:
        tags.append(f'<span class="pit-tag pit-win">승 {rg.win_pitcher}</span>')
    if rg.save_pitcher:
        tags.append(f'<span class="pit-tag pit-save">S {rg.save_pitcher}</span>')
    if rg.lose_pitcher:
        tags.append(f'<span class="pit-tag pit-lose">패 {rg.lose_pitcher}</span>')
    if tags:
        pit_html = f'<div class="pitchers">{"".join(tags)}</div>'

    away_pct = sim.get("away_win_pct", 50.0)
    predicted = sim.get("predicted_winner", "")
    actual = rg.away_team if away_score > home_score else (rg.home_team if home_score > away_score else "")
    hit = predicted and actual and predicted == actual
    result_label = ("✅ 예측 적중 분석" if hit else "❌ 예측 빗나감 분석") if (predicted and actual) else "예측 분석"

    return f"""
<div class="game-card">
  <div class="matchup">
    <div class="team-block">
      <div class="team-role">원정</div>
      <div class="team-name">{rg.away_team}</div>
      <div class="team-score{away_win_cls}">{away_score}</div>
    </div>
    <div class="vs-col">
      <div class="vs-text">VS</div>
      {badge}
    </div>
    <div class="team-block">
      <div class="team-role">홈</div>
      <div class="team-name">{rg.home_team}</div>
      <div class="team-score{home_win_cls}">{home_score}</div>
    </div>
  </div>
  {pit_html}
  {_prob_bar(away_pct, rg.away_team, rg.home_team)}
  {_danger_html(sim)}
  {_analysis_accordion(result_html, f"r{idx}", result_label)}
  {_analysis_accordion(pregame_html, f"a{idx}", "이전 경기 분석 보기")}
  {boxscore_html}
</div>"""


def build_upcoming_card(tg: "TodayGame", sim: dict, analysis_html: str, idx: int) -> str:
    away_pct = sim.get("away_win_pct", 50.0)
    home_pct = 100 - away_pct

    away_pitcher = sim.get("away_pitcher", "") or tg.away_pitcher or "미정"
    home_pitcher = sim.get("home_pitcher", "") or tg.home_pitcher or "미정"

    away_era = sim.get("away_pitcher_era")
    home_era = sim.get("home_pitcher_era")

    winner = sim.get("predicted_winner", "")
    win_side = "원정" if winner == tg.away_team else ("홈" if winner == tg.home_team else "")
    confidence = sim.get("confidence", 0)

    la = sim.get("lambda_away", 0)
    lh = sim.get("lambda_home", 0)

    no_pitcher = (away_pitcher == "미정" or home_pitcher == "미정")
    warn_html = '<span class="pred-badge pred-warn">⚠ 선발 미정 — 신뢰도 낮음</span>' if no_pitcher else ''

    return f"""
<div class="game-card">
  <div class="matchup">
    <div class="team-block">
      <div class="team-role">원정</div>
      <div class="team-name">{tg.away_team}</div>
    </div>
    <div class="vs-col">
      <div class="vs-text">VS</div>
      {'<span class="pred-badge pred-hit">'+winner+' 예상 '+str(confidence)+'%</span>' if winner else ''}
      {warn_html}
    </div>
    <div class="team-block">
      <div class="team-role">홈</div>
      <div class="team-name">{tg.home_team}</div>
    </div>
  </div>
  <div class="pitchers-box">
    <div class="pitcher-chip">
      <div class="pc-role">원정 선발</div>
      <div class="pc-name">{away_pitcher}</div>
      {'<div class="pc-era">ERA '+str(round(away_era,2))+'</div>' if away_era else ''}
    </div>
    <div class="pitcher-chip">
      <div class="pc-role">홈 선발</div>
      <div class="pc-name">{home_pitcher}</div>
      {'<div class="pc-era">ERA '+str(round(home_era,2))+'</div>' if home_era else ''}
    </div>
  </div>
  {_prob_bar(away_pct, tg.away_team, tg.home_team)}
  <div class="lambda-row">
    <div class="lambda-chip">
      <div class="lc-label">{tg.away_team} 기대득점</div>
      <div class="lc-val">{la}</div>
    </div>
    <div class="lambda-chip">
      <div class="lc-label">{tg.home_team} 기대득점</div>
      <div class="lc-val">{lh}</div>
    </div>
  </div>
  {_danger_html(sim)}
  {_analysis_accordion(analysis_html, f"u{idx}")}
</div>"""


# ── 페이지 생성 ───────────────────────────────────────────────────

def build_completed_page(game_date: date, games: list, team_stats: dict) -> str:
    from app.scraper import scrape_game_boxscore
    date_str   = game_date.strftime("%Y년 %-m월 %-d일")
    cards_html = ""

    for idx, rg in enumerate(games):
        away_stat = team_stats.get(rg.away_team)
        home_stat = team_stats.get(rg.home_team)

        # 저장된 예측 데이터 사용 (원래 선발투수/스탯 반영된 예측값)
        if rg.sim_json:
            try:
                sim = json.loads(rg.sim_json)
            except Exception:
                sim = run_simulation(rg.away_team, rg.home_team, away_stat, home_stat, None)
        else:
            sim = run_simulation(rg.away_team, rg.home_team, away_stat, home_stat, None)

        # 드롭다운 1: 예측 적중/빗나감 분석
        result_html = md_lib.markdown(rg.result_analysis, extensions=["extra", "nl2br"]) if rg.result_analysis else ""

        # 드롭다운 2: 사전 시뮬레이션 분석 (이전 경기 분석 보기)
        # sim_json은 compact 저장이라 누락 키가 있을 수 있어 fresh 재실행
        try:
            fresh_sim = run_simulation(rg.away_team, rg.home_team, away_stat, home_stat, None)
            pregame_html = md_lib.markdown(
                generate_analysis(fresh_sim, away_stat, home_stat), extensions=["extra", "nl2br"]
            )
        except Exception:
            pregame_html = ""

        try:
            bs = scrape_game_boxscore(rg.game_date, rg.away_team, rg.home_team)
        except Exception:
            bs = {}
        bs_html = _boxscore_html(bs, rg.away_team, rg.home_team, idx)
        cards_html += build_completed_card(rg, sim, result_html, pregame_html, bs_html, idx)

    body = f"""
<div class="site-header">
  <a href="../index.html" class="back-btn" style="display:inline-block;margin-bottom:8px">← 목록으로</a>
  <h1>⚾ {date_str} 경기 분석</h1>
  <div class="subtitle">몬테카를로 시뮬레이션 기반 분석 (10,000회)</div>
</div>
<div class="games-list">{cards_html}</div>
<div class="update-info">빌드: {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>"""

    return _html_page(f"KBO {date_str} 경기 분석", body)


def build_prediction_page(game_date: date, games: list, team_stats: dict) -> str:
    date_str   = game_date.strftime("%Y년 %-m월 %-d일")
    cards_html = ""

    for idx, tg in enumerate(games):
        away_stat = team_stats.get(tg.away_team)
        home_stat = team_stats.get(tg.home_team)
        sim  = run_simulation(tg.away_team, tg.home_team, away_stat, home_stat, tg)
        ana  = generate_analysis(sim, away_stat, home_stat)
        ana_html = md_lib.markdown(ana, extensions=["extra", "nl2br"])
        cards_html += build_upcoming_card(tg, sim, ana_html, idx)

    body = f"""
<div class="site-header">
  <a href="../index.html" class="back-btn" style="display:inline-block;margin-bottom:8px">← 목록으로</a>
  <h1>⚾ {date_str} 경기 예상</h1>
  <div class="subtitle">몬테카를로 시뮬레이션 기반 예측 (10,000회)</div>
</div>
<div class="games-list">{cards_html}</div>
<div class="update-info">빌드: {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>"""

    return _html_page(f"KBO {date_str} 경기 예상", body)


def _readme_to_html() -> str:
    readme_path = Path(__file__).parent / "README.md"
    try:
        raw = readme_path.read_text(encoding="utf-8")
        return md_lib.markdown(raw, extensions=["extra", "tables", "nl2br"])
    except Exception:
        return "<p>소개 내용을 불러올 수 없습니다.</p>"


def build_index(completed_dates: list, upcoming_dates: list,
                completed_counts: dict, upcoming_counts: dict) -> str:
    weekdays = ["월","화","수","목","금","토","일"]

    def date_card(d: date, count: int, href: str, label: str) -> str:
        wd   = weekdays[d.weekday()]
        ds   = d.strftime(f"%-m월 %-d일 ({wd})")
        today = date.today()
        tag  = ""
        if d == today:
            tag = '<span style="font-size:.68rem;background:#1d4ed8;color:#fff;padding:2px 6px;border-radius:99px;margin-left:6px">오늘</span>'
        elif d == today.__class__(today.year, today.month, today.day - 1) if today.day > 1 else None:
            pass
        return f"""<a href="{href}">
  <div class="date-card">
    <div>
      <div class="dc-date">{ds}{tag}</div>
      <div class="dc-meta">경기 {count}건</div>
    </div>
    <div class="dc-arrow">›</div>
  </div>
</a>"""

    games_list = "\n".join(
        date_card(d, completed_counts[d], f"games/{d.isoformat()}.html", "경기 분석")
        for d in completed_dates
    ) or '<div class="empty-state"><div class="es-icon">📭</div><div class="es-text">완료된 경기가 없습니다</div></div>'

    pred_list = "\n".join(
        date_card(d, upcoming_counts[d], f"predictions/{d.isoformat()}.html", "경기 예상")
        for d in upcoming_dates
    ) or '<div class="empty-state"><div class="es-icon">📭</div><div class="es-text">예정된 경기가 없습니다</div></div>'

    now_str    = datetime.now().strftime('%Y-%m-%d %H:%M')
    readme_html = _readme_to_html()

    body = f"""
<div class="site-header">
  <h1>⚾ KBO 경기 예측</h1>
  <div class="subtitle">몬테카를로 시뮬레이션 기반 · 최종 업데이트 {now_str}</div>
</div>

<div class="tab-bar">
  <button class="tab-btn active" onclick="switchTab('games', this)">경기 분석</button>
  <button class="tab-btn"        onclick="switchTab('preds', this)">경기 예상</button>
  <button class="tab-btn"        onclick="location.href='betman.html'" style="color:#f59e0b">🎯 배트맨</button>
  <button class="tab-btn"        onclick="location.href='samsung.html'" style="color:#C8A951">🦁 삼성</button>
  <button class="tab-btn"        onclick="switchTab('intro', this)">소개</button>
</div>

<div class="tab-panel active" id="tab-games">
  <div class="date-list">{games_list}</div>
</div>
<div class="tab-panel" id="tab-preds">
  <div class="date-list">{pred_list}</div>
</div>
<div class="tab-panel" id="tab-intro">
  <div class="intro-body">{readme_html}</div>
</div>

<div class="update-info">빌드: {now_str}</div>

<script>
function switchTab(name, btn) {{
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('tab-' + name).classList.add('active');
}}
(function() {{
  var h = location.hash;
  var map = {{'#preds': 1, '#intro': 2}};
  if (map[h] !== undefined) {{
    switchTab(h.slice(1), document.querySelectorAll('.tab-btn')[map[h]]);
  }}
}})();
</script>"""

    return _html_page("KBO 경기 예측 블로그", body)


# ── 배트맨 배팅 분석 페이지 ─────────────────────────────────────

def build_betman_page(games_picks: list, game_date) -> str:
    """배트맨 종목별 추천 + 조합 배팅 가이드 페이지."""
    from app.simulator import calc_betman_picks

    date_str = game_date.strftime("%Y년 %-m월 %-d일")

    # ── 각 경기 픽 카드 ──
    cards_html = ""
    combo_picks = []  # 조합 추천용

    for idx, (tg, sim) in enumerate(games_picks):
        p = calc_betman_picks(sim)
        away, home = p["away"], p["home"]

        # 배팅 종목 행
        def conf_bar(conf):
            color = "#22c55e" if conf >= 63 else ("#f59e0b" if conf >= 57 else "#64748b")
            return (
                f'<div class="bt-bar-row">'
                f'<div class="bt-bar-wrap"><div class="bt-bar" style="width:{conf:.0f}%;background:{color}"></div></div>'
                f'<span class="bt-bar-pct">{conf:.1f}%</span>'
                f'</div>'
            )

        win   = p["win"]
        uo    = p["uo"]
        oe    = p["oe"]
        best  = p["best"]

        is_best_win = best["type"] == "승패"
        is_best_uo  = best["type"] == "언더오버"
        is_best_oe  = best["type"] == "홀짝"

        ambig_badge = '<span class="bt-ambig">⚠ 애매한 경기</span>' if p["is_ambiguous"] else ""
        no_pit_badge = '<span class="bt-nopitch">선발 미정</span>' if p["no_pitcher"] else ""
        best_badge = f'<span class="bt-best-badge">★ 추천: {best["type"]} — {best["detail"]}</span>'

        # 조합 추천 대상 (신뢰도 58% 이상 + 비애매)
        if not p["is_ambiguous"] and best["conf"] >= 58:
            combo_picks.append({
                "game": f"{away} vs {home}",
                "pick": best["pick"],
                "type": best["type"],
                "conf": best["conf"],
                "detail": best["detail"],
            })

        cards_html += f"""
<div class="bt-card {'bt-ambig-card' if p['is_ambiguous'] else ''}">
  <div class="bt-card-header">
    <div class="bt-matchup">{away} <span class="bt-at">@</span> {home}</div>
    <div class="bt-badges">{no_pit_badge}{ambig_badge}</div>
  </div>

  <div class="bt-best-row">{best_badge}</div>

  <div class="bt-grid">
    <div class="bt-item {'bt-item-best' if is_best_win else ''}">
      <div class="bt-item-label">승패</div>
      <div class="bt-item-pick">{win['pick']} 승</div>
      {conf_bar(win['conf'])}
      <div class="bt-item-sub">{away} {win['away_pct']}% / {home} {win['home_pct']}%</div>
    </div>
    <div class="bt-item {'bt-item-best' if is_best_uo else ''}">
      <div class="bt-item-label">언더오버 ({uo['line']})</div>
      <div class="bt-item-pick">{uo['pick']}</div>
      {conf_bar(uo['conf'])}
      <div class="bt-item-sub">예상합산 {p['lambda_away']+p['lambda_home']:.1f}점 · 오버 {uo['p_over']}%</div>
    </div>
    <div class="bt-item {'bt-item-best' if is_best_oe else ''}">
      <div class="bt-item-label">홀짝</div>
      <div class="bt-item-pick">{oe['pick']}</div>
      {conf_bar(oe['conf'])}
      <div class="bt-item-sub">홀 {oe['p_odd']}% / 짝 {oe['p_even']}%</div>
    </div>
  </div>

  <div class="bt-lambda">기대득점 — {away} <strong>{p['lambda_away']}</strong>점 / {home} <strong>{p['lambda_home']}</strong>점</div>
</div>"""

    # ── 조합 배팅 추천 ──
    if combo_picks:
        combo_picks.sort(key=lambda x: x["conf"], reverse=True)
        top = combo_picks[:min(5, len(combo_picks))]

        def combo_section(picks, amount, label):
            rows = ""
            joint_conf = 1.0
            for pk in picks:
                joint_conf *= pk["conf"] / 100
                rows += f"""
<div class="cb-row">
  <span class="cb-type">{pk['type']}</span>
  <span class="cb-game">{pk['game']}</span>
  <span class="cb-pick">{pk['pick']}</span>
  <span class="cb-conf">{pk['conf']:.1f}%</span>
</div>"""
            joint_pct = joint_conf * 100
            return f"""
<div class="cb-block">
  <div class="cb-title">{label} ({amount}원)</div>
  {rows}
  <div class="cb-joint">조합 적중 확률 ≈ <strong>{joint_pct:.1f}%</strong></div>
</div>"""

        combo_html = ""
        if len(top) >= 2:
            combo_html += combo_section(top[:2], "5,000", "2경기 조합")
        if len(top) >= 3:
            combo_html += combo_section(top[:3], "3,000", "3경기 조합")
        if len(top) >= 4:
            combo_html += combo_section(top[:4], "1,000", "4경기 조합")

        combo_box = f"""
<div class="bt-section">
  <div class="bt-section-title">조합 배팅 가이드 (1,000~5,000원)</div>
  <div class="bt-combo-note">신뢰도 58% 이상 경기만 선별 · 실제 배당률에 따라 기대값 다를 수 있음</div>
  {combo_html}
</div>"""
    else:
        combo_box = '<div class="bt-section"><div class="bt-combo-note">오늘은 자신있는 경기가 없습니다. 배팅을 권장하지 않습니다.</div></div>'

    # ── 애매한 경기 요약 ──
    ambig_list = [(tg, sim) for tg, sim in games_picks if calc_betman_picks(sim)["is_ambiguous"]]
    ambig_html = ""
    if ambig_list:
        rows = ""
        for tg, sim in ambig_list:
            pk = calc_betman_picks(sim)
            rows += f'<li><strong>{pk["away"]} vs {pk["home"]}</strong> — 승패 {pk["win"]["conf"]:.0f}% / 언더오버 {pk["uo"]["conf"]:.0f}% / 홀짝 {pk["oe"]["conf"]:.0f}%</li>'
        ambig_html = f"""
<div class="bt-section">
  <div class="bt-section-title">⚠ 애매한 경기 (배팅 주의)</div>
  <ul class="bt-ambig-list">{rows}</ul>
  <div class="bt-combo-note">해당 경기는 시뮬레이션 신뢰도가 낮습니다. 조합에 포함 시 리스크 증가.</div>
</div>"""

    css = """
.bt-card {
  background: #1e293b; border-radius: 14px; margin-bottom: 16px;
  border: 1px solid #334155; overflow: hidden;
}
.bt-ambig-card { border-color: #b45309; }
.bt-card-header {
  padding: 14px 16px 8px; display: flex;
  justify-content: space-between; align-items: center;
}
.bt-matchup { font-size: 1rem; font-weight: 800; }
.bt-at { color: #64748b; margin: 0 6px; font-weight: 400; }
.bt-badges { display: flex; gap: 6px; }
.bt-ambig { background: #422006; color: #fb923c; font-size: 0.7rem; padding: 2px 8px; border-radius: 99px; font-weight: 700; }
.bt-nopitch { background: #1e293b; color: #64748b; font-size: 0.7rem; padding: 2px 8px; border-radius: 99px; border: 1px solid #334155; }
.bt-best-row { padding: 0 16px 10px; }
.bt-best-badge {
  display: inline-block; background: #0f2744; border: 1px solid #1d4ed8;
  color: #60a5fa; font-size: 0.75rem; font-weight: 700;
  padding: 5px 12px; border-radius: 8px;
}
.bt-grid { display: grid; grid-template-columns: repeat(3,1fr); gap: 8px; padding: 0 12px 10px; }
.bt-item {
  background: #0f172a; border-radius: 8px; padding: 10px 8px;
  border: 1px solid #1e293b; text-align: center;
}
.bt-item-best { border-color: #1d4ed8; background: #0a1628; }
.bt-item-label { font-size: 0.68rem; color: #64748b; margin-bottom: 4px; }
.bt-item-pick { font-size: 0.95rem; font-weight: 800; margin-bottom: 6px; }
.bt-bar-row { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
.bt-bar-wrap { flex: 1; background: #0f172a; border-radius: 4px; height: 6px; }
.bt-bar { height: 6px; border-radius: 4px; }
.bt-bar-pct { font-size: 0.72rem; color: #94a3b8; min-width: 36px; text-align: right; white-space: nowrap; }
.bt-item-sub { font-size: 0.65rem; color: #475569; margin-top: 2px; }
.bt-lambda { padding: 8px 16px 12px; font-size: 0.75rem; color: #475569; }
.bt-section { background: #1e293b; border-radius: 12px; padding: 16px; margin-bottom: 16px; border: 1px solid #334155; }
.bt-section-title { font-size: 0.85rem; font-weight: 700; color: #C8A951; margin-bottom: 10px; }
.bt-combo-note { font-size: 0.72rem; color: #64748b; margin-bottom: 10px; }
.cb-block { background: #0f172a; border-radius: 8px; padding: 12px; margin-bottom: 10px; border: 1px solid #1e293b; }
.cb-title { font-size: 0.8rem; font-weight: 700; color: #e2e8f0; margin-bottom: 8px; }
.cb-row { display: flex; gap: 8px; align-items: center; padding: 5px 0; border-bottom: 1px solid #1e293b; font-size: 0.78rem; }
.cb-type { min-width: 60px; color: #64748b; }
.cb-game { flex: 1; }
.cb-pick { font-weight: 700; color: #60a5fa; min-width: 60px; }
.cb-conf { color: #22c55e; font-weight: 700; }
.cb-joint { font-size: 0.8rem; color: #94a3b8; margin-top: 8px; text-align: right; }
.bt-ambig-list { padding-left: 18px; font-size: 0.82rem; line-height: 1.9; color: #94a3b8; }"""

    body = f"""
<style>{css}</style>
<div class="page-header">
  <a class="back-btn" href="index.html">‹ 홈</a>
  <div>
    <div class="page-title">🎯 배트맨 배팅 분석</div>
    <div class="page-subtitle">{date_str} · 시뮬레이션 기반 종목별 확률</div>
  </div>
</div>
<div style="padding:12px 16px">
  <div class="bt-section" style="background:#0a1628;border-color:#1d4ed8">
    <div style="font-size:0.75rem;color:#64748b;line-height:1.7">
      ⚠ 본 분석은 몬테카를로 시뮬레이션 기반 통계적 확률입니다.<br>
      실제 배트맨 배당률·라인과 다를 수 있으며, 수익을 보장하지 않습니다.<br>
      배트맨은 국민체육진흥공단 운영 합법 스포츠 복권입니다.
    </div>
  </div>
  <div class="bt-section-title" style="color:#e2e8f0;font-size:0.9rem;margin-bottom:12px">경기별 종목 분석</div>
  {cards_html}
  {combo_box}
  {ambig_html}
</div>"""

    return _html_page("배트맨 배팅 분석 — KBO 예측", body)


# ── 삼성 라이온즈 전용 페이지 ────────────────────────────────────

SAMSUNG_BLUE = "#074CA1"
SAMSUNG_GOLD = "#C8A951"

def build_samsung_page(samsung_stat, samsung_games: list, leaders: dict) -> str:
    """삼성 라이온즈 전용 스탯 페이지 생성."""

    # ── 팀 현황 카드 ──
    if samsung_stat:
        rank      = samsung_stat.rank
        wins      = samsung_stat.wins
        draws     = samsung_stat.draws
        losses    = samsung_stat.losses
        win_pct   = samsung_stat.win_pct
        rpg       = round(samsung_stat.runs_per_game, 2) if samsung_stat.runs_per_game else "-"
        rapg      = round(samsung_stat.runs_allowed_per_game, 2) if samsung_stat.runs_allowed_per_game else "-"
        last10    = samsung_stat.last10 or "-"
        streak    = samsung_stat.streak or "-"
        home_rec  = samsung_stat.home_record or "-"
        away_rec  = samsung_stat.away_record or "-"
        games_n   = samsung_stat.games or 0
    else:
        rank = wins = draws = losses = games_n = "-"
        win_pct = rpg = rapg = "-"
        last10 = streak = home_rec = away_rec = "-"

    streak_color = "#22c55e" if str(streak).startswith("W") or "연승" in str(streak) else "#f87171"

    team_card = f"""
<div class="ss-team-card">
  <div class="ss-rank">
    <div class="ss-rank-num">{rank}위</div>
    <div class="ss-rank-label">리그 순위</div>
  </div>
  <div class="ss-record">
    <div class="ss-wdl">{wins}승 {draws}무 {losses}패</div>
    <div class="ss-pct">승률 {win_pct} · {games_n}경기</div>
  </div>
  <div class="ss-streak" style="color:{streak_color}">
    <div class="ss-streak-val">{streak}</div>
    <div class="ss-streak-label">현재 연속</div>
  </div>
</div>

<div class="ss-stat-row">
  <div class="ss-stat-chip">
    <div class="ss-stat-label">경기당 득점</div>
    <div class="ss-stat-val">{rpg}</div>
  </div>
  <div class="ss-stat-chip">
    <div class="ss-stat-label">경기당 실점</div>
    <div class="ss-stat-val">{rapg}</div>
  </div>
  <div class="ss-stat-chip">
    <div class="ss-stat-label">최근 10경기</div>
    <div class="ss-stat-val" style="font-size:1rem">{last10}</div>
  </div>
  <div class="ss-stat-chip">
    <div class="ss-stat-label">홈 전적</div>
    <div class="ss-stat-val" style="font-size:0.95rem">{home_rec}</div>
  </div>
  <div class="ss-stat-chip">
    <div class="ss-stat-label">원정 전적</div>
    <div class="ss-stat-val" style="font-size:0.95rem">{away_rec}</div>
  </div>
</div>"""

    # ── 최근 경기 차트 (Chart.js) ──
    recent15 = [g for g in samsung_games if g.away_score is not None][:15]
    recent15.reverse()  # 오래된→최신 순

    labels, scored, allowed, results = [], [], [], []
    for g in recent15:
        is_home = g.home_team == "삼성"
        ss = g.home_score if is_home else g.away_score
        sa = g.away_score if is_home else g.home_score
        opp = g.away_team if is_home else g.home_team
        labels.append(f"{g.game_date.strftime('%-m/%-d')} vs {opp}")
        scored.append(ss)
        allowed.append(sa)
        if ss > sa:   results.append("W")
        elif ss < sa: results.append("L")
        else:         results.append("D")

    labels_js  = str(labels).replace("'", '"')
    scored_js  = str(scored)
    allowed_js = str(allowed)
    results_js = str(results).replace("'", '"')

    chart_section = f"""
<div class="ss-section">
  <div class="ss-section-title">최근 {len(recent15)}경기 득실점</div>
  <div class="ss-chart-wrap">
    <canvas id="scoreChart"></canvas>
  </div>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
  <script>
  (function(){{
    var labels  = {labels_js};
    var scored  = {scored_js};
    var allowed = {allowed_js};
    var results = {results_js};
    var bgScored  = scored.map((_,i) => results[i]==='W' ? '#3b82f6' : results[i]==='L' ? '#475569' : '#94a3b8');
    var bgAllowed = allowed.map(() => '#ef4444aa');
    new Chart(document.getElementById('scoreChart'), {{
      type: 'bar',
      data: {{
        labels: labels,
        datasets: [
          {{ label: '득점', data: scored,  backgroundColor: bgScored,  borderRadius: 4, order: 1 }},
          {{ label: '실점', data: allowed, backgroundColor: bgAllowed, borderRadius: 4, order: 2 }},
        ]
      }},
      options: {{
        responsive: true,
        plugins: {{
          legend: {{ labels: {{ color: '#94a3b8', font: {{ size: 12 }} }} }},
          tooltip: {{
            callbacks: {{
              title: ctx => labels[ctx[0].dataIndex],
              afterBody: ctx => '결과: ' + results[ctx[0].dataIndex],
            }}
          }}
        }},
        scales: {{
          x: {{ ticks: {{ color: '#64748b', font: {{ size: 10 }} }}, grid: {{ color: '#1e293b' }} }},
          y: {{ ticks: {{ color: '#64748b' }}, grid: {{ color: '#1e293b' }}, beginAtZero: true }},
        }}
      }}
    }});
  }})();
  </script>
</div>"""

    # ── 최근 경기 결과 목록 ──
    game_rows = ""
    for g in reversed(recent15):
        is_home = g.home_team == "삼성"
        ss = g.home_score if is_home else g.away_score
        sa = g.away_score if is_home else g.home_score
        opp = g.away_team if is_home else g.home_team
        home_label = "홈" if is_home else "원정"
        if ss > sa:
            result_badge = '<span class="ss-win">W</span>'
        elif ss < sa:
            result_badge = '<span class="ss-loss">L</span>'
        else:
            result_badge = '<span class="ss-draw">D</span>'
        game_rows += f"""
<div class="ss-game-row">
  <div class="ss-game-date">{g.game_date.strftime('%-m/%-d')} <span class="ss-home-label">({home_label})</span></div>
  <div class="ss-game-opp">vs {opp}</div>
  <div class="ss-game-score">{ss} - {sa}</div>
  {result_badge}
</div>"""

    games_section = f"""
<div class="ss-section">
  <div class="ss-section-title">최근 경기 결과</div>
  <div class="ss-games-list">{game_rows}</div>
</div>"""

    # ── 리그 상위권 삼성 선수 ──
    SAMSUNG_PLAYERS = {
        "최원준","박성한","구자욱","김지찬","디아즈","오스틴","강민호","이재현",
        "김헌곤","류지혁","맥키넌","원태인","양창섭","백정현","오러클린","임창민",
        "이승현","김재윤","레예스","페라자","황동재","황재균","강한울","조상우",
    }

    def leader_rows(data: list) -> str:
        html_out = ""
        for item in data:
            stat = item["stat"]
            top3 = item["top3"]
            for i, entry in enumerate(top3):
                name = entry["name"]
                val  = entry["value"]
                is_ss = name in SAMSUNG_PLAYERS
                rank_badge = ["🥇","🥈","🥉"][i] if i < 3 else ""
                row_cls = "ss-leader-row ss-leader-highlight" if is_ss else "ss-leader-row"
                html_out += f"""
<div class="{row_cls}">
  <span class="ss-leader-rank">{rank_badge}</span>
  <span class="ss-leader-stat">{stat if i==0 else ""}</span>
  <span class="ss-leader-name">{name}{"  🦁" if is_ss else ""}</span>
  <span class="ss-leader-val">{val}</span>
</div>"""
        return html_out

    bat_rows = leader_rows(leaders.get("batting", []))
    pit_rows = leader_rows(leaders.get("pitching", []))

    leaders_section = f"""
<div class="ss-section">
  <div class="ss-section-title">리그 타격 순위 (삼성 선수 강조)</div>
  <div class="ss-leaders">{bat_rows}</div>
</div>
<div class="ss-section">
  <div class="ss-section-title">리그 투구 순위 (삼성 선수 강조)</div>
  <div class="ss-leaders">{pit_rows}</div>
</div>"""

    css = """
.ss-team-card {
  display: flex; align-items: center; justify-content: space-between;
  background: linear-gradient(135deg, #0a2244, #074CA1);
  border-radius: 16px; padding: 20px; margin-bottom: 12px;
  border: 1px solid #1d4ed8;
}
.ss-rank-num { font-size: 2.4rem; font-weight: 900; color: #C8A951; line-height: 1; }
.ss-rank-label { font-size: 0.7rem; color: #94a3b8; margin-top: 2px; }
.ss-wdl { font-size: 1.2rem; font-weight: 800; color: #fff; }
.ss-pct { font-size: 0.8rem; color: #93c5fd; margin-top: 4px; }
.ss-streak-val { font-size: 1.3rem; font-weight: 800; }
.ss-streak-label { font-size: 0.7rem; color: #94a3b8; margin-top: 2px; }
.ss-stat-row { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 20px; }
.ss-stat-chip {
  flex: 1; min-width: 80px; background: #1e293b; border-radius: 10px;
  padding: 10px 8px; text-align: center; border: 1px solid #334155;
}
.ss-stat-label { font-size: 0.68rem; color: #64748b; margin-bottom: 4px; }
.ss-stat-val { font-size: 1.1rem; font-weight: 800; color: #e2e8f0; }
.ss-section { margin-bottom: 24px; }
.ss-section-title {
  font-size: 0.85rem; font-weight: 700; color: #C8A951;
  margin-bottom: 10px; padding-bottom: 6px;
  border-bottom: 1px solid #1e293b; letter-spacing: 0.5px;
}
.ss-chart-wrap { background: #1e293b; border-radius: 12px; padding: 16px; }
.ss-games-list { display: flex; flex-direction: column; gap: 6px; }
.ss-game-row {
  display: flex; align-items: center; gap: 10px;
  background: #1e293b; border-radius: 8px; padding: 10px 12px;
  font-size: 0.85rem;
}
.ss-game-date { color: #64748b; min-width: 70px; }
.ss-home-label { font-size: 0.72rem; }
.ss-game-opp { flex: 1; font-weight: 600; }
.ss-game-score { font-weight: 800; font-size: 1rem; min-width: 50px; text-align: right; }
.ss-win  { background: #14532d; color: #4ade80; border-radius: 6px; padding: 2px 8px; font-weight: 700; font-size: 0.8rem; }
.ss-loss { background: #450a0a; color: #f87171; border-radius: 6px; padding: 2px 8px; font-weight: 700; font-size: 0.8rem; }
.ss-draw { background: #1e293b; color: #94a3b8; border-radius: 6px; padding: 2px 8px; font-weight: 700; font-size: 0.8rem; border: 1px solid #334155; }
.ss-leaders { display: flex; flex-direction: column; gap: 3px; }
.ss-leader-row {
  display: flex; align-items: center; gap: 8px;
  padding: 7px 10px; border-radius: 6px; font-size: 0.82rem;
}
.ss-leader-highlight { background: #0f2744; border: 1px solid #1d4ed8; }
.ss-leader-rank { min-width: 22px; text-align: center; }
.ss-leader-stat { min-width: 55px; color: #64748b; font-size: 0.75rem; }
.ss-leader-name { flex: 1; font-weight: 600; }
.ss-leader-val { font-weight: 800; color: #60a5fa; }"""

    body = f"""
<style>{css}</style>
<div class="page-header">
  <a class="back-btn" href="index.html">‹ 홈</a>
  <div>
    <div class="page-title">🦁 삼성 라이온즈</div>
    <div class="page-subtitle">2026 시즌 현황</div>
  </div>
</div>
<div style="padding:12px 16px">
{team_card}
{chart_section}
{games_section}
{leaders_section}
</div>"""

    return _html_page("삼성 라이온즈 — KBO 예측", body)


# ── 메인 빌드 ────────────────────────────────────────────────────

def main():
    print("🔨 KBO 블로그 빌드 시작...")

    app = create_app()
    with app.app_context():
        # 팀 스탯
        latest = TeamStat.query.order_by(TeamStat.scraped_at.desc()).first()
        team_stats = {}
        if latest:
            for ts in TeamStat.query.filter_by(scraped_at=latest.scraped_at).all():
                team_stats[ts.team] = ts

        # 삼성 경기 (최근 15경기)
        from app.models import db as _db
        samsung_games = RecentGame.query.filter(
            _db.or_(RecentGame.away_team == "삼성", RecentGame.home_team == "삼성")
        ).order_by(RecentGame.game_date.desc()).limit(15).all()

        # 완료 경기 (최근 7일치)
        from collections import defaultdict
        completed_map: dict = defaultdict(list)
        for rg in RecentGame.query.order_by(RecentGame.game_date.desc()).all():
            completed_map[rg.game_date].append(rg)

        # 다음 예정 경기
        upcoming_map: dict = defaultdict(list)
        seen = set()
        for tg in TodayGame.query.order_by(TodayGame.game_date.asc(), TodayGame.scraped_at.desc()).all():
            key = (tg.game_date, tg.away_team, tg.home_team)
            if key not in seen:
                seen.add(key)
                upcoming_map[tg.game_date].append(tg)

    # 출력 디렉토리 준비
    (OUT_DIR / "games").mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "predictions").mkdir(parents=True, exist_ok=True)

    # 완료 경기 페이지 생성
    print(f"  📋 완료 경기 페이지 {len(completed_map)}건...")
    with app.app_context():
        for d, games in sorted(completed_map.items(), reverse=True):
            html = build_completed_page(d, games, team_stats)
            (OUT_DIR / "games" / f"{d.isoformat()}.html").write_text(html, encoding="utf-8")
            print(f"    → games/{d.isoformat()}.html ({len(games)}경기)")

    # 예정 경기 페이지 생성
    print(f"  🔮 예정 경기 페이지 {len(upcoming_map)}건...")
    with app.app_context():
        for d, games in sorted(upcoming_map.items()):
            html = build_prediction_page(d, games, team_stats)
            (OUT_DIR / "predictions" / f"{d.isoformat()}.html").write_text(html, encoding="utf-8")
            print(f"    → predictions/{d.isoformat()}.html ({len(games)}경기)")

    # 배트맨 배팅 분석 페이지
    print("  🎯 배트맨 분석 페이지 생성...")
    with app.app_context():
        from app.simulator import run_simulation, calc_betman_picks
        from app.routes import _pitcher_days_since_last_start, _recent_rpg
        betman_games = []
        if upcoming_map:
            next_date = sorted(upcoming_map.keys())[0]
            for tg in upcoming_map[next_date]:
                away_stat = team_stats.get(tg.away_team)
                home_stat = team_stats.get(tg.home_team)
                fatigue_a = _pitcher_days_since_last_start(tg.away_pitcher, next_date)
                fatigue_h = _pitcher_days_since_last_start(tg.home_pitcher, next_date)
                recent_a  = _recent_rpg(tg.away_team, next_date)
                recent_h  = _recent_rpg(tg.home_team, next_date)
                sim = run_simulation(tg.away_team, tg.home_team, away_stat, home_stat, tg,
                                     fatigue_away_days=fatigue_a, fatigue_home_days=fatigue_h,
                                     recent_rpg_away=recent_a, recent_rpg_home=recent_h)
                betman_games.append((tg, sim))
            betman_html = build_betman_page(betman_games, next_date)
            (OUT_DIR / "betman.html").write_text(betman_html, encoding="utf-8")
            print(f"    → betman.html ({len(betman_games)}경기)")

    # 삼성 라이온즈 페이지
    print("  🦁 삼성 라이온즈 페이지 생성...")
    from app.scraper import scrape_samsung_leaders
    leaders = scrape_samsung_leaders()
    samsung_stat = team_stats.get("삼성")
    samsung_html = build_samsung_page(samsung_stat, samsung_games, leaders)
    (OUT_DIR / "samsung.html").write_text(samsung_html, encoding="utf-8")
    print("    → samsung.html")

    # 인덱스 페이지
    print("  📄 index.html 생성...")
    completed_dates = sorted(completed_map.keys(), reverse=True)
    upcoming_dates  = sorted(upcoming_map.keys())
    index_html = build_index(
        completed_dates, upcoming_dates,
        {d: len(v) for d, v in completed_map.items()},
        {d: len(v) for d, v in upcoming_map.items()},
    )
    (OUT_DIR / "index.html").write_text(index_html, encoding="utf-8")

    # GitHub Pages push
    print("  🚀 GitHub Pages push...")

    def run_git(*args):
        return subprocess.run(["git", "-C", str(BLOG_ROOT)] + list(args),
                               capture_output=True, text=True)

    run_git("add", "kbo/")
    commit_r = run_git("commit", "-m", f"Update KBO blog: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    if "nothing to commit" in commit_r.stdout + commit_r.stderr:
        print("    (변경 없음, push 스킵)")
    else:
        print("    ✓ commit")
        # pull --rebase 후 push (원격 충돌 방지)
        pull_r = run_git("pull", "--rebase", "--autostash")
        if pull_r.returncode != 0:
            print(f"    ⚠️  pull: {pull_r.stderr.strip()[:120]}")
        push_r = run_git("push")
        if push_r.returncode != 0:
            print(f"    ⚠️  push: {push_r.stderr.strip()[:120]}")
        else:
            print("    ✓ push")

    print(f"\n✅ 완료! https://namjinwoo.github.io/kbo/")


if __name__ == "__main__":
    main()
