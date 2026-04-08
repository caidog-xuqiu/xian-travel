# 西安智能出行 Agent（v4.1 可演示版）

## 项目简介
本项目是一个面向西安市区核心路线的智能出行后端 Agent。  
当前版本聚焦碑林区、莲湖区、雁塔区，并通过规则约束 + Agent 控制层 + LLM 增强，输出可执行的半日/一日路线建议与可读文案。

当前定位：
- 已具备单 Agent 状态图能力（v2/v3）。
- 已具备 Planning Loop V1（有限动作循环）。
- 已具备真实 API 可选接入与稳定回退能力。
- 已进入 v4.1：区域感知路线优化 + 稳定性压实 + 知识解释增强 + 固定评估收口。

## 核心能力
- 自然语言解析：规则解析 + LLM 解析（失败自动回退）。
- 候选方案生成：`classic_first` / `relaxed_first` / `food_friendly`。
- LLM 方案选优：仅在候选方案内选择，不直接生成路线。
- 知识层轻量增强：候选摘要、选优理由、readable 文案可注入知识标签/短说明。
- 澄清分支：信息不足时先追问，再继续执行。
- 线程恢复：基于 `thread_id` 的 checkpoint 恢复执行。
- 记忆写回：轻量偏好写回与召回。
- 动态搜索：按策略矩阵补搜候选点。
- 高德真实 Web 服务接入 v1（可选）：POI 搜索、地理编码/逆地理、路径规划、输入提示、天气查询。
- 发现前治理：`data_quality` 在 `candidate_discovery` 前执行，先做去重/隔离再发现。
- 多来源候选发现：`candidate_discovery` 已接入 `existing_poi_pipeline + local_extended_corpus`，并在发现层做轻量合并去重。
- 可配置区域发现 v1：新增 `area_registry`，discovery 可按 `area_scope` 在城墙钟鼓楼/小寨文博/大雁塔/曲江夜游/回民街/高新/电视塔会展/浐灞未央间动态发现。
- 区域感知路线优化 v1.1：`area_scope_used / area_priority_order / discovered_area_counts` 已进入候选生成与排序，`classic/relaxed/food` 在跨区域与节奏上差异更明确。
- Skills 能力目录运行时接入：`skills_registry` 已进入主链，节点与 planning action 会记录 `skill_name`、`skill_trace` 与结果摘要。
- Skills 覆盖范围已扩展到：`parse / recall / search / generate / select / render / knowledge_enrichment / evaluation`。
- 可读文案层：输出用户可读的中文行程建议。
- 固定回归评估：`evaluation_harness` 支持固定 case 批跑与 v2/v3/v4_current 对比。
- 稳定性诊断增强：日志可区分 `llm_call_exception`、`planning_action_invalid_after_repair`、`llm_selector_call_exception`、`selector_local_rank_fallback`。
- `v4_current` 已升级为独立执行链（不再仅复用 v3 标签），可用于更真实的 A/B 对比验收。

## 架构分层
1. 硬约束执行层  
负责可行性、预算、营业时间、交通代价、过滤与基础评分。

2. Agent 控制层  
负责状态流转、澄清、发现前治理（data_quality）、动态搜索、候选组织、线程恢复、记忆写回，以及 skill 级调用追踪。

3. LLM 决策增强层  
负责输入理解、planning action 决策、候选方案选优。  
LLM 不直接替代硬规划器。

补充：`knowledge_layer` 当前只服务解释层和选优层（summary/reason/readable），不直接参与硬约束排程。

## 主要接口
- `POST /plan`
- `POST /plan-readable`
- `POST /plan-from-text-readable`
- `POST /plan-with-llm-selection`
- `POST /plan-from-text-with-llm-selection`
- `POST /agent-plan`
- `POST /agent-plan/continue`
- `POST /agent-plan-v2`
- `POST /agent-plan-v3`

## 快速启动
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 测试
```bash
pytest -q tests
```

固定回归评估：
```bash
python scripts/run_eval.py --cases data/eval_cases.json --output-dir eval_results
```
输出文件：
- `eval_results/summary.json`
- `eval_results/details.json`
- `eval_results/compare.json`

评估输出已包含高德真实调用信号：
- `amap_usage_rate`
- `amap_search_hit_rate`
- `amap_route_hit_rate`
- `amap_weather_hit_rate`

## 环境变量
- LLM 解析/选优
  - `LLM_PARSER_ENABLED`
  - `LLM_PROVIDER`
  - `LLM_API_KEY`
  - `LLM_BASE_URL`
  - `LLM_MODEL`
- 地图与天气（可选，当前统一走高德）
  - `AMAP_API_KEY`
  - `AMAP_CITY`（可选，默认西安）
  - `AMAP_WEATHER_CITY`（可选，天气查询 city/adcode，未设置时复用 `AMAP_CITY`）
- Redis（可选）
  - `REDIS_HOST`
  - `REDIS_PORT`
  - `REDIS_DB`
  - `REDIS_PASSWORD`
- 发现层灰度开关（可选）
  - `CANDIDATE_DISCOVERY_ENABLED` (true/false, default true; set false to disable discovery layer)

高德 Key 安全使用说明：
- 只从 `.env` 读取，不写入代码和日志。
- 启用基础格式校验（非法 key 自动降级，不发起真实请求）。
- 高德请求固定到官方 `https://restapi.amap.com/v3/...` 路径，并使用超时保护。
- 调用失败会回退到既有 mock/fallback 主链，`/plan` 与 agent 接口不会因地图调用失败而崩溃。
- 天气能力现在优先高德天气 API；未配置或调用失败时回退 `request.weather`。

