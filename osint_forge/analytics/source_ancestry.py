"""Deterministic source ancestry and independence scoring."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse


DEPENDENCY_WEIGHTS = {
    "exact_duplicate": 1.00,
    "syndicated_copy": 0.90,
    "near_duplicate": 0.80,
    "derivative_report": 0.65,
    "shared_primary_source": 0.55,
    "independent_corroboration": 0.15,
    "unrelated": 0.00,
}


def normalize_text(text: str) -> str:
    text = (text or "").lower().strip()
    text = re.sub(r"\s+", " ", text)
    return re.sub(r"[^\w\s]", "", text)


def token_ngrams(text: str, n: int = 3) -> set[str]:
    tokens = normalize_text(text).split()
    if len(tokens) < n:
        return set(tokens)
    return {" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def jaccard_similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def text_similarity(a: str, b: str, n: int = 3) -> float:
    return jaccard_similarity(token_ngrams(a, n), token_ngrams(b, n))


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower() or "unknown"
    except Exception:
        return "unknown"


def _timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _hours_apart(a: Any, b: Any) -> float | None:
    da = _timestamp(a)
    db = _timestamp(b)
    if not da or not db:
        return None
    if da.tzinfo is None:
        da = da.replace(tzinfo=timezone.utc)
    if db.tzinfo is None:
        db = db.replace(tzinfo=timezone.utc)
    return abs((da - db).total_seconds()) / 3600


def classify_relationship(a: dict[str, Any], b: dict[str, Any], config: dict[str, Any]) -> str:
    if a.get("content_hash") and a.get("content_hash") == b.get("content_hash"):
        return "exact_duplicate"

    sim = text_similarity(str(a.get("text", "")), str(b.get("text", "")))
    same_domain = (a.get("domain") or _domain(a.get("url", ""))) == (b.get("domain") or _domain(b.get("url", "")))
    hours = _hours_apart(a.get("publication_timestamp"), b.get("publication_timestamp"))

    if sim >= config["near_duplicate_threshold"]:
        return "near_duplicate" if not same_domain else "syndicated_copy"
    if same_domain and (sim >= config["derivative_threshold"] or (hours is not None and hours <= config["temporal_derivative_hours"])):
        return "derivative_report"
    if a.get("primary_source_id") and a.get("primary_source_id") == b.get("primary_source_id"):
        return "shared_primary_source"
    if sim >= config["corroboration_threshold"]:
        return "independent_corroboration"
    return "unrelated"


def calculate_independence_score(cluster_items: list[dict[str, Any]], ancestry_links: list[dict[str, Any]]) -> dict[str, Any]:
    if len(cluster_items) <= 1:
        return {
            "independence_score": 1.0,
            "independence_explanation": {
                "average_dependency": 0.0,
                "unique_domain_ratio": 1.0,
                "unique_publisher_ratio": 1.0,
                "relationship_counts": {},
                "caveats": ["single item cluster"],
            },
        }

    item_ids = {item["id"] for item in cluster_items}
    relevant = [link for link in ancestry_links if link["source_id"] in item_ids and link["target_id"] in item_ids]
    dependency = [link.get("dependency_weight", 0.0) for link in relevant]
    average_dependency = sum(dependency) / len(dependency) if dependency else 0.0

    domains = {item.get("domain") or _domain(item.get("url", "")) for item in cluster_items}
    publishers = {item.get("publisher", "unknown") for item in cluster_items}
    domain_ratio = len(domains) / len(cluster_items)
    publisher_ratio = len(publishers) / len(cluster_items)
    score = round(0.70 * (1.0 - average_dependency) + 0.20 * domain_ratio + 0.10 * publisher_ratio, 3)

    return {
        "independence_score": score,
        "independence_explanation": {
            "average_dependency": round(average_dependency, 3),
            "unique_domain_ratio": round(domain_ratio, 3),
            "unique_publisher_ratio": round(publisher_ratio, 3),
            "relationship_counts": dict(Counter(link["relationship_type"] for link in relevant)),
            "caveats": ["high dependency inside cluster"] if average_dependency > 0.6 else [],
        },
    }


def analyze_source_ancestry(evidence_items: list[dict[str, Any]], config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = {
        "near_duplicate_threshold": 0.90,
        "derivative_threshold": 0.65,
        "corroboration_threshold": 0.35,
        "temporal_derivative_hours": 12,
        **(config or {}),
    }
    items = []
    for index, item in enumerate(evidence_items):
        enriched = dict(item)
        enriched.setdefault("id", f"src-{index + 1}")
        enriched.setdefault("domain", _domain(enriched.get("url", "")))
        items.append(enriched)

    ancestry_links: list[dict[str, Any]] = []
    parent: dict[str, str] = {item["id"]: item["id"] for item in items}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i, left in enumerate(items):
        for right in items[i + 1 :]:
            relationship = classify_relationship(left, right, cfg)
            weight = DEPENDENCY_WEIGHTS[relationship]
            ancestry_links.append(
                {
                    "source_id": left["id"],
                    "target_id": right["id"],
                    "relationship_type": relationship,
                    "dependency_weight": weight,
                }
            )
            if relationship != "unrelated":
                union(left["id"], right["id"])

    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        grouped.setdefault(find(item["id"]), []).append(item)

    clusters = []
    for idx, cluster_items in enumerate(grouped.values(), start=1):
        cluster_links = [
            link
            for link in ancestry_links
            if link["source_id"] in {item["id"] for item in cluster_items}
            and link["target_id"] in {item["id"] for item in cluster_items}
        ]
        clusters.append(
            {
                "cluster_id": f"cluster-{idx}",
                "item_ids": [item["id"] for item in cluster_items],
                "domains": sorted({item.get("domain", "unknown") for item in cluster_items}),
                **calculate_independence_score(cluster_items, cluster_links),
            }
        )

    return {
        "source_clusters": clusters,
        "ancestry_links": ancestry_links,
        "summary": {"total_items": len(items), "cluster_count": len(clusters)},
    }


def source_ancestry_tool(evidence_items: list[dict[str, Any]], config: dict[str, Any] | None = None) -> dict[str, Any]:
    return analyze_source_ancestry(evidence_items, config=config)
