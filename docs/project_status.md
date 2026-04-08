# 项目现状总表（2026-04）

## 已完成
- FastAPI 主服务与结构化规划接口可用
- 自然语言入口可用（rule + llm，含回退）
- 候选方案生成与差异增强可用
- LLM 方案选优可用，含后置约束复核
- Agent v2/v3 状态图可用
- 澄清分支与 `/agent-plan/continue` 可用
- Planning Loop V1 可用（动作集合固定）
- 动态搜索与策略矩阵已接入
- SQLite 持久化可用（checkpoint / memory / logs）
- readable 文案层可用

## 进行中
- 真实 API 下 selector 稳定性持续回归
- planning loop 动作稳定性持续回归（减少 invalid fallback）
- 候选差异与最终结果差异的一致性优化

## 下一步建议
- 强化复杂输入解析稳定性（混合意图、噪声文本）
- 引入节假日与人流量策略（先规则化，再外部数据）
- 增强回归基线与自动化报告（固定样本集 + 指标对比）

## 不做 / 暂缓
- 不做前端
- 不做多智能体（multi-agent）
- 不做全链路 RAG / MCP 决策改造
- 不做复杂全局最优路径算法（当前阶段）

## 技术债
- 仓库存在大量 `pytest-cache-files-*` 目录，影响全量 pytest 收集
- 部分测试数据包含编码历史问题（已通过 Unicode 输入规避）
- 局部枚举字段在序列化时有 warning，需要后续清理
- 仍有个别真实 API 场景网络抖动导致 fallback
