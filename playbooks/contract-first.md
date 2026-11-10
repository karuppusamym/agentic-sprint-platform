# Playbook: contract first

Parallel UI and backend sessions depend on this task, so the contract must be exact before either starts.

1. From the frozen spec, define every request, response, event and error shape the story needs.
2. Write them where this repository keeps contracts (see the wiki) and add contract tests that fail
   until an implementation satisfies them. Name tests with the criterion ids they protect.
3. Include error and empty cases for every operation, not only the success path.
4. Do not implement behaviour here. Do not change an existing contract in a breaking way; if the
   story needs that, stop and report.
