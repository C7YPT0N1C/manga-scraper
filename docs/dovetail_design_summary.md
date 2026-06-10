x# Dovetail — Design Summary (Ideas, Changes, and Planned Extensions)

This document consolidates all discussed ideas, proposed changes, and potential extensions for the Dovetail project.

---

# 1. Core Dovetail (Existing Foundation)

These are the current stable primitives of Dovetail.

## Execution primitives
- `dvt.task.run_blocking`
- `dvt.task.to_thread`
- `dvt.task.map_blocking`
- `dvt.task.schedule`

## Existing capabilities
- Sync ↔ async bridging
- ThreadPoolExecutor-based execution
- Async task scheduling
- Rate limiting (token bucket)
- Retry + backoff system
- Timeout handling
- Lifecycle management (shutdown / registry)
- Event system (`on_start`, `on_end`, etc.)
- Execution statistics (`dvt.events.stats()`)

---

# 2. API Evolution Ideas (Within Dovetail)

These are possible improvements or facades over the existing core.

## A. Cleaner Public API (Optional Facade)
Possible simplified interface:
- `dvt.run(...)`
- `dvt.spawn(...)`
- `dvt.map(...)`

Backed internally by existing primitives.

---

## B. Context-Aware Execution
- Same API behaves differently depending on sync vs async context
- Removes need for separate sync/async method namespaces

---

## D. Structured Concurrency (Liked Idea)
Concept:
- Task scopes
- Grouped cancellation
- Automatic cleanup of child tasks

Example:
```python
with dvt.scope() as s:
    s.spawn(task1)
    s.spawn(task2)
```

---

## E. Execution Policy System (Liked Idea)
Attach rules to execution units:

```python
dvt.policy(
    function=fetch,
    retries=3,
    timeout=10,
    rate_limit=5
)
```

Or named operations:

```python
dvt.register("fetch", fn, retries=3)
```

---

# 3. Separate Tooling (Not Core Dovetail)

These are intended as external or optional systems built on top of Dovetail.

## A. Concurrency Testing Harness
Purpose:
- Simulate load
- Inject latency
- Simulate failures
- Stress test concurrency logic

Example concept:
```python
dvt.test.run(
    func=my_pipeline,
    load=1000,
    failure_rate=0.1
)
```

---

## B. Observability Dashboard
A runtime UI for:
- Active tasks
- Execution durations
- Failures
- Retries
- Rate limiting delays

Goal:
> Debug and understand concurrency behavior visually

---

## C. Plugin System
Possible extensions:
- Logging plugins
- Metrics exporters
- Policy modifiers
- Event listeners

---

# 4. Ideas Explicitly Rejected or Deferred

- ❌ Full workflow/DAG engine (Airflow-style system)
- ❌ Distributed execution framework (Dovetail Dispatch as core)
- ❌ Custom network protocol for task shipping
- ❌ Arbitrary code execution over network

---

# 5. Deferred Concept: Distributed Execution (Dispatch)

Explored idea:
- Sending tasks to other machines

Final decision:
- Not part of core Dovetail
- Possibly a separate project in the future
- Should use safe function registries instead of arbitrary code transfer

---

# 6. Key Design Principle

Dovetail is best understood as:

> A local execution control layer for Python concurrency

Not:
- A distributed system
- A workflow engine
- A job orchestration platform

---

# 7. Architectural Direction

## Layer 1 — Execution Core
- run / map / schedule
- threading + asyncio bridging

## Layer 2 — Control Layer
- retries
- rate limits
- timeouts
- structured concurrency
- execution policies

## Layer 3 — Tooling Layer (External)
- dashboards
- testing harness
- plugins

---

# 8. Summary

Dovetail is evolving toward:

- Cleaner execution primitives
- Optional higher-level control abstractions
- External tooling for observability and testing

While explicitly avoiding:

- Distributed system complexity
- Workflow orchestration scope creep
- Network protocol design