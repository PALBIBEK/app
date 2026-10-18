# `githi-memory` — design plan

**Status: plan only. Nothing here is implemented.**

A memory module for githi in the shape of xai's `xai-grok-memory` (markdown as
truth, SQLite as a disposable index, hybrid retrieval, a periodic
consolidation pass) — plus the one thing Grok's design deliberately lacks: a
**measurable self-improvement loop**.

---

## 0. Why the obvious version fails

This is written against evidence collected in this session, not from first
principles. Two prior attempts at agent memory were measured, and both
outcomes constrain the design.

### 0.1 `agentmemory` measured exactly 0% improvement

Two runs, same signature, same scope:

```
api_calls   : first=22  best=22  delta=+0 (+0%)
plan steps  : first=5   best=5   delta=+0
```

Three independent causes, each of which this plan must answer:

| Cause | Detail | Consequence for this plan |
|---|---|---|
| **The recalled plan was never shown to the model** | `MemoryContext.best_prior_plan` held the full 2,063-char plan; `__str__` rendered only `"best prior plan: yes"`. The model was told a good plan existed and given no way to read it — it called `read_plan`, got the *current* run's empty file, and re-derived from scratch. | §5.4: injected content is verbatim, and a test asserts the *body* reaches the wire, not a flag. |
| **Nothing was learnable** | The constraint mechanism only fires on write failures (a 422 teaches `invalid_labels`). The task was read-only, so zero constraints were learned across both runs. | §3: the primary record is **strategy + cost**, not error constraints. |
| **The capability saved nothing** | The synthesized capability formatted already-fetched data: 0 API calls saved. | §6: fitness is measured in turns/calls/cost, so a zero-saving artifact cannot register as improvement. |

### 0.2 The facts that would actually have helped were never representable

githi failed Q2 (find the commit that introduced a line) **four times**. The
root causes, in order of discovery:

1. A system-prompt line — *"work with what is on disk and what the task gives
   you"* — suppressed `git clone` entirely. Three runs never once considered it.
2. `MAX_TIMEOUT_S = 120` against a **measured 188s** full clone. The model
   correctly wanted the repo, correctly inferred cloning was right, and
   rationally fell back to `--depth 100`. Shallow history makes `git blame`
   misattribute silently → a confident wrong answer.

The knowledge that would have prevented run 4's failure is:

> *"A full clone of langchain takes ~190s and needs `timeout ≥ 300`. Shallow
> clones break `git blame` attribution. `git log -S <code>` finds the
> introducing commit in one call."*

**None of that is an error constraint.** No exception was raised; every
command "succeeded". It is *resource and strategy knowledge learned from
measured outcomes* — and it is the thing a memory for an autonomous agent
most needs to carry. That observation is the organising principle of this
document.

### 0.3 The failure mode to design against

Both bugs above share a shape: **a constraint that silently narrows the
solution space**. The agent cannot report an approach it never considered, and
cannot distinguish "too slow" from "your request was overridden".

Memory makes this risk *worse*, because injected text is authoritative and
compounds across runs. A single bad note — *"never clone, it's too slow"* —
would permanently foreclose the correct approach and look like wisdom.

**Design rule (binding, §5.5):** memory records what *was done and what it
cost*. It never issues prohibitions.

---

## 1. Goal, stated so it can be falsified

> For a repeated task signature, run *N* completes with **materially fewer
> turns, tool calls and tokens** than run 1, at equal-or-better correctness,
> and the reduction is **attributable** to specific recalled entries.

Non-goals, stated to keep the claim honest:

- Not conversational memory. For "what did we discuss", use a semantic store.
- Not policy optimisation (GEPA/DSPy territory). Prompts stay fixed; what
  compounds is operational knowledge.
- Not correctness improvement. Memory should not make wrong answers right; if
  measured runs get *faster and wronger*, the design has failed (§6.3).

---

## 2. Architecture

