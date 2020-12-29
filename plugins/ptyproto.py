from core.types import Proto, OutPort, Message, Config
from core.bridge import Bridge
from typing import Callable, Any, Dict, List, Awaitable
import io
import asyncio
import re
import logging
import functools
import inspect


class PtyProto(Proto):
    """
    Base class for protocols which wrap a server console over a pseudoterminal.

    This class opens the pty defined in the instance config as pty_file, adds
    a reader which handles data reception on that pty, and dispatches to match
    functions which use the match decorator above.

    Derived classes can redefine SERVER_MESSAGE_RE to pre-match messages to handle
    and extract the text to match on later in the first group. By default, this simply
    matches anything.
    """
    SERVER_MESSAGE_RE = "(.+)"

    @staticmethod
    def match(regex: str) -> Callable:
        def decorator(fn: Callable) -> Callable:
            @functools.wraps(fn)
            async def wrapper(self: Any, line: str) -> None:
                re_match = re.match(regex, line)
                if re_match:
                    await fn(self, line, re_match.groups())
            setattr(wrapper, '_is_match_func', True)
            return wrapper
        return decorator


    def register_match_functions(self) -> None:
        self._match_funcs: List[Callable[[str], Awaitable[None]]] = []
        for _, val in inspect.getmembers(self):
            if getattr(val, '_is_match_func', False):
                self._match_funcs.append(val)


    async def start(self, bridge: Bridge, out_port: OutPort, instance_cfg: Config) -> None:
        if not hasattr(self, 'logger'):
            # derived classes can define their own logger earlier
            self.logger = logging.getLogger('ptyproto')

        self.config: Config = instance_cfg
        self.pty = io.FileIO(instance_cfg['pty_file'], 'r+')
        asyncio.get_event_loop().add_reader(self.pty, self.handle_recv)

        self.out_port = out_port
        self._buffer = ""

    def handle_recv(self) -> None:
        # mypy will complain, but read should never return None here
        # as we've just been informed data /is/ available.
        data = self.pty.read(8192).decode('utf-8')  # type: ignore
        self._buffer += data

        *lines, self._buffer = self._buffer.split('\n')

        async def handle_matches(lines: List[str]) -> None:
            for line in lines:
                # we don't do RTL/LTR marks in text
                line = line.replace("\u200e", "")
                self.logger.info(f"Read line from PTY: {line!r}")
                match = re.match(self.SERVER_MESSAGE_RE, line)
                if not match:
                    continue

                text = match.group(1)
                for fn in self._match_funcs:
                    await fn(text)

        asyncio.create_task(handle_matches(lines))

