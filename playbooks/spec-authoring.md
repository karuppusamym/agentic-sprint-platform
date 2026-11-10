# Playbook: deepen a specification from the codebase

Goal: make the spec safe to build from by adding what only someone who has read the code can know.
You are not implementing anything and you must not change any file.

1. Read AGENTS.md and the project wiki, then the modules the story touches.
2. For each acceptance criterion, find where the behaviour lives today and what would have to change.
3. Report, as JSON matching the schema in the task package:
   - business_rules and error_cases the code already enforces or will need, each tied to its criterion id.
   - assumptions you had to make, written so a person can confirm or reject them.
   - open_questions wherever the story and the code disagree. Prefer a question to a guess.
   - code_impact: every file or directory you expect to change, what changes, and the risk (low, medium, high).
     Mark as high anything involving schema, auth, shared contracts or data deletion.
   - screens a prototype needs, if the story has a user interface.
4. Do not reword acceptance criteria, invent criterion ids, or add scope the story does not ask for.

Done when: the JSON is valid, every item references a real criterion id where one is required, and
code_impact covers every criterion.