Grok's split, adopted wholesale because it is right: **markdown is the source
of truth, SQLite is a rebuildable index.** Everything the model reads or
writes is human-editable `.md`; the database can be deleted and reconstructed.

```
~/.githi/memory/
├── MEMORY.md                                 # global, cross-project
├── index.sqlite3                             # DISPOSABLE: FTS5 + vec0
└── {slug}-{blake3(cwd)[:8]}/                 # workspace-scoped
    ├── MEMORY.md                             # curated project knowledge
    ├── strategies/{signature}.md             # measured playbooks  ← the new layer
    └── sessions/YYYY-MM-DD-{slug}-{run8}.md  # raw run logs
```

Workspace memory lives under `~/.githi/`, **never inside the user's repo** —
copied from Grok, and correct: memory is not a project artifact.

### 2.1 Three layers

| Layer | Content | Written by | Lifetime |
|---|---|---|---|
| **Episodic** | per-run traces: signature, turns, tool calls, tokens, cost, outcome, command sequence | harness, automatically | pruned after consolidation |
| **Semantic** | `MEMORY.md` — durable prose facts | consolidation pass | permanent, human-editable |
| **Strategy** | `strategies/{signature}.md` — *measured* playbooks | consolidation pass, **from trace comparison** | permanent |

The strategy layer is the self-improvement mechanism and is what neither Grok
(no fitness signal) nor `agentmemory` (no strategy representation) has.

### 2.2 Module layout

```
src/githi/memory/
├── __init__.py        # public surface: open_memory() -> Memory
├── paths.py           # ~/.githi/memory resolution, workspace hashing
├── store.py           # markdown read/write; the source of truth
├── schema.py          # SQL DDL (meta, chunks, chunks_fts, chunks_vec, runs)
├── index.py           # chunk upsert, blake3 change detection, reindex
├── chunker.py         # markdown-aware chunking w/ ancestor headers
├── embedding.py       # EmbeddingProvider protocol + OpenRouter impl + null impl
├── search.py          # hybrid: BM25 + KNN + decay + weights + MMR
├── mmr.py             # Jaccard diversity re-rank (no extra embeddings)
├── trace.py           # RunTrace dataclass; deterministic telemetry capture
├── consolidate.py     # the "dream": gates, prompt, response handling
├── strategy.py        # trace comparison -> measured playbook synthesis
├── recall.py          # assembles the briefing injected per run
└── tools.py           # memory_search / memory_note tool definitions
```

Separate top-level package (`githi-memory`) versus subpackage is an open
decision — see §8.1.

---

## 3. Data model

### 3.1 SQLite (index + telemetry)

Ported from Grok's `schema.rs`, plus two tables for the feedback loop.

```sql
-- ---- retrieval index (Grok parity) ----
meta(key TEXT PRIMARY KEY, value TEXT)
chunks(rowid, id TEXT UNIQUE, path, start_line, end_line, text,
       hash TEXT,            -- blake3: skip re-embedding unchanged chunks
       source TEXT,          -- 'global' | 'workspace' | 'strategy' | 'session'
       created_at, updated_at, access_count, last_accessed)
chunks_fts USING fts5(text, content='')          -- contentless BM25
chunks_vec USING vec0(chunk_id TEXT PK, embedding FLOAT[N])   -- if available

-- ---- feedback substrate (new) ----
runs(run_id TEXT PRIMARY KEY, signature, scope, instruction,
     turns, tool_calls, input_tokens, output_tokens, cache_read_tokens,
     cost_usd, wall_ms,
     outcome TEXT,           -- 'done' | 'max_turns' | 'stopped' | 'error'
     verified INTEGER,       -- 0 = self-reported (see §6.3)
     recalled_ids TEXT,      -- JSON: which memory entries were injected
     started_at, finished_at)
run_commands(run_id, seq, tool, argument_digest, exit_code, duration_ms,
             bytes_out, was_error)
```

`run_commands` is what makes strategy synthesis possible: it is the record of
*how* the work was actually done, at a granularity that supports "these 14
calls were redundant".

### 3.2 Markdown (truth)

