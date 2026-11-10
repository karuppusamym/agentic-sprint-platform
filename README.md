# Agentic Sprint Delivery Platform

A control plane that takes active-sprint Jira stories through a gated path:

**Jira story → versioned spec → playable HTML prototype → deterministic checks → human approval bound to exact digests → task plan → agent runs → CI gates → staging → human UAT → evidence in Jira → human-authorised Done.**

Built from *Full Implementation Specification v1.0*. Differences from that document are listed in
[docs/spec-corrections.md](docs/spec-corrections.md).

## Built for a very small team

People make three decisions per story: approve the spec and prototype, accept the build, authorise
Done. Everything between those is automatic. Why, and what is still unproven, is in
[docs/5x-analysis.md](docs/5x-analysis.md).

| Capability | What it does | Where |
|---|---|---|
| Autopilot | analyzes eligible stories in priority order, dispatches approved plans, publishes evidence; never approves, tests or closes | `autopilot` in `config/policy.yaml` |
| Inbox | shows each person only what needs them, and what waits on someone else and why | portal home, `GET /api/v1/inbox` |
| Git integration | a pull request on a portal branch completes the agent run with its real commit; later pushes reset the gates | `POST /api/v1/webhooks/github` |
| CI integration | check runs establish G4, G5, G6 on the exact commit; JUnit maps tests to acceptance criteria by id | `ci.checks`, `POST /stories/{key}/ci/junit` |
| Playbooks | one reviewed procedure per kind of work, sent to every engine | `playbooks/` |
| Project wiki | codebase guidance scoped by component, added to every task package | `docs/wiki/`, portal |
| Learning loop | failed acceptance tests and exhausted repairs become proposed wiki notes; inactive until a person activates them | portal |
| Spec deepening | Devin, Claude Code or Copilot reads the repo and returns rules, questions and file-level impact, merged into the spec | story workspace |
| Four eyes | the approver of a baseline cannot sign its acceptance test | `team.four_eyes` |
| Team file | two people holding several roles each | `config/users.two-person.example.yaml` |

## Quick start (no enterprise access needed)

Runs on SQLite with a built-in mock Jira (8 sample stories), so everything works on a laptop.

```bash
pip install -r requirements.txt
```

```bash
cd services/api && python -m uvicorn asdp.main:app_factory --factory --port 8000
```

```bash
cd apps/portal && npm install && npm run dev
```

Open http://localhost:5173. Pick a user in the top bar (dev auth), press **Sync**.

```bash
python -m pytest tests -q
```

## Agent engines: configure all three, or just one

| Engine | Kind | How work is launched | How results return |
|---|---|---|---|
| `devin` | autonomous | API session if the tenant grants it (`devin_api_enabled` + `ASDP_DEVIN_API_KEY`); otherwise the portal prepares a brief, a developer launches it and pastes the session URL | reported commit + PR, then CI |
| `copilot_vscode` | attended | task package pasted into Copilot Agent Mode in VS Code | commit / PR, then CI |
| `claude_vscode` | attended | task package given to Claude Code in VS Code | commit / PR, then CI |

Set them in `config/policy.yaml` under `agents.engines`, or toggle at runtime on the Administration page.
At least one must stay enabled; the platform rejects a configuration with none. `primary` is preferred for
multi-file autonomous work, `attended_engine` for small fixes and rework; if the preferred engine is off,
the next enabled one is used. An engineering lead can reassign any pending task's engine at dispatch.

The portal never scripts the VS Code UI and never reports an unattended session it did not create.

## Claude-written specs and prototypes (optional)

By default specs and prototypes come from deterministic templates. Set `generation.provider: claude` in
`config/policy.yaml` to have Claude (`claude-opus-5-5` by default) enrich each spec with business rules,
assumptions, error cases, open questions and a screen list, and write the playable prototype.

```bash
pip install "anthropic>=2.0"
```

Credentials come from `ANTHROPIC_API_KEY` or an `ant auth login` profile. Restart the API afterwards;
Administration → `generation` shows which generator is active.

- Claude writes; it does not judge. Criterion ids, the criterion-to-test mapping and the test manifest stay
  deterministic, so G1 and G2 check Claude's output exactly as they check a template.
- A failed prototype check is sent back to Claude as specific rule ids, within the same three-retry budget.
- An API error, refusal, truncated or unusable reply falls back to the template for that artifact, and the
  artifact and audit log record the fallback and the reason.
- Refusal fallbacks are enabled (`fallbacks: "default"`) when the installed SDK supports them.
- Jira story text is sent to the Anthropic API. Confirm data classification before turning this on.

Status: covered by tests with a stand-in client; **not yet run against the live API** (no key on the build machine).

## Walkthrough

