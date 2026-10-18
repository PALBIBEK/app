# Decision: extract capability Protocols (protocols-extract)

**Date:** 2026-08-23  
**Status:** accepted  
**Queue:** step 0 (`protocols-extract`)

## Context

githi constructed `SubagentService` / job / continuable registries inside `Agent.__init__`
and assembled system prompts by importing `build_system_prompt` on the run path. That
blocks swapping providers and violates pluggability rules P-3 / A-4.

## Decision

Introduce `githi.seams` with four Protocols (`ToolProvider`, `PromptAssembler`,
`SubagentTransport`, `ModelGatewayClient`), in-process first providers, and
`build_default_seams` as the sole production name→impl map. `Agent` accepts injected
seams; default path binds via factory without constructing registries in `Agent.__init__`
source.

## Consequences

- Façades `agent.subagents` / `subagent_jobs` / `subagent_continuable` remain for tools.
- Catalog digests, ToolSet load, and Model Gateway service stay later QUEUE steps.
- Supervisor journal/session/api stay free of githi types; mirror doc at
  `agent-platform/contracts/seams.md`.
