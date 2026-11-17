# 合流交付说明

本次交付在现有 Warehouse 上新增 MERGING.md 规定的递归模式合流能力，原业务、
接口及全部原测试不变（26 项原回归全部保留并通过）。

## 新增入口

- `Warehouse.merge_schema(base, left, right)`：三个确切整数修订 ID（不跟随
  head）的三方合流，返回可交给 SDK1 `Schema` 的独立 JSON 模式；纯计算，
  不改变任何持久状态。
- `Warehouse.create_merged_read(name, base, left, right, batch_ids,
  before_commit=None)`：同一事务内共同提交合并修订、名称 head 与 pending
  读取任务，返回 `{"revision": ..., "task_id": ...}`；任务固定新修订与给定
  有序批次列表，旧任务绑定不变。name/batch_ids 校验沿用 register/create_read
  的异常类型；缺失引用抛原 NotFound；before_commit 在业务状态写入后、提交前
  调用，观察者异常原样传播并整次回滚，不留半个发布。
- `warehouse.MergeConflict`：继承原 `Conflict`，提供非空字符串
  `definition`（具名定义全名，根问题为 `"$"`）、`path`、`reason`；相同输入
  稳定报告同一冲突。

## 实现位置

- `warehouse/merge.py`：合流纯计算。定义按全名对应且只合一次；字段身份按
  base 原名（同名优先，再看别名）在所属 record 内建立；普通属性三方规则
  （不存在是独立状态）；union 先按分支身份序列整体合流成员与顺序、再合流
  存续分支内容，删除分支对内容修改即冲突；默认值先按来源模式解释为含分支
  身份与字段身份的有限值再比较，选出后按合并字段名与联合顺序重编码（递归
  记录与数组同样保义），无法保留、别名歧义或任何 SDK1 非法结果均报冲突；
  未被输出根引用的定义仍检查但不发射。
- `warehouse/__init__.py`：两个入口与 `MergeConflict` 导出。
- `warehouse/errors.py`：`MergeConflict`。
- `tests/test_merge.py`：独立合流核账与联合回归（共享递归定义、改名与类型
  修改、嵌套默认值、新增/删除字段、枚举与别名属性、不可达定义仍核账、九类
  冲突定位与稳定性、无半个发布、读取失败保留修订与任务、确切修订不跟随
  head、参数校验）。

## 核验方式

- `python3.11 -B merge_examples.py verify`：fixtures + 全部原回归 + 合流
  核账 + 真实数据库关闭重开后的固定合并读取（`.pointwise-runtime.json` 的
  verify 已接到此入口）。
- `python3.11 -B merge_examples.py demo`：真实重开展示（demo 入口）。
- `python3.11 -B -m unittest discover -s tests -v`：38 项测试（含新增
  test_merge.py）。

合并读取仍走原 run/result：惰性分支、首错、无成功前缀、成果回执语义不变；
不承诺任意 batch_ids 都能在合并模式下成功读取，读取失败不撤销已合法创建
的修订与任务。允许全量解析、合并与重新验证；未做并发、SDK 迁移或增量性能。
