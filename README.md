# commitgraph — 提交图索引库

纯标准库实现的持久化提交图索引，供代码托管服务计算合并请求的
diff 基准（merge base）与领先/落后计数。持久化只用一个 SQLite 文件。

## 用法

```python
from commitgraph import CommitGraph

with CommitGraph("repo.db") as g:
    g.ingest([(cid, [parent1, parent2], timestamp), ...])
    bases, touched   = g.merge_bases(a, b)      # 全部最佳公共祖先
    yes, touched     = g.is_ancestor(a, b)      # b 沿父边能否到达 a
    (ahead, behind), touched = g.ahead_behind(a, b)
```

- 提交 id 为 40 位十六进制；父列表有序；允许 0 个（根）或 2 个以上
  （合并 / 章鱼合并）父提交。
- 每个查询返回结果和本次触及的不同提交数。
- 查询未知 id 抛 `UnknownCommitError`。
- `ingest` 整批原子：缺父（`MissingParentError`）、批内成环
  （`CycleError`）、与已存提交父列表冲突（`ParentConflictError`）
  都会整批拒绝，库中不留痕迹；父列表相同的重复提交静默忽略。

## 设计

**存储（线性开销）**：`commits(id, gen, ts)` 每提交一行，
`parents(child, ord, parent)` 每条父边一行。不存传递闭包、
不按提交对预计算。`gen` 是拓扑层数（`1 + max(父 gen)`，根为 1），
只在 `ingest` 时按批内拓扑序计算，代价与批大小成正比，
不重算已入库提交。

**查询（与时间戳无关，与历史总长无关）**：时间戳只存不用。
三个查询都基于按 generation 递减弹出的大顶堆遍历：

- 沿父边世代严格递减，因此一个提交首次被弹出时，它所有可能带来
  标记的子提交（世代更大）都已被弹出——弹出瞬间其标记即为最终值，
  计数与 merge-base 判定都精确。
- 双端染色（P1/P2/STALE）：某提交同时被两端到达即标记 STALE。
  当堆中剩余提交全部 STALE 时，未访问部分必然全部属于公共历史，
  遍历立即停止——答案位置以下的历史（无论多长）完全不被触及。
- `is_ancestor` 以 `gen(a)` 为下界剪枝：世代小于等于 `gen(a)` 的
  提交不可能是 a 的后代路径上的点，直接停止展开。
- merge base 的冗余项被 STALE 传播天然排除：结果是另一公共祖先的
  祖先的提交，会在被弹出前先收到 STALE 标记。

触及数只由两端 tip 到答案所在位置之间的图段决定；规模测试
（`tests/test_scale.py`）验证同一顶部结构叠在 1 万与 50 万提交的
主干上时，三种查询的结果与触及数完全一致。

## 测试

```
python3 -m unittest discover -s tests
```

- `tests/test_queries.py`：随机 DAG（普通合并、章鱼合并、
  criss-cross、多根、时间戳打乱/全同），随机分批乱序入库，
  与"全祖先集合"朴素参考实现逐一对照，并重开库验证持久化。
- `tests/test_scale.py`：规模测试（见上）。
- `tests/test_reject.py`：三类拒绝路径，拒绝后库内容逐条一致。
