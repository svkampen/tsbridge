import asyncio
from typing import Iterable


def load_cfg_value(val: str) -> str:
    if val.startswith("file:"):
        path = val.split(":", 1)[1]
        with open(path, "rb") as f:
            return f.read().decode("utf-8").strip()
    elif val.startswith("const:"):
        return val.split(":", 1)[1]
    raise ValueError("invalid config value")


async def wait_forever() -> None:
    await asyncio.Future()


async def wait_reraise(fs: Iterable[asyncio.Task]) -> None:
    done, pending = await asyncio.wait(fs, return_when=asyncio.FIRST_EXCEPTION)
    for task in done:
        if ex := task.exception():
            raise ex
