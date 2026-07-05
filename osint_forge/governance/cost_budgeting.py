"""Mission step and token budgeting."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


ModelTier = Literal["cheap", "balanced", "strong"]


class BudgetExceededError(Exception):
    """Raised when a mission exceeds configured governance limits."""


@dataclass
class BudgetConfig:
    mission_total_tokens: int = 150_000
    mission_total_steps: int = 50
    per_agent_token_limit: dict[str, int] = field(default_factory=dict)

    def model_for_step(self, agent_name: str, complexity: Literal["low", "high"] = "low") -> ModelTier:
        if complexity == "high" or agent_name in {"analysis", "devils_advocate", "sat"}:
            return "strong"
        return "balanced"


@dataclass
class BudgetTracker:
    config: BudgetConfig = field(default_factory=BudgetConfig)
    used_tokens: int = 0
    used_steps: int = 0
    agent_usage: dict[str, int] = field(default_factory=dict)

    def record(self, agent: str, tokens: int = 0, step: bool = True) -> None:
        if step:
            self.used_steps += 1
        self.used_tokens += tokens
        self.agent_usage[agent] = self.agent_usage.get(agent, 0) + tokens
        if self.is_exhausted():
            raise BudgetExceededError("mission budget exhausted")

    def is_exhausted(self) -> bool:
        return self.used_tokens > self.config.mission_total_tokens or self.used_steps > self.config.mission_total_steps

    def remaining(self) -> dict[str, int]:
        return {
            "tokens_left": max(0, self.config.mission_total_tokens - self.used_tokens),
            "steps_left": max(0, self.config.mission_total_steps - self.used_steps),
        }

    def snapshot(self) -> dict[str, object]:
        return {
            "used_tokens": self.used_tokens,
            "used_steps": self.used_steps,
            "remaining": self.remaining(),
            "agent_usage": dict(self.agent_usage),
            "config": {
                "mission_total_tokens": self.config.mission_total_tokens,
                "mission_total_steps": self.config.mission_total_steps,
                "per_agent_token_limit": dict(self.config.per_agent_token_limit),
            },
        }

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, object]) -> BudgetTracker:
        config_data = snapshot.get("config", {}) if isinstance(snapshot.get("config"), dict) else {}
        tracker = cls(
            config=BudgetConfig(
                mission_total_tokens=int(config_data.get("mission_total_tokens", 150_000)),
                mission_total_steps=int(config_data.get("mission_total_steps", 50)),
                per_agent_token_limit=dict(config_data.get("per_agent_token_limit", {})),
            )
        )
        tracker.used_tokens = int(snapshot.get("used_tokens", 0))
        tracker.used_steps = int(snapshot.get("used_steps", 0))
        tracker.agent_usage = dict(snapshot.get("agent_usage", {}))
        return tracker
