# 评估体系计划（Evaluation Plan）

## 目标
建立可复用的回归评估框架，支持每次升级后做稳定对比，而不是仅靠主观观察。

## 评估对象
- 解析层（rule / llm）
- planning loop 动作层
- 候选生成与差异层
- 方案选优层
- fallback 稳定性
- v3 vs v2 端到端结果差异

## 核心指标
1. 解析指标
- `parsed_by_llm_rate`
- `parsed_by_rule_rate`

2. 选优指标
- `selected_by_llm_rate`
- `selected_by_fallback_rate`

3. 稳定性指标
- invalid action fallback 次数
- selector fallback 次数
- 平均 planning step 数

4. 候选质量指标
- `candidate_diversity_score`
- `candidate_quality_signal`
- `amap_usage_rate`
- `amap_search_hit_rate`
- `amap_route_hit_rate`
- `amap_weather_hit_rate`
- `discovery_source_coverage`
- `area_coverage_signal`
- `cross_area_signal`
- 平均候选数
- 低质量候选占比（空方案/单站弱方案）
- 区域跨度与区域适配命中（cross_area_signal / area_fit_hit_rate）

5. 路线质量指标（约束命中）
- need_meal 命中率
- 夜游意图命中率
- 轻松意图命中率（少跨簇、步行负担）
- nearby 起点偏好命中率

6. 对比指标
- `v3_better_than_v2_count`
- `v3_equal_v2_count`
- `v3_worse_than_v2_count`

## 评估流程（最小版）
1. 固定 case 集（建议 10~20 条）
2. 分别执行 v2 与 v3
3. 收集结构化结果（json）
4. 调用 `evaluation_harness` 聚合指标
5. 输出结论与问题清单

## 当前实现状态
- 已有：
  - 固定 case 集：`data/eval_cases.json`
  - 批量执行入口：`run_eval_for_endpoint`
  - 聚合与对比：`summarize_eval_results` / `compare_eval_results`
  - 可执行脚本：`scripts/run_eval.py`
- 当前支持端点标签：
  - `v2`（`/plan-from-text-with-llm-selection` 对应链路）
  - `v3`（`/agent-plan-v3` 对应链路）
  - `v4_current`（当前版本独立执行链，专用于与 v3 做更真实 A/B 对比）

## 当前输出
- `summary` 指标：
  - `total_cases`
  - `parsed_by_llm_rate`
  - `selected_by_llm_rate`
  - `clarification_rate`
  - `invalid_fallback_total`
  - `candidate_avg_count`
  - `candidate_diversity_score`
  - `candidate_quality_signal`
  - `amap_usage_rate`
  - `amap_search_hit_rate`
  - `amap_route_hit_rate`
  - `amap_weather_hit_rate`
  - `discovery_source_coverage`
  - `area_coverage_signal`
  - `cross_area_signal`
  - `area_fit_hit_rate`
  - `route_quality_hit_rate`
  - `meal_intent_hit_rate`
  - `night_intent_hit_rate`
  - `nearby_intent_hit_rate`
  - `relax_intent_hit_rate`
- `details` 明细（每条 case）：
  - `case_id`, `text`, `parsed_by`, `selected_by`, `clarification_needed`
  - `invalid_action_fallback`, `candidate_count`, `candidate_diversity_score`
  - `candidate_quality_signal`
  - `amap_called`, `amap_sources_used`, `route_source`, `weather_source`, `amap_fallback_reason`
  - `discovery_source_coverage`
  - `area_coverage_signal`
  - `cross_area_count`, `area_transition_summary`, `area_fit_hit`
  - `area_scope_used`, `discovered_area_counts`
  - `route_quality_hit`, `short_result_label`
- `compare` 对比：
  - `parsed_by_llm_rate_delta`
  - `selected_by_llm_rate_delta`
  - `candidate_diversity_delta`
  - `candidate_quality_signal_delta`
  - `amap_usage_rate_delta`
  - `amap_search_hit_rate_delta`
  - `amap_route_hit_rate_delta`
  - `amap_weather_hit_rate_delta`
  - `discovery_source_coverage_delta`
  - `area_coverage_signal_delta`
  - `cross_area_signal_delta`
  - `area_fit_hit_rate_delta`
  - `route_quality_hit_rate_delta`
  - `invalid_fallback_total_delta`

## 使用建议
- 每次升级后至少跑一次固定回归集。
- 报告必须同时包含“过程指标”和“结果质量指标”。
- 不要只看 `parsed_by/selected_by`，要看最终 itinerary 是否真的更优。

## 版本验收入口（当前已可用）
- 固定 case：`data/eval_cases.json`
- 执行脚本：`python scripts/run_eval.py --cases data/eval_cases.json --output-dir eval_results`
- 输出：`eval_results/summary.json`、`eval_results/details.json`、`eval_results/compare.json`


