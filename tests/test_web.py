import io
import sqlite3
import threading

import pytest

from app import create_app
from app.service import correct_statement, load_statement, lock_row

CSV = ("院系,设备费,差旅费,材料费\n"
       "物理学院,4500,5500,12000\n"
       "化学学院,5500,4500,8000\n").encode("utf-8")


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "test.sqlite3")


@pytest.fixture()
def client(db_path):
    app = create_app(db_path)
    app.config.update(TESTING=True)
    with app.test_client() as c:
        c.post("/upload", data={"csv": (io.BytesIO(CSV), "a.csv")},
               content_type="multipart/form-data")
        yield c


def _form_from_stmt(stmt, overrides=None):
    overrides = overrides or {}
    form = {"version": str(stmt["version"]), "base_version": str(stmt["base_version"])}
    for r in stmt["rows"]:
        for c in stmt["cols"]:
            value = overrides.get((r.key, c.key), stmt["base"][(r.key, c.key)])
            form[f"cell_{r.key}_{c.key}"] = str(value)
    return form


def test_seed_sample_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SEED_SAMPLE", "1")
    db = str(tmp_path / "seeded.sqlite3")
    create_app(db)
    stmt = load_statement(db)
    assert stmt is not None and stmt["verified"] is True
    assert len(stmt["rows"]) == 3


def test_upload_creates_balanced_statement(client, db_path):
    stmt = load_statement(db_path)
    assert stmt is not None
    assert stmt["verified"] is True
    assert len(stmt["rows"]) == 2 and len(stmt["cols"]) == 3
    html = client.get("/statement").get_data(as_text=True)
    assert "核对通过" in html
    assert "版号 1" in html


def test_upload_rejects_bad_csv(client):
    bad = "院系,设备\n甲,-1\n".encode()
    resp = client.post("/upload", data={"csv": (io.BytesIO(bad), "b.csv")},
                       content_type="multipart/form-data", follow_redirects=True)
    assert "非负整数" in resp.get_data(as_text=True)


def test_upload_limits_dimensions(client):
    header = "院系," + ",".join(f"k{j}" for j in range(11))
    lines = [header] + ["x%d,%s" % (i, ",".join("1" for _ in range(11))) for i in range(2)]
    resp = client.post("/upload", data={"csv": (io.BytesIO(("\n".join(lines)).encode()), "c.csv")},
                       content_type="multipart/form-data", follow_redirects=True)
    assert "最多 10 列" in resp.get_data(as_text=True)


def test_lock_record_visible_on_statement(client, db_path):
    stmt = load_statement(db_path)
    client.post("/lock/r1", data={"version": stmt["version"]})
    html = client.get("/statement").get_data(as_text=True)
    assert "锁定记录" in html
    assert "物理学院" in html


def test_lock_current_view_and_reject_stale(client, db_path):
    stmt = load_statement(db_path)
    resp = client.post("/lock/r1", data={"version": stmt["version"]}, follow_redirects=True)
    assert "已按当前所见版锁定" in resp.get_data(as_text=True)
    stmt2 = load_statement(db_path)
    assert "r1" in stmt2["locks"]
    assert stmt2["locks"]["r1"]["subtotal"] == stmt["row_totals"]["r1"]
    assert stmt2["locks"]["r1"]["cells"] == [stmt["public"][("r1", c.key)] for c in stmt["cols"]]
    resp = client.post("/lock/r2", data={"version": stmt["version"]}, follow_redirects=True)
    assert "版号已过期" in resp.get_data(as_text=True)
    assert "r2" not in load_statement(db_path)["locks"]


def test_correction_keeps_locked_values_and_updates_both_tables(client, db_path):
    stmt = load_statement(db_path)
    client.post("/lock/r1", data={"version": stmt["version"]})
    stmt = load_statement(db_path)
    resp = client.post("/correct", data=_form_from_stmt(stmt, {("r2", "c1"): 4600}),
                       follow_redirects=True)
    assert "更正已生效" in resp.get_data(as_text=True)
    new = load_statement(db_path)
    assert new["verified"] is True
    assert new["base"][("r2", "c1")] == 4600
    assert new["public"][("r1", "c1")] == stmt["public"][("r1", "c1")]
    assert new["row_totals"]["r1"] == stmt["row_totals"]["r1"]
    assert new["locks"]["r1"]["cells"] == stmt["locks"]["r1"]["cells"]
    assert new["version"] == stmt["version"] + 1


