from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class KnowledgeBundle:
    """Knowledge retrieval output (RAG layer scaffold)."""

    snippets: List[Dict[str, str]] = field(default_factory=list)
    source_tags: List[str] = field(default_factory=list)
    confidence: float = 0.0
    usage_hint: str = ""


_MOCK_KNOWLEDGE_ITEMS: List[Dict[str, Any]] = [
    {
        "keywords": ["钟楼", "鼓楼", "城墙"],
        "title": "城墙钟鼓楼带",
        "content": "适合经典地标串联，步行与短距离换乘都比较友好。",
        "source": "mock_city_guide",
    },
    {
        "keywords": ["小寨", "博物馆", "陕历博"],
        "title": "小寨文博带",
        "content": "文博点位密度高，适合上午或雨天做室内优先安排。",
        "source": "mock_city_guide",
    },
    {
        "keywords": ["大雁塔", "慈恩寺"],
        "title": "大雁塔带",
        "content": "地标性强，适合经典路线或拍照打卡。",
        "source": "mock_city_guide",
    },
    {
        "keywords": ["曲江", "夜景", "不夜城"],
        "title": "曲江夜游带",
        "content": "晚间氛围更好，适合夜游与餐饮衔接。",
        "source": "mock_city_guide",
    },
    {
        "keywords": ["回民街", "北院门", "小吃"],
        "title": "回民街美食带",
        "content": "餐饮密度高，适合把正餐或小吃安排在游览中前段。",
        "source": "mock_city_food",
    },
    {
        "keywords": ["高新", "科技路", "唐延路", "锦业路"],
        "title": "高新商圈带",
        "content": "室内商业点较集中，天气不稳时体验更平滑。",
        "source": "mock_city_area_profile",
    },
    {
        "keywords": ["电视塔", "会展", "会展中心"],
        "title": "电视塔会展带",
        "content": "区域风格偏会展与商圈，适合作为午后轻松衔接区。",
        "source": "mock_city_area_profile",
    },
    {
        "keywords": ["浐灞", "未央", "世博园", "奥体"],
        "title": "浐灞未央扩展带",
        "content": "区域跨度较大，适合时间更充裕时做扩展覆盖。",
        "source": "mock_city_area_profile",
    },
]


def retrieve_place_knowledge(query: str, context: Dict[str, Any] | None = None) -> KnowledgeBundle:
    """Retrieve lightweight place knowledge.

    Stage note:
    - This is a placeholder for future RAG integration.
    - Current output should be used for explanation/tag enhancement first.
    - It is intentionally not a route optimizer.
    """

    context = context or {}
    context_tokens = [
        str(context.get("preferred_period", "")),
        str(context.get("purpose", "")),
        str(context.get("cluster", "")),
        " ".join(str(item) for item in context.get("tags", []) if item),
    ]
    text = " ".join([query, *context_tokens])

    snippets: List[Dict[str, str]] = []
    source_tags: List[str] = []

    for item in _MOCK_KNOWLEDGE_ITEMS:
        if any(keyword in text for keyword in item["keywords"]):
            snippets.append({"title": item["title"], "content": item["content"]})
            source_tags.append(item["source"])

    if not snippets:
        snippets.append(
            {
                "title": "通用提示",
                "content": "当前知识层为脚手架实现，建议配合候选差异与硬约束结果解释路线。",
            }
        )
        source_tags.append("mock_fallback")

    confidence = 0.4 if snippets and source_tags[0] != "mock_fallback" else 0.2
    usage_hint = "当前知识层优先用于推荐理由与文案解释，不直接替代硬规划层。"

    return KnowledgeBundle(
        snippets=snippets,
        source_tags=sorted(set(source_tags)),
        confidence=confidence,
        usage_hint=usage_hint,
    )


def bundle_to_tags(bundle: KnowledgeBundle) -> List[str]:
    """Map snippets to lightweight tags for summary/selection/readable layers."""
    if "mock_fallback" in bundle.source_tags:
        return []
    tags: List[str] = []
    for snippet in bundle.snippets:
        title = str(snippet.get("title", ""))
        content = str(snippet.get("content", ""))
        text = f"{title} {content}"
        if "文博" in text or "博物馆" in text:
            tags.append("文博密度高")
        if "晚间" in text or "夜游" in text or "不夜城" in text:
            tags.append("晚间氛围更强")
        if "拍照" in text or "打卡" in text or "地标" in text:
            tags.append("适合拍照打卡")
        if "雨天" in text or "室内" in text:
            tags.append("雨天室内友好")
        if "步行" in text or "换乘" in text:
            tags.append("动线更顺")
        if "餐饮密度" in text or "小吃" in text or "正餐" in text:
            tags.append("餐饮选择更丰富")
        if "区域风格" in text or "商圈" in text or "会展" in text:
            tags.append("区域风格鲜明")

    ordered: List[str] = []
    seen = set()
    for tag in tags:
        if tag in seen:
            continue
        seen.add(tag)
        ordered.append(tag)
    return ordered


def bundle_to_notes(bundle: KnowledgeBundle, limit: int = 2) -> List[str]:
    """Extract concise notes from snippets; keep output short."""
    if "mock_fallback" in bundle.source_tags:
        return []
    notes: List[str] = []
    for snippet in bundle.snippets:
        content = str(snippet.get("content", "")).strip()
        if not content:
            continue
        if len(content) > 36:
            content = content[:36].rstrip("，。； ") + "。"
        notes.append(content)

    deduped: List[str] = []
    seen = set()
    for note in notes:
        if note in seen:
            continue
        seen.add(note)
        deduped.append(note)
    return deduped[: max(limit, 0)]
