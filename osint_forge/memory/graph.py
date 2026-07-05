"""Proposal-gated distributed memory graph."""

from __future__ import annotations

import math
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal

from osint_forge.provenance import ProvenanceRef, stable_hash, utc_now


MemoryNodeType = Literal["source", "event", "entity", "claim", "artifact", "agent_output", "report"]
MemoryRelation = Literal[
    "supports",
    "contradicts",
    "same_as",
    "mentions",
    "located_in",
    "temporal_next",
    "derived_from",
    "verified_by",
]
ProposalStatus = Literal["pending", "promoted", "rejected"]


class MemoryGraphError(Exception):
    """Raised when a memory proposal or graph operation is invalid."""


@dataclass
class MemoryNode:
    id: str
    type: MemoryNodeType
    title: str
    summary: str
    createdAt: str
    confidence: float
    provenance: list[ProvenanceRef]
    embeddingRef: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise MemoryGraphError("memory node id is required")
        if self.type not in {"source", "event", "entity", "claim", "artifact", "agent_output", "report"}:
            raise MemoryGraphError(f"unsupported memory node type: {self.type}")
        if not self.provenance:
            raise MemoryGraphError("memory node provenance is required")
        self.confidence = max(0.0, min(1.0, float(self.confidence)))
        self.provenance = [ProvenanceRef.from_value(item) for item in self.provenance]

    def text_for_embedding(self) -> str:
        return f"{self.title} {self.summary}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "summary": self.summary,
            "createdAt": self.createdAt,
            "confidence": self.confidence,
            "provenance": [item.to_dict() for item in self.provenance],
            "embeddingRef": self.embeddingRef,
        }


@dataclass
class MemoryEdge:
    from_id: str
    to_id: str
    relation: MemoryRelation
    strength: float
    createdBy: str
    provenance: list[ProvenanceRef]
    createdAt: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.from_id or not self.to_id:
            raise MemoryGraphError("memory edge endpoints are required")
        if self.relation not in {
            "supports",
            "contradicts",
            "same_as",
            "mentions",
            "located_in",
            "temporal_next",
            "derived_from",
            "verified_by",
        }:
            raise MemoryGraphError(f"unsupported memory edge relation: {self.relation}")
        if not self.provenance:
            raise MemoryGraphError("memory edge provenance is required")
        self.strength = max(0.0, min(1.0, float(self.strength)))
        self.provenance = [ProvenanceRef.from_value(item) for item in self.provenance]

    def to_dict(self) -> dict[str, Any]:
        return {
            "from": self.from_id,
            "to": self.to_id,
            "relation": self.relation,
            "strength": self.strength,
            "createdBy": self.createdBy,
            "createdAt": self.createdAt,
            "provenance": [item.to_dict() for item in self.provenance],
        }


@dataclass
class MemoryProposal:
    agentId: str
    taskId: str
    purpose: Literal["write", "merge", "verify", "challenge"]
    nodes: list[MemoryNode] = field(default_factory=list)
    edges: list[MemoryEdge] = field(default_factory=list)
    rationale: str = ""
    provenance: list[ProvenanceRef] = field(default_factory=list)
    id: str | None = None
    submittedAt: str = field(default_factory=utc_now)
    status: ProposalStatus = "pending"
    decisionReason: str | None = None
    verifierId: str | None = None

    def __post_init__(self) -> None:
        if self.purpose not in {"write", "merge", "verify", "challenge"}:
            raise MemoryGraphError(f"unsupported memory proposal purpose: {self.purpose}")
        if not self.nodes and not self.edges:
            raise MemoryGraphError("memory proposal must include at least one node or edge")
        self.provenance = [ProvenanceRef.from_value(item) for item in self.provenance]
        if self.id is None:
            self.id = stable_hash(
                {
                    "agentId": self.agentId,
                    "taskId": self.taskId,
                    "purpose": self.purpose,
                    "nodes": [node.to_dict() for node in self.nodes],
                    "edges": [edge.to_dict() for edge in self.edges],
                    "submittedAt": self.submittedAt,
                }
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agentId": self.agentId,
            "taskId": self.taskId,
            "purpose": self.purpose,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "rationale": self.rationale,
            "provenance": [item.to_dict() for item in self.provenance],
            "submittedAt": self.submittedAt,
            "status": self.status,
            "decisionReason": self.decisionReason,
            "verifierId": self.verifierId,
        }


