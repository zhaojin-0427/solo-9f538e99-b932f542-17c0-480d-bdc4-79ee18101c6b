"""万元取整核算。

规则:
* 每个单元格可向下或向上取整到万元；金额恰为 10000 的整数倍时取整唯一。
* 每行/每列小计同样可向下或向下取整到万元。
* 总计按四舍五入到万元（.5 向上）。
* 公示表必须横竖加平：行小计 = 该行单元格之和，列小计 = 该列单元格之和，
  总计 = 行小计之和 = 列小计之和。
* 已锁定院系行的公示单元格与小计保持锁定时的原值。

用带下界的可行流求一个满足全部约束的解（不是逐格独立四舍五入）。
更正场景下用最小费用可行流，在全部合法方案中选择与更正前公示表相比
“院系 × 科目”交叉格改动最少的方案（行/列小计与总计不计入格数）。
"""
from collections import deque

UNIT = 10000

__all__ = ["UNIT", "solve_display", "solve_min_change",
           "floor_wan", "ceil_wan", "round_wan"]


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
    for (u, idx), (_, _, lo, hi) in zip(refs, edges):
        e = din.graph[u][idx]
        flows.append(lo + (e.initial - e.cap))
    return flows


class _CostEdge:
    __slots__ = ("to", "rev", "cap", "cost", "initial")

    def __init__(self, to, rev, cap, cost):
        self.to = to
        self.rev = rev
        self.cap = cap
        self.cost = cost
        self.initial = cap


def _min_cost_circulation(node_count, edges):
    """带下界的最小费用可行环流。

    edges: (u, v, lo, hi, cost)，cost >= 0。
    与 ``_feasible_circulation`` 逐行对应，仅把 Dinic 最大流换成 SPFA
    最短增广路：每条业务边正向残量容量 hi-lo、反向边初始容量 0，下界只
    计入节点供需；超级源汇补平供需。最短增广路可处理增广产生的负费反向边，
    故在所有可行环流中取费用最小者。图很小。
    返回每条业务边的实际流量；不可行返回 None。
    """
    n = node_count + 2
    SS, TT = node_count, node_count + 1
    graph = [[] for _ in range(n)]
    demand = [0] * node_count
    refs = []

    def add_edge(u, v, cap, cost):
        if cap < 0:
            raise ValueError("negative capacity")
        forward = _CostEdge(v, len(graph[v]), cap, cost)
        reverse = _CostEdge(u, len(graph[u]), 0, -cost)
        graph[u].append(forward)
        graph[v].append(reverse)
        return u, len(graph[u]) - 1

    for u, v, lo, hi, cost in edges:
        if lo > hi or lo < 0 or cost < 0:
            return None
        _u, idx = add_edge(u, v, hi - lo, cost)
        refs.append((u, idx, lo))
        demand[v] += lo
        demand[u] -= lo

    need = 0
    for v in range(node_count):
        if demand[v] > 0:
            add_edge(SS, v, demand[v], 0)
            need += demand[v]
        elif demand[v] < 0:
            add_edge(v, TT, -demand[v], 0)

    def spfa():
        inf = 10 ** 30
        dist = [inf] * n
        prev_node = [-1] * n
        prev_edge = [-1] * n
        in_queue = [False] * n
        dist[SS] = 0
        queue = deque([SS])
        in_queue[SS] = True
        while queue:
            u = queue.popleft()
            in_queue[u] = False
            for idx, e in enumerate(graph[u]):
                if e.cap > 0 and dist[e.to] > dist[u] + e.cost:
                    dist[e.to] = dist[u] + e.cost
                    prev_node[e.to] = u
                    prev_edge[e.to] = idx
                    if not in_queue[e.to]:
                        queue.append(e.to)
                        in_queue[e.to] = True
        if dist[TT] == inf:
            return None
        return prev_node, prev_edge

    pushed_total = 0
    while pushed_total < need:
        found = spfa()
        if found is None:
            return None
        prev_node, prev_edge = found
        augment = need - pushed_total
        v = TT
        while v != SS:
            augment = min(augment, graph[prev_node[v]][prev_edge[v]].cap)
            v = prev_node[v]
        v = TT
        while v != SS:
            e = graph[prev_node[v]][prev_edge[v]]
            e.cap -= augment
            graph[v][e.rev].cap += augment
            v = prev_node[v]
        pushed_total += augment

    flows = []
    for u, idx, lo in refs:
        e = graph[u][idx]
        # 与 Dinic 版一致：实际流量 = 下界 lo + 残量正向边已用 (initial - cap)
        flows.append(lo + e.initial - e.cap)
    return flows


