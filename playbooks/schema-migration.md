# Playbook: schema migration

Schema work is never run in parallel and never without a way back.

1. Write the migration contract first: forward change, backfill, rollback, and how each is verified.
2. Forward migrations must be compatible with the code currently deployed (expand, then contract).
3. Prove the rollback on a copy of realistic data and record row counts before and after.
4. Destructive steps (drop, rewrite, irreversible backfill) need named human approval. Stop and ask.