def test_infeasible_correction_leaves_tables_untouched(db_path):
    app = create_app(db_path)
    csv = ("院系,设备,差旅\n"
           "甲,4500,0\n"
           "乙,4500,10000\n").encode()
    with app.test_client() as c:
        c.post("/upload", data={"csv": (io.BytesIO(csv), "a.csv")},
               content_type="multipart/form-data")
    stmt = load_statement(db_path)
    lock_row(db_path, "r1", stmt["version"])
    stmt = load_statement(db_path)
    before = (dict(stmt["base"]), dict(stmt["public"]), dict(stmt["row_totals"]),
              dict(stmt["col_totals"]), stmt["total"], stmt["version"])
    with app.test_client() as c:
        resp = c.post("/correct", data=_form_from_stmt(stmt, {("r1", "c1"): 0,
                                                              ("r1", "c2"): 0,
                                                              ("r2", "c1"): 4500,
                                                              ("r2", "c2"): 10000}))
    assert resp.status_code == 422
    assert "无可行取整方案" in resp.get_data(as_text=True)
    after = load_statement(db_path)
    assert (dict(after["base"]), dict(after["public"]), dict(after["row_totals"]),
            dict(after["col_totals"]), after["total"], after["version"]) == before


def test_upload_rejects_duplicate_names_and_bad_encoding(client):
    dup_rows = "院系,设备\n甲,100\n甲,200\n".encode()
    resp = client.post("/upload", data={"csv": (io.BytesIO(dup_rows), "d.csv")},
                       content_type="multipart/form-data", follow_redirects=True)
    assert "院系名称不能重复" in resp.get_data(as_text=True)

    dup_cols = "院系,设备,设备\n甲,100,200\n".encode()
    resp = client.post("/upload", data={"csv": (io.BytesIO(dup_cols), "d.csv")},
                       content_type="multipart/form-data", follow_redirects=True)
    assert "支出科目名称不能重复" in resp.get_data(as_text=True)

    gbk = "院系,设备\n甲,100\n".encode("gbk")
    resp = client.post("/upload", data={"csv": (io.BytesIO(gbk), "g.csv")},
                       content_type="multipart/form-data", follow_redirects=True)
    assert "UTF-8" in resp.get_data(as_text=True)


def test_rounding_total_uses_half_up(tmp_path):
    app = create_app(str(tmp_path / "half.sqlite3"))
    db_path = app.config["DB_PATH"]
    csv = ("院系,设备\n"
           "甲,5000\n").encode()
    with app.test_client() as c:
        c.post("/upload", data={"csv": (io.BytesIO(csv), "a.csv")},
               content_type="multipart/form-data")
    stmt = load_statement(db_path)
    assert stmt["total"] == 1  # 5000 元四舍五入为 1 万元
    assert stmt["verified"] is True


def test_non_integer_correction_rejected(client, db_path):
    stmt = load_statement(db_path)
    form = _form_from_stmt(stmt)
    form["cell_r1_c1"] = "12.5"
    resp = client.post("/correct", data=form)
    assert resp.status_code == 400
    assert "非负整数" in resp.get_data(as_text=True)
    assert load_statement(db_path)["base"][("r1", "c1")] == stmt["base"][("r1", "c1")]


def test_correct_page_prefills_base_amounts(client, db_path):
    html = client.get("/correct").get_data(as_text=True)
    stmt = load_statement(db_path)
    for r in stmt["rows"]:
        for c in stmt["cols"]:
            assert f'name="cell_{r.key}_{c.key}"' in html
    assert 'value="4500"' in html


