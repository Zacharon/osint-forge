"""Governed async tool registry."""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from osint_forge.governance.pii_sanitizer import PIISanitizer, ToolRiskLevel
from osint_forge.governance.schema_validator import CaseFileManifest, SchemaDriftError
from osint_forge.orchestrator.mission_store import MissionStore


ToolCallable = Callable[..., dict[str, Any]] | Callable[..., Awaitable[dict[str, Any]]]


class GovernedToolError(Exception):
    """Raised when tool execution or governance validation fails."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    func: ToolCallable
    output_schema: str
    risk_level: ToolRiskLevel = "medium"
    timeout_seconds: float = 30.0


class ToolRegistry:
    """Single execution choke point for all tools."""

    def __init__(self, sanitizer: PIISanitizer, manifest: CaseFileManifest, store: MissionStore) -> None:
        self.sanitizer = sanitizer
        self.manifest = manifest
        self.store = store
        self._tools: dict[str, ToolSpec] = {}
        self._active_tasks: set[asyncio.Task[Any]] = set()

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def names(self) -> list[str]:
        return sorted(self._tools)

    async def run_governed_tool(self, mission_id: str, tool_name: str, **kwargs: Any) -> dict[str, Any]:
        spec = self._tools.get(tool_name)
        if spec is None:
            raise GovernedToolError(f"unknown tool: {tool_name}")

        started = time.time()
        execution = self.store.get_tool_execution(mission_id, tool_name, kwargs)
        cached_output = self.store.get_tool_output(mission_id, tool_name, kwargs)
        if execution and execution["status"] == "completed":
            if cached_output is None:
                self.store.mark_tool_execution(
                    mission_id,
                    tool_name,
                    kwargs,
                    "blocked",
                    error="completed execution is missing cached output",
                    retryable=False,
                )
                raise GovernedToolError(f"completed tool execution is missing cached output: {tool_name}")
            output_check = self.sanitizer.check_payload(cached_output, tool_risk_level=spec.risk_level)
            if not output_check.is_safe:
                self.store.log_event(
                    mission_id,
                    "PII_REJECTED_CACHED_TOOL_OUTPUT",
                    {"tool_name": tool_name, "reason": output_check.reason, "log_entry": output_check.log_entry},
                )
                self.store.mark_tool_execution(
                    mission_id,
                    tool_name,
                    kwargs,
                    "blocked",
                    error=output_check.reason,
                    retryable=False,
                )
                raise GovernedToolError(output_check.reason)
            self.manifest.validate_handoff(spec.output_schema, cached_output)
            return cached_output

        if execution and execution["status"] in {"failed", "blocked"} and not execution["retryable"]:
            raise GovernedToolError(f"tool execution is {execution['status']} and not retryable: {tool_name}")

        self.store.mark_tool_execution(mission_id, tool_name, kwargs, "pending", retryable=True)
        arg_check = self.sanitizer.check_payload(kwargs, tool_risk_level=spec.risk_level)
        if not arg_check.is_safe:
            self.store.log_event(
                mission_id,
                "PII_REJECTED_TOOL_ARGUMENTS",
                {"tool_name": tool_name, "reason": arg_check.reason, "log_entry": arg_check.log_entry},
            )
            self.store.record_tool_run(
                mission_id=mission_id,
                tool_name=tool_name,
                status="PII_REJECTED",
                input_payload=kwargs,
                error=arg_check.reason,
                started_at=started,
            )
            self.store.mark_tool_execution(
                mission_id,
                tool_name,
                kwargs,
                "blocked",
                error=arg_check.reason,
                retryable=False,
            )
            raise GovernedToolError(arg_check.reason)

        if cached_output is not None:
            output_check = self.sanitizer.check_payload(cached_output, tool_risk_level=spec.risk_level)
            if not output_check.is_safe:
                self.store.log_event(
                    mission_id,
                    "PII_REJECTED_CACHED_TOOL_OUTPUT",
                    {"tool_name": tool_name, "reason": output_check.reason, "log_entry": output_check.log_entry},
                )
                self.store.mark_tool_execution(
                    mission_id,
                    tool_name,
                    kwargs,
                    "blocked",
                    error=output_check.reason,
                    retryable=False,
                )
                raise GovernedToolError(output_check.reason)
            self.manifest.validate_handoff(spec.output_schema, cached_output)
            self.store.mark_tool_execution(
                mission_id,
                tool_name,
                kwargs,
                "completed",
                output_hash=self.store.payload_hash(cached_output),
                retryable=False,
            )
            return cached_output

        try:
            self.store.mark_tool_execution(mission_id, tool_name, kwargs, "running", retryable=True)
            if inspect.iscoroutinefunction(spec.func):
                coro = spec.func(**kwargs)
            else:
                coro = asyncio.to_thread(spec.func, **kwargs)
            task = asyncio.create_task(asyncio.wait_for(coro, timeout=spec.timeout_seconds))
            self._active_tasks.add(task)
            try:
                output = await task
            finally:
                self._active_tasks.discard(task)

            output_check = self.sanitizer.check_payload(output, tool_risk_level=spec.risk_level)
            if not output_check.is_safe:
                self.store.log_event(
                    mission_id,
                    "PII_REJECTED_TOOL_OUTPUT",
                    {"tool_name": tool_name, "reason": output_check.reason, "log_entry": output_check.log_entry},
                )
                self.store.mark_tool_execution(
                    mission_id,
                    tool_name,
                    kwargs,
                    "blocked",
                    error=output_check.reason,
                    retryable=False,
                )
                raise GovernedToolError(output_check.reason)

            self.manifest.validate_handoff(spec.output_schema, output)
            self.store.save_tool_output(mission_id, tool_name, kwargs, output)
            self.store.record_tool_run(
                mission_id=mission_id,
                tool_name=tool_name,
                status="SUCCEEDED",
                input_payload=kwargs,
                output_payload=output,
                started_at=started,
            )
            return output
        except asyncio.TimeoutError as exc:
            self.store.record_tool_run(
                mission_id=mission_id,
                tool_name=tool_name,
                status="TIMEOUT",
                input_payload=kwargs,
                error=str(exc),
                started_at=started,
            )
            self.store.mark_tool_execution(
                mission_id,
                tool_name,
                kwargs,
                "failed",
                error=f"timeout: {exc}",
                retryable=True,
            )
            raise GovernedToolError(f"tool timed out: {tool_name}") from exc
        except SchemaDriftError as exc:
            self.store.record_tool_run(
                mission_id=mission_id,
                tool_name=tool_name,
                status="SCHEMA_VALIDATION_FAILED",
                input_payload=kwargs,
                error=str(exc),
                started_at=started,
            )
            self.store.mark_tool_execution(
                mission_id,
                tool_name,
                kwargs,
                "blocked",
                error=str(exc),
                retryable=False,
            )
            raise GovernedToolError(str(exc)) from exc
        except GovernedToolError as exc:
            latest = self.store.get_tool_execution(mission_id, tool_name, kwargs)
            if latest is None or latest["status"] not in {"blocked", "failed"}:
                self.store.mark_tool_execution(
                    mission_id,
                    tool_name,
                    kwargs,
                    "blocked",
                    error=str(exc),
                    retryable=False,
                )
            raise
        except Exception as exc:
            status = "blocked" if exc.__class__.__name__.endswith("ConnectorError") else "failed"
            retryable = status == "failed"
            self.store.record_tool_run(
                mission_id=mission_id,
                tool_name=tool_name,
                status=status.upper(),
                input_payload=kwargs,
                error=str(exc),
                started_at=started,
            )
            self.store.mark_tool_execution(
                mission_id,
                tool_name,
                kwargs,
                status,
                error=str(exc),
                retryable=retryable,
            )
            if status == "blocked":
                raise GovernedToolError(str(exc)) from exc
            raise

    async def cancel_active(self) -> None:
        for task in list(self._active_tasks):
            task.cancel()
        if self._active_tasks:
            await asyncio.gather(*self._active_tasks, return_exceptions=True)
