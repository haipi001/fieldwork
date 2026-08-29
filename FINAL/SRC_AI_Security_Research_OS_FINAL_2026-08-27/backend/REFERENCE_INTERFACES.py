from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, Any, Literal

class SecurityMode(str, Enum):
    TRADITIONAL_SRC = "traditional_src"
    WEB3 = "web3"

@dataclass(frozen=True)
class Capability:
    name: str
    risk: Literal["passive","low","medium","high"]
    domain: str
    deterministic: bool = False

@dataclass
class ToolResultEnvelope:
    adapter: str
    invocation_id: str
    status: Literal["success","failed","timeout","blocked"]
    observations: list[dict[str, Any]] = field(default_factory=list)
    artifact_refs: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

class DomainRuntime(Protocol):
    mode: SecurityMode
    def supported_target_kinds(self) -> set[str]: ...
    def capabilities(self) -> list[Capability]: ...
    async def build_target_model(self, engagement_id: str) -> dict[str, Any]: ...
    async def propose_analysis_tasks(self, engagement_id: str) -> list[dict[str, Any]]: ...
    async def assess_impact(self, finding_id: str) -> dict[str, Any]: ...

class ToolAdapter(Protocol):
    name: str
    capabilities: set[str]
    async def health(self) -> dict[str, Any]: ...
    async def execute(self, task: dict[str, Any]) -> ToolResultEnvelope: ...

class VerificationOracle(Protocol):
    name: str
    async def verify(self, candidate_id: str) -> dict[str, Any]: ...

class ReportAdapter(Protocol):
    platform: str
    def completeness(self, finding: dict[str, Any], program: dict[str, Any] | None) -> dict[str, Any]: ...
    def render(self, finding: dict[str, Any], program: dict[str, Any] | None) -> dict[str, str]: ...
