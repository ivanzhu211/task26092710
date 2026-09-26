# commitgraph — 提交图索引库

只用 Python 标准库实现的提交图索引，持久化使用 `sqlite3`（一个库实例对应一个
SQLite 文件，重开数据仍在）。用于代码托管服务计算合并请求的 diff 基准
（merge base）和领先/落后计数。

## 用法

```python
from commitgraph import Commit, CommitGraph

with CommitGraph("repo.db") as graph:
    graph.ingest([
        Commit("…40位十六进制…", (), 1710000000),      # 根提交
        Commit("…", (parent_id,), 1710000001),         # 普通提交
        Commit("…", (p1, p2, p3), 1710000002),         # 章鱼合并
    ])
    bases, touched = graph.merge_bases(a, b)           # 全部最佳公共祖先
    is_anc, touched = graph.is_ancestor(a, b)          # a 能否从 b 沿父边到达
    (ahead, behind), touched = graph.ahead_behind(a, b)
```

每个查询返回 `(结果, touched)`，`touched` 是本次查询触及的不同提交数。
查询中出现未知 id 时抛出 `UnknownCommitError`。

`ingest` 整批校验、整批写入：缺父（`MissingParentError`）、批内成环
（`CycleError`）、与已存在提交的父列表冲突（`ParentConflictError`）都会
整批拒绝且不留下任何痕迹；已存在且父列表相同的提交静默忽略。

## 设计

- **存储线性**：`commits(id, gen, ts)` 与 `edges(child, parent, ord)` 两张表，
  不存传递闭包、祖先集合或按提交对预计算的结果。
- **世代号（generation number）**：入库时计算 `gen = 1 + max(父代 gen)`，
  只依赖本批与已入库数据，`ingest` 开销与批大小成正比，不重算旧数据。
- **查询与时间戳无关**：时间戳只存不查。查询采用按世代号排序的
  best-first 双向染色（与 Git commit-graph 的 paint-down-to-common 同类），
  前沿全部越过答案位置即停止，因此触及的提交数只取决于两端 tip 到答案
  之间的那段图，与下面压着的历史总长无关（规模测试：同一顶部结构分别
  叠在 1 万 / 50 万提交的主干上，结果与触及数完全一致）。
- **查询不整图读入**：逐提交按需取父列表，不在 SQL 里展开祖先。

## 测试

```sh
python3 -m unittest discover
```

- `tests/test_queries.py`：随机 DAG（含普通合并、章鱼合并、criss-cross、
  多根、时间戳打乱）分批乱序入库，与朴素"全祖先集合"参考实现逐一对照。
- `tests/test_scale.py`：相同顶部结构叠在 1 万 / 50 万提交主干上，
  校验三种查询的结果与触及数完全一致。
- `tests/test_ingest.py`：缺父、批内成环、父列表冲突等拒绝路径，
  每次拒绝后逐行校验库内容与拒绝前一致。
