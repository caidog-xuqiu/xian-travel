const { useMemo, useState } = React;

const DEFAULT_FORM = {
  text: "陪父母半天，不想太累，中午想吃饭，下雨也能玩",
  companion_type: "solo",
  available_hours: 4,
  budget_level: "medium",
  purpose: "tourism",
  need_meal: true,
  walking_tolerance: "medium",
  weather: "sunny",
  origin: "钟楼",
  preferred_plan: "relaxed_first",
};

const MOCK_RESULT = {
  selected_plan: {
    summary: "mock：已生成一条轻松路线",
    route: [
      {
        time_slot: "11:30-12:10",
        type: "sight",
        name: "钟楼",
        district_cluster: "城墙钟鼓楼簇",
        transport_from_prev: "起点步行",
        reason: "地标打卡，步行较少",
        estimated_distance_meters: 300,
        estimated_duration_minutes: 10,
      },
      {
        time_slot: "12:20-13:20",
        type: "restaurant",
        name: "老李家面(钟楼总店)",
        district_cluster: "城墙钟鼓楼簇",
        transport_from_prev: "步行 8 分钟",
        reason: "顺路就餐",
        estimated_distance_meters: 500,
        estimated_duration_minutes: 8,
      },
    ],
    tips: ["mock 模式返回，便于前端联调"],
  },
  selected_by: "mock",
  itinerary_title: "Mock 半日路线",
  total_duration_minutes: 78,
  cross_area_count: 0,
  stops: [
    {
      time_slot: "11:30-12:10",
      type: "sight",
      name: "钟楼",
      district_cluster: "城墙钟鼓楼簇",
      transport_from_prev: "起点步行",
      reason: "地标打卡，步行较少",
    },
    {
      time_slot: "12:20-13:20",
      type: "restaurant",
      name: "老李家面(钟楼总店)",
      district_cluster: "城墙钟鼓楼簇",
      transport_from_prev: "步行 8 分钟",
      reason: "顺路就餐",
    },
  ],
  knowledge_used_count: 3,
  knowledge_ids: ["k_parents_relaxed", "k_need_meal_anchor", "k_short_time_3h"],
  knowledge_bias: {
    prefer_single_cluster: true,
    prefer_low_walk: true,
    prefer_meal_experience: true,
    avoid_too_many_stops: true,
  },
  explanation_basis: [
    "陪父母偏低强度：减少连续移动和站点数。",
    "需用餐时餐点为硬锚点：优先保留吃饭节点。",
  ],
  route_source: "mock",
  weather_source: "mock",
  raw: {
    mode: "mock",
  },
};

function toNumber(value, fallbackValue = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallbackValue;
}

function sumDurationMinutes(route) {
  if (!Array.isArray(route)) {
    return 0;
  }
  return route.reduce((acc, item) => {
    const minutes = toNumber(item?.estimated_duration_minutes, 0);
    return acc + Math.max(0, minutes);
  }, 0);
}

function estimateCrossAreaCount(route) {
  if (!Array.isArray(route) || route.length === 0) {
    return 0;
  }
  const clusters = new Set(
    route
      .map((item) => String(item?.district_cluster || "").trim())
      .filter(Boolean)
  );
  return Math.max(0, clusters.size - 1);
}

function buildPlanPayload(form) {
  const preferredTripStyle = form.preferred_plan === "relaxed_first" ? "relaxed" : "balanced";
  const purpose =
    form.preferred_plan === "food_friendly"
      ? "food"
      : form.preferred_plan === "classic_first"
      ? "tourism"
      : form.purpose;

  return {
    companion_type: form.companion_type,
    available_hours: toNumber(form.available_hours, 4),
    budget_level: form.budget_level,
    purpose,
    need_meal: Boolean(form.need_meal),
    walking_tolerance: form.walking_tolerance,
    weather: form.weather,
    origin: form.origin || "钟楼",
    preferred_trip_style: preferredTripStyle,
  };
}

async function readErrorMessage(response) {
  try {
    const data = await response.json();
    if (typeof data?.detail === "string") {
      return data.detail;
    }
    return JSON.stringify(data);
  } catch (_err) {
    try {
      const text = await response.text();
      return text || response.statusText;
    } catch (_textErr) {
      return response.statusText || "请求失败";
    }
  }
}

