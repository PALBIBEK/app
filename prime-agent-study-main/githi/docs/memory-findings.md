# Memory: what four experiments established

Written down because the negative results cost real time to obtain and would
otherwise be re-discovered.

## The pipeline works. The weak model does not use it.

The final cross-session test isolated the failure exactly:

| step | result |
|---|---|
| agent calls `remember` unprompted | works |
| fact written to `MEMORY.md`, correctly phrased | works |
| survives into a separate process | works |
| recall finds it | works |
| injected into the next session's wire payload | works |
| **model acts on it** | **fails** |

Control arm 3/3 `UNRESOLVED`, so the test itself was sound — without memory
the agent genuinely could not resolve the alias. With memory the recalled text
was sitting in the transcript, immediately followed by the model asking which
repository was meant.

Two suspects, both in the injection rather than the storage:

* The label reads `Relevant notes recalled from memory (not part of the user's
  message)`. Intended to stop the model attributing the fact to the user; may
  instead read as "this is not the real input".
* The snippet renders as `- # Memory - When the user says...`, carrying its
  own markdown header inline. Looks like noise.

Untested: whether a stronger model uses the same injection correctly. That is
the cheapest next experiment if this is revisited.

## Public GitHub facts cannot demonstrate memory's value

Three targets were screened and all three were already known to the model:

| target | baseline without memory |
|--------|------------------------|
| issues vs PRs in `search/issues` | 7/8 correct |
| total commit count (Link-header trick) | 4/4 correct |
| contributor count (pagination) | 3/3 correct |

The pattern: **a fact well-documented enough to come to mind as "a trap agents
fall into" is well-documented enough to be in the weights.** Selecting targets
by asking "what trips up agents on this API?" selects precisely for facts
training already covers.

The original "8 open issues" observation that motivated all of this was a
single unlucky sample, over-fitted into an entire experimental design.

## What only worked because it was checked

Every one of these was caught by an explicit check, not by tests passing:

* **The scorer scored its own error messages.** `\b(\d+)\b` over the whole
  answer matched the `3` in `ConnectError: [Errno -3]`, so seven trials that
  never reached the API scored `RIGHT (3)`. The arm read 8/8 having run zero
  times.
* **`DISCARDED` swallowed the control signal.** An empty answer was binned
  with network errors, when "the agent could not answer" is exactly the
  control result being looked for. Now split by `stop_reason`.
* **Only 5 of 14 memory modules run.** Traced, not assumed. `mmr`,
  `query_expansion`, `embedding`, `dream`, `dream_lock`, `watcher`,
  `archive` and `text_utils` never execute; vector KNN, MMR re-ranking,
  query expansion and access-boost are all off by default in
  `MemoryService`. What is actually running is BM25 keyword search over a
  markdown file, plus temporal decay.

## Practical rules for the next attempt

1. **Screen the baseline before running an A/B.** A target must fail at least
   half the time without memory, or there is no headroom to measure.
2. **A trial that errored is never data.** Discard it; never let a digit in a
   stack trace become a score.
3. **Score the final line, not the whole answer** — working notes routinely
   contain the wrong value on the way to the right one.
4. **Compute ground truth at run time.** An active repository invalidates a
   hardcoded expected value within days.
5. **Log the session.** The one diagnosis that mattered came from reading the
   transcript, and was impossible in the run that omitted it.
