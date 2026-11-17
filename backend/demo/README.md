# Demo database

This database is a sanitized, curated snapshot for the recorded demo.

It contains:

- `RUN-20261117-115022-58E6`: live `heatwave-iii` Planner run
- `RUN-20261114-210325-F769`: saved rework loop, second-round pass, approval and inventory reservations
- Base scenarios, materials, warehouses, inventory snapshots, Agent definitions and knowledge documents
- Only the events, LLM calls, A2A messages, tasks and reservations linked to those runs

It does not contain:

- users
- auth sessions
- login attempts
- audit logs
- retry jobs
- external AgentArts endpoints
- unrelated historical runs

Start with:

```powershell
.\setup.cmd
.\start-demo.cmd
```

`start-demo.cmd` copies the snapshot to `backend/farmmatch.demo-runtime.db`, so the source snapshot is not modified. The first run generates a fresh administrator password in `data/bootstrap-admin-password.txt`.