`strategies/{signature}.md` — the novel artifact:

```markdown
---
signature: git.archaeology.introducing-commit
runs: 5
best: {turns: 12, tool_calls: 11, cost_usd: 0.09, run_id: a1b2c3}
worst: {turns: 30, tool_calls: 29, cost_usd: 0.256, run_id: d4e5f6}
updated: 2026-07-29
---

## What worked (measured)

- Full `git clone` then `git log -S "<code>" -- <path>` located the commit in
  **1 call** (run a1b2c3). Cloning langchain took **188s** — the command
  timeout must be ≥300s or the clone is killed and only `--depth` clones
  finish.
- Grepping for the *enclosing function name* reached the right region in 2
  calls; grepping the *symptom string* hit an unrelated match 2,600 lines
  away and cost 8 calls to recover (run d4e5f6).

## What cost the most (measured)

- After the commit was identified, 14 of 29 calls re-queried the same commit
  one field at a time. `git show --format=fuller <sha>` returns sha, author,
  date, subject and diff together.

## Caveats

- `git blame` on a shallow clone attributes to the oldest *grafted* commit and
  reports no error. Observed producing a confident wrong answer (run d4e5f6).
```

Every claim carries a **measurement and a run id**. That is the difference
between a playbook and folklore, and it is what makes an entry auditable and
removable when it stops being true.

---

## 4. Retrieval

Grok's pipeline, ported, because it is well-designed and degrades gracefully:

1. FTS5 BM25 (always available)
2. Vector KNN via `vec0` (when embeddings exist)
3. Merge by `chunk_id`, normalise to `[0,1]`
4. Drop content-free chunks (scaffold templates)
5. **Temporal decay** `score × e^(−λ·age_days)`, `λ = ln2/half_life`, applied
   to `session` chunks only — `global`, `workspace` and `strategy` are
   **evergreen**
6. Source weights + access-frequency boost + `min_score` filter
7. **MMR** diversity re-rank (Jaccard on tokens — no second embedding pass)
8. Truncate to `max_results`

**Deviation from Grok:** `strategy` is a new evergreen source and gets the
*highest* weight. A measured playbook for the current signature should
outrank a semantically similar prose paragraph.

**Degradation:** no embeddings → FTS-only with `text_weight = 1.0`, plus
stop-word query expansion. Verified available this session (OpenRouter
`/embeddings` → HTTP 200; SQLite has FTS5 and `load_extension`), but the
module must run without either — an agent that hard-fails when an embedding
provider is down is worse than one that searches by keyword.

---

## 5. Integration with githi

### 5.1 Where it hooks in

githi already has the four seams needed; none of this requires touching the
agent loop's control flow.

| Seam | Use |
|---|---|
| `Hooks.transform_context` | inject the recall briefing into the per-turn **view** |
| `Hooks.after_tool_call` | append to `run_commands` |
| `AgentState` (turns, tool_calls, tool_counts, usage) | the fitness signal — **already measured today** |
| end of `Agent.run` | write the `RunTrace`, append the session log |

`AgentState` already tracks turns, per-tool call counts, input/output/cache
tokens and cost. **The telemetry needed for the fitness signal exists; nothing
new must be measured.**

### 5.2 The sandbox conflict (must be resolved before coding)

`sandbox/paths.resolve_in_root` confines every file tool to the workspace
root. Memory lives at `~/.githi/memory/` — **outside it**. Three options:

| Option | Trade-off |
|---|---|
| **A. Dedicated tools** (`memory_search`, `memory_note`) that use a second confined root and never touch `resolve_in_root` | Preserves the sandbox exactly; costs 2 tools. **Recommended.** |
| B. Add the memory dir as a second allowed root for `read`/`grep` | Follows Grok/pi (memory is just files) but widens the sandbox — bash could then read and exfiltrate memory |
| C. Memory inside the workspace | No global layer, and pollutes the user's tree — rejected |

