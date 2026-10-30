# Prime Agent BFF

FastAPI control-plane for the Prime Agent product.

## Database schema

The schema is the Alembic migrations in `prime_agent/db/migrations`. The API applies them at
startup in every environment, under a Postgres advisory lock, so replicas starting together
migrate once. A database made before the migrations could run (by development's
`create_all`) is adopted: the last revision it already holds is recorded, and the rest are
applied. Tests on SQLite use `create_all`; `tests/test_migrations.py` (on Postgres, with
`BACKEND_TEST_PG_URL`) checks that the migrations and the models describe the same schema.

A model change needs a migration:

```bash
cd backend
DATABASE_URL=postgresql://yal:yal@127.0.0.1:5433/prime_agent .venv/bin/alembic revision --autogenerate -m "what changed"
```

Review the generated file, then run `tests/test_migrations.py` against a Postgres.

## Limits

- A request body may be at most 2 MiB (`prime_agent.web.body_limit`); the one upload proxied
  through the API, a workspace file, may be as large as the workspace's file limit.
- A person may hold at most `MAX_STREAMS_PER_USER` (20) live session streams at once per API
  process (`prime_agent.web.stream_slots`); the next is refused with 429 until one closes.
