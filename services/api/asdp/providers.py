"""Agent execution adapters behind one contract (spec 6.3).

Three engines, any subset of which may be enabled (at least one):
  devin           autonomous Devin SaaS session. API mode only when the tenant grants it and
                  policy enables it; otherwise an explicit manual-launch handoff.
  copilot_vscode  attended GitHub Copilot Agent Mode in VS Code (developer handoff).
  claude_vscode   attended Claude Code in VS Code (developer handoff).

Attended engines never pretend to be unattended: the portal produces a task package, the
developer runs it in VS Code, and the result comes back as a commit / PR that CI validates.
No adapter scripts the VS Code UI.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from .config import ENGINES, Policy, Settings
from .core import DomainError, new_id


@dataclass
class AgentRunRequest:
    story_key: str
    task_key: str
    spec_digest: str
    branch: str
    allowed_paths: list[str]
    brief: str
    cost_limit: float
    timeout_minutes: int
    trace_id: str
    idempotency_key: str
    playbook_id: str = ""
    tool_policy: dict = field(default_factory=dict)
    test_plan_ref: str = ""


@dataclass
class AgentRunReceipt:
    mode: str  # api | manual | simulated
    state: str  # RUNNING | AWAITING_MANUAL_LAUNCH
    external_id: str = ""
    external_url: str = ""
    note: str = ""


@dataclass
class AgentRunStatus:
    state: str  # RUNNING | SUCCEEDED | FAILED | CANCELLED
    pr_url: str = ""
    commit: str = ""
    last_event: str = ""
    failure: str = ""
    cost: float = 0.0


class ProviderError(Exception):
    pass


class DevinProvider:
    name = "devin"
    label = "Devin SaaS"
    attended = False

    def __init__(self, settings: Settings, policy: Policy, client: httpx.Client | None = None):
        self.settings, self.policy = settings, policy
        self.http = client

    def _api(self) -> bool:
        return bool(self.policy.agents.devin_api_enabled and self.settings.devin_api_key)

    def capabilities(self) -> dict:
        api = self._api()
        return {"engine": self.name, "label": self.label, "attended": False, "api": api,
                "mode": "api" if api else "manual", "verified": False,
                "note": ("Session API enabled. Endpoint shape follows the public Devin API and must be verified against your tenant."
                         if api else
                         "Session API not entitled/enabled: the portal prepares a ready-to-run Devin brief and a developer "
                         "launches the session and pastes its URL. No unattended dispatch is claimed.")}

    def _client(self) -> httpx.Client:
        if self.http is None:
            self.http = httpx.Client(base_url=self.settings.devin_api_base, timeout=30,
                                     headers={"Authorization": f"Bearer {self.settings.devin_api_key}"})
        return self.http

    def create_run(self, req: AgentRunRequest) -> AgentRunReceipt:
        if not self._api():
            return AgentRunReceipt(mode="manual", state="AWAITING_MANUAL_LAUNCH",
                                   note="Launch this brief in Devin, then attach the session URL.")
        try:
            body = {"prompt": req.brief, "idempotent": True, "title": f"{req.story_key} {req.task_key}",
                    "tags": [req.story_key, req.idempotency_key[:40]]}
            if req.playbook_id:  # the matching playbook in the Devin org, when one is mapped in policy
                body["playbook_id"] = req.playbook_id
            r = self._client().post("/sessions", json=body)
        except httpx.HTTPError as e:
            raise ProviderError(str(e)) from e
        if r.status_code in (401, 403):
            raise ProviderError(f"Devin API rejected the credentials ({r.status_code}); check entitlement")
        if r.status_code >= 400:
            raise ProviderError(f"Devin API {r.status_code}: {r.text[:200]}")
        d = r.json()
        return AgentRunReceipt(mode="api", state="RUNNING", external_id=str(d.get("session_id", "")),
                               external_url=d.get("url", ""))

    def get_run(self, external_id: str) -> AgentRunStatus | None:
        if not self._api() or not external_id:
            return None
        try:
            r = self._client().get(f"/sessions/{external_id}")
        except httpx.HTTPError as e:
            raise ProviderError(str(e)) from e
        if r.status_code >= 400:
            raise ProviderError(f"Devin API {r.status_code}: {r.text[:200]}")
        d = r.json()
        status = str(d.get("status_enum") or d.get("status") or "").lower()
        pr = (d.get("pull_request") or {}).get("url", "")
        # A finished session is NOT a success signal on its own: success needs a commit, which
        # arrives through the run report / PR ingestion and is then proven by CI.
        if status in ("expired", "failed", "error"):
            return AgentRunStatus(state="FAILED", pr_url=pr, last_event=status, failure=f"Devin session {status}")
        return AgentRunStatus(state="RUNNING", pr_url=pr, last_event=status or "working")

    def cancel_run(self, external_id: str) -> None:
        # No verified cancel endpoint is assumed; the run is cancelled in the portal and the
        # developer is told to stop the session in Devin.
        return None


class _AttendedVSCode:
    attended = True
    name = ""
    label = ""
    instructions_file = ""
    how_to = ""

    def capabilities(self) -> dict:
        return {"engine": self.name, "label": self.label, "attended": True, "api": False, "mode": "manual",
                "verified": True,
                "note": f"Attended handoff. {self.how_to} Results return as a commit/PR; the portal never drives the editor."}

    def create_run(self, req: AgentRunRequest) -> AgentRunReceipt:
        return AgentRunReceipt(mode="manual", state="AWAITING_MANUAL_LAUNCH", note=self.how_to)

    def get_run(self, external_id: str) -> AgentRunStatus | None:
        return None

    def cancel_run(self, external_id: str) -> None:
        return None


class CopilotVSCodeProvider(_AttendedVSCode):
    name = "copilot_vscode"
    label = "GitHub Copilot (VS Code Agent Mode)"
    instructions_file = ".github/copilot-instructions.md"
    how_to = ("Check out the branch, open Copilot Chat in Agent Mode, paste the task package, review the edits, "
              "run the listed tests and push.")


class ClaudeVSCodeProvider(_AttendedVSCode):
    name = "claude_vscode"
    label = "Claude Code (VS Code)"
    instructions_file = "CLAUDE.md"
    how_to = ("Check out the branch, open the Claude Code panel in VS Code, paste the task package (or save it as "
              ".asdp/task.md and ask Claude to follow it), review the edits, run the listed tests and push.")


class SimulatedProvider:
    """Local demo only (policy agents.simulated_enabled). Runs are labelled SIMULATED everywhere
    and produce no real code; the commit id is a clearly fake 'sim-' value."""
    name = "simulated"
    label = "Simulated (demo only)"
    attended = False

    def capabilities(self) -> dict:
        return {"engine": self.name, "label": self.label, "attended": False, "api": True, "mode": "simulated",
                "verified": True, "note": "Demo stand-in. Produces no code."}

    def create_run(self, req: AgentRunRequest) -> AgentRunReceipt:
        return AgentRunReceipt(mode="simulated", state="RUNNING", external_id=new_id("sim-"))

    def get_run(self, external_id: str) -> AgentRunStatus | None:
        return AgentRunStatus(state="SUCCEEDED", commit="sim-" + external_id[-12:], cost=1.5,
                              pr_url="", last_event="simulated completion")

    def cancel_run(self, external_id: str) -> None:
        return None


class ProviderRegistry:
    def __init__(self, settings: Settings, policy: Policy):
        self.policy = policy
        self.providers = {"devin": DevinProvider(settings, policy), "copilot_vscode": CopilotVSCodeProvider(),
                          "claude_vscode": ClaudeVSCodeProvider(), "simulated": SimulatedProvider()}

    def enabled(self, overrides: dict | None = None) -> dict[str, bool]:
        state = dict(self.policy.agents.engines)
        state.update({k: bool(v) for k, v in (overrides or {}).items() if k in ENGINES})
        return {e: bool(state.get(e)) for e in ENGINES}

    def get(self, engine: str, overrides: dict | None = None):
        if engine == "simulated":
            if not self.policy.agents.simulated_enabled:
                raise DomainError(409, "ENGINE_DISABLED", "The simulated engine is disabled by policy")
            return self.providers[engine]
        if engine not in ENGINES:
            raise DomainError(400, "UNKNOWN_ENGINE", f"Unknown engine '{engine}'")
        if not self.enabled(overrides)[engine]:
            raise DomainError(409, "ENGINE_DISABLED", f"Engine '{engine}' is not enabled for this workspace")
        return self.providers[engine]

    def resolve(self, preferred: str, attended: bool, overrides: dict | None = None) -> str:
        """Pick the preferred engine if enabled, otherwise the best enabled fallback."""
        on = self.enabled(overrides)
        if preferred == "simulated" and self.policy.agents.simulated_enabled:
            return preferred
        if on.get(preferred):
            return preferred
        a = self.policy.agents
        order = ([a.attended_engine, "claude_vscode", "copilot_vscode", a.primary, "devin"] if attended
                 else [a.primary, "devin", a.attended_engine, "claude_vscode", "copilot_vscode"])
        for e in order:
            if on.get(e):
                return e
        raise DomainError(409, "NO_ENGINE", "No agent engine is enabled; enable at least one of devin, copilot_vscode, claude_vscode")

    def matrix(self, overrides: dict | None = None) -> list[dict]:
        on = self.enabled(overrides)
        out = [{**self.providers[e].capabilities(), "enabled": on[e]} for e in ENGINES]
        out.append({"engine": "copilot_sdk", "label": "Copilot SDK / CLI runner", "attended": False, "api": False,
                    "mode": "unavailable", "verified": False, "enabled": False,
                    "note": "Not implemented. Requires separate enterprise authorization, runner identity and audit (spec 6.2)."})
        if self.policy.agents.simulated_enabled:
            out.append({**self.providers["simulated"].capabilities(), "enabled": True})
        return out