function normalizeLiveResult(planData, agentData, agentNotice) {
  const selectedPlan = agentData?.selected_plan || planData || {};
  const route = Array.isArray(selectedPlan?.route) ? selectedPlan.route : [];

  const crossAreaFromAgent = agentData?.selected_plan_area_summary?.cross_area_count;
  const crossAreaCount =
    typeof crossAreaFromAgent === "number" ? crossAreaFromAgent : estimateCrossAreaCount(route);

  return {
    selected_plan: selectedPlan,
    selected_by: agentData?.selected_by || "plan_only",
    itinerary_title:
      agentData?.readable_output?.title ||
      (selectedPlan?.summary ? String(selectedPlan.summary) : "路线结果"),
    total_duration_minutes: sumDurationMinutes(route),
    cross_area_count: crossAreaCount,
    stops: route,
    knowledge_used_count: agentData?.knowledge_used_count || 0,
    knowledge_ids: agentData?.knowledge_ids || [],
    knowledge_bias: agentData?.knowledge_bias || {},
    explanation_basis: agentData?.explanation_basis || [],
    route_source: agentData?.route_source || "from_/plan",
    weather_source: agentData?.weather_source || "request",
    agent_notice: agentNotice || "",
    raw: {
      plan: planData,
      agent: agentData,
    },
  };
}

