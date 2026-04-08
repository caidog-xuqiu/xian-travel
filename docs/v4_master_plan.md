# v4 下一阶段总体设计（v4.1 冲刺后）

## 目标定位
本阶段不推翻现有 v3 主链，而是在其上搭建下一阶段扩展框架：
- 保留 v3：单 Agent 状态图、planning loop、动态搜索、SQLite、LLM 解析/选优。
- 引入新分层：开放候选发现层、知识层、skills 化、评估体系、数据治理。
- 本轮重点：在保留主链的前提下，完成区域感知候选优化、稳定性压实、知识解释增强与固定评估收口。

## 主线 1：开放候选发现层
### 目标
从“固定候选池”升级为“开放候选空间”，让系统能在更大范围发现可选点位。

### 边界
- 发现层负责：找点、覆盖度、来源说明。
- 优化层（现有 planner/selector）负责：排程、预算、营业时间、路线可行性。

### 当前落地（已接主链）
- `app/services/candidate_discovery.py` 已接入 `agent_graph`。
- `app/services/discovery_sources.py` 已提供 source adapters。
- `app/services/area_registry.py` 已提供 area 注册、area_scope 解析、place->area 映射。
- 当前链路：`analyze_search_intent -> data_quality -> candidate_discovery -> dynamic_search -> refine_search_results`。
- `dynamic_search` 优先消费 discovery 结果，发现质量弱时回退旧路径。
- `data_quality` 已作为发现前治理护栏，先做去重/隔离，再进入 discovery。
- discovery 已从单来源扩展为多来源：
  - `existing_poi_pipeline`
  - `local_extended_corpus`（本地扩展语料）
- 发现层输出已包含来源覆盖信息（source counts / merge summary）。
- 发现层已接入高德真实搜索来源 `amap_web_search`（失败自动回退本地 source）。
- 发现层输出已包含区域覆盖信息（area_scope_used / discovered_area_counts / area_coverage_summary）。
- 已支持区域范围扩展 v1（可配置 area discovery）：
  - 城墙钟鼓楼
  - 小寨文博
  - 大雁塔
  - 曲江夜游
  - 回民街
  - 高新
  - 电视塔会展
  - 浐灞未央

## 主线 2：知识层 / RAG
### 目标
为景点/商家补充解释与语义标签能力，先服务解释层与标签层。

### 当前落地
- 新增 `app/services/knowledge_layer.py` 骨架。
- 统一入口：`retrieve_place_knowledge(query, context)`。
- 已轻量接入：
  - 候选摘要增强（`PlanSummary.knowledge_tags / knowledge_notes`）
  - 选优理由增强（`selection_reason / reason_tags`）
  - 文案增强（`overview / schedule_text / tips_text`）
- 当前定位仍是“补解释层”，不进入硬规划约束链。

## 主线 3：Skills 化
### 目标
把已有能力显式注册为 skill 目录，形成可枚举、可解释、可扩展能力层。

### 当前落地
- `app/services/skills_registry.py` 已提供能力目录 + runtime 映射。
- 关键节点（parse/recall/search/generate/select/render）与 planning loop action 已写入 `skill_name`。
- knowledge 增强调用点已写入 `knowledge_enrichment_skill`（主链可观测）。
- evaluation_harness 已写入 `evaluation_skill`（评估链可观测）。
- `AgentState.skill_trace` 可回放节点能力调用轨迹。
- 首批注册：parse/recall/search/generate/select/render。

## 主线 4：评估体系
### 目标
建立稳定回归指标与对比流程，避免迭代后质量漂移。

### 当前落地
- 新增 `docs/evaluation_plan.md`
- 新增 `app/services/evaluation_harness.py` 骨架
- 指标覆盖：`parsed_by`、`selected_by`、invalid fallback、candidate diversity、route quality、v3 vs v2

## 主线 5：数据治理
### 目标
为更大规模 POI/商家数据提供去重、可信度、新鲜度、脏数据隔离能力。

### 当前落地
- 新增 `docs/data_governance.md`
- `app/services/data_quality.py` 已接入 discovery 前置路径（最小可用治理）

## 与现有 v3 的连接关系
1. v3 主链继续保留，不替换。
2. `candidate_discovery` 已作为前置节点接入动态搜索链路。
3. `knowledge_layer` 当前先服务解释层，后续再进入规划偏置。
   - 现阶段仅在 summary/reason/readable 轻量生效，命中失败自动回退原模板。
4. `skills_registry` 已进入运行链路，提供显式能力映射与可观测性增强；当前仍不改变硬规划主逻辑。
5. `evaluation_harness` 与 `data_quality` 当前属于基础设施层。
6. 高德真实增强（search/geocode/route/weather/tips）当前作为 v4 的“真实世界增强层”，不替代硬规划主链。

## 分阶段建议
### v4.0（当前）
- 完成脚手架与文档分层。
- 完成发现层最小接入与回退保护。

### v4.1（当前）
- 已完成区域感知候选优化：discovery 区域结果进入候选生成与排序。
- 已压实 selector/planning 回退语义与诊断日志。
- 已增强 knowledge 解释层并接入 summary/reason/readable。
- 已形成固定评估入口（run_eval + summary/details/compare）。

### v4.2
- 知识层接入轻量检索存储（仍不替代硬规划器）。
- 数据治理规则开始作用于候选输入质量。

## 为什么先做脚手架 + 最小接入
- 避免主链继续堆 if/else，降低后续迭代成本。
- 先明确边界并跑通最小闭环，再逐步增强能力。
- 保证可回归、可解释、可回退。


