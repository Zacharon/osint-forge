from __future__ import annotations

import unittest

from osint_forge.memory import DistributedMemoryGraph, MemoryEdge, MemoryNode, MemoryProposal
from osint_forge.provenance import ProvenanceRef, utc_now


def prov(locator: str = "fixture:1") -> ProvenanceRef:
    return ProvenanceRef(source="test", locator=locator, observedAt=utc_now(), contentHash="hash-" + locator)


class DistributedMemoryGraphTests(unittest.TestCase):
    def test_canonical_promotion_preserves_provenance_and_logs_mutations(self) -> None:
        graph = DistributedMemoryGraph()
        node = MemoryNode(
            id="claim-a",
            type="claim",
            title="Port delay claim",
            summary="Public sources report port delay increases.",
            createdAt=utc_now(),
            confidence=0.68,
            provenance=[prov("claim-a")],
        )
        proposal = MemoryProposal(agentId="agent-1", taskId="task-1", purpose="write", nodes=[node])
        proposal_id = graph.submit_proposal(proposal)

        graph.promote_proposal(proposal_id, verifierId="verifier-1", reason="provenance checked")

        self.assertIn("claim-a", graph.nodes)
        self.assertEqual(graph.nodes["claim-a"].provenance[0].locator, "claim-a")
        self.assertEqual(proposal.status, "promoted")
        self.assertTrue(graph.eventLog.by_type("MEMORY_PROPOSAL_SUBMITTED"))
        self.assertTrue(graph.eventLog.by_type("MEMORY_NODE_PROMOTED"))

    def test_contradiction_edges_are_promoted_into_canonical_graph(self) -> None:
        graph = DistributedMemoryGraph()
        nodes = [
            MemoryNode("claim-a", "claim", "Delay", "Port delays increased.", utc_now(), 0.7, [prov("a")]),
            MemoryNode("claim-b", "claim", "Normal", "Port operations are normal.", utc_now(), 0.5, [prov("b")]),
        ]
        edge = MemoryEdge("claim-a", "claim-b", "contradicts", 0.82, "agent-2", [prov("edge")])
        proposal = MemoryProposal(agentId="agent-2", taskId="task-2", purpose="challenge", nodes=nodes, edges=[edge])

        graph.promote_proposal(graph.submit_proposal(proposal), verifierId="verifier-1", reason="conflict verified")

        contradictions = graph.contradiction_edges()
        self.assertEqual(len(contradictions), 1)
        self.assertEqual(contradictions[0].relation, "contradicts")
        self.assertEqual(contradictions[0].provenance[0].locator, "edge")

    def test_proposal_rejection_does_not_mutate_canonical_memory(self) -> None:
        graph = DistributedMemoryGraph()
        node = MemoryNode("claim-reject", "claim", "Weak", "Unsupported claim.", utc_now(), 0.3, [prov("weak")])
        proposal = MemoryProposal(agentId="agent-3", taskId="task-3", purpose="write", nodes=[node])

        graph.reject_proposal(graph.submit_proposal(proposal), verifierId="verifier-1", reason="single weak source")

        self.assertEqual(proposal.status, "rejected")
        self.assertNotIn("claim-reject", graph.nodes)
        self.assertTrue(graph.eventLog.by_type("MEMORY_PROPOSAL_REJECTED"))

    def test_hybrid_retrieval_uses_vector_similarity_and_graph_traversal(self) -> None:
        graph = DistributedMemoryGraph()
        source = MemoryNode("src-1", "source", "GDELT article", "Port delays near Gdansk.", utc_now(), 0.8, [prov("src")])
        claim = MemoryNode("claim-1", "claim", "Gdansk delay", "Port access delays increased.", utc_now(), 0.7, [prov("claim")])
        edge = MemoryEdge("src-1", "claim-1", "supports", 0.9, "agent-1", [prov("support")])
        proposal = MemoryProposal(agentId="agent-1", taskId="task-4", purpose="write", nodes=[source, claim], edges=[edge])
        graph.promote_proposal(graph.submit_proposal(proposal), verifierId="verifier-1", reason="valid")

        results = graph.hybrid_retrieve("Gdansk port delays", startNodeId="src-1", relations={"supports"}, depth=1)

        ids = {item["node"]["id"] for item in results}
        self.assertIn("src-1", ids)
        self.assertIn("claim-1", ids)


if __name__ == "__main__":
    unittest.main()
