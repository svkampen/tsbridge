"""
 * noload
"""
import os
import json
import asyncio
import logging
import traceback
from typing import Dict, List, NoReturn, Awaitable, Iterator, Tuple, Any, Optional
from asyncio import Future, Task, FIRST_COMPLETED
from datetime import timedelta

logger = logging.getLogger('server-query')

def kv_parse(string: str) -> Dict[str, str]:
    data = {}
    for kv in string.split(' '):
        try:
            val = kv.split('=', 1)[1]
        except IndexError:
            val = ''
        data[kv.split('=')[0]] = ServerQueryClient.unescape(val)
    return data

class SQResult:
    def __init__(self, data: str) -> None:
        self.data = data

    def kvs(self) -> Dict[str, str]:
        return kv_parse(self.data)

    def split(self, token: str) -> List['SQResult']:
        return [SQResult(part) for part in self.data.split(token)]

class SQError(Exception):
    def __init__(self, message: str, errno: int):
        super().__init__(f'{errno}: {message}')
        self.errno = errno

class ServerQueryClient:
    def __init__(self, username: str, password: str,
                 host: str = '127.0.0.1', port: int = 10011) -> None:
        self.running = True
        self.host = host
        self.port = port
        self.username = username
        self.password = password

        self.request_queue: asyncio.Queue = asyncio.Queue()
        self.notify_queue: asyncio.Queue = asyncio.Queue()

        self.task = asyncio.create_task(self.async_client(), name="sq: client")
        self.ping_task = asyncio.create_task(self.ping(), name="sq: ping")
        self.loop = asyncio.get_event_loop()
        self.debug = True

    @classmethod
    async def create(cls, *args, **kwargs) -> 'ServerQueryClient': # type: ignore
        self = cls(*args, **kwargs)
        await self.raw_request(f'login {self.username} {self.password}')
        await self.raw_request(f'use 1')
        return self

    async def ping(self) -> None:
        while self.running:
            await asyncio.sleep(60)
            response = await self.raw_request('version')

    def raw_request(self, req: str) -> asyncio.Future:
        fut = self.loop.create_future()
        # The queue has no limit, so put_nowait works.
        # This allows us to just return the future, which means we won't
        # get odd syntax like await await self.raw_request(...)
        self.request_queue.put_nowait((req, fut))
        return fut

    def request(self, req: str) -> asyncio.Future:
        fut = self.raw_request(req)
        new_fut = self.loop.create_future()
        def _callback(fut: asyncio.Future) -> None:
            try:
                result = fut.result()
                new_fut.set_result(SQResult(result))
            except SQError as e:
                if (e.errno == 1541):
                    new_fut.set_result(SQResult(''))
                else:
                    new_fut.set_exception(e)
        fut.add_done_callback(_callback)
        return new_fut

    async def stop(self) -> None:
        self.running = False
        self.task.cancel()
        self.ping_task.cancel()

    def __await__(self) -> Iterator[Tuple[Any, Any]]:
        return iter(asyncio.gather(self.task, self.ping_task))

    def get_error(self, in_data: str) -> Tuple[str, int]:
        try:
            errstr = in_data.split('\n')[-2].strip()
        except IndexError:
            errstr = in_data.strip()
        assert errstr.startswith('error')

        data = kv_parse(errstr)
        return ServerQueryClient.unescape(data['msg']), int(data['id'])

    async def process_notification(self, line: str) -> None:
        assert line.startswith('notify')
        logger.info(f'Received notification: {line}')
        res = SQResult(line)
        await self.notify_queue.put(res)

    async def readline(self) -> str:
        return (await self.reader.readline()).strip().decode('utf-8')

    async def handle_req_or_notify(self, req_aw: Optional[Task] = None, notify_aw: Optional[Task] = None) -> Tuple[Optional[Task], Optional[Task]]:
        if req_aw is None:
            req_aw = asyncio.create_task(self.request_queue.get(), name="sq: request queue waiter")
        if notify_aw is None:
            notify_aw = asyncio.create_task(self.readline(), name="sq: network read waiter")

        done, pending = await asyncio.wait({req_aw, notify_aw},
                                           return_when=FIRST_COMPLETED)

        if notify_aw in done:
            line = await notify_aw
            await self.process_notification(line)
            return (req_aw, None)

        request, future = await req_aw

        self.writer.write((request + '\n').encode('utf-8'))
        await self.writer.drain()

        req_data = ''
        line = await notify_aw # the 'notify' task will contain the first response str

        if not 'error' in line:
            req_data += line
            line = (await self.readline())
            while not line.startswith('error'):
                if line.startswith('notify'):
                    await self.process_notification(line)

                req_data += line + '\n'
                line = (await self.readline())

        error_data = kv_parse(line)
        logger.debug(f'Req: {request}. Read response: {req_data.strip()}. Error line: {line.strip()}')

        if error_data['id'] != '0':
            future.set_exception(SQError(error_data['msg'], int(error_data['id'])))
        else:
            future.set_result(req_data.strip())

        return (None, None)

    @staticmethod
    def escape(string: str) -> str:
        """ Escape a string according to the replacement rules in the SQ manual. """
        string = string.replace('\\', r'\\').replace('/', r'\/').replace(' ', r'\s').replace('|', r'\p')

        for c in "abfnrtv":
            # replace, e.g., \n (newline) with \\n (backslash-n)
            string = string.replace(eval(rf'"\{c}"'), rf"\{c}")

        return string

    @staticmethod
    def unescape(string: str) -> str:
        """ Unescape a string """
        string = string.replace(r'\\', '\\').replace(r'\/', '/').replace(r'\s', ' ').replace(r'\p', '|')

        for c in "abfnrtv":
            string = string.replace(rf"\{c}", eval(rf'"\{c}"'))

        return string

    async def async_client(self) -> None:
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        welcome_message = await self.reader.read(16384)

        logger.info("[sq_client] Read welcome message.")
        args: Tuple[Optional[Task], Optional[Task]] = (None, None)

        try:
            while self.running:
                args = await self.handle_req_or_notify(*args)
        finally:
            self.writer.close()
            await self.writer.wait_closed()
