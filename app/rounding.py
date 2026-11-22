"""万元取整核算。

规则:
* 每个单元格可向下或向上取整到万元；金额恰为 10000 的整数倍时取整唯一。
* 每行/每列小计同样可向下或向上取整到万元。
* 总计按四舍五入到万元（.5 向上）。
* 公示表必须横竖加平：行小计 = 该行单元格之和，列小计 = 该列单元格之和，
  总计 = 行小计之和 = 列小计之和。
* 已锁定院系行的公示单元格与小计保持锁定时的原值。
* 更正时若给出原公示表，则在全部合法解中取“院系 × 科目”交叉格改动数
  最少的一张（行/列小计与总计不计入格数）。

用带下界的可行流求满足全部约束的解（不是逐格独立四舍五入）；在可行流
基础上用负环消去求改动格数最少的最小费用循环流。
"""
from collections import deque

UNIT = 10000

__all__ = ["UNIT", "solve_display", "floor_wan", "ceil_wan", "round_wan",
           "count_cell_changes"]


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


class _CostEdge:
    __slots__ = ("to", "rev", "cap", "cost")

    def __init__(self, to, rev, cap, cost):
        self.to = to
        self.rev = rev
        self.cap = cap
        self.cost = cost


class _CostGraph:
    """残量网络；一对互为反向的弧，用于最小费用循环流。"""

    def __init__(self, n):
        self.n = n
        self.graph = [[] for _ in range(n)]

    def add_pair(self, u, v, cap_uv, cap_vu, cost_uv, cost_vu):
        self.graph[u].append(_CostEdge(v, len(self.graph[v]), cap_uv, cost_uv))
        self.graph[v].append(_CostEdge(u, len(self.graph[u]) - 1, cap_vu, cost_vu))
        return u, len(self.graph[u]) - 1

    def _find_negative_cycle(self):
        """返回一条负环的边列表 [(u, edge_index), ...]；无负环返回 None。

        以“虚拟源点向所有点连 0 费用边”等价于 dist 全 0 初始化，再用
        Bellman–Ford 做 n 轮松弛：第 n 轮仍能松弛说明存在负环。
        """
        n = self.n
        dist = [0] * n
        pre_node = [-1] * n
        pre_edge = [-1] * n
        last = -1
        for _ in range(n):
            last = -1
            for u in range(n):
                du = dist[u]
                for idx, e in enumerate(self.graph[u]):
                    if e.cap > 0 and dist[e.to] > du + e.cost:
                        dist[e.to] = du + e.cost
                        pre_node[e.to] = u
                        pre_edge[e.to] = idx
                        last = e.to
            if last == -1:
                return None
        # last 落在某个负环上；沿前驱回退 n 步必然仍在环内
        v = last
        for _ in range(n):
            v = pre_node[v]
        cycle = []
        cur = v
        while True:
            cycle.append((pre_node[cur], pre_edge[cur]))
            cur = pre_node[cur]
            if cur == v:
                break
        return cycle

    def cancel_negative_cycles(self):
        """反复沿负环增广，直到残量网络无负环（即当前流费用最小）。"""
        while True:
            cycle = self._find_negative_cycle()
            if cycle is None:
                return
            add = min(self.graph[u][idx].cap for u, idx in cycle)
            for u, idx in cycle:
                e = self.graph[u][idx]
                e.cap -= add
                self.graph[e.to][e.rev].cap += add


def _min_cost_circulation(node_count, edges):
    """带下界的最小费用循环流。

    edges: (u, v, lower, upper, cost)。先用可行流求一组可行解，再沿残量
    网络的负环增广到费用最小。返回每条边的流量；不可行返回 None。
    """
    plain = [(u, v, lo, hi) for u, v, lo, hi, _cost in edges]
    flows = _feasible_circulation(node_count, plain)
    if flows is None:
        return None
    g = _CostGraph(node_count)
    refs = []
    for (u, v, lo, hi, cost), f in zip(edges, flows):
        # 正向残量 (u->v) 费用 cost，反向残量 (v->u) 费用 -cost
        refs.append((u, g.add_pair(u, v, hi - f, f - lo, cost, -cost)[1], hi))
    g.cancel_negative_cycles()
    result = []
    for u, idx, hi in refs:
        result.append(hi - g.graph[u][idx].cap)
    return result


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


def count_cell_changes(P, previous):
    """数“院系 × 科目”交叉格的改动数，不计行/列小计与总计。

    P: 新公示矩阵（嵌套列表）。previous: {(row, col): 万元} 或其嵌套列表。
    缺失的对侧格视为不同（计一处改动）。
    """
    if previous is None:
        return 0
    changes = 0
    for i, row in enumerate(P):
        for j, v in enumerate(row):
            if isinstance(previous, dict):
                old = previous.get((i, j))
            else:
                try:
                    old = previous[i][j]
                except (IndexError, KeyError, TypeError):
                    old = None
            if old != v:
                changes += 1
    return changes


def _is_nonneg_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def solve_display(cells, locks=None, previous=None):
    """求一张满足取整与横竖加平的公示表。

    cells: R 行 C 列的非负整数元。
    locks: {row_index: (subtotal_wan, [cell_wan, ...])}，锁定原值。
    previous: 可选，改动前公示表的“院系 × 科目”值（嵌套列表或
        {(row_index, col_index): 万元}）。给出时在全部合法解中取交叉格
        改动数最少的一张；只数院系与科目交叉处，不数小计/总计。
        并列时结果确定（由算法固定选取，不引入随机）。
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

    prev_map = {}
    if previous is not None:
        if isinstance(previous, dict):
            prev_map = {(i, j): v for (i, j), v in previous.items()}
        else:
            try:
                prev_map = {(i, j): previous[i][j]
                            for i in range(len(previous)) for j in range(len(previous[i]))}
            except (TypeError, IndexError):
                prev_map = {}

    # 网络: 源 -> 行 -> 列 -> 汇，另加 汇->源 的精确“总向上取整格数”边。
    # 给出 previous 时，每格的“上/下取整”按是否与改动前一致赋 0/1 费用，
    # 求最小费用循环流即得交叉格改动数最少的一张表。
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
        edges.append((S, row_node[i], lo, hi, 0))
    cell_edge_index = {}
    for i in unlocked_rows:
        for j in range(C):
            if rem[i][j] == 0:
                continue
            cell_edge_index[(i, j)] = len(edges)
            if previous is None:
                cost = 0
            else:
                old = prev_map.get((i, j))
                # 流量 1 表示向上取整。选上/下取整相对改动前是否变化，
                # 折算为单条弧的单位费用（常数项不影响最优化）。
                cost_down = 0 if old == base[i][j] else 1
                cost_up = 0 if old == base[i][j] + 1 else 1
                cost = cost_up - cost_down
            edges.append((row_node[i], col_base + j, 0, 1, cost))
    for j in range(C):
        fsum = sum(base[i][j] for i in unlocked_rows)
        lo = col_lo[j] - fsum
        hi = col_hi[j] - fsum
        if hi < 0 or lo > hi:
            return None
        edges.append((col_base + j, T, max(0, lo), hi, 0))
    edges.append((T, S, target_ups, target_ups, 0))

    flows = _min_cost_circulation(node_count, edges)
    if flows is None:
        return None

    P = [[0] * C for _ in range(R)]
    for (i, j), v in locked_values.items():
        P[i][j] = v
    for i in unlocked_rows:
        for j in range(C):
            P[i][j] = base[i][j]
    for (i, j), idx in cell_edge_index.items():
        if flows[idx]:
            P[i][j] += 1

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
