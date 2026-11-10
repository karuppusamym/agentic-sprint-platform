# Architecture of this repository

- `services/api/asdp` is the FastAPI control plane. `core.py` owns the state machine, roles and audit
  chain; `gates.py` owns gate status; `delivery.py` owns planning, runs, CI gates, UAT and evidence.
- `apps/portal` is the React and TypeScript portal. It only talks to `/api/v1`.
- Every state change goes through `core.transition` so that it is audited. Never set `story.state` directly.
- Expected failures raise `DomainError(status, CODE, message)`; the portal shows the message to the user.