def _is_nonneg_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _build_context(cells, locks):
    """校验输入并构造两个求解器共用的取整约束；输入非法返回 None。"""
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

    return {
        "R": R, "C": C, "base": base, "rem": rem,
        "row_exact": row_exact, "col_exact": col_exact, "total": total,
        "locks": locks, "locked_values": locked_values,
        "locked_subtotal": locked_subtotal, "locked_contribution": locked_contribution,
        "unlocked_rows": unlocked_rows, "row_lo": row_lo, "row_hi": row_hi,
        "col_lo": col_lo, "col_hi": col_hi, "target_ups": target_ups,
    }


def _all_locked_solution(ctx):
    """全部行均锁定时的唯一候选解校验；不可行返回 None。"""
    R, C = ctx["R"], ctx["C"]
    if ctx["unlocked_rows"]:
        return None
    locks = ctx["locks"]
    P = [[ctx["locked_values"][(i, j)] for j in range(C)] for i in range(R)]
    rows = [sum(r) for r in P]
    cols = [sum(P[i][j] for i in range(R)) for j in range(C)]
    if sum(rows) != ctx["total"]:
        return None
    for j in range(C):
        if not (floor_wan(ctx["col_exact"][j]) <= cols[j] <= ceil_wan(ctx["col_exact"][j])):
            return None
    for i in locks:
        if rows[i] != locks[i][0] or P[i] != list(locks[i][1]):
            return None
    return {"P": P, "rows": rows, "cols": cols, "total": ctx["total"]}


def _build_edges(ctx):
    """构造源→行→列→汇 + 汇→源精确边的下界网络。

    返回 (node_count, edges, cell_edge_index)；edges 为 (u, v, lo, hi)。
    """
    C = ctx["C"]
    unlocked_rows = ctx["unlocked_rows"]
    S = 0
    row_node = {i: 1 + k for k, i in enumerate(unlocked_rows)}
    col_base = 1 + len(unlocked_rows)
    T = col_base + C
    node_count = T + 1
    edges = []
    for i in unlocked_rows:
        lo = max(0, ctx["row_lo"][i])
        hi = ctx["row_hi"][i]
        if hi < 0 or lo > hi:
            return None
        edges.append((S, row_node[i], lo, hi))
    cell_edge_index = {}
    for i in unlocked_rows:
        for j in range(C):
            if ctx["rem"][i][j] == 0:
                continue
            cell_edge_index[(i, j)] = len(edges)
            edges.append((row_node[i], col_base + j, 0, 1))
    for j in range(C):
        fsum = sum(ctx["base"][i][j] for i in unlocked_rows)
        lo = ctx["col_lo"][j] - fsum
        hi = ctx["col_hi"][j] - fsum
        if hi < 0 or lo > hi:
            return None
        edges.append((col_base + j, T, max(0, lo), hi))
    edges.append((T, S, ctx["target_ups"], ctx["target_ups"]))
    return node_count, edges, cell_edge_index


def _assemble(ctx, flows, cell_edge_index):
    """按边流量拼出公示矩阵并做最终独立校验；不合法返回 None。"""
    R, C = ctx["R"], ctx["C"]
    P = [[0] * C for _ in range(R)]
    for (i, j), v in ctx["locked_values"].items():
        P[i][j] = v
    for i in ctx["unlocked_rows"]:
        for j in range(C):
            P[i][j] = ctx["base"][i][j]
            if ctx["rem"][i][j] > 0:
                P[i][j] += flows[cell_edge_index[(i, j)]]

    rows = [sum(r) for r in P]
    cols = [sum(P[i][j] for i in range(R)) for j in range(C)]
    if sum(rows) != ctx["total"]:
        return None
    for i in ctx["unlocked_rows"]:
        if not (floor_wan(ctx["row_exact"][i]) <= rows[i] <= ceil_wan(ctx["row_exact"][i])):
            return None
    for j in range(C):
        if not (floor_wan(ctx["col_exact"][j]) <= cols[j] <= ceil_wan(ctx["col_exact"][j])):
            return None
    for i in ctx["locks"]:
        if rows[i] != ctx["locks"][i][0]:
            return None
        for j in range(C):
            if P[i][j] != ctx["locks"][i][1][j]:
                return None
    return {"P": P, "rows": rows, "cols": cols, "total": ctx["total"]}


