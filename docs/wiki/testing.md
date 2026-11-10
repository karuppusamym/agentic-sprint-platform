# Testing conventions

- Backend: `python -m pytest tests -q`. Tests use a temp-file SQLite database and the in-process mock Jira.
- Name every test that proves an acceptance criterion with that criterion's id, for example
  `test_AC_02_rejects_short_prompt`. The platform maps JUnit results to criteria by this id.
- A behaviour change needs a test in the same pull request. Do not weaken an assertion to make it pass.
- Portal: `npm run build` must pass with zero TypeScript errors.
