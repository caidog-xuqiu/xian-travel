const { useEffect, useMemo, useRef, useState } = React;

const START_MESSAGES = [
  {
    id: `agent-${Date.now()}`,
    role: "agent",
    text: "你好，我是你的西安行程助手。直接告诉我你的需求，我会自动规划路线；如果信息不够，我会继续追问。",
  },
];

const MOCK_PLAN = {
  selectedBy: "mock",
  title: "钟楼周边轻松半日路线（示例）",
  totalDurationMinutes: 96,
  crossAreaCount: 0,
  stops: [
    {
      time_slot: "11:30-12:10",
      type: "sight",
      name: "钟楼",
      district_cluster: "城墙钟鼓楼簇",
      transport_from_prev: "从起点前往（步行 约10分钟）",
      reason: "地标打卡，步行负担低",
      estimated_duration_minutes: 10,
    },
    {
      time_slot: "12:20-13:20",
      type: "restaurant",
      name: "老李家面(钟楼总店)",
      district_cluster: "城墙钟鼓楼簇",
      transport_from_prev: "步行 约8分钟",
      reason: "顺路用餐，减少绕行",
      estimated_duration_minutes: 8,
    },
  ],
  knowledgeUsedCount: 2,
  knowledgeIds: ["k_parents_relaxed", "k_need_meal_anchor"],
  knowledgeBias: {
    prefer_single_cluster: true,
    prefer_low_walk: true,
    prefer_meal_experience: true,
  },
  explanationBasis: ["陪父母优先低强度", "需要用餐时先保留餐饮节点"],
  totalScore: 8.2,
  scoreBreakdown: {
    total_score: 8.2,
    constraint_score: 2.8,
    plan_quality_score: 2.0,
    user_feedback_score: 3.4,
  },
  retrievedCaseCount: 1,
  retrievedCaseIds: [1],
  retrievedCaseSummaries: [{ case_id: 1, query: "陪父母半天，中午吃饭", score: 8.1, selected_plan: "relaxed_first" }],
  caseBias: { prefer_low_walk: true, prefer_meal_experience: true },
  caseMemoryId: 1,
  storedToCaseMemory: true,
  storedReason: "high_score_and_constraints_met",
  routeSource: "mock",
  weatherSource: "mock",
  searchMode: "llm_planned",
  searchPlanSummary: "先找公园类地点，再围绕候选找烧烤；偏好靠近起点、少步行。",
  searchRoundCount: 2,
  searchRoundsDebug: [
    { goal: "找公园", tool: "keyword_search", queries: ["公园", "城市公园"] },
    { goal: "找烧烤", tool: "nearby_search", queries: ["烧烤", "烤肉"] },
  ],
  primaryIntents: ["park", "bbq"],
  clarificationFromSearchPlanner: false,
  selectionReason: "Mock 模式：用于页面联调。",
  reasonTags: ["mock"],
  raw: { mode: "mock" },
};

function toNumber(value, fallbackValue = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallbackValue;
}

function calcTotalMinutes(route) {
  if (!Array.isArray(route)) return 0;
  return route.reduce((sum, item) => sum + Math.max(0, toNumber(item?.estimated_duration_minutes, 0)), 0);
}

function calcCrossArea(route) {
  if (!Array.isArray(route) || route.length === 0) return 0;
  const clusters = new Set(
    route
      .map((item) => String(item?.district_cluster || "").trim())
      .filter(Boolean)
  );
  return Math.max(0, clusters.size - 1);
}

function inferRouteSource(route) {
  if (!Array.isArray(route) || route.length === 0) return "unknown";
  const hasEstimate = route.some((item) => String(item?.transport_from_prev || "").includes("估算"));
  return hasEstimate ? "fallback_local" : "amap";
}

function selectedByText(value) {
  if (value === "llm") return "模型选优";
  if (value === "fallback_rule") return "规则回退";
  if (value === "timeout_degrade") return "超时降级";
  if (value === "mock") return "演示模式";
  return "未知";
}

function stopTypeText(type) {
  return type === "restaurant" ? "餐饮" : "景点";
}

function routeSourceText(value) {
  if (value === "amap") return "高德真实路线";
  if (value === "fallback_local") return "本地估算回退";
  if (value === "mock") return "演示模式";
  return "未标注";
}

