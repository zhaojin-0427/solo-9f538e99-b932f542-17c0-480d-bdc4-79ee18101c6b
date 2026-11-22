import itertools
import random

from app.rounding import (ceil_wan, floor_wan, round_wan, solve_display,
                          solve_min_change)


def brute_force(cells, locks=None):
    locks = locks or {}
    R, C = len(cells), len(cells[0])
    options = []
    for i in range(R):
        for j in range(C):
            v = cells[i][j]
            opts = {v // 10000}
            if v % 10000:
                opts.add(v // 10000 + 1)
            options.append(sorted(opts))
    total = round_wan(sum(map(sum, cells)))
    found = []
    for combo in itertools.product(*options):
        P = [list(combo[i * C:(i + 1) * C]) for i in range(R)]
        rows = [sum(r) for r in P]
        cols = [sum(P[i][j] for i in range(R)) for j in range(C)]
        if sum(rows) != total:
            continue
        ok = True
        for i in range(R):
            if i in locks:
                if (rows[i], P[i]) != (locks[i][0], list(locks[i][1])):
                    ok = False
                    break
                continue
            exact = sum(cells[i])
            if not (floor_wan(exact) <= rows[i] <= ceil_wan(exact)):
                ok = False
                break
        if not ok:
            continue
        for j in range(C):
            exact = sum(cells[i][j] for i in range(R))
            if not (floor_wan(exact) <= cols[j] <= ceil_wan(exact)):
                ok = False
                break
        if ok:
            found.append(P)
    return found


def assert_valid(cells, locks, sol):
    R, C = len(cells), len(cells[0])
    P = sol["P"]
    rows = [sum(r) for r in P]
    cols = [sum(P[i][j] for i in range(R)) for j in range(C)]
    assert sol["rows"] == rows
    assert sol["cols"] == cols
    assert sum(rows) == sol["total"] == round_wan(sum(map(sum, cells)))
    for i in range(R):
        exact = sum(cells[i])
        if i in (locks or {}):
            assert (rows[i], P[i]) == (locks[i][0], list(locks[i][1]))
        else:
            assert floor_wan(exact) <= rows[i] <= ceil_wan(exact)
    for j in range(C):
        exact = sum(cells[i][j] for i in range(R))
        assert floor_wan(exact) <= cols[j] <= ceil_wan(exact)
    for i in range(R):
        if i in (locks or {}):
            continue
        for j in range(C):
            v = cells[i][j]
            allowed = {v // 10000} if v % 10000 == 0 else {v // 10000, v // 10000 + 1}
            assert P[i][j] in allowed


def test_basic_rounding_helpers():
    assert floor_wan(9999) == 0
    assert ceil_wan(9999) == 1
    assert ceil_wan(10000) == 1
    assert round_wan(4999) == 0
    assert round_wan(5000) == 1
    assert round_wan(14999) == 1
    assert round_wan(15000) == 2


def test_simple_table_crossfoots():
    sol = solve_display([[4500, 5500], [5500, 4500]])
    assert_valid([[4500, 5500], [5500, 4500]], {}, sol)
    assert sol["total"] == 2


def test_exact_multiples_are_fixed():
    sol = solve_display([[10000, 20000], [30000, 40000]])
    assert sol["P"] == [[1, 2], [3, 4]]
    assert sol["rows"] == [3, 7]
    assert sol["cols"] == [4, 6]
    assert sol["total"] == 10


def test_solver_matches_bruteforce_random():
    rng = random.Random(7)
    vals = [0, 2500, 4500, 5000, 5500, 7500, 9999, 10000, 10001, 19999, 25000]
    for R, C in [(1, 2), (2, 2), (2, 3), (3, 3)]:
        for _ in range(300):
            cells = [[rng.choice(vals) for _ in range(C)] for _ in range(R)]
            locks = {}
            base = solve_display(cells)
            assert base is not None
            assert_valid(cells, {}, base)
            if rng.random() < 0.6:
                i = rng.randrange(R)
                locks = {i: (base["rows"][i], base["P"][i][:])}
            expected = brute_force(cells, locks)
            got = solve_display(cells, locks)
            assert bool(expected) == bool(got), (cells, locks, got)
            if got is not None:
                assert_valid(cells, locks, got)


def test_locked_row_is_preserved_exactly():
    cells = [[4500, 4500, 0], [5500, 5500, 0], [0, 0, 30000]]
    base = solve_display(cells)
    lock = {0: (base["rows"][0], base["P"][0][:])}
    sol = solve_display(cells, lock)
    assert sol["P"][0] == base["P"][0]
    assert sol["rows"][0] == base["rows"][0]
    assert_valid(cells, lock, sol)


def test_infeasible_with_locked_row_returns_none():
    # 锁定行公示为 [1,0]，底表把其它行改成导致列/总约束无法满足
    cells = [[4500, 0], [4500, 10000]]
    base = solve_display(cells)
    lock = {0: (base["rows"][0], base["P"][0][:])}
    assert solve_display([[0, 0], [4500, 10000]], lock) is None


def test_locked_row_keeps_original_values_even_if_base_moves():
    cells = [[4500, 0], [0, 0], [0, 10000]]
    base = solve_display(cells)
    lock = {0: (base["rows"][0], base["P"][0][:])}
    moved = [[9000, 0], [4500, 0], [0, 10000]]
    sol = solve_display(moved, lock)
    assert sol is not None
    # 锁定行的公示值与小计保持原值，不随底表重新取整
    assert (sol["rows"][0], sol["P"][0]) == (lock[0][0], lock[0][1])
    assert_valid(moved, lock, sol)


def test_rejects_invalid_inputs():
    assert solve_display([]) is None
    assert solve_display([[]]) is None
    assert solve_display([[1, 2], [3]]) is None
    assert solve_display([[-1, 2]]) is None
    assert solve_display([[1.5, 2]]) is None
    assert solve_display([[True, 2]]) is None


def test_max_size_table_is_fast():
    rng = random.Random(3)
    cells = [[rng.randrange(0, 10_000_000) for _ in range(10)] for _ in range(20)]
    sol = solve_display(cells)
    assert sol is not None
    assert_valid(cells, {}, sol)


def _all_legal(cells, locks=None):
    """暴力枚举全部合法公示矩阵（小表用）。"""
    return brute_force(cells, locks)


def _diff(P, prev):
    return sum(1 for i in range(len(P)) for j in range(len(P[0])) if P[i][j] != prev[i][j])


def test_min_change_matches_bruteforce_random():
    rng = random.Random(11)
    vals = [0, 2500, 4500, 5000, 5500, 7500, 9999, 10000, 10001, 19999, 25000]
    multi = 0
    for R, C in [(1, 2), (2, 2), (2, 3), (3, 3)]:
        for _ in range(120):
            cells = [[rng.choice(vals) for _ in range(C)] for _ in range(R)]
            base = solve_display(cells)
            assert base is not None
            locks = {}
            if rng.random() < 0.5:
                i = rng.randrange(R)
                locks = {i: (base["rows"][i], base["P"][i][:])}
            legal = _all_legal(cells, locks)
            got = solve_min_change(cells, locks, previous=base["P"])
            assert got is not None
            if len(legal) > 1:
                multi += 1
            assert got["P"] in legal
            best = min(_diff(P, base["P"]) for P in legal)
            assert got["changes"] == best == _diff(got["P"], base["P"])
            # 确定性：同输入重复求解一致
            again = solve_min_change(cells, locks, previous=base["P"])
            assert again["P"] == got["P"]
    assert multi > 0


def test_min_change_keeps_previous_when_it_is_legal():
    # 每行精确合计 9000、ceil=1，每行至多 1 格向上；总需 2 格向上。
    cells = [[4500, 4500], [4500, 4500]]
    prev = [[1, 0], [0, 1]]
    got = solve_min_change(cells, None, prev)
    assert got["P"] == prev
    assert got["changes"] == 0
    other = [[0, 1], [1, 0]]
    got2 = solve_min_change(cells, None, other)
    assert got2["P"] == other and got2["changes"] == 0


def test_min_change_picks_fewest_diff_cells():
    # previous 非合法（0 格向上，不满足总计），最优合法方案应恰好改 2 格
    cells = [[4500, 4500], [4500, 4500]]
    prev = [[0, 0], [0, 0]]
    got = solve_min_change(cells, None, prev)
    legal = _all_legal(cells)
    assert got["P"] in legal
    assert got["changes"] == min(_diff(P, prev) for P in legal) == 2


def test_min_change_respects_locks_and_counts_only_cross_cells():
    cells = [[4500, 4500, 0], [5500, 5500, 0], [0, 0, 30000]]
    base = solve_display(cells)
    lock = {0: (base["rows"][0], base["P"][0][:])}
    got = solve_min_change(cells, lock, previous=base["P"])
    assert got is not None
    # 锁定行原值不变
    assert got["P"][0] == base["P"][0] and got["rows"][0] == base["rows"][0]
    assert_valid(cells, lock, got)
    legal = _all_legal(cells, lock)
    best = min(_diff(P, base["P"]) for P in legal)
    assert got["changes"] == best


def test_min_change_infeasible_returns_none():
    assert solve_min_change([[0, 0], [4500, 10000]],
                            {0: (1, [1, 0])},
                            previous=[[1, 0], [0, 1]]) is None


def test_min_change_bad_previous_still_returns_legal_solution():
    cells = [[4500, 5500], [5500, 4500]]
    got = solve_min_change(cells, None, previous=[[99, 99], [99, 99]])
    assert got is not None and got["P"] in _all_legal(cells)
    assert got["changes"] == _diff(got["P"], [[99, 99], [99, 99]])
    got2 = solve_min_change(cells, None, previous=[[1]])  # 形状非法
    assert got2 is not None and got2["P"] in _all_legal(cells)