1. **Sync** (any role). WF-142/143/144/149 become eligible; WF-145 needs refinement (vague criteria), WF-146 is blocked, WF-147/148 are not eligible. Each shows why.
2. Open **WF-142**, **Analyze** (developer `dan`). A spec and a playable prototype are generated and checked.
3. Play the demo, then **Approve** as product owner `priya`. The approval records the three digests.
4. **Dispatch** as lead `lena`. Tasks start within concurrency and path-ownership limits; manual-mode runs show their task package.
5. Report each run's commit and PR (developer). When all tasks finish the story enters CI validation.
6. Record **G4, G5, G6** as `svc-automation` (this is what your CI calls), then **UAT** as `quinn`.
7. **Publish evidence** to Jira, then **Transition to Done** as the developer.

To watch failure handling: on Administration → Mock Jira controls, add the label `proto-fault-once` or
`proto-fault-always` to a story before analyzing it, inject Jira write faults, close the sprint, or edit
acceptance criteria after approval.

## Quality gates

| Gate | Evidence | Bound to |
|---|---|---|
| G0 Eligibility | fresh Jira snapshot, no blockers, policy | Jira source digest |
| G1 Spec completeness | rule ids S-001…S-006 | spec digest |
| G2 Playable prototype | rule ids P-001…P-010, per-journey traces | prototype digest |
| G3 Human approval | named approver + three digests | spec, prototype, live Jira criteria |
| G4 Code quality / G5 Integration + security / G6 Staging | reported by CI identities with evidence | head commit |
| G7 Human UAT | named tester, tested commit | head commit |
| G8 Evidence + Jira | Jira comment receipt, live issue state | head commit |

A gate whose binding changed shows as `STALE`. There is no aggregate score and no override.

## No model SDK or API key at the office

The platform calls no model with `generation.provider: template`. Specs, prototypes and code are
improved through VS Code chat (Copilot, with Claude as a model if your Copilot policy allows it) and
Devin, using packages the platform writes and checks. Setup for Jira, Devin and the keys, and the
`doctor` connection check, are in [docs/office-setup.md](docs/office-setup.md).

## Wiring Git and CI

1. In the repository, add a webhook to `<api>/api/v1/webhooks/github` for **Pull requests** and
   **Check runs**, content type JSON, with a secret; set the same value in `ASDP_GITHUB_WEBHOOK_SECRET`.
2. Name CI jobs to match `ci.checks` in `config/policy.yaml` (defaults: `lint`, `unit` for G4;
   `integration`, `security` for G5; `staging-e2e` for G6).
3. Optional but recommended: upload JUnit so each acceptance criterion is matched to its tests. Set
   `ASDP_AUTOMATION_TOKEN` and send it as a bearer token. `ci/github-actions-asdp.yml` shows both.
4. Agents work on branches named `asdp/<JIRA-KEY>/<task>-a<attempt>` and name tests with the
   criterion id (`AC-01`). The task package tells them so.

Covered by tests with synthetic GitHub payloads; **not yet run against a live repository**.

## Connecting real systems

| System | Setting | Status |
|---|---|---|
| Jira Cloud | `jira.source: cloud`, env `ASDP_JIRA_BASE_URL`, `ASDP_JIRA_EMAIL`, `ASDP_JIRA_API_TOKEN`, optional `ASDP_JIRA_SPRINT_FIELD`, `ASDP_JIRA_CRITERIA_FIELD`. Start with `write_enabled: false`. | Implemented against REST v3 (`/search/jql` + `nextPageToken`); **not run against a real tenant** |
| Devin API | `agents.devin_api_enabled: true`, env `ASDP_DEVIN_API_KEY` | Implemented from the public API shape; **not run against a real tenant** |
| CI | your pipeline calls `POST /api/v1/stories/{key}/gates` and `/commits` as an automation identity | API tested; pipeline wiring is yours |
| PostgreSQL | `ASDP_DATABASE_URL=postgresql+psycopg://…` | Schema is portable SQLAlchemy; **tested on SQLite only** |
| SSO | `ASDP_AUTH_MODE=proxy` behind a proxy that sets `X-Forwarded-User` / `X-Forwarded-Groups` and strips client copies | Header mapping only; no proxy included |

Set `ASDP_SECRET` (preview-link signing), `ASDP_WEBHOOK_SECRET`, and `ASDP_DEV_MODE=false` outside local use.
Secrets come from the environment only and are never written to Jira, logs or briefs.

## Layout

```
services/api/asdp/   FastAPI app: core, gates, sync, jira, specgen, prototype, refine, delivery, providers, main
apps/portal/         React + TypeScript portal
config/policy.yaml   workflow policy
tests/               12 acceptance scenarios, platform, Claude generation and small-team tests
playbooks/           procedures sent to every engine
ci/                  example GitHub Actions workflow
docs/                5x analysis, spec corrections, wiki pages (docs/wiki)
AGENTS.md  CLAUDE.md  .github/copilot-instructions.md   instructions for each engine
```
