# 架构说明（Agent v3 + v4.1 可演示版）

## 1. 总体结构
系统由三层组成：
- 硬约束执行层：保证路线可行性与稳定性。
- Agent 控制层：负责状态流转、澄清、搜索、选优编排。
- LLM 决策增强层：负责解析、动作决策、候选选优。

## 2. AgentState 的作用
`AgentState` 是控制层共享状态对象，承载：
- 输入与解析：`user_input`, `parsed_request`, `parsed_by`
- 澄清控制：`clarification_needed`, `clarification_question`, `clarification_answer`
- 发现与搜索：
  - `primary_strategies`, `secondary_strategies`, `search_strategy`
  - `area_scope_used`, `discovered_area_counts`, `area_coverage_summary`
  - `discovered_pois`, `discovered_pois_count`
  - `discovery_sources`, `discovery_notes`, `discovery_coverage_summary`
  - `search_results`, `search_results_count`
- 候选与选优：`candidate_plans`, `alternative_plans_summary`, `selected_plan`, `selection_reason`, `reason_tags`
- 知识增强：`retrieved_knowledge_count`, `knowledge_source_tags`, `knowledge_usage_notes`
- 执行追踪：`current_step`, `current_node`, `debug_logs`
- Skills 追踪：`active_skill`, `skill_trace`, `last_skill_result_summary`
- 持久化关联：`thread_id`, `memory_write_payload`

## 3. 关键模块关系
- `agent_graph.py`：主编排层，组织节点执行与分支。
- `candidate_discovery.py`：开放候选发现层（找点与覆盖说明）。
- `area_registry.py`：区域注册、scope 解析、place->area 映射。
- `planning_loop.py`：v3 动作循环（SEARCH / GENERATE_CANDIDATES / REVISE / FINISH）。
- `plan_selector.py`：候选方案生成与差异增强。
- `llm_planner.py`：候选选优与本地约束回退。
- `knowledge_layer.py`：轻量知识检索（仅增强解释与选优理由）。
- `skills_registry.py`：能力目录与 runtime skill 映射（node/action -> skill）。
- `amap_client.py`：高德 Web 能力统一入口（search/geocode/route/tips/weather）。

## 4. 当前主链（含发现层与区域感知候选）
- `analyze_search_intent`
- `candidate_discovery`
- `dynamic_search`
- `refine_search_results`
- `generate_candidates`
- `select_plan`
- `render_output`

说明：
- `candidate_discovery` 负责找点，不负责排路线。
- `candidate_discovery` 会按 `area_scope` 优先发现区域，并输出区域覆盖统计。
- `generate_candidates` 会消费 `area_scope_used / area_priority_order / discovered_area_counts` 做区域感知候选排序。
- `PlanSummary` 已包含 `is_cross_area / cross_area_count / area_transition_summary / area_bias_note`，供选优层与评估层使用。
- `dynamic_search` 优先消费 discovery 结果。
- 若 discovery 为空或质量弱，自动回退旧搜索路径。
- `knowledge_layer` 在 summary / selection / readable 三处轻量调用，命中失败时回退原逻辑。
- `skills_registry` 已在关键节点与 planning loop 动作中写入 `skill_name`，可通过 `skill_trace` 和 `debug_logs` 回放。
- 高德能力在主链中按“优先真实、失败回退”策略运行：
  - discovery：`amap_web_search` 与本地 source 并存
  - route：优先 AMap route，失败回退本地估算
  - weather：优先 AMap weather，失败回退 `request.weather`
  - clarify：地点模糊时可用 AMap input tips 生成更真实补全提示
- `knowledge_layer` 调用点已显式记录 `knowledge_enrichment_skill`（hit/miss 均可观测）。
- `evaluation_harness` 已显式记录 `evaluation_skill`（evaluation chain trace）。

## 5. LLM 参与与不参与边界
LLM 参与：
- 自然语言解析（`llm_parser`）
- planning loop 动作决策（`planning_loop`）
- 候选方案选优（`llm_planner`）

LLM 不参与：
- POI 过滤与基础质量约束
- 路线可行性、预算、营业时间、交通代价
- 直接生成最终 itinerary

知识层不参与：
- 直接规划路线
- 覆盖评分主链
- 直接替代硬约束决策

Skills 层当前定位：
- 当前是项目内能力目录与运行映射，不是 MCP 工具编排。
- 负责让 Agent 显式知道“正在调用哪个能力”，不改变硬规划职责边界。
- 当前已覆盖：parse / recall / search / generate / select / render / knowledge_enrichment / evaluation。

## 6. SQLite 使用位置
通过 `sqlite_store.py` 落库最小持久化：
- `thread_checkpoints`：节点状态快照
- `user_memory`：轻量偏好记忆
- `agent_logs`：结构化执行日志

对应能力：
- thread 恢复执行（含澄清后 continue）
- 记忆召回与写回
- 主链诊断与回归分析

## 7. v4.1 稳定性约定
- Planning Loop 关键回退标签：`llm_call_exception`、`planning_action_invalid_after_repair`。
- Selector 关键回退标签：`llm_selector_call_exception`、`selector_local_rank_fallback`。
- 回退不是异常退出，而是可解释降级，确保主链可用。

## 8. v3/v4 执行链说明
- `run_agent_v3` 与 `run_agent_v4_current` 现为两条独立执行链。
- 两者共享硬规划底座与公共节点实现，但评估入口不再把 `v4_current` 当作 `v3` 别名。
- `evaluation_harness` 会按 endpoint 显式路由，支持更真实的 v3 vs v4_current A/B 对比。


