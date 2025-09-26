#!/usr/bin/env python3
# nhscraper/core/scheduler.py

import asyncio
from nhscraper.core.configurator import *

class Scheduler:
    def __init__(self):
        self.tasks: list[asyncio.Task] = []

    def schedule(self, coro, name: str = "unnamed"):
        task = asyncio.create_task(self._wrap(coro, name))
        self.tasks.append(task)
        return task

    async def _wrap(self, coro, name: str):
        try:
            await coro
        except Exception as e:
            log(f"Scheduler task {name} failed: {e}", "error")

    async def cancel_all(self):
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()

scheduler = Scheduler()