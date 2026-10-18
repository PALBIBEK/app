# Independent D7 acceptance (frontend /memory)

Authored from D7 §5 D / T14 — `/memory` must use live BFF hooks **or** an
explicit not-wired empty state. Silent `mockMemories` theater fails.

```bash
cd /Users/rahulrawatadmin/Downloads/prime-agent-study-main/frontend
./node_modules/.bin/vitest run src/d7_acceptance/memoryHonestyPages.test.ts
```
