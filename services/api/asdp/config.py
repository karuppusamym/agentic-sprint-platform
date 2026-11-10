"""Settings (environment) and workflow policy (config/policy.yaml)."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

ROOT = Path(__file__).resolve().parents[3]


class JiraPolicy(BaseModel):
    source: str = "mock"  # mock | cloud | datacenter
    project: str = "WF"
    # Discovery reads the whole active sprint so the board can show *why* a story
    # is not eligible. Eligibility (status + label) is applied by policy below.
    discovery_jql: str = "project = WF AND sprint in openSprints() ORDER BY priority DESC"
    eligible_statuses: list[str] = ["In Progress"]
    require_label: str | None = "ai-ready"
    reconcile_minutes: int = 5
    auto_close: bool = False
    write_enabled: bool = True
    done_transition: str = "Done"
    supported_attachment_types: list[str] = ["image/png", "image/jpeg", "application/pdf", "text/plain"]


class PrototypePolicy(BaseModel):
    mandatory_for: list[str] = ["ui", "ux", "navigation", "interactive_behavior"]
    auto_refinement_max_attempts: int = 3
    require_human_approval: bool = True
    require_browser_pass: bool = False  # real-browser Playwright run; off until a runner is provisioned
    preview_ttl_seconds: int = 900


ENGINES = ("devin", "copilot_vscode", "claude_vscode")


class AgentsPolicy(BaseModel):
    # Configure all three engines or just one; at least one must be enabled.
    engines: dict[str, bool] = Field(
        default_factory=lambda: {"devin": True, "copilot_vscode": True, "claude_vscode": True})
    primary: str = "devin"  # preferred engine for autonomous multi-file work
    attended_engine: str = "claude_vscode"  # preferred engine for attended fixes / rework in VS Code
    devin_api_enabled: bool = False  # true only after tenant admin confirms entitlement
    # playbook file id -> Devin playbook id in your Devin org (sent as playbook_id in API mode)
    devin_playbooks: dict[str, str] = Field(default_factory=dict)
    copilot_sdk_enabled: bool = False  # true only after approval / POC
    simulated_enabled: bool = False  # local demo only; runs are labelled SIMULATED
    max_active_sessions: int = 2
    enforce_path_ownership: bool = True
    per_run_timeout_minutes: int = 120
    auto_repair_max_attempts: int = 3
    path_ownership: dict[str, str] = Field(
        default_factory=lambda: {
            "ui": "apps/web/src",
            "backend": "services",
            "data": "database/migrations",
            "contracts": "packages/contracts",
            "integration": "tests/integration",
        }
    )

    @model_validator(mode="after")
    def _check_engines(self) -> "AgentsPolicy":
        validate_engines(self.engines)
        return self


def validate_engines(engines: dict[str, bool]) -> None:
    unknown = set(engines) - set(ENGINES)
    if unknown:
        raise ValueError(f"unknown engines: {sorted(unknown)}; valid: {list(ENGINES)}")
    if not any(engines.get(e) for e in ENGINES):
        raise ValueError("at least one engine (devin, copilot_vscode, claude_vscode) must be enabled")


class GenerationPolicy(BaseModel):
    provider: str = "template"  # template | claude
    model: str = "claude-opus-5-5"
    effort: str = "high"  # low | medium | high | xhigh | max
    max_output_tokens: int = 64000

    @model_validator(mode="after")
    def _check(self) -> "GenerationPolicy":
        if self.provider not in ("template", "claude"):
            raise ValueError("generation.provider must be 'template' or 'claude'")
        return self


class QualityPolicy(BaseModel):
    require: list[str] = ["G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8"]
    require_approval_digest_match: bool = True
    # JUnit ingestion: every acceptance criterion needs at least one passing test whose name carries its id
    require_criterion_tests: bool = True


class AutopilotPolicy(BaseModel):
    """What the platform does on its own between the human decisions (approve, UAT, Done)."""
    enabled: bool = False
    auto_analyze: bool = True
    auto_dispatch: bool = True
    auto_publish_evidence: bool = True
    max_wip: int = 4  # stories in flight between analysis and Done-eligible


class CiPolicy(BaseModel):
    # CI check names (GitHub check runs) that together establish each gate on the head commit
    checks: dict[str, list[str]] = Field(default_factory=lambda: {
        "G4": ["lint", "unit"], "G5": ["integration", "security"], "G6": ["staging-e2e"]})


class TeamPolicy(BaseModel):
    four_eyes: bool = True  # the person who approved the baseline cannot also sign its UAT


class CostPolicy(BaseModel):
    max_per_story_usd: float = 60.0
    max_per_run_usd: float = 20.0


class ReleasePolicy(BaseModel):
    production_human_approval: bool = True
    destructive_migration_human_approval: bool = True


class Policy(BaseModel):
    version: str = "1.0"
    jira: JiraPolicy = JiraPolicy()
    prototype: PrototypePolicy = PrototypePolicy()
    generation: GenerationPolicy = GenerationPolicy()
    agents: AgentsPolicy = AgentsPolicy()
    quality: QualityPolicy = QualityPolicy()
    cost: CostPolicy = CostPolicy()
    autopilot: AutopilotPolicy = AutopilotPolicy()
    ci: CiPolicy = CiPolicy()
    team: TeamPolicy = TeamPolicy()
    release: ReleasePolicy = ReleasePolicy()


class DevUser(BaseModel):
    name: str
    display: str
    roles: list[str]
    human: bool = True


DEFAULT_USERS = [
    DevUser(name="priya", display="Priya (Product owner)", roles=["product_owner"]),
    DevUser(name="dan", display="Dan (Developer)", roles=["developer"]),
    DevUser(name="quinn", display="Quinn (QA)", roles=["qa"]),
    DevUser(name="lena", display="Lena (Engineering lead)", roles=["eng_lead"]),
    DevUser(name="omar", display="Omar (DevOps)", roles=["devops"]),
    DevUser(name="sam", display="Sam (Security)", roles=["security"]),
    DevUser(name="admin", display="Admin", roles=["admin"]),
    DevUser(name="svc-automation", display="Automation service", roles=["automation"], human=False),
]


class Settings(BaseModel):
    database_url: str = "sqlite:///./asdp.db"
    tenant_id: str = "default"
    auth_mode: str = "dev"  # dev | proxy
    dev_mode: bool = True
    secret: str = "dev-only-change-me"
    inline_jobs: bool = False
    portal_origin: str = "http://localhost:5173"
    public_base_url: str = "http://localhost:8000"
    webhook_secret: str = ""
    github_webhook_secret: str = ""
    automation_token: str = ""  # bearer token CI uses to report gates and JUnit
    users_path: str = str(ROOT / "config" / "users.yaml")
    policy_path: str = str(ROOT / "config" / "policy.yaml")
    users: list[DevUser] = DEFAULT_USERS
    # integration credentials come from the environment / vault only
    jira_base_url: str = ""
    jira_email: str = ""
    jira_api_token: str = ""
    jira_sprint_field: str = "customfield_10020"
    jira_criteria_field: str = ""
    devin_api_base: str = "https://api.devin.ai/v1"
    devin_api_key: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        load_env_file(os.environ.get("ASDP_ENV_FILE") or str(ROOT / ".env"))
        e = os.environ
        s = cls()
        for field in cls.model_fields:
            v = e.get("ASDP_" + field.upper())
            if v is None:
                continue
            cur = getattr(s, field)
            if isinstance(cur, bool):
                setattr(s, field, v.lower() in ("1", "true", "yes"))
            elif isinstance(cur, str):
                setattr(s, field, v)
        for field in ("policy_path", "users_path"):  # relative paths are relative to the project root
            if not Path(getattr(s, field)).is_absolute():
                setattr(s, field, str(ROOT / getattr(s, field)))
        if Path(s.users_path).exists():  # optional team file; overrides the built-in dev users
            data = yaml.safe_load(Path(s.users_path).read_text(encoding="utf-8")) or {}
            s.users = [DevUser.model_validate(u) for u in data.get("users", [])] or s.users
        return s


def load_env_file(path: str) -> list[str]:
    """Read KEY=VALUE lines from a local .env file into the process environment. Values already set
    in the real environment win, so a vault or the shell always overrides the file."""
    p = Path(path)
    if not p.is_file():
        return []
    loaded = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        if k and k not in os.environ:
            os.environ[k] = v
            loaded.append(k)
    return loaded


def load_policy(path: str | None) -> Policy:
    if path and Path(path).exists():
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return Policy.model_validate(data)
    return Policy()
