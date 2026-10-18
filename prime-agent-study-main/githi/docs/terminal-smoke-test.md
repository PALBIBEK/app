# Terminal smoke test

Run `githi` from a real terminal with a configured OpenRouter key and workspace.

1. Enter a multiline prompt with Shift+Enter; submit with Enter.
2. Paste a multi-line prompt and use Up to recall it.
3. During streaming, press ESC and confirm the terminal remains usable.
4. During a run, type a steering message and press Enter; type a follow-up and press Alt+Enter.
5. Run `!printf 'one\n'; sleep 10`, then press ESC; confirm partial output and cancellation.
6. Ask the agent to run a long bash command, press ESC, and confirm the command stops and the next prompt works.
7. Use `/new`, `/mark checkpoint`, `/tree`, and select a branch; confirm `/rewind` restores it.
8. Resize the terminal during output, exit with Ctrl-D, and confirm normal shell echo and line editing remain intact.

Record the terminal emulator, OS, and any failed step with the session path.
