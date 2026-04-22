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
  routeSource: "mock",
  weatherSource: "mock",
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
    routeSource: agentData?.route_source || inferRouteSource(stops),
    weatherSource: agentData?.weather_source || "unknown",
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
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const messageEndRef = useRef(null);

  useEffect(() => {
    localStorage.setItem("xian_agent_user_key", userKey || "playground_user");
  }, [userKey]);

  useEffect(() => {
    localStorage.setItem("xian_agent_origin", originInput || "");
  }, [originInput]);

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
        setPlanView(MOCK_PLAN);
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
        setClarificationOptions(extractClarificationOptions(question));
        pushMessage("agent", question);
        return;
      }

      setWaitingClarification(false);
      setClarificationOptions([]);
      const normalized = normalizeAgentPlan(agentData);
      setPlanView(normalized);
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
    </div>
  );
}

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<App />);