@dataclass(frozen=True)
class AppendOnlyEvent:
    id: str
    type: str
    actor: str
    createdAt: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "actor": self.actor,
            "createdAt": self.createdAt,
            "payload": dict(self.payload),
        }


class AppendOnlyEventLog:
    def __init__(self) -> None:
        self._events: list[AppendOnlyEvent] = []

    def append(self, event_type: str, actor: str, payload: dict[str, Any]) -> AppendOnlyEvent:
        event = AppendOnlyEvent(
            id=stable_hash({"type": event_type, "actor": actor, "payload": payload, "index": len(self._events)}),
            type=event_type,
            actor=actor,
            createdAt=utc_now(),
            payload=dict(payload),
        )
        self._events.append(event)
        return event

    def all(self) -> list[AppendOnlyEvent]:
        return list(self._events)

    def by_type(self, event_type: str) -> list[AppendOnlyEvent]:
        return [event for event in self._events if event.type == event_type]


class InMemoryVectorIndex:
    def __init__(self) -> None:
        self._vectors: dict[str, dict[str, float]] = {}

    def upsert(self, node: MemoryNode) -> None:
        self._vectors[node.id] = _tf_vector(node.text_for_embedding())

    def search(self, query: str, limit: int = 5) -> list[tuple[str, float]]:
        query_vector = _tf_vector(query)
        scored = [
            (node_id, _cosine(query_vector, vector))
            for node_id, vector in self._vectors.items()
        ]
        scored = [(node_id, score) for node_id, score in scored if score > 0]
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]


