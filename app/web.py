from __future__ import annotations

import os
import re
from pathlib import Path

from flask import (Flask, flash, redirect, render_template, request, url_for)

from .csvio import CsvError, parse_csv
from .db import init_db
from .service import (AppError, ConflictError, InfeasibleError, ValidationError,
                      correct_statement, load_statement, lock_row, upload_statement)


def create_app(db_path: str | None = None) -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    app.config["DB_PATH"] = db_path or os.environ.get(
        "DB_PATH", str(Path(__file__).resolve().parent.parent / "data" / "finance.sqlite3"))
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
    init_db(app.config["DB_PATH"])
    if os.environ.get("SEED_SAMPLE") == "1":
        _seed_sample(app.config["DB_PATH"])

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/")
    def index():
        stmt = load_statement(app.config["DB_PATH"])
        if stmt is None:
            return redirect(url_for("upload_page"))
        return redirect(url_for("statement_page"))

    @app.get("/upload")
    def upload_page():
        stmt = load_statement(app.config["DB_PATH"])
        return render_template("upload.html", statement=stmt)

    @app.post("/upload")
    def upload():
        file = request.files.get("csv")
        if file is None or not file.filename:
            flash("请选择 CSV 文件。", "error")
            return redirect(url_for("upload_page"))
        try:
            parsed = parse_csv(file.read())
            expected = request.form.get("version")
            try:
                expected_version = int(expected) if expected not in (None, "") else None
            except ValueError:
                flash("页面版号无效，请刷新后重试。", "error")
                return redirect(url_for("upload_page"))
            upload_statement(app.config["DB_PATH"], parsed.row_labels,
                             parsed.col_labels, parsed.amounts, expected_version)
        except CsvError as exc:
            flash(str(exc), "error")
            return redirect(url_for("upload_page"))
        except InfeasibleError as exc:
            flash(str(exc), "error")
            return redirect(url_for("upload_page"))
        except (ConflictError, ValidationError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("upload_page"))
        flash("底表已导入，公示表已生成。", "success")
        return redirect(url_for("statement_page"))

    @app.get("/statement")
    def statement_page():
        stmt = load_statement(app.config["DB_PATH"])
        if stmt is None:
            return redirect(url_for("upload_page"))
        view = build_view(stmt)
        return render_template("statement.html", s=stmt, v=view)

    @app.post("/lock/<row_key>")
    def lock(row_key: str):
        try:
            expected_version = int(request.form.get("version", ""))
        except ValueError:
            flash("缺少版号，请刷新页面后重试。", "error")
            return redirect(url_for("statement_page"))
        try:
            lock_row(app.config["DB_PATH"], row_key, expected_version)
        except ConflictError as exc:
            flash(str(exc), "error")
        except AppError as exc:
            flash(str(exc), "error")
        else:
            flash("已按当前所见版锁定该院系行。", "success")
        return redirect(url_for("statement_page"))

    @app.get("/correct")
    def correct_page():
        stmt = load_statement(app.config["DB_PATH"])
        if stmt is None:
            return redirect(url_for("upload_page"))
        form_values = {f"cell_{r.key}_{c.key}": str(stmt["base"][(r.key, c.key)])
                       for r in stmt["rows"] for c in stmt["cols"]}
        return render_template("correct.html", s=stmt, v=build_view(stmt),
                               form_values=form_values)

    @app.post("/correct")
    def correct():
        stmt = load_statement(app.config["DB_PATH"])
        if stmt is None:
            return redirect(url_for("upload_page"))
        rows, cols = stmt["rows"], stmt["cols"]
        cells = []
        error = None
        for r in rows:
            row = []
            for c in cols:
                field = f"cell_{r.key}_{c.key}"
                raw = request.form.get(field, "").strip()
                if not re.fullmatch(r"[0-9]{1,16}", raw):
                    error = f"{r.label} / {c.label} 的金额须为非负整数元。"
                    break
                row.append(int(raw))
            if error:
                break
            cells.append(row)
        # 保留用户已填内容用于回显
        form_values = {f"cell_{r.key}_{c.key}": request.form.get(f"cell_{r.key}_{c.key}", "")
                       for r in rows for c in cols}
        if error:
            flash(error + " 更正未生效，原表保持不变。", "error")
            return render_template("correct.html", s=stmt, v=build_view(stmt),
                                   form_values=form_values), 400
        try:
            base_version = int(request.form.get("base_version", ""))
        except (TypeError, ValueError):
            flash("页面版号无效，请刷新页面后重新填写。", "error")
            fresh = load_statement(app.config["DB_PATH"])
            return render_template("correct.html", s=fresh, v=build_view(fresh),
                                   form_values=form_values), 409
        try:
            correct_statement(app.config["DB_PATH"], cells, base_version)
        except ConflictError as exc:
            flash(str(exc), "error")
            fresh = load_statement(app.config["DB_PATH"])
            if exc.reason == "stale_base":
                # 底表已变：丢弃基于旧底表填写的内容，避免覆盖他人更正
                form_values = {f"cell_{r.key}_{c.key}": str(fresh["base"][(r.key, c.key)])
                               for r in fresh["rows"] for c in fresh["cols"]}
            return render_template("correct.html", s=fresh, v=build_view(fresh),
                                   form_values=form_values), 409
        except InfeasibleError as exc:
            flash(str(exc), "error")
            fresh = load_statement(app.config["DB_PATH"])
            return render_template("correct.html", s=fresh, v=build_view(fresh),
                                   form_values=form_values), 422
        except ValidationError as exc:
            flash(str(exc) + " 更正未生效，原表保持不变。", "error")
            fresh = load_statement(app.config["DB_PATH"])
            return render_template("correct.html", s=fresh, v=build_view(fresh),
                                   form_values=form_values), 400
        except AppError as exc:
            flash(str(exc), "error")
            fresh = load_statement(app.config["DB_PATH"])
            return render_template("correct.html", s=fresh, v=build_view(fresh),
                                   form_values=form_values), 500
        flash("更正已生效：底表与公示表已同时更新，锁定行保持原值。", "success")
        return redirect(url_for("statement_page"))

    @app.errorhandler(413)
    def too_large(_exc):
        flash("上传文件过大（上限 2 MB）。", "error")
        return redirect(url_for("upload_page"))

    return app


