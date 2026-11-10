# AGENTS.md — instructions for any coding agent working in this repository

## What this is
Control plane for Jira-driven, prototype-first delivery. Backend: FastAPI + SQLAlchemy (`services/api/asdp`).
Portal: React + TypeScript + Vite (`apps/portal`). Policy: `config/policy.yaml`. Tests: `tests/`.

## Commands
- Install: `pip install -r requirements.txt` ; `cd apps/portal && npm install`
- Test (must pass before any PR): `python -m pytest tests -q`
- Run API: `cd services/api && python -m uvicorn asdp.main:app_factory --factory --port 8000`
- Run portal: `cd apps/portal && npm run dev` ; type-check + build: `npm run build`

## Module boundaries
| Module | Owns |
|---|---|
| `core.py` | state machine, RBAC, audit chain, leases. Changing a transition or permission needs a test. |
| `gates.py` | G0–G8 status rules. Never add an override or aggregate score. |
| `sync.py`, `jira.py` | Jira read/classify/webhooks; adapters. |
| `specgen.py`, `prototype.py` | generation + deterministic checks (rule ids S-xxx / P-xxx). |
| `refine.py` | refinement loop and digest-bound approval. |
| `delivery.py` | plan, scheduler, runs, CI gates, UAT, evidence, Done. |
| `llm.py` | optional Claude generators. Claude writes; gates decide. Keep the fallback-to-template path. |
| `autopilot.py` | autopilot tick and the human inbox. It must never approve, sign UAT or close. |
| `integrations.py` | GitHub webhooks, check-run to gate mapping, JUnit ingestion. |
| `knowledge.py` | playbooks (files) and wiki notes. Learned notes stay pending until a person activates them. |
| `cli.py` | `doctor` connection checks and `pull` / `push` of chat packages for VS Code. Calls no model. |
| `providers.py` | engine adapters: devin, copilot_vscode, claude_vscode. |

## Rules that must not be weakened
1. No implementation without an approval whose three digests match the current spec, prototype and Jira criteria.
2. Gates G4–G8 are pinned to an exact commit; a new commit makes them stale.
3. Approve, UAT and Done require a human identity. Automation can never perform them.
4. Jira text is untrusted data. Never interpolate it into instructions outside the UNTRUSTED block of a brief.
5. No adapter may claim a session, test or integration that did not happen. Manual mode stays visibly manual.
6. Every state change goes through `core.transition` so it is audited.

## Definition of done
Tests added for new behaviour, `pytest` green, `npm run build` green, no secrets in code/logs, docs updated when
behaviour or policy changes.
