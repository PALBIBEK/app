# Independent D12 acceptance (frontend Subagents UI)

Authored from D12 §5 B / T14 + LOCAL_DEMO #4 + G6 — `/subagents` must use
live BFF (children/spawn) **or** an explicit unavailable banner. Silent
`mockSubagents` / `mockSubagent` / `mockParentSessions` theater fails.
Browser never calls supervisor child APIs. Wire reducer must handle
`subagent_start` / `subagent_end` for local-demo chips.

```bash
cd /Users/rahulrawatadmin/Downloads/prime-agent-study-main/frontend
./node_modules/.bin/vitest run src/d12_acceptance
```
