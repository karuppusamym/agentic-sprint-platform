"""Playbooks (versioned, in git) and the project wiki (knowledge notes with a learning loop).

Both are engine-neutral: the same playbook and the same notes go into a Devin brief, a VS Code
Copilot package, a VS Code Claude package and the Claude generator prompts. That is what lets a
two-person team keep three engines consistent without re-explaining the project every time.

Trust model: playbooks and file-sourced notes are reviewed in git. Notes written in the portal
are written by named team members. Notes the platform proposes from failures ("learned") stay
PENDING until a person activates them, so nothing an agent or a CI log said can silently become
standing instructions.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import ROOT
from .core import sha256_text, utcnow
from .models import KnowledgeNote, PlanTask, Story

PLAYBOOK_DIR = ROOT / "playbooks"
WIKI_DIR = ROOT / "docs" / "wiki"
MAX_NOTE_CHARS = 6000


def load_playbooks(directory: Path = PLAYBOOK_DIR) -> dict[str, dict]:
    out = {}
    for f in sorted(directory.glob("*.md")) if directory.exists() else []:
        body = f.read_text(encoding="utf-8").strip()
        title = next((line.lstrip("# ").strip() for line in body.splitlines() if line.startswith("#")), f.stem)
        out[f.stem] = {"id": f.stem, "title": title, "body": body, "sha256": sha256_text(body)}
    return out


def playbook_for(task: PlanTask) -> str:
    key = task.task_key.split("~")[0]
    if task.kind == "repair":
        return "repair-ci"
    if task.kind == "rework":
        return "rework-uat"
    if key.startswith("migration") or key == "regression":
        return "schema-migration"
    if key == "contract":
        return "contract-first"
    if key == "integration":
        return "integration"
    return "implement-task"


def seed_wiki(db: Session, tenant: str, directory: Path = WIKI_DIR) -> int:
    """Load docs/wiki/*.md as notes. An optional first line `tags: a, b` scopes a page."""
    n = 0
    for f in sorted(directory.glob("*.md")) if directory.exists() else []:
        text = f.read_text(encoding="utf-8").strip()
        tags: list[str] = []
        if text.lower().startswith("tags:"):
            first, _, text = text.partition("\n")
            tags = [t.strip().lower() for t in first.split(":", 1)[1].split(",") if t.strip()]
        text = text.strip()
        title = next((line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("#")), f.stem)
        note = db.execute(select(KnowledgeNote).where(KnowledgeNote.tenant_id == tenant, KnowledgeNote.source == "file",
                                                      KnowledgeNote.created_by == f.name)).scalar()
        if note is None:
            note = KnowledgeNote(tenant_id=tenant, source="file", created_by=f.name, created_at=utcnow(), status="active",
                                 title=title, body=text, tags=tags)
            db.add(note)
        note.title, note.body, note.tags = title, text, tags
        n += 1
    db.flush()
    return n


def story_tags(story: Story, domains: list[str] | None = None, extra: list[str] | None = None) -> set[str]:
    return {t.lower() for t in (story.components or []) + (story.labels or []) + (domains or []) + (extra or [])}


def select_notes(db: Session, tenant: str, tags: set[str], limit_chars: int = MAX_NOTE_CHARS) -> list[KnowledgeNote]:
    """Active notes that are global (no tags) or share a tag with the work. Scoped notes first."""
    rows = db.execute(select(KnowledgeNote).where(KnowledgeNote.tenant_id == tenant, KnowledgeNote.status == "active")
                      .order_by(KnowledgeNote.id)).scalars().all()
    scoped = [n for n in rows if n.tags and set(n.tags) & tags]
    general = [n for n in rows if not n.tags]
    out, used = [], 0
    for n in scoped + general:
        if used + len(n.body) > limit_chars:
            continue
        out.append(n)
        used += len(n.body)
    return out


def render_notes(notes: list[KnowledgeNote]) -> str:
    return "\n\n".join(f"### {n.title}\n{n.body}" for n in notes)


def propose_lesson(db: Session, story: Story, title: str, body: str, actor: str) -> KnowledgeNote:
    """Capture a failure as a PENDING wiki note. A person decides whether it becomes guidance."""
    note = KnowledgeNote(tenant_id=story.tenant_id, title=title[:200], body=body.strip()[:2000],
                         tags=sorted({c.lower() for c in story.components or []}), source="learned", status="pending",
                         story_key=story.key, created_by=actor, created_at=utcnow())
    db.add(note)
    db.flush()
    return note


def note_json(n: KnowledgeNote) -> dict:
    return {"id": n.id, "title": n.title, "body": n.body, "tags": n.tags, "source": n.source, "status": n.status,
            "storyKey": n.story_key, "createdBy": n.created_by, "createdAt": n.created_at.isoformat() + "Z"}
