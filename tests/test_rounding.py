import itertools
import random

from app.rounding import (ceil_wan, count_cell_changes, floor_wan, round_wan,
                          solve_display)


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


def test_min_changes_matches_bruteforce_when_previous_given():
    """给出更正前公示表时，解应是全部合法解中交叉格改动最少的。"""
    rng = random.Random(13)
    vals = [0, 2500, 4500, 5000, 5500, 7500, 9999, 10000, 10001, 19999, 25000]
    for R, C in [(1, 2), (2, 2), (2, 3), (3, 3)]:
        for _ in range(200):
            cells = [[rng.choice(vals) for _ in range(C)] for _ in range(R)]
            previous = [[rng.choice([0, 1, 2, 3, 4]) for _ in range(C)] for _ in range(R)]
            locks = {}
            if rng.random() < 0.4:
                base = solve_display(cells)
                i = rng.randrange(R)
                locks = {i: (base["rows"][i], base["P"][i][:])}
            legal = brute_force(cells, locks)
            got = solve_display(cells, locks, previous=previous)
            assert bool(legal) == bool(got), (cells, previous, locks)
            if not legal:
                continue
            assert_valid(cells, locks, got)
            best = min(count_cell_changes(P, previous) for P in legal)
            assert count_cell_changes(got["P"], previous) == best, (
                cells, previous, locks, got["P"])


def test_previous_legal_solution_is_kept_changed_zero():
    """原公示表本身合法时，最少改动结果应原样保留（0 格改动）。"""
    cells = [[5000, 5000], [5000, 5000]]
    legal = [[[0, 1], [1, 0]], [[1, 0], [0, 1]]]
    for previous in legal:
        sol = solve_display(cells, previous=previous)
        assert sol["P"] == previous
        assert count_cell_changes(sol["P"], previous) == 0


def test_min_changes_respects_locks_preferring_other_cells():
    """锁定行保持原值，最少改动只在未锁定的交叉格上取舍。"""
    cells = [[4500, 4500], [5500, 5500]]
    base = solve_display(cells)
    lock = {0: (base["rows"][0], base["P"][0][:])}
    previous = [[0, 1], [0, 1]]
    sol = solve_display(cells, lock, previous=previous)
    assert sol["P"][0] == base["P"][0]
    # 锁定行保持不变；其余行取与 previous 贴合、且满足加平的方案
    legal = brute_force(cells, lock)
    best = min(count_cell_changes(P, previous) for P in legal)
    assert count_cell_changes(sol["P"], previous) == best
    assert count_cell_changes(sol["P"], previous) < len(previous) * len(previous[0])


def test_min_changes_counts_only_cross_cells():
    """改动计数只针对院系×科目交叉格，不受小计/总计影响。"""
    P = [[1, 0], [0, 1]]
    previous = {**{(0, 0): 1, (0, 1): 0, (1, 0): 0, (1, 1): 0}}
    assert count_cell_changes(P, previous) == 1  # 只有 (1,1) 变了


def test_min_changes_deterministic_and_valid_for_many_optima():
    """存在多张合法结果时，同一输入重复求解应得到同一张表。"""
    cells = [[5000, 5000], [5000, 5000]]
    previous = [[0, 1], [1, 0]]
    first = solve_display(cells, previous=previous)
    for _ in range(10):
        again = solve_display(cells, previous=previous)
        assert again["P"] == first["P"]
    assert first["P"] == previous