def test_stale_base_version_correction_rejected(client, db_path):
    stmt = load_statement(db_path)
    form = _form_from_stmt(stmt, {("r1", "c1"): 5000})
    form["base_version"] = "0"
    # 锁定/更新会使页面过期；更正只依赖底表版号，因此这里显式改坏底表版号
    resp = client.post("/correct", data=form)
    assert resp.status_code == 409
    assert "页面已过期" in resp.get_data(as_text=True)
    html = resp.get_data(as_text=True)
    # 基于旧底表填写的 5000 不应被回显（避免覆盖他人更正）
    assert 'name="cell_r1_c1"' in html
    assert 'value="5000"' not in html
    assert load_statement(db_path)["base"][("r1", "c1")] == stmt["base"][("r1", "c1")]


def test_lock_succeeds_while_correction_is_computing(db_path):
    """算表（写事务之外）期间，另一请求可以成功锁定。"""
    app = create_app(db_path)
    csv = ("院系,设备,差旅\n"
           "一,4500,4500\n"
           "二,5500,5500\n"
           "三,5500,5500\n").encode()
    with app.test_client() as c:
        c.post("/upload", data={"csv": (io.BytesIO(csv), "a.csv")},
               content_type="multipart/form-data")
    stmt = load_statement(db_path)
    started = threading.Event()
    release = threading.Event()
    outcome = {}

    from app.rounding import solve_display

    def slow_compute(cells, locks):
        started.set()
        assert release.wait(5)
        return solve_display(cells, locks)

    def run_correct():
        cells = [[stmt["base"][(r.key, c.key)] for c in stmt["cols"]] for r in stmt["rows"]]
        cells[0][0] = 6000
        try:
            outcome["result"] = correct_statement(
                db_path, cells, stmt["base_version"], compute=slow_compute)
        except Exception as exc:  # pragma: no cover - surfaced by assertions
            outcome["error"] = exc

    t = threading.Thread(target=run_correct)
    t.start()
    assert started.wait(5)
    # 计算尚未提交，写事务未开启：锁定应当立即成功
    lock_row(db_path, "r2", stmt["version"])
    release.set()
    t.join(10)
    assert "error" not in outcome
    new = load_statement(db_path)
    assert new["verified"] is True
    assert "r2" in new["locks"]
    assert new["locks"]["r2"]["cells"] == [stmt["public"][("r2", c.key)]
                                           for c in stmt["cols"]]
    assert new["base"][("r1", "c1")] == 6000
    assert new["version"] == stmt["version"] + 2


def test_locked_correction_can_be_infeasible(db_path):
    """真实场景：锁定行保持原值后，新底表无可行取整方案。"""
    from app.rounding import solve_display

    app = create_app(db_path)
    csv = ("院系,设备,差旅,材料\n"
           "甲,2500,25000,7500\n"
           "乙,10001,4500,5000\n").encode()
    with app.test_client() as c:
        c.post("/upload", data={"csv": (io.BytesIO(csv), "a.csv")},
               content_type="multipart/form-data")
    stmt = load_statement(db_path)
    lock_row(db_path, "r2", stmt["version"])
    stmt = load_statement(db_path)
    assert stmt["locks"]["r2"]["cells"] == [1, 0, 0]
    before = (dict(stmt["base"]), dict(stmt["public"]), stmt["version"],
              stmt["base_version"])
    cells = [[2500, 25000, 1], [10001, 4500, 5000]]
    from app.service import InfeasibleError
    with pytest.raises(InfeasibleError):
        correct_statement(db_path, cells, stmt["base_version"])
    after = load_statement(db_path)
    assert (dict(after["base"]), dict(after["public"]), after["version"],
            after["base_version"]) == before
    assert solve_display(cells, {1: (1, [1, 0, 0])}) is None