def _seed_sample(db_path: str):
    """仅用于演示：库为空时导入 sample.csv。"""
    if load_statement(db_path) is not None:
        return
    sample = Path(__file__).resolve().parent.parent / "sample.csv"
    if not sample.exists():
        return
    try:
        parsed = parse_csv(sample.read_bytes())
        upload_statement(db_path, parsed.row_labels, parsed.col_labels,
                         parsed.amounts, None)
    except AppError:
        pass


def build_view(stmt):
    """把扁平存储整理成模板好用的结构，并做展示前的独立核对。"""
    rows, cols = stmt["rows"], stmt["cols"]
    matrix = []
    for r in rows:
        cells = [stmt["public"][(r.key, c.key)] for c in cols]
        matrix.append({
            "key": r.key,
            "label": r.label,
            "cells": [{"col": c, "value": val, "base": stmt["base"][(r.key, c.key)]}
                      for c, val in zip(cols, cells)],
            "subtotal": stmt["row_totals"][r.key],
            "locked": r.key in stmt["locks"],
            "lock": stmt["locks"].get(r.key),
        })
    # 独立复算页面上的横加/竖加，防止展示的合计与明细不一致
    row_sums = [sum(cell["value"] for cell in row["cells"]) for row in matrix]
    col_sums = [sum(row["cells"][j]["value"] for row in matrix) for j in range(len(cols))]
    display_ok = (
        all(rs == row["subtotal"] for rs, row in zip(row_sums, matrix))
        and all(cs == stmt["col_totals"][c.key] for cs, c in zip(col_sums, cols))
        and sum(row_sums) == stmt["total"] == sum(col_sums)
    )
    return {
        "matrix": matrix,
        "cols": cols,
        "col_totals": [stmt["col_totals"][c.key] for c in cols],
        "total": stmt["total"],
        "row_sums": row_sums,
        "col_sums": col_sums,
        "display_ok": display_ok,
        "verified": stmt["verified"],
        "locked_count": len(stmt["locks"]),
        "unlocked_count": len(rows) - len(stmt["locks"]),
    }

