# Independent D7 FULL-PRODUCTION acceptance (frontend /memory live)

Authored against D7 REOPENED preferred §5 D path: `/memory` uses live
`useMemory` / `memoryAPI` hooks against the outer BFF — **not**
`MemoryUnavailable`-only MVP honesty.

MVP not-theater greens stay in `src/d7_acceptance/`.

```bash
cd /Users/rahulrawatadmin/Downloads/prime-agent-study-main/frontend
./node_modules/.bin/vitest run src/d7_production
```
