import logging
import coloredlogs
from typing import Callable, Any, Coroutine
import asyncio

FORMAT = "[%(asctime)s] [%(levelname)8s] %(name)14s: %(message)s"


class FutureHandler(logging.Handler):
    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        coro_factory: Callable[[str], Coroutine[Any, Any, None]],
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.coro_factory = coro_factory
        self.loop = loop

    def emit(self, record: logging.LogRecord) -> None:
        text = self.format(record)
        coro = self.coro_factory(text)
        asyncio.run_coroutine_threadsafe(coro, self.loop)


def init(name: str = "bridge", level: int = logging.INFO) -> None:
    fh = logging.FileHandler(f"{name}.log")
    sh = logging.StreamHandler()

    formatter = coloredlogs.ColoredFormatter(
        fmt=FORMAT,
        field_styles={
            "name": {"color": "red"},
            "levelname": {"color": "magenta"},
            "asctime": {"color": "cyan"},
        },
    )

    sh.setFormatter(formatter)
    fh.setFormatter(formatter)

    logging.basicConfig(handlers=[sh, fh], level=level)