def test_repeated_interleaved_locks_end_with_retry_message(db_path):
    app = create_app(db_path)
    csv = ("院系,设备\n" + "\n".join(f"院{i},{4000 + i * 1000}" for i in range(6))).encode()
    with app.test_client() as c:
        c.post("/upload", data={"csv": (io.BytesIO(csv), "a.csv")},
               content_type="multipart/form-data")
    stmt = load_statement(db_path)
    from app.rounding import solve_display

    counter = {"n": 0}

    def compute(cells, locks):
        # 每次核算都模拟另一请求抢先锁定一个新行
        key = f"r{counter['n'] + 1}"
        counter["n"] += 1
        try:
            lock_row(db_path, key, load_statement(db_path)["version"])
        except Exception:
            pass
        return solve_display(cells, locks)

    cells = [[stmt["base"][(r.key, c.key)] for c in stmt["cols"]] for r in stmt["rows"]]
    from app.service import ConflictError
    with pytest.raises(ConflictError) as err:
        correct_statement(db_path, cells, stmt["base_version"],
                          compute=compute, max_attempts=3)
    assert "并发" in str(err.value)
    after = load_statement(db_path)
    assert dict(after["base"]) == dict(stmt["base"])
    assert after["base_version"] == stmt["base_version"]
    assert len(after["locks"]) >= 3


def test_save_failure_leaves_both_tables_unchanged(db_path, monkeypatch):
    app = create_app(db_path)
    csv = ("院系,设备,差旅\n"
           "甲,4500,4500\n"
           "乙,5500,5500\n").encode()
    with app.test_client() as c:
        c.post("/upload", data={"csv": (io.BytesIO(csv), "a.csv")},
               content_type="multipart/form-data")
    stmt = load_statement(db_path)
    before = (dict(stmt["base"]), dict(stmt["public"]), dict(stmt["row_totals"]),
              dict(stmt["col_totals"]), stmt["total"], stmt["version"],
              stmt["base_version"])

    from app import service

    def failing_write(*args, **kwargs):
        # 先写入部分数据，再模拟磁盘/数据库错误，验证事务整体回滚
        args[0].execute("DELETE FROM base_cell")
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(service, "_write_statement", failing_write)
    cells = [[stmt["base"][(r.key, c.key)] for c in stmt["cols"]] for r in stmt["rows"]]
    cells[0][0] = 6000
    from app.service import SaveError
    with pytest.raises(SaveError):
        service.correct_statement(db_path, cells, stmt["base_version"])
    after = load_statement(db_path)
    assert (dict(after["base"]), dict(after["public"]), dict(after["row_totals"]),
            dict(after["col_totals"]), after["total"], after["version"],
            after["base_version"]) == before


def test_lock_during_computation_is_picked_up(db_path):
    """模拟：更正核算期间另一请求锁定了某行。更正应使用最新锁定重算。"""
    app = create_app(db_path)
    csv = ("院系,设备,差旅\n"
           "一,4500,4500\n"
           "二,5500,5500\n"
           "三,5500,5500\n").encode()
    with app.test_client() as c:
        c.post("/upload", data={"csv": (io.BytesIO(csv), "a.csv")},
               content_type="multipart/form-data")
    stmt = load_statement(db_path)
    original = {(r.key, c.key): stmt["public"][(r.key, c.key)] for r in stmt["rows"]
                for c in stmt["cols"]}

    def interleaved_compute(cells, locks):
        # 第一次核算时，模拟并发锁定（锁定第 1 行按当前所见值）
        if not getattr(interleaved_compute, "locked", False):
            interleaved_compute.locked = True
            lock_row(db_path, "r1", load_statement(db_path)["version"])
        from app.rounding import solve_display
        return solve_display(cells, locks)

    cells = [[stmt["base"][(r.key, c.key)] for c in stmt["cols"]] for r in stmt["rows"]]
    cells[0][0] = 5000
    result = correct_statement(db_path, cells, stmt["base_version"],
                               compute=interleaved_compute)
    new = load_statement(db_path)
    assert result["version"] == new["version"]
    assert new["verified"] is True
    assert "r1" in new["locks"]
    lock = new["locks"]["r1"]
    # 锁定值必须来自并发锁定那一刻的表，且更正后保持原值
    assert [new["public"][("r1", c.key)] for c in new["cols"]] == lock["cells"]
    assert new["row_totals"]["r1"] == lock["subtotal"]
    assert lock["cells"] == [original[("r1", c.key)] for c in stmt["cols"]]
    assert interleaved_compute.locked is True


