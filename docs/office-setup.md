# Office setup: no model SDK, models only through VS Code chat and Devin

This is the plan for an office where nobody has a model API key or SDK. The only ways to reach a
model are the chat panel of a VS Code extension and Devin.

## How the platform works without an SDK

The platform never needs to call a model. With `generation.provider: template` it makes zero model
API calls. Everything that needs intelligence is handed to a chat or to Devin as a **package**, and
the answer comes back as a file or a pull request that the platform checks.

| Work | Who does the thinking | How it goes out | How it comes back | What checks it |
|---|---|---|---|---|
| First spec and prototype | nobody (rules and a template) | automatic | automatic | gates G1, G2 |
| Deepen the spec from the code | Copilot Chat, Claude, or Devin | analysis package | a JSON file | invented criteria and bad paths are dropped; G1 |
| Write a realistic prototype | Copilot Chat, Claude, or Devin | prototype package | one HTML file | the same G2 checks as a generated one |
| Implement a task | Devin, or Copilot / Claude in agent mode | task package | a pull request | CI gates G4 to G6 on the exact commit |
| Repair CI, rework after a failed test | same | repair package with the failure evidence | a new commit | same gates |

The first spec and prototype are plain: the spec restates the Jira criteria in a checked structure
and the prototype is one generic screen per criterion. They are enough to approve a simple story.
For anything else, use the two chat packages before approving.

### The chat loop from inside the repository

In the product repository, with the API running:

```
python -m asdp.cli pull WF-142 --kind spec
```

That writes `.asdp/WF-142/spec-brief.md`. In the VS Code chat (agent mode) say: *"Follow
.asdp/WF-142/spec-brief.md and save the answer as .asdp/WF-142/enrichment.json"*. Then:

```
python -m asdp.cli push WF-142 --kind spec
```

Same for `--kind prototype` (the chat writes `.asdp/WF-142/prototype.html`) and `--kind task`
(pull only; the result is a pull request). Add `.asdp/` to the product repository's `.gitignore`.
`pull` and `push` need `ASDP_API_URL` and `ASDP_USER`. The same packages can be copied from the
story page in the portal instead.

`python -m asdp.cli` must be run with `services/api` on the Python path (run it from that folder, or
set `PYTHONPATH`).

## The three engines at the office

| Engine | What you need | Platform setting |
|---|---|---|
| GitHub Copilot in VS Code | a Copilot Business or Enterprise seat and the Copilot Chat extension; agent mode allowed by your admin | `copilot_vscode: true`. No key in the platform. |
| Claude through Copilot | the same seat; your GitHub admin enables Anthropic models in the Copilot policy, and you pick Claude in the chat's model menu | still the `copilot_vscode` engine. Nothing to configure. |
| Claude Code extension | its own sign-in: a Claude Team / Enterprise account, or your company's Amazon Bedrock or Google Vertex access | `claude_vscode: true` only if that sign-in works at the office; otherwise `false` |
| Devin | a Devin seat with the repository connected | `devin: true`. Manual mode needs no key. |

**Claude at the office, plainly:** the usual route without an SDK is Claude as a model inside Copilot
Chat. That needs no Anthropic account, only the Copilot policy switch. The separate Claude Code
extension needs its own licence and sign-in, so ask IT which of the two is allowed before enabling
`claude_vscode`. If neither is, run with Copilot and Devin; one engine is enough.

Which models appear in Copilot's menu depends on your organisation's Copilot plan and policy. Confirm
with your GitHub admin; this repository cannot check it.

## Jira: what to create and where it goes

1. Decide which Jira you have. The address `https://<name>.atlassian.net` is **Cloud**; a company
   address such as `https://jira.company.com` is **Data Center**.
2. Use a service account if you can, so evidence comments are not posted under a person's name. It
   needs: browse the project, add comments, and transition issues.
3. Create the credential:
   - **Cloud:** signed in as that account, Atlassian account settings, Security, API tokens, create.
     Set `ASDP_JIRA_EMAIL` and `ASDP_JIRA_API_TOKEN`; `jira.source: cloud`.
   - **Data Center:** profile, Personal Access Tokens, create. Set `ASDP_JIRA_API_TOKEN` only;
     `jira.source: datacenter`.
4. Set `ASDP_JIRA_BASE_URL` to the site address with no path.
5. Find the Sprint field id: open `<base>/rest/api/2/field` in a browser while signed in and search
   for `"name":"Sprint"`; set `ASDP_JIRA_SPRINT_FIELD` to its `id`.
6. In the policy file set `jira.project`, the project key inside `discovery_jql`, the statuses that
   mean "ready to build", and the label that opts a story in (`ai-ready`).
7. Keep `jira.write_enabled: false` until `doctor` is green, then turn it on.

Acceptance criteria are read from an "Acceptance Criteria" heading in the description, or from a
custom field if you set `ASDP_JIRA_CRITERIA_FIELD`.

## Devin: two modes

- **Manual (no key).** The platform prepares the brief; a person starts the session in Devin and the
  pull request completes the run. This works on any Devin plan.
- **API.** Ask your Devin admin for an API key (Devin settings, API keys; a service key is better
  than a personal one). Set `ASDP_DEVIN_API_KEY` and `agents.devin_api_enabled: true`. Sessions
  then start with no click. If your organisation uses a different API address, set
  `ASDP_DEVIN_API_BASE`. Optional: create matching playbooks in Devin and map their ids under
  `agents.devin_playbooks`; paste the wiki export (portal, Playbooks & wiki) into Devin Knowledge.

The Devin adapter follows the public v1 session API and has not been run against a real
organisation from this repository. `doctor` tells you whether the key is accepted.

## Git and CI

`ASDP_GITHUB_WEBHOOK_SECRET` (repository webhook for pull requests and check runs) and
`ASDP_AUTOMATION_TOKEN` (CI uploads JUnit). Both are values you make up, long and random, and enter
in two places. Details are in the README under "Wiring Git and CI". Without them the platform still
works; a person reports the pull request and the CI results in the portal.

The webhook needs GitHub to reach the API. If the API runs on a laptop or inside the office network
and GitHub is the public service, that will not work without a relay your network team approves;
use the CI upload (the CI job calls the API) or manual reporting instead.

## Where the keys go

1. Copy `.env.example` to `.env` in the project root and fill it in. `.env` is git-ignored.
2. Values in the real environment or a vault override the file, so production can use a vault.
3. Never put a key in `config/policy.yaml`, in a playbook, or in a wiki note.

## Check it

```
cd services/api
python -m asdp.cli doctor
```

It signs in to Jira, runs your sprint query, counts stories with a sprint, with readable criteria
and with the opt-in label, tests the Devin key, and reports which Git and CI secrets are set. It
prints whether each secret is set, never its value. `--offline` checks configuration only.

## Order of work

1. `.env` and `config/policy.office.example.yaml` with Jira read-only; run `doctor` until Jira is green.
2. Start the API and portal; confirm the sprint shows your real stories.
3. One story end to end with Copilot only: deepen the spec, prototype, approve, task in agent mode,
   pull request, CI results reported by hand.
4. Add the CI upload, then the webhook if the network allows it.
5. Turn on Jira write-back. Ask for the Devin API key.
