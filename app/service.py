from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Callable, Optional

from .db import connect, now_iso
from .rounding import ceil_wan, floor_wan, round_wan, solve_display


class AppError(Exception):
    status = 400


class ValidationError(AppError):
    status = 400


class ConflictError(AppError):
    """版本/结构冲突，需要刷新后重试。reason 供页面决定是否保留已填内容。"""

    status = 409

    def __init__(self, message, reason="stale_version"):
        super().__init__(message)
        self.reason = reason


class InfeasibleError(AppError):
    status = 422


class SaveError(AppError):
    status = 500


@dataclass
class Row:
    key: str
    label: str
    pos: int


@dataclass
class Col:
    key: str
    label: str
    pos: int


def _read_rows(conn):
    return [Row(r["row_key"], r["label"], r["pos"]) for r in conn.execute(
        "SELECT row_key, label, pos FROM row_def ORDER BY pos")]


def _read_cols(conn):
    return [Col(r["col_key"], r["label"], r["pos"]) for r in conn.execute(
        "SELECT col_key, label, pos FROM col_def ORDER BY pos")]


def _read_locks(conn):
    locks = {}
    for r in conn.execute(
        "SELECT row_key, row_label, subtotal_wan, cells_json, statement_version, locked_at "
        "FROM lock_record ORDER BY id"):
        locks[r["row_key"]] = {
            "row_label": r["row_label"],
            "subtotal": r["subtotal_wan"],
            "cells": json.loads(r["cells_json"]),
            "statement_version": r["statement_version"],
            "locked_at": r["locked_at"],
        }
    return locks


def _meta(conn):
    r = conn.execute("SELECT version, base_version FROM meta WHERE id = 1").fetchone()
    return r["version"], r["base_version"]


def _public_matrix(conn, rows, cols):
    values = {(r, c): 0 for r in [x.key for x in rows] for c in [x.key for x in cols]}
    for r in conn.execute("SELECT row_key, col_key, value_wan FROM public_cell"):
        values[(r["row_key"], r["col_key"])] = r["value_wan"]
    return values


def load_statement(db_path: str):
    """读取整张报表；空表返回 None。"""
    conn = connect(db_path)
    try:
        version, base_version = _meta(conn)
        rows = _read_rows(conn)
        if not rows:
            return None
        cols = _read_cols(conn)
        base = {}
        for r in conn.execute("SELECT row_key, col_key, amount_yuan FROM base_cell"):
            base[(r["row_key"], r["col_key"])] = r["amount_yuan"]
        public = _public_matrix(conn, rows, cols)
        row_totals = {r["row_key"]: r["subtotal_wan"] for r in conn.execute(
            "SELECT row_key, subtotal_wan FROM public_row")}
        col_totals = {r["col_key"]: r["subtotal_wan"] for r in conn.execute(
            "SELECT col_key, subtotal_wan FROM public_col")}
        total_row = conn.execute("SELECT total_wan FROM public_total WHERE id = 1").fetchone()
        total = total_row["total_wan"] if total_row else 0
        locks = _read_locks(conn)
        verified = _verify(rows, cols, base, public, row_totals, col_totals, total, locks)
        return {
            "version": version,
            "base_version": base_version,
            "rows": rows,
            "cols": cols,
            "base": base,
            "public": public,
            "row_totals": row_totals,
            "col_totals": col_totals,
            "total": total,
            "locks": locks,
            "verified": verified,
        }
    finally:
        conn.close()