def test_interleaved_lock_makes_correction_infeasible_and_rolls_back(db_path):
    """核算期间新锁定先落库，按最新锁定重算无解：更正不得写入。"""
    app = create_app(db_path)
    csv = ("院系,设备,差旅\n"
           "甲,4500,0\n"
           "乙,4500,10000\n").encode()
    with app.test_client() as c:
        c.post("/upload", data={"csv": (io.BytesIO(csv), "a.csv")},
               content_type="multipart/form-data")
    stmt = load_statement(db_path)
    before = (dict(stmt["base"]), dict(stmt["public"]), stmt["version"],
              stmt["base_version"])

    from app.rounding import solve_display

    def compute(cells, locks):
        if not getattr(compute, "did_lock", False):
            compute.did_lock = True
            # 用“更正前”的表锁定第一行
            lock_row(db_path, "r1", load_statement(db_path)["version"])
        return solve_display(cells, locks)

    cells = [[0, 0], [4500, 10000]]
    from app.service import InfeasibleError
    with pytest.raises(InfeasibleError):
        correct_statement(db_path, cells, stmt["base_version"], compute=compute)
    after = load_statement(db_path)
    # 更正造成的数据与底表版号保持不变；期间落库的锁定正常保留
    assert (dict(after["base"]), dict(after["public"]),
            after["base_version"]) == (before[0], before[1], before[3])
    assert after["version"] == before[2] + 1
    # 锁定内容来自更正前公示表
    assert "r1" in after["locks"]
    assert after["locks"]["r1"]["cells"] == [
        before[1][("r1", c.key)] for c in after["cols"]]


def test_upload_replaces_statement_and_clears_locks(client, db_path):
    stmt = load_statement(db_path)
    client.post("/lock/r1", data={"version": stmt["version"]})
    stmt = load_statement(db_path)
    new_csv = ("院系,设备费\n"
               "新学院,12345\n").encode()
    resp = client.post("/upload", data={"csv": (io.BytesIO(new_csv), "new.csv"),
                                        "version": str(stmt["version"])},
                       follow_redirects=True)
    assert "底表已导入" in resp.get_data(as_text=True)
    new = load_statement(db_path)
    assert not new["locks"]
    assert len(new["rows"]) == 1 and len(new["cols"]) == 1
    assert new["base"][("r1", "c1")] == 12345
    assert new["verified"] is True


def test_upload_rejects_more_than_20_rows(client):
    lines = ["院系,设备"] + [f"院系{i},100" for i in range(21)]
    resp = client.post("/upload",
                       data={"csv": (io.BytesIO(("\n".join(lines)).encode()), "big.csv")},
                       follow_redirects=True)
    assert "最多 20 行" in resp.get_data(as_text=True)


def test_correct_error_keeps_user_input(client, db_path):
    stmt = load_statement(db_path)
    form = _form_from_stmt(stmt, {("r1", "c1"): 987654})
    form["cell_r2_c1"] = "-1"
    resp = client.post("/correct", data=form)
    assert resp.status_code == 400
    html = resp.get_data(as_text=True)
    assert 'value="987654"' in html
    assert 'value="-1"' in html


def test_lock_wins_before_and_correction_retries_or_reports(db_path, monkeypatch):
    """新锁定先保存且导致按旧锁定值无解时：更正不得写入，应以最新锁定重算或报错。"""
    from app import service

    app = create_app(db_path)
    csv = ("院系,设备,差旅\n"
           "甲,4500,0\n"
           "乙,4500,10000\n").encode()
    with app.test_client() as c:
        c.post("/upload", data={"csv": (io.BytesIO(csv), "a.csv")},
               content_type="multipart/form-data")
    stmt = load_statement(db_path)
    lock_row(db_path, "r1", stmt["version"])
    stmt = load_statement(db_path)
    before = (dict(stmt["base"]), dict(stmt["public"]), stmt["version"])

    real_compute = service.solve_display
    calls = {"n": 0}

    def compute(cells, locks):
        calls["n"] += 1
        return real_compute(cells, locks)

    cells = [[stmt["base"][(r.key, c.key)] for c in stmt["cols"]] for r in stmt["rows"]]
    cells[0] = [0, 0]
    from app.service import InfeasibleError
    with pytest.raises(InfeasibleError):
        correct_statement(db_path, cells, stmt["base_version"], compute=compute)
    after = load_statement(db_path)
    assert (dict(after["base"]), dict(after["public"]), after["version"]) == before


