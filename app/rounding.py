"""万元取整核算。

规则:
* 每个单元格可向下或向上取整到万元；金额恰为 10000 的整数倍时取整唯一。
* 每行/每列小计同样可向下或向上取整到万元。
* 总计按四舍五入到万元（.5 向上）。
* 公示表必须横竖加平：行小计 = 该行单元格之和，列小计 = 该列单元格之和，
  总计 = 行小计之和 = 列小计之和。
* 已锁定院系行的公示单元格与小计保持锁定时的原值。

用带下界的可行流求一个满足全部约束的解（不是逐格独立四舍五入）。
"""
from collections import deque

UNIT = 10000

__all__ = ["UNIT", "solve_display", "floor_wan", "ceil_wan", "round_wan"]


def floor_wan(yuan: int) -> int:
    return yuan // UNIT


def ceil_wan(yuan: int) -> int:
    return -((-yuan) // UNIT)


def round_wan(yuan: int) -> int:
    """四舍五入到万元（.5 向上）。金额均为非负整数元。"""
    return (yuan + UNIT // 2) // UNIT


class _Edge:
    __slots__ = ("to", "rev", "cap", "lower", "initial")

    def __init__(self, to, rev, cap, lower=0):
        self.to = to
        self.rev = rev
        self.cap = cap        # 剩余容量
        self.lower = lower
        self.initial = cap    # 加边时的容量（不含下界）


class _Dinic:
    def __init__(self, n):
        self.n = n
        self.graph = [[] for _ in range(n)]

    def add_edge(self, u, v, cap, lower=0):
        if cap < 0:
            raise ValueError("negative capacity")
        self.graph[u].append(_Edge(v, len(self.graph[v]), cap - lower, lower))
        self.graph[v].append(_Edge(u, len(self.graph[u]) - 1, 0))
        return u, len(self.graph[u]) - 1

    def max_flow(self, s, t):
        total = 0
        n = self.n
        while True:
            level = [-1] * n
            level[s] = 0
            q = deque([s])
            while q:
                u = q.popleft()
                for e in self.graph[u]:
                    if e.cap > 0 and level[e.to] < 0:
                        level[e.to] = level[u] + 1
                        q.append(e.to)
            if level[t] < 0:
                return total
            it = [0] * n

            def dfs(u, pushed):
                if u == t:
                    return pushed
                while it[u] < len(self.graph[u]):
                    e = self.graph[u][it[u]]
                    if e.cap > 0 and level[e.to] == level[u] + 1:
                        sent = dfs(e.to, min(pushed, e.cap))
                        if sent:
                            e.cap -= sent
                            self.graph[e.to][e.rev].cap += sent
                            return sent
                    it[u] += 1
                return 0

            while True:
                sent = dfs(s, 10 ** 30)
                if not sent:
                    break
                total += sent


def _feasible_circulation(node_count, edges):
    """edges: (u, v, lower, upper)。返回每条边的流量；不可行返回 None。"""
    n = node_count + 2
    S, T = node_count, node_count + 1
    din = _Dinic(n)
    demand = [0] * node_count
    refs = []
    for u, v, lo, hi in edges:
        if lo > hi or lo < 0:
            return None
        refs.append(din.add_edge(u, v, hi, lo))
        demand[v] += lo
        demand[u] -= lo
    need = 0
    for v in range(node_count):
        if demand[v] > 0:
            din.add_edge(S, v, demand[v])
            need += demand[v]
        elif demand[v] < 0:
            din.add_edge(v, T, -demand[v])
    if din.max_flow(S, T) != need:
        return None
    flows = []
    for (u, idx), (_, _, lo, _hi) in zip(refs, edges):
        e = din.graph[u][idx]
        flows.append(lo + (e.initial - e.cap))
    return flows


def _is_nonneg_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def solve_display(cells, locks=None):
    """求一张满足取整与横竖加平的公示表。

    cells: R 行 C 列的非负整数元。
    locks: {row_index: (subtotal_wan, [cell_wan, ...])}，锁定原值。
    返回 dict(P, rows, cols, total)；无解返回 None。
    """
    if not cells or not cells[0]:
        return None
    R, C = len(cells), len(cells[0])
    if any(len(row) != C for row in cells):
        return None
    if any(not _is_nonneg_int(v) for row in cells for v in row):
        return None
    locks = locks or {}

    base = [[v // UNIT for v in row] for row in cells]
    rem = [[v % UNIT for v in row] for row in cells]
    row_exact = [sum(row) for row in cells]
    col_exact = [sum(cells[i][j] for i in range(R)) for j in range(C)]
    total = round_wan(sum(row_exact))

    locked_values = {}
    locked_subtotal = 0
    locked_contribution = [0] * C
    for i, lock in locks.items():
        if not isinstance(i, int) or isinstance(i, bool) or i < 0 or i >= R:
            return None
        if not isinstance(lock, (tuple, list)) or len(lock) != 2:
            return None
        sub, vals = lock
        if not isinstance(vals, (tuple, list)) or len(vals) != C:
            return None
        if not _is_nonneg_int(sub):
            return None
        row_sum = 0
        for j, v in enumerate(vals):
            if not _is_nonneg_int(v):
                return None
            # 锁定行以锁定时的公示值为准，保持原值，不随底表变动重新取整。
            locked_values[(i, j)] = v
            locked_contribution[j] += v
            row_sum += v
        if row_sum != sub:
            return None
        locked_subtotal += sub

    unlocked_rows = [i for i in range(R) if i not in locks]
    if not unlocked_rows:
        P = [[locked_values[(i, j)] for j in range(C)] for i in range(R)]
        rows = [sum(r) for r in P]
        cols = [sum(P[i][j] for i in range(R)) for j in range(C)]
        if sum(rows) != total:
            return None
        for j in range(C):
            if not (floor_wan(col_exact[j]) <= cols[j] <= ceil_wan(col_exact[j])):
                return None
        for i in locks:
            if rows[i] != locks[i][0] or P[i] != list(locks[i][1]):
                return None
        return {"P": P, "rows": rows, "cols": cols, "total": total}

    # 行可向上取整的格数下/上界（锁定行不在此列）
    row_lo = {}
    row_hi = {}
    for i in unlocked_rows:
        fsum = sum(base[i])
        row_lo[i] = floor_wan(row_exact[i]) - fsum
        row_hi[i] = ceil_wan(row_exact[i]) - fsum
        if row_lo[i] > row_hi[i]:
            return None

    # 列在“锁定行贡献”之外可向上取整的格数下/上界
    col_lo = [0] * C
    col_hi = [0] * C
    for j in range(C):
        col_lo[j] = floor_wan(col_exact[j]) - locked_contribution[j]
        col_hi[j] = ceil_wan(col_exact[j]) - locked_contribution[j]
        if col_lo[j] > col_hi[j]:
            return None

    base_unlocked = sum(base[i][j] for i in unlocked_rows for j in range(C))
    target_ups = total - locked_subtotal - base_unlocked
    max_ups = sum(1 for i in unlocked_rows for j in range(C) if rem[i][j] > 0)
    if target_ups < 0 or target_ups > max_ups:
        return None

    # 网络: 源 -> 行 -> 列 -> 汇，另加 汇->源 的精确“总向上取整格数”边。
    S = 0
    row_node = {i: 1 + k for k, i in enumerate(unlocked_rows)}
    col_base = 1 + len(unlocked_rows)
    T = col_base + C
    node_count = T + 1
    edges = []
    for i in unlocked_rows:
        lo = max(0, row_lo[i])
        hi = row_hi[i]
        if hi < 0 or lo > hi:
            return None
        edges.append((S, row_node[i], lo, hi))
    cell_edge_index = {}
    for i in unlocked_rows:
        for j in range(C):
            if rem[i][j] == 0:
                continue
            cell_edge_index[(i, j)] = len(edges)
            edges.append((row_node[i], col_base + j, 0, 1))
    for j in range(C):
        fsum = sum(base[i][j] for i in unlocked_rows)
        lo = col_lo[j] - fsum
        hi = col_hi[j] - fsum
        if hi < 0 or lo > hi:
            return None
        edges.append((col_base + j, T, max(0, lo), hi))
    edges.append((T, S, target_ups, target_ups))

    flows = _feasible_circulation(node_count, edges)
    if flows is None:
        return None

    P = [[0] * C for _ in range(R)]
    for (i, j), v in locked_values.items():
        P[i][j] = v
    for i in unlocked_rows:
        for j in range(C):
            P[i][j] = base[i][j]
            if rem[i][j] > 0:
                P[i][j] += flows[cell_edge_index[(i, j)]]

    rows = [sum(r) for r in P]
    cols = [sum(P[i][j] for i in range(R)) for j in range(C)]
    if sum(rows) != total:
        return None
    for i in unlocked_rows:
        if not (floor_wan(row_exact[i]) <= rows[i] <= ceil_wan(row_exact[i])):
            return None
    for j in range(C):
        if not (floor_wan(col_exact[j]) <= cols[j] <= ceil_wan(col_exact[j])):
            return None
    for i in locks:
        if rows[i] != locks[i][0]:
            return None
        for j in range(C):
            if P[i][j] != locks[i][1][j]:
                return None
    return {"P": P, "rows": rows, "cols": cols, "total": total}
