"""Evidence linking helpers."""

from __future__ import annotations

from typing import Any


def evidence_linker_tool(raw_findings: list[dict[str, Any]], source_clusters: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    links = []
    for item in raw_findings:
        item_id = str(item.get("id", ""))
        links.append(
            {
                "claim_id": item_id,
                "evidence_ids": [item_id] if item_id else [],
                "source_cluster_ids": [cluster.get("cluster_id") for cluster in source_clusters or []],
            }
        )
    return {"evidence_links": links}