def test_upload_too_large_redirects(client):
    big = b"x" * (3 * 1024 * 1024)
    resp = client.post("/upload", data={"csv": (io.BytesIO(big), "big.csv")},
                       content_type="multipart/form-data", follow_redirects=True)
    assert "上传文件过大" in resp.get_data(as_text=True)


def test_correction_publishes_minimal_cross_cell_changes(db_path):
    """更正后公示表应与更正前相比交叉格改动最少，并在结果中报告格数。"""
    app = create_app(db_path)
    csv = ("院系,设备,差旅\n"
           "一,4500,4500\n"
           "二,5500,5500\n"
           "三,5500,5500\n").encode()
    with app.test_client() as c:
        c.post("/upload", data={"csv": (io.BytesIO(csv), "a.csv")},
               content_type="multipart/form-data")
    stmt = load_statement(db_path)
    prev = {(r.key, c.key): stmt["public"][(r.key, c.key)]
            for r in stmt["rows"] for c in stmt["cols"]}

    cells = [[stmt["base"][(r.key, c.key)] for c in stmt["cols"]] for r in stmt["rows"]]
    cells[2][0] += 4000
    result = correct_statement(db_path, cells, stmt["base_version"])
    new = load_statement(db_path)

    from app.rounding import count_cell_changes
    from app.service import _lock_signature
    cur = [[new["public"][(r.key, c.key)] for c in new["cols"]] for r in new["rows"]]
    old = [[prev[(r.key, c.key)] for c in new["cols"]] for r in new["rows"]]
    assert new["verified"] is True
    assert result["changed_cells"] == count_cell_changes(cur, old)
    assert result["changed_cells"] <= 1
    # 小计/总计不计入格数：这里只改一格公示值
    assert sum(c != o for r_c, r_o in zip(cur, old) for c, o in zip(r_c, r_o)) <= 1


def test_correction_with_multiple_optima_keeps_previous_where_possible(db_path):
    """存在多张合法结果时，取与上一版最接近的一张（0 改动）。"""
    app = create_app(db_path)
    csv = ("院系,设备,差旅\n"
           "一,4500,4500\n"
           "二,5500,5500\n"
           "三,5500,5500\n").encode()
    with app.test_client() as c:
        c.post("/upload", data={"csv": (io.BytesIO(csv), "a.csv")},
               content_type="multipart/form-data")
    stmt = load_statement(db_path)
    prev = {(r.key, c.key): stmt["public"][(r.key, c.key)]
            for r in stmt["rows"] for c in stmt["cols"]}

    # +1 元不改变总计，存在多张合法表；应保留原公示表
    cells = [[stmt["base"][(r.key, c.key)] for c in stmt["cols"]] for r in stmt["rows"]]
    cells[2][0] += 1
    result = correct_statement(db_path, cells, stmt["base_version"])
    new = load_statement(db_path)
    assert result["changed_cells"] == 0
    for r in new["rows"]:
        for c in new["cols"]:
            assert new["public"][(r.key, c.key)] == prev[(r.key, c.key)]
    assert new["verified"] is True


