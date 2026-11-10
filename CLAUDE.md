# CLAUDE.md
Read `AGENTS.md` first; it is the source of truth for commands, module boundaries and the rules that must not be weakened.

Claude-specific notes:
- Run `python -m pytest tests -q` after backend changes and `npm run build` in `apps/portal` after UI changes.
- When working from a portal task package, stay inside the "allowed paths" it lists and stop at its stop conditions.
- Never mark a story complete yourself; report the branch, head commit and PR URL back to the portal.