class DistributedMemoryGraph:
    """Canonical graph whose mutations flow through verifier-approved proposals."""

    def __init__(self, eventLog: AppendOnlyEventLog | None = None, vectorIndex: InMemoryVectorIndex | None = None) -> None:
        self.eventLog = eventLog or AppendOnlyEventLog()
        self.vectorIndex = vectorIndex or InMemoryVectorIndex()
        self.nodes: dict[str, MemoryNode] = {}
        self.edges: list[MemoryEdge] = []
        self.proposals: dict[str, MemoryProposal] = {}

    def submit_proposal(self, proposal: MemoryProposal) -> str:
        assert proposal.id is not None
        if proposal.id in self.proposals:
            raise MemoryGraphError(f"memory proposal already exists: {proposal.id}")
        self.proposals[proposal.id] = proposal
        self.eventLog.append("MEMORY_PROPOSAL_SUBMITTED", proposal.agentId, proposal.to_dict())
        return proposal.id

    def promote_proposal(self, proposal_id: str, *, verifierId: str, reason: str) -> MemoryProposal:
        proposal = self._proposal(proposal_id)
        if proposal.status != "pending":
            raise MemoryGraphError(f"memory proposal is not pending: {proposal_id}")
        self._validate_promotion(proposal)
        for node in proposal.nodes:
            self.nodes[node.id] = node
            self.vectorIndex.upsert(node)
            self.eventLog.append("MEMORY_NODE_PROMOTED", verifierId, {"proposalId": proposal_id, "node": node.to_dict()})
        for edge in proposal.edges:
            self.edges.append(edge)
            self.eventLog.append("MEMORY_EDGE_PROMOTED", verifierId, {"proposalId": proposal_id, "edge": edge.to_dict()})
        proposal.status = "promoted"
        proposal.verifierId = verifierId
        proposal.decisionReason = reason
        self.eventLog.append("MEMORY_PROPOSAL_PROMOTED", verifierId, proposal.to_dict())
        return proposal

    def reject_proposal(self, proposal_id: str, *, verifierId: str, reason: str) -> MemoryProposal:
        proposal = self._proposal(proposal_id)
        if proposal.status != "pending":
            raise MemoryGraphError(f"memory proposal is not pending: {proposal_id}")
        proposal.status = "rejected"
        proposal.verifierId = verifierId
        proposal.decisionReason = reason
        self.eventLog.append("MEMORY_PROPOSAL_REJECTED", verifierId, proposal.to_dict())
        return proposal

    def contradiction_edges(self) -> list[MemoryEdge]:
        return [edge for edge in self.edges if edge.relation == "contradicts"]

    def traverse(
        self,
        startNodeId: str,
        *,
        relations: set[MemoryRelation] | None = None,
        depth: int = 1,
    ) -> list[MemoryNode]:
        if startNodeId not in self.nodes:
            return []
        seen = {startNodeId}
        queue: deque[tuple[str, int]] = deque([(startNodeId, 0)])
        results: list[MemoryNode] = []
        while queue:
            current, current_depth = queue.popleft()
            if current_depth >= depth:
                continue
            for edge in self.edges:
                if relations and edge.relation not in relations:
                    continue
                neighbor = None
                if edge.from_id == current:
                    neighbor = edge.to_id
                elif edge.to_id == current:
                    neighbor = edge.from_id
                if neighbor and neighbor not in seen and neighbor in self.nodes:
                    seen.add(neighbor)
                    results.append(self.nodes[neighbor])
                    queue.append((neighbor, current_depth + 1))
        return results

    def hybrid_retrieve(
        self,
        query: str,
        *,
        startNodeId: str | None = None,
        relations: set[MemoryRelation] | None = None,
        depth: int = 1,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        scores: dict[str, float] = {}
        reasons: dict[str, set[str]] = {}
        for node_id, score in self.vectorIndex.search(query, limit=limit * 2):
            scores[node_id] = max(scores.get(node_id, 0.0), score)
            reasons.setdefault(node_id, set()).add("vector_similarity")
        if startNodeId:
            for node in self.traverse(startNodeId, relations=relations, depth=depth):
                scores[node.id] = max(scores.get(node.id, 0.0), 0.65)
                reasons.setdefault(node.id, set()).add("graph_traversal")
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:limit]
        return [
            {
                "node": self.nodes[node_id].to_dict(),
                "score": round(score, 3),
                "reasons": sorted(reasons.get(node_id, set())),
            }
            for node_id, score in ranked
            if node_id in self.nodes
        ]

    def _proposal(self, proposal_id: str) -> MemoryProposal:
        proposal = self.proposals.get(proposal_id)
        if proposal is None:
            raise MemoryGraphError(f"memory proposal not found: {proposal_id}")
        return proposal

    def _validate_promotion(self, proposal: MemoryProposal) -> None:
        proposed_node_ids = {node.id for node in proposal.nodes}
        canonical_or_proposed = set(self.nodes) | proposed_node_ids
        for node in proposal.nodes:
            if not node.provenance:
                raise MemoryGraphError(f"proposed node lacks provenance: {node.id}")
        for edge in proposal.edges:
            if not edge.provenance:
                raise MemoryGraphError(f"proposed edge lacks provenance: {edge.from_id}->{edge.to_id}")
            if edge.from_id not in canonical_or_proposed or edge.to_id not in canonical_or_proposed:
                raise MemoryGraphError(f"proposed edge references missing node: {edge.from_id}->{edge.to_id}")


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", text.lower())


def _tf_vector(text: str) -> dict[str, float]:
    counts: dict[str, float] = {}
    for token in _tokens(text):
        counts[token] = counts.get(token, 0.0) + 1.0
    return counts


def _cosine(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    common = set(left) & set(right)
    dot = sum(left[key] * right[key] for key in common)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
