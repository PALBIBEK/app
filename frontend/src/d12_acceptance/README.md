# Independent D12 acceptance (frontend subagents)

Subagents are sessions that the agent starts with its own tool calls. The product never spawns one. A person finds a session's subagents in three places:

- in its conversation: the call that started each one, with its task and answer, and what each told the agent later;
- in the session's rail: every subagent below the session, with status, cost and a stop button;
- on each subagent's own page: its conversation, and a composer for a continuable one.

All of this goes through the BFF. The browser never calls the supervisor. The UI never fakes data, never shows the retired echo stub, and never calls anything long-term memory.

```bash
cd frontend
./node_modules/.bin/vitest run src/d12_acceptance
```
