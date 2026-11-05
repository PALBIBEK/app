# Browser code mode: design, experiment, plan

## What we are building

A browser capability for supervisor agents that is as cheap and reliable as the best
public designs, built from our own pieces:
- the `ToolSpec` registry;
- sessions' sandboxes and their workspace;
- digest-pinned tool sets, sandbox profiles and compositions;
- Temporal-durable runs.

It takes the best of two references:

| | Hermes (`browser_exec`) | LangChain + Stagehand (`run`, `snapshot`, `screenshot`) |
|---|---|---|
| Took | One code-mode tool: Python in, stdout out. The browser outlives the call; Python state doesn't. A workspace folder for batch files and `agent_helpers.py`. A pinned helper digest in the tool description. | Playwright-shaped `page` code, because models write it best. Steps run next to the browser, so there's no network hop per step. Trimmed snapshots with element IDs (PR 2). A quiet wait (PR 2). |
| Skipped | Twelve per-action tools, which Hermes itself retired. Its host-side daemon, which our sandbox replaces. | An in-browser extension: our code already runs in the sandbox, next to the browser. |

The model writes a short Python program per sub-task. `browser_exec` ships the runner
(`services/supervisor/supervisor/hands/browser/runner.py`) into the session's sandbox and
runs the code there, with Playwright's `page` connected to a headless Chromium. That
Chromium starts once per browser name, in a session of its own and without the call's
run tag, so the call's end and a stop both leave it running. Only what the code prints
comes back.

## The experiment: playing the model, step by step

Claude played the agent's model. It wrote every call, read only what the tool returned,
and decided the next step, through the real `browser_exec` on a workspace that runs
commands as a sandbox does. Sandboxes have no internet, so the sites are local:

- **A real Django 5.2 admin** (a library: 4 branches, 60 members, 120 books, 420 loans):
  a login with a CSRF token, list filters, search, sorting, pagination, a bulk action
  with a confirmation page, and the two-box many-to-many picker and calendar widgets.
- **A hard single-page shop**: a product list in a shadow DOM that loads more as it
  scrolls (300 items, the target at 237); a size picker built from `div`s, not a
  `select`; a coupon read from an inbox in another tab; payment in an iframe; a native
  `confirm()`. The server checks the order.

| Task | What it asks | Correct outcome |
|---|---|---|
| D1 | Log in, then mark every overdue (due before 2026-11-01), unreturned loan of a Pune member as returned with the bulk action | 35 returned, no other loan touched |
| D2 | Add the book "The Durable Agent" (an ISBN, a date, genre Tech, two authors) | **Refused**: the ISBN already belongs to another book. The agent must notice and report it, not invent another ISBN. The collision was an accident of the seed data, and it made the test better |
| D3 | Which member borrowed the most books in 2025? | Arjun Das 17, 8 loans (216 loans over 11 pages) |
| S1 | Buy "Trail Runner X" in EU 42 with the inbox's coupon, pay by card, and report the order number | An order for item 237, size 42, coupon applied, £109.65 |

### Arms

| Arm | How the model works |
|---|---|
| **A**, the per-action baseline | One action per call, and every result is the page's whole accessibility snapshot, as Playwright MCP returns it. When a snapshot passes the workspace's 12k-character cap, the model reads the kept whole (counted as a read). |
| **B**, Hermes-style | Hermes's helpers only (`js`, `fill_input`, `goto_url`, `cdp`, …), batched, with no Playwright `page` methods. |
| **C**, Playwright code | Batched code against `page`. |
| **D**, snapshot IDs | A Stagehand-style `snapshot()` and `act(id)`, written as the agent's own `agent_helpers.py` (`arms/snapshot_ids_agent_helpers.py`), with code for data work. |
| **E**, the shipped tools (re-run) | What PRs 2–6 ship, and nothing written by the agent: the daemon's browser, the built-in `snapshot()`, `act()` and `settle()`, `browser_vault` for the login (the password never in code or context), dialogs reported and answered, and `show_screenshot()` available (not needed). |

### Results

Every arm reached every correct outcome, checked against the site's own state by
`oracle.py`, whose answers were hidden until the end. What it cost:

| Arm | Calls | Failed calls | Result characters | **Input tokens re-read** | Seconds in tools |
|---|---|---|---|---|---|
| A, per action | 96 (+27 reads) | 3 | 1,339,620 | **5,588,214** | 164 |
| B, Hermes helpers | 10 | 2 | 2,685 | **4,866** | 35 |
| C, Playwright code | 14 | 2 | 6,216 | **8,502** | 99 |
| D, snapshot IDs | 18 | 2 | 17,731 | **25,472** | 29 |
| E, the shipped tools | 27 | 1 | 21,023 | **30,832** | 57 |

"Input tokens re-read" counts each turn resending every earlier result (characters ÷ 4).
That is why per-action costs compound: A's D1 alone re-read 2.16M tokens. Per-task
figures are in `results/calls.jsonl` (`python run.py summary`). C's time is mostly two
30-second waits on a locator that named nothing (fixed, below).

**Bias.** One model played every arm in turn, so later arms knew the site. The order was
A, then C, B, D. Above all, every arm after A knew the checkout's confirm dialog existed.
The tasks run from a library admin and a shop that no arm had seen before A. Read the
gap between A and the code arms as the size of the effect, not its exact figure.

### What we found, and what changed

Found in this PR's tool, by using it, and fixed before merging:

1. **Dialogs vanished.** Playwright dismisses a dialog no handler takes, and says
   nothing. A's checkout clicked Pay, then nothing happened and nothing was reported.
   Now every dialog is listed in the result, and `answer_dialogs("accept")` sets the
   answer, which is dismiss by default. Hermes has `browser_dialog`; ours runs inside
   the call.