高德接入范围（v1）：
- POI 搜索：关键词检索 + nearby 检索（接入 discovery source）
- 地理解析：`geocode_address` / `reverse_geocode`（起点标准化增强）
- 路径规划：walking / driving（taxi/public_transit 继续走轻量代理）
- 输入提示：`input_tips`（用于澄清时地点补全）
- 天气：`weather_query`（失败回退 request.weather）

## 项目结构（核心）
- `app/routes/plan.py`：API 路由入口
- `app/models/schemas.py`：请求/响应模型
- `app/services/agent_graph.py`：Agent v2/v3 主流程
- `app/services/discovery_sources.py`：发现层 source adapters（多来源加载与合并）
- `app/services/area_registry.py`：区域注册与 area_scope 解析
- `app/services/planning_loop.py`：Planning Loop V1
- `app/services/plan_selector.py`：候选生成与差异增强
- `app/services/llm_planner.py`：候选选优与后检查
- `app/services/llm_parser.py`：自然语言参数解析
- `app/services/sqlite_store.py`：thread/memory/log 持久化
- `app/data/pois.json`：本地 mock POI
- `app/data/extended_places.json`：本地扩展候选语料（多来源发现使用）
- `docs/`：架构、状态、路线图文档

## 当前能力边界
- 仍未接入复杂人流量/拥挤度主链。
- 仍未接入复杂节假日策略。
- 仍未做全局最优路径求解（当前是规则约束 + 近似优化）。
- 当前仍为单 Agent，不是 multi-agent。
- 当前未做复杂 RAG/MCP 决策主链。

## 下一阶段方向（v4.1 之后）
当前 v4.1 已可演示，下一阶段重点不再是继续堆小规则，而是继续增强可扩展基础层：

1. 开放候选发现层  
不再局限于固定候选池，先扩展“找点”能力，再交给现有硬规划层排程。
当前进展：已支持“可配置区域发现 v1”，可按 area_scope 做区域优先发现与覆盖统计（非 GIS 引擎）。

2. 知识层 / RAG  
先服务解释层与标签层，再逐步影响规划偏置，不直接替代硬约束引擎。

当前进展：知识层已轻量接入候选摘要、selection_reason/reason_tags 与 readable_output 增强，命中失败会自动回退原模板逻辑。

3. Skills 化  
把解析、搜索、候选、选优、渲染、知识增强、评估等能力显式注册为 skill 目录，已接入运行链路并支持 skill_trace 回放。当前仍是项目内技能目录，不是 MCP 工具调用。

4. 更强路线优化  
在不破坏可行性约束的前提下，后续逐步增强跨簇成本、节奏控制与全局质量。

5. 评估体系 + 数据治理  
建立稳定回归指标与数据质量治理，避免升级过程质量漂移。

说明：MCP 与 multi-agent 属于更后阶段，不在本轮落地范围内。

## Windows PowerShell 固定环境指令
一、进入项目目录  
Set-Location D:\xian-travel-agent

二、固定环境变量  
$Env:PYTHONPATH="D:\xian-travel-agent"  
$py="D:\xian-travel-agent\.venv\Scripts\python.exe"

三、解释器自检  
& $py -c "import sys; print(sys.executable)"

四、运行 AMap 探针  
Set-Location D:\xian-travel-agent  
$Env:PYTHONPATH="D:\xian-travel-agent"  
$py="D:\xian-travel-agent\.venv\Scripts\python.exe"  
& $py scripts\amap_probe.py  
echo $LASTEXITCODE

五、运行全部 pytest  
Set-Location D:\xian-travel-agent  
$Env:PYTHONPATH="D:\xian-travel-agent"  
$py="D:\xian-travel-agent\.venv\Scripts\python.exe"  
& $py -m pytest -q

六、运行指定测试  
Set-Location D:\xian-travel-agent  
$Env:PYTHONPATH="D:\xian-travel-agent"  
$py="D:\xian-travel-agent\.venv\Scripts\python.exe"  
& $py -m pytest -q tests\test_amap_client_debug.py tests\test_evaluation_harness.py

七、查看环境变量  
$Env:AMAP_API_KEY  
$Env:PYTHONPATH  
$Env:HTTP_PROXY  
$Env:HTTPS_PROXY  
$Env:NO_PROXY

八、临时清理代理  
Remove-Item Env:HTTP_PROXY -ErrorAction SilentlyContinue  
Remove-Item Env:HTTPS_PROXY -ErrorAction SilentlyContinue  
Remove-Item Env:NO_PROXY -ErrorAction SilentlyContinue  
Remove-Item Env:REQUESTS_CA_BUNDLE -ErrorAction SilentlyContinue  
Remove-Item Env:CURL_CA_BUNDLE -ErrorAction SilentlyContinue

九、首次初始化时放行 Python 出站  
New-NetFirewallRule -DisplayName "Allow Xian Travel venv Python Outbound" -Direction Outbound -Program "D:\xian-travel-agent\.venv\Scripts\python.exe" -Action Allow  
New-NetFirewallRule -DisplayName "Allow System Python313 Outbound" -Direction Outbound -Program "D:\Program Files\Python313\python.exe" -Action Allow

十、文档里明确写入以下规则  
- 不要在 PowerShell 里写 cd /d  
- 不要默认使用系统 Python  
- 不要在项目目录外直接运行脚本  
- 不要不设 PYTHONPATH 就运行项目脚本  
- 以后统一使用 .venv 的 python.exe



