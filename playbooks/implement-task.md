# Playbook: implement a task from an approved baseline

1. Read AGENTS.md, the project wiki in the package, then the frozen spec. Open the approved prototype
   if the task has a user interface; it is the reference for behaviour, not for code.
2. Work test-first. For every acceptance criterion in the checklist write at least one automated test
   whose name contains the criterion id (for example `test_AC_01_generates_draft` or
   `it("AC-01 generates a draft")`). CI maps tests to criteria by that id; a criterion with no
   passing test fails the gate.
3. Make the smallest change that satisfies the criteria. Stay inside the allowed paths.
4. Run lint, type-check and the full test suite locally before pushing.
5. Push to the named branch and open a pull request. Put the Jira key in the title. The portal picks
   up the pull request and its checks on its own; you do not need to report anything by hand.

Stop and report instead of guessing when the spec is ambiguous, the change needs files outside the
allowed paths, or a check cannot be made to pass without weakening it.
