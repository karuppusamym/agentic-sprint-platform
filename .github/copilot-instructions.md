# Copilot instructions
Follow `AGENTS.md` at the repository root (commands, module boundaries, non-negotiable rules).

- Backend is Python 3.11+, FastAPI, SQLAlchemy 2.0 typed models; raise `DomainError(status, CODE, message)` for expected failures.
- Portal is React + TypeScript strict; no new runtime dependencies without discussion.
- When given a portal task package, modify only its allowed paths, run its test commands, and open a PR; do not close Jira issues.