def _verify(rows, cols, base, public, row_totals, col_totals, total, locks) -> bool:
    """核对表内横竖加平以及每格取整合法性。"""
    try:
        for row in rows:
            s = 0
            for col in cols:
                v = public[(row.key, col.key)]
                amount = base[(row.key, col.key)]
                if row.key not in locks:
                    if v not in ({amount // 10000} if amount % 10000 == 0
                                 else {amount // 10000, amount // 10000 + 1}):
                        return False
                s += v
            if s != row_totals[row.key]:
                return False
            if row.key in locks:
                # 锁定行按锁定原值核对，不随底表重新取整
                lock = locks[row.key]
                if row_totals[row.key] != lock["subtotal"]:
                    return False
                if [public[(row.key, c.key)] for c in cols] != lock["cells"]:
                    return False
            else:
                exact = sum(base[(row.key, c.key)] for c in cols)
                if not (floor_wan(exact) <= row_totals[row.key] <= ceil_wan(exact)):
                    return False
        for col in cols:
            s = sum(public[(r.key, col.key)] for r in rows)
            if s != col_totals[col.key]:
                return False
            exact = sum(base[(r.key, col.key)] for r in rows)
            if not (floor_wan(exact) <= col_totals[col.key] <= ceil_wan(exact)):
                return False
        if sum(row_totals[r.key] for r in rows) != total:
            return False
        if sum(col_totals[c.key] for c in cols) != total:
            return False
        if total != round_wan(sum(base.values())):
            return False
    except (KeyError, TypeError):
        return False
    return True


def _write_statement(conn, rows, cols, cells, solution, bump_base: bool):
    """在当前写事务内整体替换底表与公示表（行/列定义保持不变）。"""
    ts = now_iso()
    conn.execute("DELETE FROM base_cell")
    conn.execute("DELETE FROM public_cell")
    conn.execute("DELETE FROM public_row")
    conn.execute("DELETE FROM public_col")
    conn.execute("DELETE FROM public_total")
    conn.executemany(
        "INSERT INTO base_cell (row_key, col_key, amount_yuan) VALUES (?, ?, ?)",
        [(r.key, c.key, cells[i][j])
         for i, r in enumerate(rows) for j, c in enumerate(cols)])
    conn.executemany(
        "INSERT INTO public_cell (row_key, col_key, value_wan) VALUES (?, ?, ?)",
        [(r.key, c.key, solution["P"][i][j])
         for i, r in enumerate(rows) for j, c in enumerate(cols)])
    conn.executemany(
        "INSERT INTO public_row (row_key, subtotal_wan) VALUES (?, ?)",
        [(r.key, solution["rows"][i]) for i, r in enumerate(rows)])
    conn.executemany(
        "INSERT INTO public_col (col_key, subtotal_wan) VALUES (?, ?)",
        [(c.key, solution["cols"][j]) for j, c in enumerate(cols)])
    conn.execute("INSERT INTO public_total (id, total_wan) VALUES (1, ?)", (solution["total"],))
    version, base_version = _meta(conn)
    conn.execute(
        "UPDATE meta SET version = ?, base_version = ?, updated_at = ? WHERE id = 1",
        (version + 1, base_version + (1 if bump_base else 0), ts))


def upload_statement(db_path: str, row_labels, col_labels, amounts,
                     expected_version: Optional[int]):
    """新建/替换整张报表（替换会清空锁定记录）。"""
    if not row_labels or not col_labels:
        raise ValidationError("CSV 至少需要一行数据和一列支出科目。")
    if len(row_labels) > 20:
        raise ValidationError("最多 20 行院系。")
    if len(col_labels) > 10:
        raise ValidationError("最多 10 列支出科目。")
    if len(amounts) != len(row_labels):
        raise ValidationError("数据行数与院系数不一致。")
    validate_cells(amounts, len(row_labels), len(col_labels))

    solution = solve_display(amounts, {})
    if solution is None:
        raise InfeasibleError("该底表无法生成行列都加平的万元公示表，未保存。")

    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            version, _ = _meta(conn)
            if expected_version is not None and version != expected_version:
                raise ConflictError("报表已被他人更新，版号已过期，请刷新后重试。")
            conn.execute("DELETE FROM lock_record")
            conn.execute("DELETE FROM public_total")
            conn.execute("DELETE FROM public_col")
            conn.execute("DELETE FROM public_row")
            conn.execute("DELETE FROM public_cell")
            conn.execute("DELETE FROM base_cell")
            conn.execute("DELETE FROM row_def")
            conn.execute("DELETE FROM col_def")
            rows = [Row(f"r{i + 1}", label, i + 1) for i, label in enumerate(row_labels)]
            cols = [Col(f"c{j + 1}", label, j + 1) for j, label in enumerate(col_labels)]
            conn.executemany("INSERT INTO row_def (row_key, label, pos) VALUES (?, ?, ?)",
                             [(r.key, r.label, r.pos) for r in rows])
            conn.executemany("INSERT INTO col_def (col_key, label, pos) VALUES (?, ?, ?)",
                             [(c.key, c.label, c.pos) for c in cols])
            _write_statement(conn, rows, cols, amounts, solution, bump_base=True)
            conn.execute("COMMIT")
            return {"version": version + 1}
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    except sqlite3.Error as exc:
        raise SaveError("保存失败，原报表未变更。") from exc
    finally:
        conn.close()


def lock_row(db_path: str, row_key: str, expected_version: int):
    """锁定当前版公示表中的一行；版本过期则拒绝。"""
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            version, _ = _meta(conn)
            if version != expected_version:
                raise ConflictError("版号已过期（有人刚刚更新或锁定），请刷新页面后重试。")
            row = conn.execute(
                "SELECT row_key, label FROM row_def WHERE row_key = ?", (row_key,)).fetchone()
            if row is None:
                raise ValidationError("院系行不存在，请刷新页面。")
            if conn.execute("SELECT 1 FROM lock_record WHERE row_key = ?", (row_key,)).fetchone():
                raise ConflictError("该院系行已锁定，请刷新页面。")
            cols = _read_cols(conn)
            cells = []
            for col in cols:
                r = conn.execute(
                    "SELECT value_wan FROM public_cell WHERE row_key = ? AND col_key = ?",
                    (row_key, col.key)).fetchone()
                if r is None:
                    raise SaveError("公示表数据缺失，未锁定。")
                cells.append(r["value_wan"])
            sub = conn.execute(
                "SELECT subtotal_wan FROM public_row WHERE row_key = ?", (row_key,)).fetchone()
            if sub is None or sub["subtotal_wan"] != sum(cells):
                raise SaveError("公示行小计不一致，未锁定。")
            conn.execute(
                "INSERT INTO lock_record (row_key, row_label, subtotal_wan, cells_json, "
                "statement_version, locked_at) VALUES (?, ?, ?, ?, ?, ?)",
                (row_key, row["label"], sub["subtotal_wan"],
                 json.dumps(cells, ensure_ascii=False), version, now_iso()))
            conn.execute(
                "UPDATE meta SET version = version + 1, updated_at = ? WHERE id = 1",
                (now_iso(),))
            conn.execute("COMMIT")
            return {"version": version + 1, "row_label": row["label"]}
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    except sqlite3.Error as exc:
        raise SaveError("锁定保存失败，数据未变更。") from exc
    finally:
        conn.close()


def _snapshot(db_path: str):
    conn = connect(db_path)
    try:
        version, base_version = _meta(conn)
        rows = _read_rows(conn)
        cols = _read_cols(conn)
        locks = _read_locks(conn)
        return version, base_version, rows, cols, locks
    finally:
        conn.close()


def _lock_signature(locks):
    return tuple(sorted((key, v["subtotal"], tuple(v["cells"])) for key, v in locks.items()))


def correct_statement(db_path: str, cells, expected_base_version: int,
                      compute: Callable = solve_display, max_attempts: int = 5):
    """更正底表并按最新锁定重算公示表。

    计算在写事务之外完成，期间其他请求仍可锁定。
    提交前用底表版号 + 锁定快照做乐观校验；遇到新锁定会按最新锁定重算后重试。
    底表与公示表在同一事务写入，失败/无解均保持原样。
    """
    for _attempt in range(max_attempts):
        version, base_version, rows, cols, locks = _snapshot(db_path)
        if version == 0 or not rows:
            raise ConflictError("报表不存在，请刷新页面。")
        if base_version != expected_base_version:
            raise ConflictError("底表已被他人更正，页面已过期，请刷新后重新填写。",
                                reason="stale_base")
        row_keys = [r.key for r in rows]
        col_keys = [c.key for c in cols]
        validate_cells(cells, len(row_keys), len(col_keys))
        lock_map = {i: (locks[r.key]["subtotal"], locks[r.key]["cells"])
                    for i, r in enumerate(rows) if r.key in locks}
        solution = compute(cells, lock_map)
        if solution is None:
            # 可能是并发锁定导致，确认版本未变后再判定无解
            v2, b2, _, _, _ = _snapshot(db_path)
            if v2 != version or b2 != base_version:
                continue
            raise InfeasibleError(
                "在保留已锁定院系公示值的前提下无可行取整方案，更正未生效，原表保持不变。")

        conn = connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                version2, base_version2 = _meta(conn)
                if base_version2 != base_version:
                    raise ConflictError("底表已被他人更正，页面已过期，请刷新后重新填写。",
                                        reason="stale_base")
                if version2 != version:
                    # 计算期间有新锁定（或其它变更）：回滚，按最新锁定重算
                    conn.execute("ROLLBACK")
                    continue
                locks2 = _read_locks(conn)
                if _lock_signature(locks2) != _lock_signature(locks):
                    conn.execute("ROLLBACK")
                    continue
                rows2 = _read_rows(conn)
                cols2 = _read_cols(conn)
                if ([r.key for r in rows2] != row_keys or
                        [c.key for c in cols2] != col_keys):
                    raise ConflictError("院系或支出科目已变化，请刷新页面。")
                _write_statement(conn, rows2, cols2, cells, solution, bump_base=True)
                conn.execute("COMMIT")
                return {"version": version + 1}
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        except sqlite3.Error as exc:
            raise SaveError("保存失败，原报表未变更。") from exc
        finally:
            conn.close()
    raise ConflictError("保存时持续发生并发更新，请刷新页面后重试。", reason="busy")

MAX_AMOUNT = 10 ** 15


def validate_cells(cells, row_count=None, col_count=None):
    if not isinstance(cells, (list, tuple)) or not cells:
        raise ValidationError("金额数据为空。")
    if row_count is not None and len(cells) != row_count:
        raise ValidationError("院系数与当前报表不一致，请刷新页面。")
    width = len(cells[0])
    if col_count is not None and width != col_count:
        raise ValidationError("支出科目数与当前报表不一致，请刷新页面。")
    for row in cells:
        if len(row) != width:
            raise ValidationError("每行金额列数必须一致。")
        for v in row:
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                raise ValidationError("金额须为非负整数元。")
            if v > MAX_AMOUNT:
                raise ValidationError("金额超出可处理范围。")
