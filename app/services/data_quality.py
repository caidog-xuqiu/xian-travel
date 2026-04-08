from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Tuple


REQUIRED_POI_FIELDS = (
    "id",
    "name",
    "kind",
    "district_cluster",
    "category",
    "latitude",
    "longitude",
)
SUPPORTED_KINDS = {"sight", "restaurant"}


@dataclass
class DataQualityReport:
    total_input: int
    total_after_dedup: int
    quarantined_count: int
    issue_counts: Dict[str, int] = field(default_factory=dict)
    source_distribution: Dict[str, int] = field(default_factory=dict)
    trust_distribution: Dict[str, int] = field(default_factory=dict)
    freshness_distribution: Dict[str, int] = field(default_factory=dict)
    quality_notes: List[str] = field(default_factory=list)


@dataclass
class DataQualityOutcome:
    usable_pois: List[Dict[str, Any]] = field(default_factory=list)
    quarantined_pois: List[Dict[str, Any]] = field(default_factory=list)
    report: DataQualityReport | None = None


def _normalize_name(name: str) -> str:
    return "".join(str(name).lower().split())


def _dedupe_key(poi: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        str(poi.get("kind", "")),
        str(poi.get("district_cluster", "")),
        _normalize_name(str(poi.get("name", ""))),
    )


def deduplicate_pois(pois: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[Tuple[str, str, str]] = set()
    deduped: List[Dict[str, Any]] = []
    for poi in pois:
        key = _dedupe_key(poi)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(poi)
    return deduped


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _source_distribution(pois: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for poi in pois:
        source = str(poi.get("poi_source", "unknown"))
        counts[source] = counts.get(source, 0) + 1
    return counts


def _trust_bucket(poi: Dict[str, Any], source_priority: Dict[str, int]) -> str:
    source = str(poi.get("poi_source", "unknown"))
    score = source_priority.get(source, 0)

    missing_count = sum(1 for field in REQUIRED_POI_FIELDS if poi.get(field) in (None, ""))
    if missing_count >= 2:
        score -= 1

    if score >= 2:
        return "high"
    if score >= 1:
        return "medium"
    return "low"


def _freshness_bucket(poi: Dict[str, Any]) -> str:
    updated_at = poi.get("updated_at") or poi.get("last_updated")
    if not updated_at:
        return "unknown"
    try:
        dt = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
    except ValueError:
        return "unknown"

    now = datetime.now(timezone.utc)
    delta_days = (now - dt.astimezone(timezone.utc)).days
    if delta_days <= 30:
        return "fresh"
    if delta_days <= 180:
        return "stale"
    return "outdated"


def _increment(counter: Dict[str, int], key: str, delta: int = 1) -> None:
    counter[key] = counter.get(key, 0) + delta


def _build_notes(
    total_input: int,
    total_after_dedup: int,
    usable_count: int,
    quarantined_count: int,
    issue_counts: Dict[str, int],
) -> List[str]:
    notes = [
        f"Data quality processed {total_input} -> dedup {total_after_dedup} -> usable {usable_count}.",
        f"Quarantined={quarantined_count}.",
    ]
    if quarantined_count > 0:
        top_issues = sorted(issue_counts.items(), key=lambda x: x[1], reverse=True)[:3]
        issue_text = ", ".join([f"{k}:{v}" for k, v in top_issues]) if top_issues else "none"
        notes.append(f"Top issues: {issue_text}.")
    if usable_count < 6:
        notes.append("Usable pool is low; downstream can fallback to raw pool.")
    return notes


def govern_candidate_pool(
    pois: List[Dict[str, Any]],
    source_priority: Dict[str, int] | None = None,
) -> DataQualityOutcome:
    """Run minimal pre-discovery governance on candidate pool."""
    source_priority = source_priority or {"amap": 2, "mock": 1, "unknown": 0}
    total_input = len(pois)

    deduped = deduplicate_pois(pois)
    duplicates_removed = max(0, total_input - len(deduped))

    issue_counts: Dict[str, int] = {}
    if duplicates_removed > 0:
        issue_counts["duplicate"] = duplicates_removed

    usable: List[Dict[str, Any]] = []
    quarantined: List[Dict[str, Any]] = []

    for poi in deduped:
        reasons: List[str] = []
        for field in REQUIRED_POI_FIELDS:
            if poi.get(field) in (None, ""):
                reasons.append(f"missing_{field}")
                _increment(issue_counts, "missing_field")

        kind = str(poi.get("kind", ""))
        if kind not in SUPPORTED_KINDS:
            reasons.append("invalid_kind")
            _increment(issue_counts, "invalid_kind")

        latitude = _safe_float(poi.get("latitude"))
        longitude = _safe_float(poi.get("longitude"))
        if latitude is None or longitude is None:
            reasons.append("invalid_geo")
            _increment(issue_counts, "invalid_geo")

        if reasons:
            quarantined.append(
                {
                    **poi,
                    "quarantine_reasons": list(dict.fromkeys(reasons)),
                }
            )
            continue

        usable.append(poi)

    trust_distribution: Dict[str, int] = {}
    freshness_distribution: Dict[str, int] = {}
    for poi in usable:
        trust = _trust_bucket(poi, source_priority)
        freshness = _freshness_bucket(poi)
        _increment(trust_distribution, trust)
        _increment(freshness_distribution, freshness)

    report = DataQualityReport(
        total_input=total_input,
        total_after_dedup=len(deduped),
        quarantined_count=len(quarantined),
        issue_counts=issue_counts,
        source_distribution=_source_distribution(deduped),
        trust_distribution=trust_distribution,
        freshness_distribution=freshness_distribution,
        quality_notes=_build_notes(
            total_input=total_input,
            total_after_dedup=len(deduped),
            usable_count=len(usable),
            quarantined_count=len(quarantined),
            issue_counts=issue_counts,
        ),
    )
    return DataQualityOutcome(
        usable_pois=usable,
        quarantined_pois=quarantined,
        report=report,
    )


# Backward-compatible helpers
def assess_data_quality(
    pois: List[Dict[str, Any]],
    source_priority: Dict[str, int] | None = None,
) -> DataQualityReport:
    return govern_candidate_pool(pois=pois, source_priority=source_priority).report or DataQualityReport(
        total_input=len(pois),
        total_after_dedup=len(pois),
        quarantined_count=0,
    )


def build_data_governance_snapshot(pois: List[Dict[str, Any]]) -> Dict[str, Any]:
    report = assess_data_quality(pois)
    return asdict(report)
