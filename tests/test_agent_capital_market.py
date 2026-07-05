from __future__ import annotations

import unittest

from osint_forge.market import (
    AgentAccount,
    BudgetGovernor,
    CreditLedger,
    MemoryAuctioneer,
    MemoryBid,
    ReputationEngine,
)


class AgentCapitalMarketTests(unittest.TestCase):
    def test_bid_approval_charges_credits_and_allocates_memory(self) -> None:
        ledger = CreditLedger()
        ledger.register(AgentAccount("agent-a", credits=20, reputation=0.9, recentAccuracy=0.85))
        auctioneer = MemoryAuctioneer(
            ledger,
            BudgetGovernor(perTaskCreditLimit=10, perAgentOutstandingLimit=10, globalCreditLimit=50),
            allocator=lambda _bid: ["mem-1", "mem-2"],
        )

        decision = auctioneer.decide(
            MemoryBid(
                agentId="agent-a",
                taskId="task-a",
                memoryQuery="gdansk port delay",
                maxCredits=5,
                claimedValue=12,
                purpose="retrieve",
            )
        )

        self.assertTrue(decision.approved)
        self.assertEqual(decision.allocatedMemoryIds, ["mem-1", "mem-2"])
        self.assertGreater(decision.chargedCredits, 0)
        self.assertLess(ledger.account("agent-a").credits, 20)

    def test_budget_exhaustion_rejects_bid_without_charge(self) -> None:
        ledger = CreditLedger()
        ledger.register(AgentAccount("agent-b", credits=20, reputation=0.9, recentAccuracy=0.9))
        auctioneer = MemoryAuctioneer(
            ledger,
            BudgetGovernor(perTaskCreditLimit=1, perAgentOutstandingLimit=10, globalCreditLimit=50),
            allocator=lambda _bid: ["mem-1"],
        )

        decision = auctioneer.decide(
            MemoryBid("agent-b", "task-b", "high value retrieval", maxCredits=5, claimedValue=20, purpose="retrieve")
        )

        self.assertFalse(decision.approved)
        self.assertEqual(decision.reason, "task market budget exhausted")
        self.assertEqual(ledger.account("agent-b").credits, 20)

    def test_reputation_changes_after_verified_and_false_positive_outcomes(self) -> None:
        ledger = CreditLedger()
        ledger.register(AgentAccount("agent-c", credits=10, reputation=0.5, recentAccuracy=0.5))
        engine = ReputationEngine(ledger)

        improved = engine.apply_verification_result("agent-c", useful=True, qualityScore=1.0)
        self.assertGreater(improved.credits, 10)
        self.assertGreater(improved.reputation, 0.5)
        self.assertGreater(improved.recentAccuracy, 0.5)

        before_false_positive = improved.credits
        degraded = engine.apply_verification_result("agent-c", useful=False, falsePositive=True)
        self.assertLess(degraded.credits, before_false_positive)
        self.assertGreater(degraded.falsePositiveRate, 0)
        self.assertLess(degraded.reputation, improved.reputation + 0.001)

    def test_failed_agent_penalty_reduces_credits_and_reputation(self) -> None:
        ledger = CreditLedger()
        ledger.register(AgentAccount("agent-d", credits=12, reputation=0.7, recentAccuracy=0.6))
        engine = ReputationEngine(ledger)

        result = engine.apply_verification_result("agent-d", useful=False, failedGate=True)

        self.assertLess(result.credits, 12)
        self.assertLess(result.reputation, 0.7)

    def test_write_bid_is_proposal_based_not_direct_memory_mutation(self) -> None:
        ledger = CreditLedger()
        ledger.register(AgentAccount("agent-e", credits=20, reputation=0.8, recentAccuracy=0.8))
        auctioneer = MemoryAuctioneer(
            ledger,
            BudgetGovernor(perTaskCreditLimit=20, perAgentOutstandingLimit=20, globalCreditLimit=50),
            allocator=lambda _bid: ["canonical-memory-id"],
        )

        decision = auctioneer.decide(
            MemoryBid("agent-e", "task-e", "write claim proposal", maxCredits=8, claimedValue=18, purpose="write")
        )

        self.assertTrue(decision.approved)
        self.assertEqual(decision.allocatedMemoryIds, ["memory_proposal_queue"])
        self.assertIn("proposal submission only", decision.reason)


if __name__ == "__main__":
    unittest.main()