Option A also keeps memory writes out of reach of the credential-free bash
tool, which matters given §0.3: an agent that can rewrite its own memory
through a general shell can poison it.

### 5.3 Consolidation ("dream")

Grok's gate order, cheapest-first: `enabled` → `hours_since ≥ min_hours` →
`sessions_since ≥ min_sessions`, with a PID lock. Ported, plus two additions:

- **Trace-aware**: the consolidation prompt receives the `runs` rows for the
  signatures being consolidated, so it can write *measured* claims rather than
  impressions.
- **Never consolidate from a `max_turns` run's strategy.** A truncated run's
  approach must not be promoted as good practice; its *cost* data is still
  recorded. This is the `MAX_TURNS ≠ DONE` distinction, carried into memory.

Grok's operational details worth copying verbatim: the 32 KB input cap where
only sessions actually *read* are deleted (`processed_stems` vs
`sessions_eligible`, so over-cap sessions survive for the next pass), and
scaffold detection requiring **both** `< 500 bytes` **and** a marker.

### 5.4 Injection — the bug that must not recur

`agentmemory` stored the prior plan and rendered a boolean. The rule:

> **Recalled entries are injected as their full text, into the message view
> actually sent to the provider.**

Testable, and the test asserts on the **wire payload**, not on a context
object: build a memory with a known sentinel string, run one turn against a
fake LLM, assert the sentinel appears in the messages the client received.
Anything less repeats the exact failure of §0.1.

Injected per-turn into the **view**, never written into stored history —
githi's existing `transform_context` discipline — so a stale briefing cannot
accumulate.

### 5.5 Additive framing (binding)

Memory notes state **what was done and what it cost**. They must not contain
prohibitions.

- Allowed: *"`git log -S` found it in 1 call (run a1b2). Full clone: 188s."*
- Forbidden: *"Never use `git log -p`."* / *"The network is unavailable."*

Rationale in §0.3: a prohibition that turns out to be wrong is invisible —
the agent stops considering the approach and never reports having done so.
A cost measurement lets the model decide; a prohibition decides for it,
permanently, on one run's evidence.

Enforcement: a lint over generated strategy files rejecting `never `, `do not `,
`don't `, `always ` in claim positions, plus the consolidation prompt stating
the rule.

---

## 6. The self-improvement loop

### 6.1 Cycle

```
run ──► RunTrace (deterministic: turns, calls, tokens, cost, outcome)
         │
         ├──► session log (markdown)  ──► chunk ──► index ──► searchable
         │
         └──► runs / run_commands (sqlite)
                       │
              [gates: ≥N runs of this signature, ≥M hours]
                       │
                       ▼
              consolidation pass ── compares traces across runs
                       │              of the same signature
                       ▼
              strategies/{signature}.md   (measured playbook)
                       │
                       ▼
              recall() on next run ──► injected verbatim ──► fewer turns
                       │
                       └──► next RunTrace records whether it helped
```

### 6.2 Attribution

Improvement is only meaningful if it can be attributed. `runs.recalled_ids`
stores which entries were injected, so the comparison is not merely "run 5 was
cheaper" but "run 5 recalled entries {X, Y} and was cheaper". A `githi memory
report --signature S` surfaces:

```
signature: git.archaeology.introducing-commit   (n=5 runs)
  turns      : first=30  best=12  delta=-60%
  tool_calls : first=29  best=11  delta=-62%
  cost       : first=$0.256  best=$0.09  delta=-65%
  outcome    : 5/5 done          (0 max_turns)
  recalled   : strategies/git.archaeology.introducing-commit.md (runs 2-5)
  ⚠ verified : 0/5 runs had an independent correctness check