function weatherSourceText(value) {
  if (value === "amap_weather") return "高德实时天气";
  if (value === "fallback_request") return "请求参数兜底";
  if (value === "mock") return "演示模式";
  return "未标注";
}

function extractClarificationOptions(question) {
  const text = String(question || "").trim();
  const marker = "可参考：";
  const index = text.indexOf(marker);
  if (index < 0) return [];

  let optionsText = text.slice(index + marker.length).trim();
  optionsText = optionsText.replace(/[。！!？?]+$/g, "").trim();
  if (!optionsText) return [];

  let parts = optionsText.split("、");
  if (parts.length <= 1) {
    parts = optionsText.split("，");
  }
  return Array.from(
    new Set(
      parts
        .map((item) => String(item || "").trim())
        .map((item) => item.replace(/[。！!？?]+$/g, "").trim())
        .filter(Boolean)
    )
  ).slice(0, 6);
}

function shouldInjectOrigin(text, originInput) {
  const origin = String(originInput || "").trim();
  if (!origin) return false;
  const raw = String(text || "").trim();
  if (!raw) return false;
  const compactText = raw.replace(/\s+/g, "");
  const compactOrigin = origin.replace(/\s+/g, "");
  if (compactOrigin && compactText.includes(compactOrigin)) return false;
  if (/(我在|人在|起点在|起点是|从.+出发|附近|这边|周边)/.test(raw)) return false;
  return true;
}

async function readErrorMessage(response) {
  try {
    const data = await response.json();
    if (typeof data?.detail === "string") return data.detail;
    return JSON.stringify(data);
  } catch (_err) {
    try {
      const text = await response.text();
      return text || response.statusText || "请求失败";
    } catch (_textErr) {
      return response.statusText || "请求失败";
    }
  }
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const message = await readErrorMessage(response);
    throw new Error(message || "请求失败");
  }
  return response.json();
}

function normalizeAgentPlan(agentData) {
  const selectedPlan = agentData?.selected_plan || {};
  const stops = Array.isArray(selectedPlan?.route) ? selectedPlan.route : [];
  const scoreBreakdown = agentData?.score_breakdown || agentData?.final_response?.score_breakdown || {};
  return {
    selectedBy: agentData?.selected_by || "unknown",
    title:
      agentData?.readable_output?.title ||
      String(selectedPlan?.summary || "已生成路线"),
    totalDurationMinutes: calcTotalMinutes(stops),
    crossAreaCount:
      typeof agentData?.selected_plan_area_summary?.cross_area_count === "number"
        ? agentData.selected_plan_area_summary.cross_area_count
        : calcCrossArea(stops),
    stops,
    knowledgeUsedCount: toNumber(agentData?.knowledge_used_count, 0),
    knowledgeIds: Array.isArray(agentData?.knowledge_ids) ? agentData.knowledge_ids : [],
    knowledgeBias: agentData?.knowledge_bias || {},
    explanationBasis: Array.isArray(agentData?.explanation_basis) ? agentData.explanation_basis : [],
    totalScore: toNumber(agentData?.total_score ?? scoreBreakdown?.total_score, 0),
    scoreBreakdown,
    retrievedCaseCount: toNumber(agentData?.retrieved_case_count, 0),
    retrievedCaseIds: Array.isArray(agentData?.retrieved_case_ids) ? agentData.retrieved_case_ids : [],
    retrievedCaseSummaries: Array.isArray(agentData?.retrieved_case_summaries) ? agentData.retrieved_case_summaries : [],
    caseBias: agentData?.case_bias || {},
    caseMemoryUsed: Boolean(agentData?.case_memory_used),
    caseMemoryId: agentData?.case_memory_id || null,
    storedToCaseMemory: Boolean(agentData?.stored_to_case_memory),
    storedReason: agentData?.stored_reason || "",
    routeSource: agentData?.route_source || inferRouteSource(stops),
    weatherSource: agentData?.weather_source || "unknown",
    searchMode: agentData?.search_mode || "rule_based",
    searchPlanSummary: agentData?.search_plan_summary || "",
    searchRoundCount: toNumber(agentData?.search_round_count, 0),
    searchRoundsDebug: Array.isArray(agentData?.search_rounds_debug) ? agentData.search_rounds_debug : [],
    primaryIntents: Array.isArray(agentData?.search_plan_used?.primary_intents)
      ? agentData.search_plan_used.primary_intents
      : [],
    clarificationFromSearchPlanner: Boolean(agentData?.clarification_from_search_planner),
    selectionReason: String(agentData?.selection_reason || ""),
    reasonTags: Array.isArray(agentData?.reason_tags) ? agentData.reason_tags : [],
    raw: agentData,
  };
}

