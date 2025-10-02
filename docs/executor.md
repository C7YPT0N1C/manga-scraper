Executor Usage Guide
===================

Executor functions:
- executor.invoke()
- executor.await_async()
- executor.spawn_task()

General Rules:
1. If Step B depends on Step A finishing → use await.
2. If order/result doesn't matter → call without await (fire-and-forget).
3. Sync functions: only executor.await_async() (always blocks).
   ⚠️ Can run async coroutines here; it will block until completion.
4. Async functions: use executor.spawn_task(), executor.invoke(), or executor.await_async() depending on need.

Best Practices:
- Always pass function + arguments, never pre-call the function.
- For async tasks, always pass a coroutine object to executor.spawn_task().
- Use executor.invoke() in async code for sync functions (runs them in a thread).
- executor.await_async() is blocking: use in sync code or rare async cases where blocking is intentional.



1️⃣ executor.invoke()

Use case: you are in an async function and want to safely call a sync function (or coroutine if you’re unsure).

It’s like a “smart wrapper” that decides for you.

✅ Great for generic code where you don’t know what kind of function you’ll get.

🛑 Avoid if you already know the type and can pick the most efficient executor method.



2️⃣ executor.spawn_task()

Use case: you are in an async function and want to run a coroutine asynchronously.

Two modes:

await executor.spawn_task(coro(...)) → pause until result.

executor.spawn_task(coro(...)) → fire-and-forget.

✅ Use this for known async functions.

✅ Can wrap sync functions with lambda for fire-and-forget background execution.




3️⃣ executor.await_async()

Use case: you are in a sync function and want to run a coroutine synchronously, or you need to block the event loop in an async function for some reason.

✅ Use this when you must wait immediately for a result.

⚠️ Rare in async code; it blocks the entire loop.


Quick Reference Table
---------------------
| Context            | Called Function Type | Need Result?               | Method                                                        | Lambda Needed? | Notes                                          |
|--------------------|----------------------|----------------------------|---------------------------------------------------------------|----------------|------------------------------------------------|
| Sync (def)         | Sync                 | Yes                        | executor.await_async(func, *args)                            | ❌             | Blocks current thread until done               |
| Sync (def)         | Sync                 | No                         | executor.await_async(func, *args)                            | ❌             | Must block; fire-and-forget not possible       |
| Sync (def)         | Async                | Yes                        | executor.await_async(coro(...))                              | ❌             | Blocks until coroutine finishes                |
| Sync (def)         | Async                | No                         | ❌ Not possible                                               | ❌             | Cannot fire-and-forget in sync context         |
| Async (async def)  | Async                | Yes                        | await executor.spawn_task(coro(...))                          | ❌             | Pauses until result available                  |
| Async (async def)  | Async                | No                         | executor.spawn_task(coro(...))                                | ❌             | Fire-and-forget                                |
| Async (async def)  | Sync                 | Yes                        | await executor.invoke(func, *args)                | ❌             | Runs sync function in background thread        |
| Async (async def)  | Sync                 | No                         | executor.spawn_task(lambda: func(*args), type="io")           | ✅             | Fire-and-forget sync function; lambda wraps it |
| Async (async def)  | Any                  | Yes, must block event loop | executor.await_async(func_or_coro(...))                      | ❌             | Blocks event loop; rare use case               |

Decision Tree: Choosing the right executor method
---------------------------------------------------
Am I in async or sync?

 ├── Sync (def function):
 │    ├── Do I need the result (and block until done)?
 │    │     └── Yes → executor.await_async(func, *args)
 │    │     └── No  → executor.await_async(func, *args) and ignore result.
 │    └── Do I want fire-and-forget?
 │          └── Not possible → must block in sync context.

 └── Async (async def function):
      ├── Do I need the result?
      │     ├── Is the function async? 
      │     │      └── Yes → await executor.spawn_task(coro(...))
      │     │      └── No  → await executor.invoke(func, *args)
      │     └── Do I want to block the event loop anyway?
      │            └── Yes (rare) → executor.await_async(func, *args)
      │
      └── Do I NOT need the result (fire-and-forget / background)?
            ├── Async function → executor.spawn_task(coro(...))
            └── Sync function → executor.spawn_task(lambda: func(*args), type="io")
                  # ✅ Use lambda to wrap sync function in async fire-and-forget

Examples:

# Sync context
def build_session():
    session = executor.await_async(get_session)
    return session

# Async context
async def process_gallery():
    data = await executor.spawn_task(fetch_gallery(), type="gallery")
    config = await executor.invoke(read_config_file, "config.json")
    executor.spawn_task(save_gallery(data), type="io")  # fire-and-forget