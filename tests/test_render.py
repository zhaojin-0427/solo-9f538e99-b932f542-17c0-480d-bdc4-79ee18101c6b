import io
import random
import re

from app import create_app
from app.rounding import ceil_wan, floor_wan, round_wan
from app.service import load_statement

CELL_RE = re.compile(r'data-row="([^"]+)" data-col="([^"]+)">(\d+)<')
ROW_RE = re.compile(r'data-row-subtotal="([^"]+)">(\d+)<')
COL_RE = re.compile(r'data-col-subtotal="([^"]+)">(\d+)<')
TOTAL_RE = re.compile(r'data-grand-total>(\d+)<')


def parse_page(html):
    cells = {(m.group(1), m.group(2)): int(m.group(3)) for m in CELL_RE.finditer(html)}
    row_sub = {m.group(1): int(m.group(2)) for m in ROW_RE.finditer(html)}
    col_sub = {m.group(1): int(m.group(2)) for m in COL_RE.finditer(html)}
    total = int(TOTAL_RE.search(html).group(1))
    return cells, row_sub, col_sub, total


def crossfoot(stmt, cells, row_sub, col_sub, total):
    rows, cols = [r.key for r in stmt["rows"]], [c.key for c in stmt["cols"]]
    for r in rows:
        assert sum(cells[(r, c)] for c in cols) == row_sub[r], r
    for c in cols:
        assert sum(cells[(r, c)] for r in rows) == col_sub[c], c
    assert sum(row_sub.values()) == total
    assert sum(col_sub.values()) == total
    assert total == round_wan(sum(stmt["base"].values()))


def test_rendered_page_crossfoots_for_random_tables(tmp_path):
    rng = random.Random(5)
    for n in range(8):
        db = str(tmp_path / f"r{n}.sqlite3")
        app = create_app(db)
        R, C = rng.randint(1, 5), rng.randint(1, 5)
        labels = [f"院系{i}" for i in range(R)]
        cols = [f"科目{j}" for j in range(C)]
        vals = [[rng.randrange(0, 5_000_000) for _ in range(C)] for _ in range(R)]
        csv = ",".join(["院系"] + cols) + "\n" + "\n".join(
            labels[i] + "," + ",".join(str(v) for v in vals[i]) for i in range(R))
        with app.test_client() as c:
            c.post("/upload", data={"csv": (io.BytesIO(csv.encode()), "a.csv")},
                   content_type="multipart/form-data")
            stmt = load_statement(db)
            assert stmt["verified"] is True
            html = c.get("/statement").get_data(as_text=True)
        cells, row_sub, col_sub, total = parse_page(html)
        crossfoot(stmt, cells, row_sub, col_sub, total)
        for i, r in enumerate(stmt["rows"]):
            for j, c in enumerate(stmt["cols"]):
                amount = vals[i][j]
                v = cells[(r.key, c.key)]
                if amount % 10000 == 0:
                    assert v == amount // 10000
                else:
                    assert v in (amount // 10000, amount // 10000 + 1)
            exact = sum(vals[i])
            assert floor_wan(exact) <= row_sub[r.key] <= ceil_wan(exact)
        for j, c in enumerate(stmt["cols"]):
            exact = sum(vals[i][j] for i in range(R))
            assert floor_wan(exact) <= col_sub[c.key] <= ceil_wan(exact)


def test_rendered_page_crossfoots_with_locked_row(tmp_path):
    db = str(tmp_path / "lock.sqlite3")
    app = create_app(db)
    csv = ("院系,设备,差旅,材料\n"
           "甲,4500,5500,12000\n"
           "乙,5500,4500,8000\n").encode()
    with app.test_client() as c:
        c.post("/upload", data={"csv": (io.BytesIO(csv), "a.csv")},
               content_type="multipart/form-data")
        stmt = load_statement(db)
        c.post("/lock/r1", data={"version": stmt["version"]})
        stmt = load_statement(db)
        html = c.get("/statement").get_data(as_text=True)
    cells, row_sub, col_sub, total = parse_page(html)
    crossfoot(stmt, cells, row_sub, col_sub, total)
    lock = stmt["locks"]["r1"]
    assert [cells[("r1", c.key)] for c in stmt["cols"]] == lock["cells"]
    assert row_sub["r1"] == lock["subtotal"]
