"""Static agent role definitions for v3.2.

These are specifications, not autonomous spawning logic.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentRoleSpec:
    name: str
    purpose: str
    inputs: list[str]
    outputs: list[str]
    allowed_tools: list[str]
    required_gates: list[str]
    failure_behavior: str
    logging_requirements: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "purpose": self.purpose,
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "allowed_tools": list(self.allowed_tools),
            "required_gates": list(self.required_gates),
            "failure_behavior": self.failure_behavior,
            "logging_requirements": list(self.logging_requirements),
        }


def default_agent_roles() -> list[AgentRoleSpec]:
    return [
        AgentRoleSpec(
            name="SourceComplianceAgent",
            purpose="Verify source registry policy, allowed use, auth, rate limit, and retention constraints before collection.",
            inputs=["source_registry_entry", "mission_scope", "operator_flags"],
            outputs=["source_compliance_decision"],
            allowed_tools=["source_registry"],
            required_gates=["legal_source_registry", "operator_approval", "pre_ingestion_checklist"],
            failure_behavior="Fail closed and log LIVE_INGESTION_DENIED.",
            logging_requirements=["source_id", "decision", "reason"],
        ),
        AgentRoleSpec(
            name="IngestionAgent",
            purpose="Collect mission-scoped public records through approved adapters only.",
            inputs=["source_compliance_decision", "query", "adapter_client"],
            outputs=["raw_payload_hashes", "normalized_event_proposals"],
            allowed_tools=["GDELTPollingAdapter", "official_api_clients"],
            required_gates=["source_compliance", "pii_raw_scan"],
            failure_behavior="Drop blocked records, persist no unsafe payload, and log reason.",
            logging_requirements=["source_id", "payload_hash", "event_fingerprint", "blocked_reason"],
        ),
        AgentRoleSpec(
            name="GeoEnrichmentAgent",
            purpose="Normalize public location signals into map-ready lat/lon/place fields.",
            inputs=["normalized_event"],
            outputs=["geo_enriched_event"],
            allowed_tools=["geocoder_when_licensed", "event_normalizer"],
            required_gates=["provenance_required", "confidence_required"],
            failure_behavior="Keep event without coordinates and mark confidence degraded.",
            logging_requirements=["event_id", "geo_confidence", "source_fields"],
        ),
        AgentRoleSpec(
            name="DedupCorroborationAgent",
            purpose="Suppress duplicates and identify corroborating or contradictory source clusters.",
            inputs=["normalized_events", "memory_graph"],
            outputs=["dedupe_decisions", "corroboration_links"],
            allowed_tools=["source_ancestry", "memory_graph_retrieval"],
            required_gates=["deterministic_fingerprint", "provenance_required"],
            failure_behavior="Fail to conservative duplicate suppression only when fingerprint matches.",
            logging_requirements=["event_fingerprint", "decision", "linked_event_ids"],
        ),
        AgentRoleSpec(
            name="SourceBiasAgent",
            purpose="Estimate provenance-backed public source/creator bias indicators without verdicts or dossiers.",
            inputs=["CreatorSourceProfile"],
            outputs=["SourceBiasEstimate"],
            allowed_tools=["SourceBiasEstimator"],
            required_gates=["provenance_required", "privacy_review"],
            failure_behavior="Reject estimates lacking provenance or containing protected-class/private-person profiling.",
            logging_requirements=["profile_id", "estimate_confidence", "rejection_reason"],
        ),
        AgentRoleSpec(
            name="PrivacyReviewAgent",
            purpose="Block unnecessary PII, doxxing, private groups, individual targeting, and raw sensitive dashboard exposure.",
            inputs=["raw_payload", "normalized_event", "dashboard_event"],
            outputs=["privacy_decision"],
            allowed_tools=["PIISanitizer"],
            required_gates=["pii_scan", "storage_minimization"],
            failure_behavior="Block persistence or dashboard release and log safe metadata only.",
            logging_requirements=["event_id", "payload_hash", "blocked_attribute"],
        ),
        AgentRoleSpec(
            name="MapCuratorAgent",
            purpose="Select sanitized normalized events for live map display and degraded-source indicators.",
            inputs=["normalized_events", "source_health"],
            outputs=["dashboard_events"],
            allowed_tools=["dashboard_events_response"],
            required_gates=["sanitized_output_only"],
            failure_behavior="Hide unsafe fields and show degraded/error state.",
            logging_requirements=["event_count", "filters", "excluded_fields"],
        ),
        AgentRoleSpec(
            name="ReportAgent",
            purpose="Draft cited findings with confidence, provenance, contradictions, and uncertainty.",
            inputs=["memory_graph", "dashboard_events", "source_bias_estimates"],
            outputs=["draft_report"],
            allowed_tools=["report_builder", "memory_graph_retrieval"],
            required_gates=["SAT_preconditions", "human_review"],
            failure_behavior="Do not generate final report until review gates pass.",
            logging_requirements=["artifact_hash", "citation_count", "review_status"],
        ),
    ]
