from aiorun import run
import asyncio
from core.bridge import Bridge

bridge = Bridge()

run(bridge.start(), loop=asyncio.get_event_loop(), stop_on_unhandled_errors=True)