2. **One missed locator cost 30 seconds.** That is Playwright's default, hit 4 times
   in the counted calls, and 13 more in runs excluded for harness and site bugs. Actions now wait 5 s; navigation keeps 30 s.
3. **The workspace's 12k cut went unreported.** A's snapshots silently lost the middle
   of the table. The result now says how much was cut and keeps the whole in the
   workspace, as `bash` does.
4. **`agent_helpers.py` ran in a namespace of its own**, so a saved helper couldn't see
   `page`. It now runs in the call's own scope.

Lessons for PR 2's snapshot, from arm D's prototype:

5. **IDs must be stable and checked as stale.** After one click, the IDs shifted: a
   checkbox was `0-38`, then `0-39`, and `0-38` named another element. Old tags also
   collided with new ones, and only Playwright's strict mode stopped a fill landing in
   the wrong field. Stagehand's rule holds: each snapshot replaces the page's ID map, and
   an ID from an older one is refused.
6. **A flat list is not enough.** 240 lines of "button: View" can't say which one is
   Trail Runner X's. Keep the tree's structure, as Stagehand does, collapse repeated
   siblings, and favour the topmost layer (an open dialog).
7. **Settling matters.** C's first read after a save came before the page had loaded,
   and showed no error. B's `js(submit)` followed by `wait_for_load()` returned at once,
   on the old page, and cut the login short. B then counted one page's rows twice and
   followed Django's past-the-end redirect, for a confident wrong answer (596 rows,
   "Ravi Das 50") that only the `?e=1` in the URL gave away. A wait that settles after an
   action (Stagehand's quiet wait, plus waiting for a navigation the action started)
   belongs in the helpers.

For later:

8. **Secrets reach the context.** A's snapshots echoed the typed password, and every arm
   wrote the password and card number into its code. Hermes's `browser_vault` (fill from
   saved credentials, never through the model) is the shape to follow.
9. **Raw JavaScript is brittle on stateful widgets.** Setting `option.selected` didn't
   move authors through Django's picker until the code sent the double-click it listens
   for. Playwright's `select_option` gets this right. Hermes-style `js()` stays as an
   escape hatch, not the default.

### The re-run: arm E, the shipped tools

Every outcome was correct again: D1's 35 loans returned and none other touched, D2
refused and reported with the book that holds the ISBN ("Beyond Hidden Engine 42"),
D3's Arjun Das 17 counted over all 420 loans (216 in 2025), and S1's order `SO-10230E`
for item 237 in size 42 with the inbox's coupon, £109.65. The login went through
`browser_vault` by snapshot IDs: the password is in no call's code and no result.

D2's and S1's inputs were not written down in the first run, so they are pinned here:
D2 is ISBN 9780000000042, published 2026-05-01, genre Tech, authors Ada Lovelace and
Grace Hopper; S1 pays with the test card 4242 4242 4242 4242, 12/28, 123.

E cost more than B to D, and less than A by two orders of magnitude. The same bias
applies as before, the other way round: E came months later and had to find the site
again (the paginator, the shadow list's own scroll box, the inbox). Most of the gap is
four things the tools did wrong, found by using them, and fixed in the same PR:

10. **A search missed what a collapsed run hid.** `snapshot(contains="Loan")` on the
    admin index found nothing: "Loans" was in "… 3 more like the above". A search now
    renders the tree uncollapsed, then picks the matches and their groups.
11. **Disclosures were invisible.** The inbox's messages are `<details>`; Chromium calls
    a `<summary>` a `DisclosureTriangle`, which the snapshot left out, so a model that
    reads only snapshots could not have found the coupon. It is shown now
    (`disclosure: Your coupon`) and opens by its ID.
12. **An ID could not become a locator.** Choosing two authors in a multi-select is
    beyond `act()`; `locate(id)` is now in scope and returns the Playwright locator.
13. **One repeated event flooded the result.** The admin logs the same console error on
    every page: D3's paging call returned it 20 times (most of its 3,535 characters). A
    repeat is now listed once, with `times`.

One cost was the model's own: `snapshot(contains="42")` for the shoe size matched every
£42 price (3,941 characters). The browser prompt now says to read what matters with code
when a page is known, and the snapshot to find it.

## Reproduce

```
pip install "django==5.2.*"            # serve and score
python run.py serve                    # fresh data; the hidden answers go to site/oracle.json
.venv/bin/python run.py call C D1 <<'PY'   # the supervisor's virtualenv
new_tab("http://127.0.0.1:8800/admin/")
print(page.title())
PY
.venv/bin/python run.py vault E D1 <<'JSON'  # browser_vault, with the admin's login saved
{"action": "fill", "login": "http://127.0.0.1:8800", "username": "0-1", "password": "0-3", "submit": "0-8"}
JSON
python run.py score && python run.py summary
```

## Plan, and where it landed

- **PR 1 (#158):** the tool, the runner, the provider seam, the opt-in catalog entries,
  and fixes 1–4.
- **PR 2 (#161):** a daemon per browser that owns Chromium, a CDP connection and an event
  buffer (Hermes's daemon, in the sandbox).
- **PR 3 (#163):** `snapshot()` and `act()` with stable, checked IDs (lessons 5–6), and
  the settle and quiet waits (lesson 7).
- **PR 4 (#164):** sandbox networks by profile (`none`, `open`, an allow-list) enforced by
  the provider, and a remote browser provider over CDP.
- **PR 5 (#165):** `browser_vault`: saved logins typed in for the agent, and every result
  cleaned of them (lesson 8).
- **PR 6 (#167):** screenshots shown to vision models.
- **PR 7 (#168):** browser agents from the builder (network and browser on the Sandbox
  tab), and the shipped browser agent's prompt and open network.
- **PR 8 (this one):** the re-run above, and fixes 10–13.
