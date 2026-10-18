# Independent D6 acceptance (frontend /tools)

Authored from D6 §5 F–H and LOCAL_DEMO #3 — `/tools` must use live `useTools` /
`toolsAPI`, not `mockTools` theater; never browser→supervisor. Harness detail
must attach tool UUIDs via `tools: [uuid…]`; Test invokes via BFF.

```bash
cd /Users/rahulrawatadmin/Downloads/prime-agent-study-main/frontend
./node_modules/.bin/vitest run src/d6_acceptance
```