def test_new_lock_during_computation_recomputes_minimal_changes(db_path):
    """算表期间新增锁定：先保存锁定，再按最新锁定重新求最少改动。"""
    app = create_app(db_path)
    csv = ("院系,设备,差旅\n"
           "一,4500,4500\n"
           "二,5500,5500\n"
           "三,5500,5500\n").encode()
    with app.test_client() as c:
        c.post("/upload", data={"csv": (io.BytesIO(csv), "a.csv")},
               content_type="multipart/form-data")
    stmt = load_statement(db_path)
    prev = {(r.key, c.key): stmt["public"][(r.key, c.key)]
            for r in stmt["rows"] for c in stmt["cols"]}

    from app.rounding import solve_display

    calls = {"n": 0}

    def compute(cells, locks, previous=None):
        calls["n"] += 1
        if calls["n"] == 1:
            # 第一次核算期间，另一请求按“更正前”公示表锁定第二行
            lock_row(db_path, "r2", load_statement(db_path)["version"])
        return solve_display(cells, locks, previous=previous)

    cells = [[stmt["base"][(r.key, c.key)] for c in stmt["cols"]] for r in stmt["rows"]]
    cells[2][0] += 1
    result = correct_statement(db_path, cells, stmt["base_version"], compute=compute)
    new = load_statement(db_path)
    assert calls["n"] == 2  # 新锁定触发一次重算
    assert result["changed_cells"] == 0
    assert "r2" in new["locks"]
    assert [new["public"][("r2", c.key)] for c in new["cols"]] == \
        [prev[("r2", c.key)] for c in new["cols"]]
    for r in new["rows"]:
        for c in new["cols"]:
            assert new["public"][(r.key, c.key)] == prev[(r.key, c.key)]
    assert new["verified"] is True
    # 锁定行保持锁定原值
    assert new["row_totals"]["r2"] == new["locks"]["r2"]["subtotal"]


def test_upload_new_base_after_correction_starts_fresh(db_path):
    """他人先更正或重传底表：旧页面的更正提示刷新；重传后按新底表重建公示表。"""
    app = create_app(db_path)
    csv = ("院系,设备,差旅\n"
           "甲,4500,4500\n"
           "乙,5500,5500\n").encode()
    with app.test_client() as c:
        c.post("/upload", data={"csv": (io.BytesIO(csv), "a.csv")},
               content_type="multipart/form-data")
    old = load_statement(db_path)
    form = _form_from_stmt(old)
    # 他人先重传了底表
    new_csv = ("院系,设备,差旅\n"
               "甲,4500,4500\n"
               "乙,5500,5500\n"
               "丙,10000,0\n").encode()
    with app.test_client() as c:
        c.post("/upload", data={"csv": (io.BytesIO(new_csv), "b.csv"),
                                "version": str(old["version"])},
               content_type="multipart/form-data")
    # 旧页面提交更正：应提示刷新，且不写入
    before = load_statement(db_path)
    resp = app.test_client().post("/correct", data=form)
    assert resp.status_code == 409
    assert "刷新" in resp.get_data(as_text=True)
    after = load_statement(db_path)
    assert (dict(after["base"]), after["base_version"], after["version"]) == \
        (dict(before["base"]), before["base_version"], before["version"])


def test_lock_before_submit_is_used_for_minimal_changes(db_path):
    """页面打开后、提交前他人锁定一行：底表版号未变，更正应以最新锁定求最少改动。"""
    app = create_app(db_path)
    csv = ("院系,设备,差旅\n"
           "一,4500,4500\n"
           "二,5500,5500\n"
           "三,5500,5500\n").encode()
    with app.test_client() as c:
        c.post("/upload", data={"csv": (io.BytesIO(csv), "a.csv")},
               content_type="multipart/form-data")
    stmt = load_statement(db_path)
    prev = {(r.key, c.key): stmt["public"][(r.key, c.key)]
            for r in stmt["rows"] for c in stmt["cols"]}
    # 页面打开时看到的是 base_version；随后他人按当前所见版锁定 r2
    lock_row(db_path, "r2", stmt["version"])
    form = _form_from_stmt(load_statement(db_path), {("r3", "c1"): 5501})
    resp = app.test_client().post("/correct", data=form,
                                  follow_redirects=True)
    assert "更正已生效" in resp.get_data(as_text=True)
    new = load_statement(db_path)
    # 锁定行仍保持锁定原值
    assert [new["public"][("r2", c.key)] for c in new["cols"]] == \
        [prev[("r2", c.key)] for c in new["cols"]]
    assert new["row_totals"]["r2"] == new["locks"]["r2"]["subtotal"]
    # 其它交叉格尽量不变
    changed = [(r.key, c.key) for r in new["rows"] for c in new["cols"]
               if new["public"][(r.key, c.key)] != prev[(r.key, c.key)]]
    assert changed == []
    assert new["verified"] is True
