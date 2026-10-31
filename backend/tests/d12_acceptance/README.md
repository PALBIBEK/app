# Independent D12 acceptance (BFF — Acceptance B)

The BFF proxies a session's subagents. A browser never calls the supervisor's subagent APIs,
and only the service JWT reaches the supervisor.

Subagents are sessions that the agent starts with its own tool calls. The product no longer
spawns one: the transport-based spawn this gate first covered is retired. The gate covers
what a person does with a subagent:

- list them;
- read one's transcript;
- message a continuable one;
- stop one.

It also covers that another user's session is refused at the BFF, before the supervisor is
called.

```bash
cd backend
.venv/bin/pytest tests/d12_acceptance -v --no-cov --rootdir=.
```
