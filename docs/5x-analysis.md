# Running delivery with two people: what limits throughput and what this build does about it

## The constraint

The team went from eight people to two. To deliver what eight delivered, each person has to carry
four times as much; 5x to 10x means more than that. Agents can write code around the clock, so agent
speed is not the limit. **Human attention per story is.** Every minute a person spends clicking,
copying a commit hash, re-explaining the codebase to an agent, or waiting to be asked is a minute
taken from the only two things that genuinely need a person: judging whether the design is right,
and judging whether the build is right.

So the question for every feature is: does it remove a human touch, shorten a human wait, or raise
the chance that work passes first time?

## Where human touches went (one story with four tasks)

| Step | Before this round | Now |
|---|---|---|
| Start analysis | click per story | autopilot, in priority order, within a WIP limit |
| Review spec and prototype, approve | person | **person** (decision 1) |
| Dispatch the plan | click | autopilot, only on a valid approval |
| Launch each agent session | per task | per task where the engine is manual; none with the Devin API |
| Attach session URL, then report commit and PR | 2 per task | none: the pull request webhook completes the run |
| Record CI gates G4, G5, G6 | 3 posts | none: check runs and JUnit establish them on the exact commit |
| Acceptance test | person | **person** (decision 2, must differ from the approver) |
| Publish evidence to Jira | click | autopilot |
| Authorise Done | person | **person** (decision 3) |
| **Total** | about 21 | **3**, plus one launch per task for manual engines |

The automated test `test_autopilot_needs_only_three_human_decisions_per_story` runs a story from
Jira to Done and asserts the platform's own counter reads three human touches.

## What raises first-pass rate

Rework is the most expensive thing a small team can do, because it spends attention twice.

- **Playbooks** (`playbooks/`): one reviewed procedure per kind of work (implement, contract-first,
  integrate, repair, rework, migrate, deepen a spec). The same playbook goes to Devin, VS Code
  Copilot and VS Code Claude, so three engines behave like one team.
- **Project wiki** (`docs/wiki/` plus notes written in the portal): what the codebase expects, scoped
  by component or domain, added to every task package and to the Claude spec prompt.
- **Learning loop**: a rejected acceptance test or an exhausted repair becomes a proposed wiki note.
  It does nothing until a person activates it, then every later task in that component carries it.
- **Spec deepening**: Devin, Claude Code or Copilot reads the repository and returns business rules,
  open questions and a file-level impact list. That is merged into the spec before approval, so the
  reviewer sees risk before code is written, and the implementing agent starts from it.
- **Tests tied to criteria**: every criterion needs a passing test whose name carries its id. A
  criterion with no test fails the gate, with that fact as the repair evidence.

## What shortens human waits

- **Inbox**: each person sees only what needs them, oldest first, and what is waiting on the other
  person and why.
- **WIP limit**: autopilot stops starting stories at `autopilot.max_wip`, so work does not pile up
  in front of two reviewers.
- **Early CI results are kept** and applied the moment a story reaches the right stage.

## What still needs a person, on purpose

Approving the design, accepting the build, and closing the story. With `team.four_eyes` on, the
approver cannot sign the acceptance test, so two people still means two sets of eyes on every story.
Autopilot cannot do any of the three, and there is no override.

## What this does not prove

- **No multiplier has been measured.** Three touches per story is a property of the workflow, shown
  in tests against a mock Jira and synthetic GitHub payloads. Whether that becomes 5x depends on how
  long review and acceptance testing take, how often agents get it right first time, and how good
  the Jira stories are. None of that can be known before a pilot.
- **The largest remaining touch is launching sessions.** Without Devin API entitlement, and for the
  two VS Code engines by design, a person starts each task. For a four-task story that is four
  launches. Getting the Devin API enabled is the single most valuable thing outside this codebase.
- **Review is now the bottleneck.** Two people reviewing every prototype and testing every build is
  the ceiling. Raising `max_wip` past what they can review only lengthens queues.
- **Vague stories stop at the door.** A story with unmeasurable criteria is not picked up. Someone
  still has to write good criteria in Jira.
- The Jira Cloud adapter, the Devin API adapter, the GitHub webhook handler and the Claude generator
  have not been run against the real services from here.

## How to find out the real number

`GET /api/v1/metrics/kpis` reports, per completed story: human touches, hours spent waiting on
people, hours spent in automated stages, the share of lead time that was human waiting, first-pass
acceptance rate, repair success rate, and agent cost. It lists what it cannot measure (active
developer hours, escaped defects) rather than estimating them.

1. Take the last four sprints as the baseline: stories done, and a rough count of people-hours.
2. Pilot with one engine and two or three well-specified stories. Leave autopilot on.
3. After each story, read the share of lead time waiting on people. If it is high, the limit is
   review capacity; if it is low, the limit is agents or CI.
4. Activate the lessons that come out of each failure. First-pass rate should climb sprint on sprint;
   if it does not, the playbooks need work, not the people.

## Suggested setup for two people

- Copy `config/users.two-person.example.yaml` to `config/users.yaml`; keep `team.four_eyes: true`.
- Point a GitHub webhook (pull requests and check runs) at `/api/v1/webhooks/github` and set
  `ASDP_GITHUB_WEBHOOK_SECRET`. Name CI jobs to match `ci.checks`, or upload JUnit with
  `ASDP_AUTOMATION_TOKEN` (see `ci/github-actions-asdp.yml`).
- Start with `autopilot.max_wip: 2` and raise it only while the inbox stays short.
- Ask for Devin API entitlement; map your Devin playbook ids under `agents.devin_playbooks`.
