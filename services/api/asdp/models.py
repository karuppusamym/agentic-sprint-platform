"""Relational schema (spec section 8). Tenant-aware, versioned, append-only where it matters."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def pk() -> Mapped[int]:
    return mapped_column(Integer, primary_key=True, autoincrement=True)


class Workspace(Base):
    __tablename__ = "workspace"
    id: Mapped[int] = pk()
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(120))
    git_repo: Mapped[str] = mapped_column(String(255), default="")
    jira_project: Mapped[str] = mapped_column(String(32))
    policy_version: Mapped[str] = mapped_column(String(32), default="1.0")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    engines: Mapped[dict] = mapped_column(JSON, default=dict)  # runtime engine toggles (override policy)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)  # global kill switch
    paused_reason: Mapped[str] = mapped_column(Text, default="")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SprintSnapshot(Base):
    __tablename__ = "sprint_snapshot"
    __table_args__ = (UniqueConstraint("workspace_id", "jira_sprint_id"),)
    id: Mapped[int] = pk()
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id"))
    jira_sprint_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(120), default="")
    state: Mapped[str] = mapped_column(String(32), default="active")
    start: Mapped[str] = mapped_column(String(40), default="")
    end: Mapped[str] = mapped_column(String(40), default="")
    fetched_at: Mapped[datetime] = mapped_column(DateTime)
    source_revision: Mapped[str] = mapped_column(String(64), default="")


class InboxEvent(Base):
    """Durable inbox: one row per external event, deduplicated."""
    __tablename__ = "inbox_event"
    __table_args__ = (UniqueConstraint("tenant_id", "dedupe_key"),)
    id: Mapped[int] = pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    dedupe_key: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)
    received_at: Mapped[datetime] = mapped_column(DateTime)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Story(Base):
    __tablename__ = "story"
    __table_args__ = (UniqueConstraint("tenant_id", "jira_issue_id"), UniqueConstraint("tenant_id", "key"))
    id: Mapped[int] = pk()
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id"))
    jira_issue_id: Mapped[str] = mapped_column(String(64))
    key: Mapped[str] = mapped_column(String(32), index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    issue_type: Mapped[str] = mapped_column(String(32), default="Story")
    state: Mapped[str] = mapped_column(String(32), default="DISCOVERED", index=True)
    jira_status: Mapped[str] = mapped_column(String(64), default="")
    owner: Mapped[str] = mapped_column(String(120), default="")
    priority: Mapped[str] = mapped_column(String(32), default="Medium")
    labels: Mapped[list] = mapped_column(JSON, default=list)
    components: Mapped[list] = mapped_column(JSON, default=list)
    sprint_jira_id: Mapped[str] = mapped_column(String(64), default="")
    in_active_sprint: Mapped[bool] = mapped_column(Boolean, default=True)
    readiness: Mapped[str] = mapped_column(String(32), default="unknown")
    readiness_reasons: Mapped[list] = mapped_column(JSON, default=list)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    criteria_ids: Mapped[dict] = mapped_column(JSON, default=dict)  # text-hash -> stable AC id
    revision: Mapped[int] = mapped_column(Integer, default=1)  # optimistic concurrency
    current_spec_version: Mapped[int] = mapped_column(Integer, default=0)
    current_prototype_version: Mapped[int] = mapped_column(Integer, default=0)
    auto_attempts: Mapped[int] = mapped_column(Integer, default=0)
    repair_attempts: Mapped[int] = mapped_column(Integer, default=0)
    head_commit: Mapped[str] = mapped_column(String(64), default="")
    pr_url: Mapped[str] = mapped_column(Text, default="")
    cost_total: Mapped[float] = mapped_column(Float, default=0.0)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expiry: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    paused_from: Mapped[str] = mapped_column(String(32), default="")
    blocker: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    eligible_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class StorySourceRevision(Base):
    __tablename__ = "story_source_revision"
    __table_args__ = (UniqueConstraint("story_id", "source_digest"),)
    id: Mapped[int] = pk()
    story_id: Mapped[int] = mapped_column(ForeignKey("story.id"), index=True)
    jira_updated_at: Mapped[str] = mapped_column(String(40), default="")
    source_digest: Mapped[str] = mapped_column(String(64))
    criteria_digest: Mapped[str] = mapped_column(String(64), default="")
    fields_json: Mapped[dict] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime)


class FeatureSpec(Base):
    """Immutable once written: a change is always a new version."""
    __tablename__ = "feature_spec"
    __table_args__ = (UniqueConstraint("story_id", "version"),)
    id: Mapped[int] = pk()
    story_id: Mapped[int] = mapped_column(ForeignKey("story.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict] = mapped_column(JSON)
    sha256: Mapped[str] = mapped_column(String(64))
    criteria_digest: Mapped[str] = mapped_column(String(64))
    source_digest: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="DRAFT")
    check_result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime)


class Prototype(Base):
    __tablename__ = "prototype"
    __table_args__ = (UniqueConstraint("story_id", "version"),)
    id: Mapped[int] = pk()
    story_id: Mapped[int] = mapped_column(ForeignKey("story.id"), index=True)
    spec_id: Mapped[int] = mapped_column(ForeignKey("feature_spec.id"))
    spec_version: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer)
    html: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[dict] = mapped_column(JSON)
    test_result: Mapped[dict] = mapped_column(JSON, default=dict)
    generator: Mapped[str] = mapped_column(String(64), default="template")
    status: Mapped[str] = mapped_column(String(32), default="BUILT")
    run_id: Mapped[str] = mapped_column(String(64), default="")
    created_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime)


class Approval(Base):
    __tablename__ = "approval"
    id: Mapped[int] = pk()
    story_id: Mapped[int] = mapped_column(ForeignKey("story.id"), index=True)
    spec_version: Mapped[int] = mapped_column(Integer)
    prototype_version: Mapped[int] = mapped_column(Integer)
    spec_digest: Mapped[str] = mapped_column(String(64))
    prototype_digest: Mapped[str] = mapped_column(String(64))
    criteria_digest: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(120))
    actor_roles: Mapped[list] = mapped_column(JSON, default=list)
    decision: Mapped[str] = mapped_column(String(32))
    comment: Mapped[str] = mapped_column(Text, default="")
    timestamp: Mapped[datetime] = mapped_column(DateTime)


class Annotation(Base):
    __tablename__ = "annotation"
    id: Mapped[int] = pk()
    story_id: Mapped[int] = mapped_column(ForeignKey("story.id"), index=True)
    prototype_version: Mapped[int] = mapped_column(Integer)
    selector: Mapped[str] = mapped_column(String(255), default="")
    comment: Mapped[str] = mapped_column(Text)
    author: Mapped[str] = mapped_column(String(120))
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_in_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class PlanTask(Base):
    __tablename__ = "plan_task"
    __table_args__ = (UniqueConstraint("story_id", "task_key"),)
    id: Mapped[int] = pk()
    story_id: Mapped[int] = mapped_column(ForeignKey("story.id"), index=True)
    task_key: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(32), default="implement")  # implement | repair | rework
    deps: Mapped[list] = mapped_column(JSON, default=list)
    owner_paths: Mapped[list] = mapped_column(JSON, default=list)
    engine: Mapped[str] = mapped_column(String(32), default="devin")
    priority: Mapped[int] = mapped_column(Integer, default=100)
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    blocker: Mapped[str] = mapped_column(Text, default="")
    spec_digest: Mapped[str] = mapped_column(String(64), default="")
    evidence_artifact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parent_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[str] = mapped_column(Text, default="")


class PathLock(Base):
    __tablename__ = "path_lock"
    __table_args__ = (UniqueConstraint("tenant_id", "path"),)
    id: Mapped[int] = pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(String(255))
    task_id: Mapped[int] = mapped_column(ForeignKey("plan_task.id"))
    story_id: Mapped[int] = mapped_column(ForeignKey("story.id"))
    acquired_at: Mapped[datetime] = mapped_column(DateTime)


class AgentRun(Base):
    __tablename__ = "agent_run"
    id: Mapped[int] = pk()
    task_id: Mapped[int] = mapped_column(ForeignKey("plan_task.id"), index=True)
    story_id: Mapped[int] = mapped_column(ForeignKey("story.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    mode: Mapped[str] = mapped_column(String(16))  # api | manual | simulated
    external_id: Mapped[str] = mapped_column(String(255), default="")
    external_url: Mapped[str] = mapped_column(Text, default="")
    branch: Mapped[str] = mapped_column(String(255), default="")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[str] = mapped_column(String(32), default="QUEUED")
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    trace_id: Mapped[str] = mapped_column(String(64), default="")
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    brief: Mapped[str] = mapped_column(Text, default="")
    pr_url: Mapped[str] = mapped_column(Text, default="")
    commit: Mapped[str] = mapped_column(String(64), default="")
    last_event: Mapped[str] = mapped_column(Text, default="")
    failure: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Artifact(Base):
    __tablename__ = "artifact"
    id: Mapped[int] = pk()
    story_id: Mapped[int] = mapped_column(ForeignKey("story.id"), index=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(48))
    uri: Mapped[str] = mapped_column(String(500), default="")
    sha256: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text, default="")
    retention_class: Mapped[str] = mapped_column(String(32), default="evidence")
    access_class: Mapped[str] = mapped_column(String(32), default="internal")
    created_at: Mapped[datetime] = mapped_column(DateTime)


class QualityGate(Base):
    """Append-only gate results; the latest row bound to the current digest/commit wins."""
    __tablename__ = "quality_gate"
    id: Mapped[int] = pk()
    story_id: Mapped[int] = mapped_column(ForeignKey("story.id"), index=True)
    gate_id: Mapped[str] = mapped_column(String(8))
    result: Mapped[str] = mapped_column(String(16))
    evidence_artifact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_commit: Mapped[str] = mapped_column(String(64), default="")
    bound_digest: Mapped[str] = mapped_column(String(64), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(120), default="")
    checked_at: Mapped[datetime] = mapped_column(DateTime)


class JiraSync(Base):
    __tablename__ = "jira_sync"
    id: Mapped[int] = pk()
    story_id: Mapped[int] = mapped_column(ForeignKey("story.id"), index=True)
    operation_key: Mapped[str] = mapped_column(String(255), unique=True)
    kind: Mapped[str] = mapped_column(String(32), default="comment")
    payload_digest: Mapped[str] = mapped_column(String(64))
    result: Mapped[str] = mapped_column(String(16), default="PENDING")
    remote_id: Mapped[str] = mapped_column(String(128), default="")
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class AuditEvent(Base):
    """Append-only, hash-chained so tampering is detectable (GET /audit/verify)."""
    __tablename__ = "audit_event"
    id: Mapped[int] = pk()
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    subject: Mapped[str] = mapped_column(String(64), index=True)
    event: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(120))
    old_state: Mapped[str] = mapped_column(String(32), default="")
    new_state: Mapped[str] = mapped_column(String(32), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    prev_hash: Mapped[str] = mapped_column(String(64), default="")
    hash: Mapped[str] = mapped_column(String(64), default="")


class CheckResult(Base):
    """A CI check outcome for an exact commit (from GitHub check_run webhooks)."""
    __tablename__ = "check_result"
    __table_args__ = (UniqueConstraint("story_id", "commit", "name"),)
    id: Mapped[int] = pk()
    story_id: Mapped[int] = mapped_column(ForeignKey("story.id"), index=True)
    commit: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(120))
    conclusion: Mapped[str] = mapped_column(String(32))
    url: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[datetime] = mapped_column(DateTime)


class KnowledgeNote(Base):
    """Project wiki entry injected into agent task packages and generator prompts."""
    __tablename__ = "knowledge_note"
    id: Mapped[int] = pk()
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)  # empty = applies to every task
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual | file | learned
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | pending | archived
    story_key: Mapped[str] = mapped_column(String(32), default="")
    created_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime)