```

### 6.3 The honesty requirement

Cost can always be reduced by doing less work. `outcome` is **self-reported**
by the model, so a memory that teaches the agent to give up early would show
as a large improvement.

- `runs.verified` defaults to `0`, and the report **always** prints the
  self-reported-rate warning. Copied from `agentmemory`'s
  `outcome_source="self_reported"` discipline, which was right.
- Where a task has a deterministic checker (Q2 has one: does the reported SHA
  match `git log -S`?), a verifier may set `verified = 1`. Until then the
  improvement claim is explicitly "cheaper", not "better".
- **Regression guard:** if mean cost falls while verified correctness falls,
  the signature's strategy file is quarantined rather than trusted.

### 6.4 Prerequisite, stated up front

Task signatures must repeat. If every instruction is unique, recall is
permanently cold and this module delivers **nothing** — not degraded value,
zero. `agentmemory`'s spec said this and it remained true: with `n=2` the
report itself warned that only `api_call_delta` survives that sample size.

Signature derivation (deterministic patterns first, LLM classification as
fallback, `unclassified` as the floor) is ported from `agentmemory`'s ladder,
including its charset constraint.

---

## 7. Phased delivery

Each phase independently testable; each ends at a state worth having.

| Phase | Delivers | Falsifiable check |
|---|---|---|
| **1. Store + index** | markdown layout, chunker, FTS5, blake3 change detection | write a note, search it by keyword, edit it, confirm re-index |
| **2. Recall + injection** | `recall()`, briefing, `transform_context` wiring | **sentinel test (§5.4)**: injected text reaches the wire payload |
| **3. Trace capture** | `RunTrace`, `runs`, `run_commands`, session logs | run githi twice; two complete traces with real telemetry |
| **4. Vector + MMR** | embeddings, `vec0`, hybrid scoring, decay, diversity | semantic query with no keyword overlap returns the right chunk; degrade cleanly with provider disabled |
| **5. Consolidation** | gates, lock, dream prompt, session pruning | 3 session logs → one coherent `MEMORY.md`; over-cap sessions survive |
| **6. Strategy synthesis** | trace comparison → measured playbook | Q2 run 1 (29 calls) + run 2 → playbook citing the 188s clone and `git log -S` |
| **7. Report + attribution** | `githi memory report` | **the headline claim**: Q2 turns/calls/cost drop across runs, with recalled ids |

**Phase 7 is the experiment this whole module exists to run.** Q2 is the ideal
benchmark: a task githi has failed four ways and solved once, at a known cost
(30 turns / 29 calls / $0.2564), with a deterministic correctness check
(`eb9b992a`). If a warm run does not beat that, the design is wrong and should
be said so.

---

## 8. Open decisions

1. **Package boundary.** Separate distribution (`githi-memory`) versus
   `githi.memory` subpackage. Separate enforces a clean dependency rule
   (memory must not import the agent loop) at the cost of packaging overhead.
   *Leaning: subpackage with an enforced import-direction test* — the rule
   matters, the packaging does not.
2. **Embedding model and cost.** `/embeddings` works, but each indexed chunk
   costs tokens. Needs a measured budget, plus whether to embed session logs
   at all or only curated/strategy layers.
3. **`sqlite-vec` distribution.** `load_extension` is available here, but the
   `.so` must ship or be fetched. Fallback to FTS-only must be the default
   path, not an error branch.
4. **Consolidation model.** Cheap model for consolidation (it is a
   summarisation task) versus the run model. Affects §6 cost accounting.
5. **Multi-agent write safety.** Grok uses a PID lock; two concurrent githi
   runs in one workspace would contend on both index and markdown.
6. **Forgetting.** Decay handles ranking, not deletion. When is an entry
   removed? Proposal: a strategy claim contradicted by a newer measured run is
   rewritten by consolidation; unreferenced session chunks are pruned.

---

## 9. What would make this fail

Stated now so it is checked later rather than explained away:

- **Signatures do not repeat** → permanently cold (§6.4).
- **Recall is not actually injected** → the `agentmemory` bug; §5.4 exists
  solely to prevent it.
- **A note forecloses an approach** → the prompt/timeout bug class, now
  compounding across runs (§5.5).
- **Cost falls because work falls** → §6.3's regression guard.
- **Consolidation invents claims** → every strategy claim must carry a
  measurement and a run id, or it does not get written.