def solve_display(cells, locks=None):
    """求一张满足取整与横竖加平的公示表。

    cells: R 行 C 列的非负整数元。
    locks: {row_index: (subtotal_wan, [cell_wan, ...])}，锁定原值。
    返回 dict(P, rows, cols, total)；无解返回 None。
    """
    ctx = _build_context(cells, locks)
    if ctx is None:
        return None
    if not ctx["unlocked_rows"]:
        return _all_locked_solution(ctx)

    built = _build_edges(ctx)
    if built is None:
        return None
    node_count, edges, cell_edge_index = built
    flows = _feasible_circulation(node_count, edges)
    if flows is None:
        return None
    return _assemble(ctx, flows, cell_edge_index)


def _normalize_previous(ctx, previous):
    """更正前公示矩阵；形状/类型非法时按“每格都不同”处理（退化为任一合法解）。"""
    R, C = ctx["R"], ctx["C"]
    sentinel = -1
    if not isinstance(previous, (list, tuple)) or len(previous) != R:
        return [[sentinel] * C for _ in range(R)]
    matrix = []
    for row in previous:
        if not isinstance(row, (list, tuple)) or len(row) != C:
            return [[sentinel] * C for _ in range(R)]
        out = []
        for v in row:
            out.append(v if _is_nonneg_int(v) else sentinel)
        matrix.append(out)
    return matrix


def solve_min_change(cells, locks=None, previous=None):
    """在全部合法公示表中求与更正前公示表改动格数最少的方案。

    格数只统计院系 × 科目交叉格（行/列小计与总计由交叉格派生，不另计）。
    返回 dict(P, rows, cols, total, changes)；无解返回 None。

    网络与 ``solve_display`` 完全同构（源→行→列→汇 + 汇→源精确流量边），
    仅给每条交叉格边赋费用：取上相对取下的“改动倾向”。整体 +1 使边费非负，
    而总向上取整格数对所有合法方案恒定，故不改变最优解，最小费用环流即
    与更正前公示表交叉格改动最少的解。最终 changes 以结果矩阵复算为准。
    """
    ctx = _build_context(cells, locks)
    if ctx is None:
        return None
    prev = _normalize_previous(ctx, previous)
    if not ctx["unlocked_rows"]:
        sol = _all_locked_solution(ctx)
        if sol is None:
            return None
        sol["changes"] = sum(
            1 for i in range(ctx["R"]) for j in range(ctx["C"])
            if sol["P"][i][j] != prev[i][j])
        return sol

    built = _build_edges(ctx)
    if built is None:
        return None
    node_count, edges, cell_edge_index = built
    edge_to_cell = {eidx: cell for cell, eidx in cell_edge_index.items()}

    cost_edges = []
    for idx, (u, v, lo, hi) in enumerate(edges):
        cost = 0
        if idx in edge_to_cell:
            i, j = edge_to_cell[idx]
            down_val = ctx["base"][i][j]
            up_val = down_val + 1
            # 取下/取上相对更正前公示值是否改动；整体 +1 保证边费非负且不改变最优解
            cost = 1 + int(up_val != prev[i][j]) - int(down_val != prev[i][j])
        cost_edges.append((u, v, lo, hi, cost))

    flows = _min_cost_circulation(node_count, cost_edges)
    if flows is None:
        return None
    sol = _assemble(ctx, flows, cell_edge_index)
    if sol is None:
        return None
    sol["changes"] = sum(
        1 for i in range(ctx["R"]) for j in range(ctx["C"])
        if sol["P"][i][j] != prev[i][j])
    return sol