function App() {
  const [mode, setMode] = useState("live");
  const [apiBase, setApiBase] = useState("http://127.0.0.1:8000");
  const [form, setForm] = useState(DEFAULT_FORM);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  const titleText = useMemo(() => {
    return mode === "mock" ? "Mock 调试中" : "Live 联调中";
  }, [mode]);

  const updateField = (key, value) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  const handleSubmit = async () => {
    setLoading(true);
    setError("");

    try {
      if (mode === "mock") {
        setResult(MOCK_RESULT);
        return;
      }

      const base = apiBase.replace(/\/$/, "");
      const planPayload = buildPlanPayload(form);

      const planResponse = await fetch(`${base}/plan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(planPayload),
      });

      if (!planResponse.ok) {
        const message = await readErrorMessage(planResponse);
        throw new Error(`live 请求失败（/plan）: ${message}`);
      }

      const planData = await planResponse.json();

      let agentData = null;
      let agentNotice = "";
      if (String(form.text || "").trim()) {
        try {
          const agentResponse = await fetch(`${base}/agent-plan-v3`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              text: form.text,
              user_key: "playground_user",
            }),
          });

          if (agentResponse.ok) {
            agentData = await agentResponse.json();
          } else {
            const msg = await readErrorMessage(agentResponse);
            agentNotice = `已拿到 /plan 结果，但 /agent-plan-v3 失败：${msg}`;
          }
        } catch (agentErr) {
          agentNotice = `已拿到 /plan 结果，但 /agent-plan-v3 异常：${agentErr.message}`;
        }
      }

      setResult(normalizeLiveResult(planData, agentData, agentNotice));
    } catch (err) {
      setResult(null);
      setError(err?.message || "生成失败，请检查后端是否启动。\n页面仍可切换到 mock 模式继续测试。");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="page">
      <header className="header">
        <h1>西安 Agent Playground</h1>
        <p>{titleText}</p>
      </header>

      <section className="panel">
        <div className="row mode-row">
          <label>
            <input
              type="radio"
              checked={mode === "live"}
              onChange={() => setMode("live")}
            />
            live
          </label>
          <label>
            <input
              type="radio"
              checked={mode === "mock"}
              onChange={() => setMode("mock")}
            />
            mock
          </label>
          <input
            className="api-input"
            value={apiBase}
            onChange={(e) => setApiBase(e.target.value)}
            placeholder="后端地址，例如 http://127.0.0.1:8000"
          />
        </div>

        <label className="block-label">自然语言需求</label>
        <textarea
          className="big-input"
          value={form.text}
          onChange={(e) => updateField("text", e.target.value)}
          placeholder="请输入旅游需求..."
        />

        <div className="grid">
          <label>
            companion_type
            <select
              value={form.companion_type}
              onChange={(e) => updateField("companion_type", e.target.value)}
            >
              <option value="solo">solo</option>
              <option value="parents">parents</option>
              <option value="friends">friends</option>
              <option value="partner">partner</option>
            </select>
          </label>

          <label>
            available_hours
            <input
              type="number"
              min="1"
              max="24"
              step="0.5"
              value={form.available_hours}
              onChange={(e) => updateField("available_hours", e.target.value)}
            />
          </label>

          <label>
            budget_level
            <select
              value={form.budget_level}
              onChange={(e) => updateField("budget_level", e.target.value)}
            >
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
            </select>
          </label>

          <label>
            purpose
            <select value={form.purpose} onChange={(e) => updateField("purpose", e.target.value)}>
              <option value="tourism">tourism</option>
              <option value="relax">relax</option>
              <option value="food">food</option>
              <option value="dating">dating</option>
            </select>
          </label>

          <label>
            need_meal
            <select
              value={String(form.need_meal)}
              onChange={(e) => updateField("need_meal", e.target.value === "true")}
            >
              <option value="true">true</option>
              <option value="false">false</option>
            </select>
          </label>

          <label>
            walking_tolerance
            <select
              value={form.walking_tolerance}
              onChange={(e) => updateField("walking_tolerance", e.target.value)}
            >
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
            </select>
          </label>

          <label>
            weather
            <select value={form.weather} onChange={(e) => updateField("weather", e.target.value)}>
              <option value="sunny">sunny</option>
              <option value="rainy">rainy</option>
              <option value="hot">hot</option>
              <option value="cold">cold</option>
            </select>
          </label>

          <label>
            origin
            <input value={form.origin} onChange={(e) => updateField("origin", e.target.value)} />
          </label>

          <label>
            preferred_plan
            <select
              value={form.preferred_plan}
              onChange={(e) => updateField("preferred_plan", e.target.value)}
            >
              <option value="relaxed_first">relaxed_first</option>
              <option value="classic_first">classic_first</option>
              <option value="food_friendly">food_friendly</option>
            </select>
          </label>
        </div>

        <button className="submit" onClick={handleSubmit} disabled={loading}>
          {loading ? "生成中..." : "生成路线"}
        </button>
      </section>

      {error ? <section className="error-box">{error}</section> : null}

      <section className="panel result">
        <h2>结果展示</h2>
        {!result ? <p>尚未生成结果。</p> : null}

        {result ? (
          <>
            {result.agent_notice ? <div className="notice">{result.agent_notice}</div> : null}

            <div className="kv">
              <div><strong>selected_by:</strong> {String(result.selected_by)}</div>
              <div><strong>itinerary.title:</strong> {String(result.itinerary_title)}</div>
              <div><strong>total_duration_minutes:</strong> {String(result.total_duration_minutes)}</div>
              <div><strong>cross_area_count:</strong> {String(result.cross_area_count)}</div>
              <div><strong>route_source:</strong> {String(result.route_source)}</div>
              <div><strong>weather_source:</strong> {String(result.weather_source)}</div>
              <div><strong>knowledge_used_count:</strong> {String(result.knowledge_used_count)}</div>
            </div>

            <h3>stops</h3>
            <ul className="stops">
              {Array.isArray(result.stops) && result.stops.length > 0 ? (
                result.stops.map((stop, idx) => (
                  <li key={`${stop.name}-${idx}`}>
                    <div>
                      <strong>{stop.time_slot || "--:--"}</strong> [{stop.type}] {stop.name}
                    </div>
                    <div>{stop.district_cluster || ""}</div>
                    <div>{stop.transport_from_prev || ""}</div>
                    <div>{stop.reason || ""}</div>
                  </li>
                ))
              ) : (
                <li>无站点</li>
              )}
            </ul>

            <h3>knowledge_ids</h3>
            <pre>{JSON.stringify(result.knowledge_ids, null, 2)}</pre>

            <h3>knowledge_bias</h3>
            <pre>{JSON.stringify(result.knowledge_bias, null, 2)}</pre>

            <h3>explanation_basis</h3>
            <pre>{JSON.stringify(result.explanation_basis, null, 2)}</pre>

            <h3>selected_plan</h3>
            <pre>{JSON.stringify(result.selected_plan, null, 2)}</pre>
          </>
        ) : null}
      </section>
    </div>
  );
}

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<App />);
