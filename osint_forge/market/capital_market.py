"""Internal credit market for agent resource allocation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

from osint_forge.provenance import utc_now


BidPurpose = Literal["retrieve", "write", "merge", "verify", "challenge"]
OutcomeType = Literal["verified_useful", "failed_gate", "duplicate_work", "weak_claim", "false_positive", "wasteful_retrieval"]


class MarketError(Exception):
    """Raised when the internal agent market is misconfigured."""


@dataclass
class AgentAccount:
    agentId: str
    credits: float
    reputation: float
    specialties: list[str] = field(default_factory=list)
    recentAccuracy: float = 0.5
    falsePositiveRate: float = 0.0

    def __post_init__(self) -> None:
        if not self.agentId:
            raise MarketError("agent id is required")
        self.credits = max(0.0, float(self.credits))
        self.reputation = max(0.0, min(1.0, float(self.reputation)))
        self.recentAccuracy = max(0.0, min(1.0, float(self.recentAccuracy)))
        self.falsePositiveRate = max(0.0, min(1.0, float(self.falsePositiveRate)))


@dataclass
class MemoryBid:
    agentId: str
    taskId: str
    memoryQuery: str
    maxCredits: float
    claimedValue: float
    purpose: BidPurpose

    def __post_init__(self) -> None:
        if self.purpose not in {"retrieve", "write", "merge", "verify", "challenge"}:
            raise MarketError(f"unsupported bid purpose: {self.purpose}")
        if self.maxCredits <= 0:
            raise MarketError("bid maxCredits must be positive")
        if self.claimedValue <= 0:
            raise MarketError("bid claimedValue must be positive")
        if not self.memoryQuery.strip():
            raise MarketError("bid memoryQuery is required")


@dataclass
class MarketDecision:
    approved: bool
    allocatedMemoryIds: list[str]
    chargedCredits: float
    reason: str


@dataclass(frozen=True)
class CreditTransaction:
    agentId: str
    amount: float
    reason: str
    createdAt: str
    balanceAfter: float


class CreditLedger:
    def __init__(self) -> None:
        self.accounts: dict[str, AgentAccount] = {}
        self.transactions: list[CreditTransaction] = []

    def register(self, account: AgentAccount) -> None:
        self.accounts[account.agentId] = account

    def account(self, agent_id: str) -> AgentAccount:
        account = self.accounts.get(agent_id)
        if account is None:
            raise MarketError(f"agent account not found: {agent_id}")
        return account

    def credit(self, agent_id: str, amount: float, reason: str) -> None:
        if amount < 0:
            raise MarketError("credit amount must be non-negative")
        account = self.account(agent_id)
        account.credits += amount
        self.transactions.append(CreditTransaction(agent_id, amount, reason, utc_now(), account.credits))

    def debit(self, agent_id: str, amount: float, reason: str) -> None:
        if amount < 0:
            raise MarketError("debit amount must be non-negative")
        account = self.account(agent_id)
        if account.credits < amount:
            raise MarketError(f"insufficient credits for {agent_id}")
        account.credits -= amount
        self.transactions.append(CreditTransaction(agent_id, -amount, reason, utc_now(), account.credits))

    def reward_verified_useful_output(self, agent_id: str, quality_score: float = 1.0) -> None:
        self.credit(agent_id, 8.0 * _clamp01(quality_score), "verified useful output")

    def penalize(self, agent_id: str, outcome: OutcomeType, severity: float = 1.0) -> None:
        penalties = {
            "failed_gate": 4.0,
            "duplicate_work": 2.0,
            "weak_claim": 3.0,
            "false_positive": 6.0,
            "wasteful_retrieval": 2.5,
            "verified_useful": 0.0,
        }
        amount = min(self.account(agent_id).credits, penalties[outcome] * max(0.0, severity))
        if amount:
            self.debit(agent_id, amount, outcome.replace("_", " "))


class ReputationEngine:
    def __init__(self, ledger: CreditLedger, smoothing: float = 0.25) -> None:
        self.ledger = ledger
        self.smoothing = _clamp01(smoothing)

    def apply_verification_result(
        self,
        agent_id: str,
        *,
        useful: bool,
        qualityScore: float = 1.0,
        falsePositive: bool = False,
        duplicate: bool = False,
        failedGate: bool = False,
    ) -> AgentAccount:
        account = self.ledger.account(agent_id)
        quality = _clamp01(qualityScore)
        target_accuracy = quality if useful and not falsePositive and not failedGate else 0.0
        account.recentAccuracy = _moving(account.recentAccuracy, target_accuracy, self.smoothing)

        fp_target = 1.0 if falsePositive else 0.0
        account.falsePositiveRate = _moving(account.falsePositiveRate, fp_target, self.smoothing)

        if useful and not falsePositive and not failedGate:
            self.ledger.reward_verified_useful_output(agent_id, quality)
            account.reputation = min(1.0, account.reputation + 0.04 + 0.06 * quality)
        if failedGate:
            self.ledger.penalize(agent_id, "failed_gate", severity=1.0)
            account.reputation = max(0.0, account.reputation - 0.10)
        if duplicate:
            self.ledger.penalize(agent_id, "duplicate_work", severity=1.0)
            account.reputation = max(0.0, account.reputation - 0.04)
        if falsePositive:
            self.ledger.penalize(agent_id, "false_positive", severity=1.0)
            account.reputation = max(0.0, account.reputation - 0.15)
        if not useful and not failedGate and not duplicate and not falsePositive:
            self.ledger.penalize(agent_id, "weak_claim", severity=0.5)
            account.reputation = max(0.0, account.reputation - 0.03)
        return account


@dataclass
class BudgetGovernor:
    perTaskCreditLimit: float = 25.0
    perAgentOutstandingLimit: float = 40.0
    globalCreditLimit: float = 250.0
    task_spend: dict[str, float] = field(default_factory=dict)
    agent_spend: dict[str, float] = field(default_factory=dict)
    global_spend: float = 0.0

    def can_spend(self, agent_id: str, task_id: str, credits: float) -> tuple[bool, str]:
        if credits <= 0:
            return False, "charge must be positive"
        if self.global_spend + credits > self.globalCreditLimit:
            return False, "global market budget exhausted"
        if self.task_spend.get(task_id, 0.0) + credits > self.perTaskCreditLimit:
            return False, "task market budget exhausted"
        if self.agent_spend.get(agent_id, 0.0) + credits > self.perAgentOutstandingLimit:
            return False, "agent market budget exhausted"
        return True, "budget available"

    def charge(self, agent_id: str, task_id: str, credits: float) -> None:
        ok, reason = self.can_spend(agent_id, task_id, credits)
        if not ok:
            raise MarketError(reason)
        self.global_spend += credits
        self.task_spend[task_id] = self.task_spend.get(task_id, 0.0) + credits
        self.agent_spend[agent_id] = self.agent_spend.get(agent_id, 0.0) + credits


class AntiGoodhartGuard:
    """Rejects or penalizes bids that optimize easy metrics instead of truth quality."""

    def __init__(self) -> None:
        self._recent_queries: dict[tuple[str, str], int] = {}

    def evaluate(self, account: AgentAccount, bid: MemoryBid) -> tuple[bool, str, float]:
        key = (bid.agentId, _canonical_query(bid.memoryQuery))
        self._recent_queries[key] = self._recent_queries.get(key, 0) + 1
        if self._recent_queries[key] > 3:
            return False, "duplicate query pattern suggests metric gaming", 1.5
        if bid.claimedValue > max(10.0, bid.maxCredits * 6.0) and account.reputation < 0.75:
            return False, "claimed value is unsupported by reputation", 1.0
        if account.falsePositiveRate > 0.35 and bid.purpose in {"write", "merge"}:
            return False, "write-like bid blocked by elevated false positive rate", 1.25
        if account.recentAccuracy < 0.35 and bid.purpose in {"verify", "challenge"}:
            return False, "verifier bid blocked by low recent accuracy", 1.0
        return True, "no anti-goodhart issue", 0.0


MemoryAllocator = Callable[[MemoryBid], list[str]]


class MemoryAuctioneer:
    def __init__(
        self,
        ledger: CreditLedger,
        budgetGovernor: BudgetGovernor,
        antiGoodhartGuard: AntiGoodhartGuard | None = None,
        allocator: MemoryAllocator | None = None,
    ) -> None:
        self.ledger = ledger
        self.budgetGovernor = budgetGovernor
        self.antiGoodhartGuard = antiGoodhartGuard or AntiGoodhartGuard()
        self.allocator = allocator or (lambda _bid: [])

    def decide(self, bid: MemoryBid) -> MarketDecision:
        account = self.ledger.account(bid.agentId)
        ok, guard_reason, penalty = self.antiGoodhartGuard.evaluate(account, bid)
        if not ok:
            self.ledger.penalize(bid.agentId, "wasteful_retrieval", severity=penalty)
            return MarketDecision(False, [], 0.0, guard_reason)

        charge = self._clearing_price(account, bid)
        if account.credits < charge:
            return MarketDecision(False, [], 0.0, "insufficient agent credits")
        budget_ok, budget_reason = self.budgetGovernor.can_spend(bid.agentId, bid.taskId, charge)
        if not budget_ok:
            return MarketDecision(False, [], 0.0, budget_reason)

        allocated_ids = self.allocator(bid)
        if bid.purpose == "write":
            allocated_ids = ["memory_proposal_queue"]
            reason = "write privilege approved for proposal submission only"
        elif bid.purpose in {"merge", "verify", "challenge"} and not allocated_ids:
            allocated_ids = ["verifier_attention_queue"]
            reason = f"{bid.purpose} attention approved"
        else:
            reason = "bid approved"

        self.ledger.debit(bid.agentId, charge, f"market bid: {bid.purpose}")
        self.budgetGovernor.charge(bid.agentId, bid.taskId, charge)
        return MarketDecision(True, allocated_ids, charge, reason)

    def _clearing_price(self, account: AgentAccount, bid: MemoryBid) -> float:
        reliability = max(0.1, (account.reputation + account.recentAccuracy) / 2.0)
        false_positive_drag = 1.0 + account.falsePositiveRate
        base = {
            "retrieve": 1.0,
            "write": 3.0,
            "merge": 2.5,
            "verify": 2.0,
            "challenge": 2.0,
        }[bid.purpose]
        value_pressure = min(bid.maxCredits, max(base, bid.claimedValue / 10.0))
        price = value_pressure * false_positive_drag / reliability
        return round(min(bid.maxCredits, max(base, price)), 2)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _moving(current: float, target: float, smoothing: float) -> float:
    return round((1.0 - smoothing) * current + smoothing * target, 3)


def _canonical_query(query: str) -> str:
    return " ".join(query.lower().split())