function App() {
  const [mode, setMode] = useState("live");
  const [apiBase, setApiBase] = useState("http://127.0.0.1:8000");
  const [userKey, setUserKey] = useState(() => localStorage.getItem("xian_agent_user_key") || "playground_user");
  const [originInput, setOriginInput] = useState(() => localStorage.getItem("xian_agent_origin") || "");
  const [threadId, setThreadId] = useState("");
  const [waitingClarification, setWaitingClarification] = useState(false);
  const [clarificationOptions, setClarificationOptions] = useState([]);
  const [inputText, setInputText] = useState("");
  const [messages, setMessages] = useState(START_MESSAGES);
  const [planView, setPlanView] = useState(null);
  const [userRating, setUserRating] = useState(0);
  const [feedbackText, setFeedbackText] = useState("");
  const [feedbackStatus, setFeedbackStatus] = useState("");
  const [highScoreCases, setHighScoreCases] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const messageEndRef = useRef(null);

  useEffect(() => {
    localStorage.setItem("xian_agent_user_key", userKey || "playground_user");
  }, [userKey]);

  useEffect(() => {
    localStorage.setItem("xian_agent_origin", originInput || "");
  }, [originInput]);

  const loadHighScoreCases = async () => {
    if (mode === "mock") {
      setHighScoreCases(MOCK_PLAN.retrievedCaseSummaries);
      return;
    }
    try {
      const base = apiBase.replace(/\/$/, "");
      const params = new URLSearchParams();
      if (String(userKey || "").trim()) params.set("user_key", String(userKey).trim());
      params.set("limit", "6");
      const response = await fetch(`${base}/route-memory?${params.toString()}`);
      if (!response.ok) return;
      const data = await response.json();
      setHighScoreCases(Array.isArray(data?.items) ? data.items : []);
    } catch (_err) {
      setHighScoreCases([]);
    }
  };

  useEffect(() => {
    loadHighScoreCases();
  }, [apiBase, userKey, mode]);

  useEffect(() => {
    if (messageEndRef.current) {
      messageEndRef.current.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [messages, loading]);

  const modeText = useMemo(() => {
    if (mode === "live_fast") return "极速联调";
    if (mode === "mock") return "Mock 演示";
    return "实时联调";
  }, [mode]);

  const pushMessage = (role, text) => {
    setMessages((prev) => [
      ...prev,
      {
        id: `${role}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
        role,
        text,
      },
    ]);
  };

  const submitText = async (rawText) => {
    const text = String(rawText || "").trim();
    if (!text || loading) return;
    const continueMode = waitingClarification && Boolean(threadId);
    const injectedText =
      !continueMode && shouldInjectOrigin(text, originInput)
        ? `我在${String(originInput || "").trim()}。${text}`
        : text;

    pushMessage("user", text);
    setError("");
    setLoading(true);

    try {
      if (mode === "mock") {
        setPlanView({ ...MOCK_PLAN, userQuery: text });
        setUserRating(0);
        setFeedbackText("");
        setFeedbackStatus("");
        pushMessage("agent", "已生成示例路线（演示模式）。切到实时联调可调用真实智能体。");
        setWaitingClarification(false);
        setClarificationOptions([]);
        return;
      }

      const base = apiBase.replace(/\/$/, "");
      const endpoint = continueMode ? "/agent-plan/continue" : "/agent-plan-v3";
      const payload = continueMode
        ? {
            thread_id: threadId,
            clarification_answer: text,
          }
        : {
            text: injectedText,
            thread_id: threadId || undefined,
            user_key: String(userKey || "").trim() || "playground_user",
            fast_mode: mode === "live_fast",
          };

      const agentData = await postJson(`${base}${endpoint}`, payload);
      if (agentData?.thread_id) {
        setThreadId(agentData.thread_id);
      }

      if (agentData?.clarification_needed) {
        setWaitingClarification(true);
        const question =
          String(agentData?.clarification_question || "") ||
          "我还缺少关键出行信息，请继续补充。";
        setClarificationOptions(
          Array.isArray(agentData?.clarification_options) && agentData.clarification_options.length > 0
            ? agentData.clarification_options
            : extractClarificationOptions(question)
        );
        pushMessage("agent", question);
        return;
      }

      setWaitingClarification(false);
      setClarificationOptions([]);
      const normalized = normalizeAgentPlan(agentData);
      normalized.userQuery = injectedText;
      setPlanView(normalized);
      setUserRating(0);
      setFeedbackText("");
      setFeedbackStatus("");
      loadHighScoreCases();
      pushMessage("agent", `路线已生成：${normalized.title}`);
    } catch (err) {
      const message = err?.message || "请求失败，请检查后端是否已启动。";
      setError(message);
      pushMessage("agent", `本次生成失败：${message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleSend = async () => {
    const text = String(inputText || "").trim();
    if (!text || loading) return;
    setInputText("");
    await submitText(text);
  };

  const handlePickClarificationOption = async (optionText) => {
    const text = String(optionText || "").trim();
    if (!text || loading) return;
    setInputText("");
    await submitText(text);
  };

  const handleResetConversation = () => {
    setMessages(START_MESSAGES);
    setPlanView(null);
    setThreadId("");
    setWaitingClarification(false);
    setClarificationOptions([]);
    setError("");
  };

  const submitFeedback = async () => {
    if (!planView || !userRating) {
      setFeedbackStatus("请选择 1 到 10 分后再提交。");
      return;
    }
    setFeedbackStatus("正在提交评分...");
    try {
      if (mode === "mock") {
        setFeedbackStatus("演示模式：评分已记录，已纳入高质量路线案例库。");
        setPlanView((prev) => prev ? { ...prev, storedToCaseMemory: true } : prev);
        return;
      }
      const base = apiBase.replace(/\/$/, "");
      const data = await postJson(`${base}/route-feedback`, {
        user_key: String(userKey || "").trim() || "playground_user",
        user_query: planView.userQuery || "",
        selected_plan: planView.raw?.selected_plan_area_summary?.plan_id || planView.selectedBy,
        itinerary: planView.raw?.selected_plan || { route: planView.stops, summary: planView.title, tips: [] },
        system_score_breakdown: planView.scoreBreakdown || {},
        user_rating: userRating,
        feedback_text: feedbackText,
        case_memory_id: planView.caseMemoryId,
        parsed_request: planView.raw?.parsed_request || {},
        route_summary: {
          ...(planView.raw?.selected_plan_area_summary || {}),
          route_source: planView.routeSource,
        },
        knowledge_ids: planView.knowledgeIds || [],
        knowledge_bias: planView.knowledgeBias || {},
      });
      setPlanView((prev) =>
        prev
          ? {
              ...prev,
              totalScore: toNumber(data?.final_total_score, prev.totalScore),
              scoreBreakdown: data?.score_breakdown || prev.scoreBreakdown,
              storedToCaseMemory: Boolean(data?.stored_to_case_memory),
              caseMemoryId: data?.case_memory_id || prev.caseMemoryId,
              storedReason: data?.stored_reason || prev.storedReason,
            }
          : prev
      );
      setFeedbackStatus(data?.stored_to_case_memory ? "评分已提交，已纳入高质量路线案例库。" : "评分已提交。");
      loadHighScoreCases();
    } catch (err) {
      setFeedbackStatus(`评分提交失败：${err?.message || "请检查后端接口"}`);
    }
  };

  return (
    <div className="page">
      <header className="header">
        <h1>西安路线规划助手</h1>
        <p>
          仅通过对话输入需求，系统自动规划；全程使用 API 实时返回，不做超时规则降级。
        </p>
      </header>

      <section className="panel controls">
        <div className="control-grid">
          <label>
            运行模式
            <div className="switch-row">
              <button
                type="button"
                className={mode === "live" ? "chip active" : "chip"}
                onClick={() => setMode("live")}
              >
                实时联调
              </button>
              <button
                type="button"
                className={mode === "live_fast" ? "chip active" : "chip"}
                onClick={() => setMode("live_fast")}
              >
                极速联调
              </button>
              <button
                type="button"
                className={mode === "mock" ? "chip active" : "chip"}
                onClick={() => setMode("mock")}
              >
                Mock 演示
              </button>
            </div>
          </label>

          <label>
            后端地址
            <input
              value={apiBase}
              onChange={(e) => setApiBase(e.target.value)}
              placeholder="例如 http://127.0.0.1:8000"
            />
          </label>

          <label>
            记忆 ID（SQLite 持久化）
            <input
              value={userKey}
              onChange={(e) => setUserKey(e.target.value)}
              placeholder="例如 playground_user"
            />
          </label>

          <label>
            起点位置（专门填写）
            <input
              value={originInput}
              onChange={(e) => setOriginInput(e.target.value)}
              placeholder="例如 西安电子科技大学长安校区"
            />
          </label>
        </div>

        <div className="meta-row">
          <span>当前模式：{modeText}</span>
          <span>线程 ID：{threadId || "未建立"}</span>
          <span>{waitingClarification ? "状态：等待你补充信息" : "状态：可直接提需求"}</span>
          {originInput ? <span>已设置起点：{originInput}</span> : null}
          <button type="button" className="minor-btn" onClick={handleResetConversation}>
            新会话
          </button>
        </div>
      </section>

      <section className="panel chat-panel">
        <div className="chat-list">
          {messages.map((item) => (
            <div key={item.id} className={`chat-item ${item.role}`}>
              <div className="chat-role">{item.role === "user" ? "你" : "助手"}</div>
              <div className="chat-bubble">{item.text}</div>
            </div>
          ))}
          {loading ? (
            <div className="chat-item agent">
              <div className="chat-role">助手</div>
              <div className="chat-bubble">正在规划中，请稍候...</div>
            </div>
          ) : null}
          <div ref={messageEndRef} />
        </div>

        <div className="input-area">
          <textarea
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            placeholder="直接描述你的需求，例如：晚上在曲江附近，想约会吃饭看夜景，步行不要太多。"
          />
          <button type="button" className="send-btn" onClick={handleSend} disabled={loading}>
            {loading ? "规划中..." : waitingClarification ? "提交补充信息" : "开始规划"}
          </button>
        </div>
        {waitingClarification && clarificationOptions.length > 0 ? (
          <div className="clarify-options">
            <div className="clarify-title">可直接点击补充：</div>
            <div className="clarify-buttons">
              {clarificationOptions.map((option) => (
                <button
                  key={option}
                  type="button"
                  className="clarify-btn"
                  disabled={loading}
                  onClick={() => handlePickClarificationOption(option)}
                >
                  {option}
                </button>
              ))}
            </div>
          </div>
        ) : null}
      </section>

      {error ? <section className="error-box">{error}</section> : null}

      <section className="panel plan-panel">
        <h2>规划信息（底部）</h2>
        {!planView ? <p className="empty-tip">当前还没有可展示的路线。</p> : null}

        {planView ? (
          <>
            <div className="summary-grid">
              <div><strong>方案来源：</strong>{selectedByText(planView.selectedBy)}</div>
              <div><strong>标题：</strong>{planView.title}</div>
              <div><strong>总时长（分钟）：</strong>{planView.totalDurationMinutes}</div>
              <div><strong>跨区域次数：</strong>{planView.crossAreaCount}</div>
              <div><strong>路线来源：</strong>{routeSourceText(planView.routeSource)}</div>
              <div><strong>天气来源：</strong>{weatherSourceText(planView.weatherSource)}</div>
            </div>

            <div className="search-strategy-card">
              <h3>搜索策略</h3>
              <div className="summary-grid compact">
                <div><strong>搜索模式：</strong>{planView.searchMode === "llm_planned" ? "模型规划搜索" : "规则兜底搜索"}</div>
                <div><strong>主要诉求：</strong>{planView.primaryIntents?.length ? planView.primaryIntents.join("、") : "未标注"}</div>
                <div><strong>搜索轮次：</strong>{planView.searchRoundCount || 0}</div>
                <div><strong>主动追问：</strong>{planView.clarificationFromSearchPlanner ? "由搜索规划触发" : "未触发"}</div>
              </div>
              <p className="strategy-summary">{planView.searchPlanSummary || "本次未返回搜索策略摘要。"}</p>
              {Array.isArray(planView.searchRoundsDebug) && planView.searchRoundsDebug.length > 0 ? (
                <div className="strategy-rounds">
                  {planView.searchRoundsDebug.slice(0, 3).map((round, index) => (
                    <span key={`${round.tool || "tool"}-${index}`}>
                      第 {index + 1} 轮：{(round.queries || []).slice(0, 3).join("、") || round.tool || "搜索"}
                    </span>
                  ))}
                </div>
              ) : null}
            </div>

            <div className="score-card">
              <div className="score-head">
                <h3>路线评分</h3>
                <div className="score-number">{toNumber(planView.totalScore, 0).toFixed(1)} / 10</div>
              </div>
              <div className="score-parts">
                <span>约束满足：{toNumber(planView.scoreBreakdown?.constraint_score, 0).toFixed(1)} / 2.9</span>
                <span>规划质量：{toNumber(planView.scoreBreakdown?.plan_quality_score, 0).toFixed(1)} / 2.1</span>
                <span>用户反馈：{toNumber(planView.scoreBreakdown?.user_feedback_score, 0).toFixed(1)} / 5</span>
              </div>
              <div className="store-tip">
                {planView.storedToCaseMemory || toNumber(planView.totalScore, 0) >= 8
                  ? "已纳入高质量路线案例库"
                  : "分数达到 8 分后可纳入高质量路线案例库"}
              </div>
              <div className="rating-row" aria-label="用户评分">
                {Array.from({ length: 10 }, (_, index) => index + 1).map((score) => (
                  <button
                    key={score}
                    type="button"
                    className={userRating === score ? "rating-btn active" : "rating-btn"}
                    onClick={() => setUserRating(score)}
                  >
                    {score}
                  </button>
                ))}
              </div>
              <textarea
                className="feedback-input"
                value={feedbackText}
                onChange={(e) => setFeedbackText(e.target.value)}
                placeholder="可选：写一句你对这条路线的反馈。"
              />
              <div className="feedback-actions">
                <button type="button" className="minor-btn primary" onClick={submitFeedback}>
                  提交评分
                </button>
                {feedbackStatus ? <span>{feedbackStatus}</span> : null}
              </div>
            </div>

            <h3>路线图框</h3>
            <div className="route-flow">
              {Array.isArray(planView.stops) && planView.stops.length > 0 ? (
                planView.stops.map((stop, index) => (
                  <React.Fragment key={`${stop.name}-${index}`}>
                    <div className="route-node">
                      <div className="route-time">{stop.time_slot || "--:--"}</div>
                      <div className="route-name">{stop.name}</div>
                      <div className="route-meta">{stopTypeText(stop.type)} · {stop.district_cluster || "未标注区域"}</div>
                      <div className="route-meta">{stop.transport_from_prev || ""}</div>
                      <div className="route-reason">{stop.reason || ""}</div>
                    </div>
                    {index < planView.stops.length - 1 ? <div className="route-link">→</div> : null}
                  </React.Fragment>
                ))
              ) : (
                <div className="empty-tip">无可展示站点</div>
              )}
            </div>
          </>
        ) : null}
      </section>

      <section className="panel memory-panel">
        <h2>我的历史高分路线</h2>
        {highScoreCases.length === 0 ? (
          <p className="empty-tip">还没有历史高分案例。</p>
        ) : (
          <div className="memory-list">
            {highScoreCases.map((item) => (
              <div key={item.case_id || item.caseId} className="memory-item">
                <div className="memory-top">
                  <strong>#{item.case_id || item.caseId}</strong>
                  <span>{toNumber(item.score, 0).toFixed(1)} 分</span>
                </div>
                <div className="memory-query">{item.query || "未记录需求"}</div>
                <div className="route-meta">
                  {item.selected_plan || "未标注方案"} · {item.created_at || "刚刚"}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<App />);


