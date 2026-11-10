"""Quality gates G0-G8 and the definition-of-done engine.

A gate result only counts while it is bound to the *current* thing it judged:
  G1 -> current spec digest          G2 -> current prototype digest
  G3 -> approval whose three digests still match spec, prototype and live Jira criteria
  G4-G8 -> the story's head commit, and recorded after the approval that is in force
There is no aggregate score and no override path.
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from .core import sha256_text, utcnow
from .models import Approval, Artifact, FeatureSpec, Prototype, QualityGate, Story, StorySourceRevision

GATES = {
    "G0": "Eligibility", "G1": "Spec completeness", "G2": "Playable prototype", "G3": "Human approval",
    "G4": "Code quality", "G5": "Integration / security", "G6": "Staging fidelity", "G7": "Human UAT",
    "G8": "Evidence + Jira",
}


def latest_source(db: Session, story: Story) -> StorySourceRevision | None:
    return db.execute(select(StorySourceRevision).where(StorySourceRevision.story_id == story.id)
                      .order_by(StorySourceRevision.id.desc()).limit(1)).scalar()


def current_spec(db: Session, story: Story) -> FeatureSpec | None:
    if not story.current_spec_version:
        return None
    return db.execute(select(FeatureSpec).where(FeatureSpec.story_id == story.id,
                                                FeatureSpec.version == story.current_spec_version)).scalar()


def current_prototype(db: Session, story: Story) -> Prototype | None:
    if not story.current_prototype_version:
        return None
    return db.execute(select(Prototype).where(Prototype.story_id == story.id,
                                              Prototype.version == story.current_prototype_version)).scalar()


def store_artifact(db: Session, story: Story, kind: str, content, run_id: int | None = None,
                   retention: str = "evidence") -> Artifact:
    text = content if isinstance(content, str) else json.dumps(content, sort_keys=True, default=str, indent=1)
    sha = sha256_text(text)
    existing = db.execute(select(Artifact).where(Artifact.story_id == story.id, Artifact.kind == kind,
                                                 Artifact.sha256 == sha)).scalar()
    if existing:
        return existing
    art = Artifact(story_id=story.id, run_id=run_id, kind=kind, sha256=sha, content=text,
                   uri=f"artifact://{story.key}/{kind}/{sha[:16]}", retention_class=retention, created_at=utcnow())
    db.add(art)
    db.flush()
    return art


def record_gate(db: Session, story: Story, gate_id: str, result: str, actor: str, *, commit: str = "",
                bound_digest: str = "", summary: str = "", evidence=None) -> QualityGate:
    art = store_artifact(db, story, f"gate-{gate_id}", evidence) if evidence is not None else None
    row = QualityGate(story_id=story.id, gate_id=gate_id, result=result, source_commit=commit,
                      bound_digest=bound_digest, summary=summary, actor=actor, checked_at=utcnow(),
                      evidence_artifact_id=art.id if art else None)
    db.add(row)
    db.flush()
    return row


def valid_baseline(db: Session, story: Story) -> Approval | None:
    """The approval in force, or None if any bound component changed since (stale approval)."""
    spec, proto, src = current_spec(db, story), current_prototype(db, story), latest_source(db, story)
    if not (spec and proto and src):
        return None
    last = db.execute(select(Approval).where(Approval.story_id == story.id)
                      .order_by(Approval.id.desc()).limit(1)).scalar()
    if not last or last.decision != "APPROVE":
        return None
    if (last.spec_digest, last.prototype_digest, last.criteria_digest) != (spec.sha256, proto.sha256, src.criteria_digest):
        return None
    if last.spec_version != spec.version or last.prototype_version != proto.version:
        return None
    return last


def gate_status(db: Session, story: Story) -> list[dict]:
    rows = db.execute(select(QualityGate).where(QualityGate.story_id == story.id).order_by(QualityGate.id)).scalars().all()
    spec, proto = current_spec(db, story), current_prototype(db, story)
    baseline = valid_baseline(db, story)
    any_approval = db.execute(select(Approval.id).where(Approval.story_id == story.id, Approval.decision == "APPROVE").limit(1)).scalar()
    out = []
    for gid, title in GATES.items():
        mine = [r for r in rows if r.gate_id == gid]
        latest = mine[-1] if mine else None
        status, row = "PENDING", None
        if gid == "G0":
            row = latest
            status = latest.result if latest else "PENDING"
        elif gid in ("G1", "G2"):
            digest = (spec.sha256 if spec else "") if gid == "G1" else (proto.sha256 if proto else "")
            bound = [r for r in mine if r.bound_digest == digest and digest]
            row = bound[-1] if bound else latest
            status = bound[-1].result if bound else ("STALE" if latest else "PENDING")
        elif gid == "G3":
            row = latest
            status = "PASS" if baseline else ("STALE" if any_approval else "PENDING")
        else:
            valid = [r for r in mine if baseline and story.head_commit and r.source_commit == story.head_commit
                     and r.checked_at >= baseline.timestamp]
            row = valid[-1] if valid else latest
            status = valid[-1].result if valid else ("STALE" if latest else "PENDING")
        out.append({"gate": gid, "title": title, "status": status,
                    "summary": row.summary if row else "", "actor": row.actor if row else "",
                    "commit": row.source_commit if row else "", "checked_at": row.checked_at.isoformat() + "Z" if row else None,
                    "evidence_artifact_id": row.evidence_artifact_id if row else None})
    return out


def gate_map(db: Session, story: Story) -> dict[str, str]:
    return {g["gate"]: g["status"] for g in gate_status(db, story)}
