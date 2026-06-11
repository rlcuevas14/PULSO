# stub temporal — se reemplaza en F1e
import asyncio


async def worker_loop():
    while True:
        await asyncio.sleep(3600)
