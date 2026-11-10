# Corrections and additions to Implementation Spec v1.0

What this build changed relative to the written specification, and why.

## Corrections

| # | Spec said | Problem | What was built |
|---|---|---|---|
| 1 | Pickup JQL filters on `status = "In Progress" AND labels = ai-ready` | Stories that fail the filter are invisible, so nobody can see *why* a story is not being picked up | Discovery reads the whole active sprint; status and label are applied as eligibility rules and shown as plain-language reasons on the board |
| 2 | State machine ends at `PLANNING` | No states for implementation, CI, staging, UAT, evidence or Done, yet the gates and scenarios depend on them | Added `PLAN_READY, IMPLEMENTING, CI_VALIDATION, STAGING, UAT, REWORK, EVIDENCE_SYNC, DONE_ELIGIBLE, DONE, CARRYOVER` with explicit transitions (`core.py`) |
| 3 | Approval is "stale when any bound component changes" | Jira criteria can change after approval without any portal version changing | The approval's criteria digest is compared with the **live** Jira snapshot; a Jira edit invalidates the approval, cancels runs and returns the story to review |
| 4 | "A release gate points to an exact commit" | Did not say what happens to earlier results when a new commit arrives | G4–G8 are valid only for the head commit **and** only if recorded after the approval in force; anything else shows as `STALE`, never green |
| 5 | Jira evidence is "idempotent" by operation key | Jira has no idempotency key; a timeout after a successful write would duplicate the comment | Each comment embeds an `[asdp-op:<key>]` marker; a retry first searches Jira for the marker and adopts the existing comment |
| 6 | `require_playwright_pass: true` | Needs a browser runner that is not provisioned; would block everything or tempt a fake pass | Deterministic static-DOM journey checks (rule ids P-001…P-010) are the mandatory layer; a real-browser run is a separate, honestly-reported optional layer (`require_browser_pass`) |
| 7 | Two Copilot roles + Devin | No path for teams standardised on Claude Code, and no rule for partial configurations | Three engines — `devin`, `copilot_vscode`, `claude_vscode` — any subset may be enabled, **at least one** is enforced at policy load and at runtime; engine choice falls back to what is enabled |
| 8 | "Criterion receives a stable ID" | Not defined how stability survives reordering or edits | Ids are keyed on normalised wording: reordering keeps ids, rewording issues a new id and retires the old one |
| 9 | Sprint ends before tests finish → "carryover/blocked as configured" | The portal's snapshot can be minutes old at the moment of Done | Evidence publication and the Done transition both re-read the live issue first; a closed sprint moves the story to `CARRYOVER` and refuses Done |
| 10 | Devin success implied by session status | A finished session is not proof of working code | A run only succeeds when a commit is reported; an un-attached manual Devin run cannot report success at all |

## Additions

- **Hash-chained audit log** with `GET /api/v1/audit/verify` (tamper evidence, spec 12 "evidence tampering").
- **Workspace kill switch** (`POST /admin/pause`): no new analysis or dispatch while paused.
- **Annotations block approval** until resolved, so "feedback resolution" is enforced rather than recorded.
- **Prompt-injection flagging** of Jira text, plus an `UNTRUSTED` quarantine block in every task package; Jira comments are never included in briefs.
- **Claude-written specs and prototypes** (`generation.provider: claude`, `llm.py`): Claude enriches the spec and writes the prototype, the deterministic gates still decide, failed rule ids are fed back on each bounded retry, and any API failure falls back to templates with the reason recorded. Off by default; not yet run against the live API.
- **Autopilot** between the three human decisions, with a WIP limit and the workspace kill switch (`autopilot.py`).
- **Human inbox** per person, including what is blocked on the other person and why.
- **GitHub webhooks**: pull requests complete agent runs; check runs establish CI gates on the exact commit; redeliveries are deduplicated and signatures verified.
- **JUnit ingestion** that maps tests to acceptance criteria by id and fails the gate when a criterion has no passing test.
- **Playbooks and a project wiki** shared by all three engines, with a learning loop whose proposals stay inactive until a person activates them.
- **Spec deepening by an engine** (Devin, Claude Code, Copilot): rules, open questions and file-level code impact merged into the spec; invented criterion references and path traversal are dropped.
- **Four-eyes rule** and a two-person team file, so a team of two still puts two people on every story.
- **Human-attention KPIs**: touches per story and share of lead time spent waiting on people.
- **Prototype upload path**: a richer prototype (from a designer or an agent) can be submitted as a revision and goes through the same checks, including no-network and secret scanning.
- **Signed, short-lived preview URLs** served with a CSP `sandbox` directive, so a prototype has no network and an opaque origin even if the link is opened outside the portal.
- **Per-task engine reassignment at dispatch** by the engineering lead.
- **Traceability matrix** per criterion with five independent proofs; `verified` is true only when all five hold.
- **KPI endpoint** that lists what it cannot measure (developer hours, escaped defects, baseline) instead of estimating them.
- **Dev-only mock Jira** with fault injection (lost writes, lost responses, sprint close, issue edits) so every failure scenario is reproducible locally.
- **No gate override path at all.** The spec allowed lead overrides "with reason + audit"; this build deliberately ships none until the organisation defines who may override which gate.

## Deliberately not built (needs enterprise access or a decision)

| Item | Why |
|---|---|
| Copilot SDK / CLI runner | Spec 6.2 requires separate enterprise authorisation; shown in the matrix as "not implemented" |
| Headless Claude Code runner | Same class of decision as the Copilot SDK runner (unattended code execution identity, sandbox, audit) |
| SSO | `auth_mode: proxy` trusts identity headers from an SSO reverse proxy; the proxy itself is yours to deploy. Dev mode uses a fixed user list |
| Git / CI adapters | CI and Git report in through the API (`/gates`, `/commits`, `/runs/{id}/report`); wiring your CI to call them is deployment work |
| Helm / OCP manifests, object store, vault, OpenTelemetry | Environment-specific; artifacts are stored in the database for now |
| Jira Data Center adapter, attachments upload | Cloud adapter only; evidence is posted as a comment with digests |
