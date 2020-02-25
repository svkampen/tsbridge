from core.types import Proto, OutPort, Message, Attachment, Channel, Config, Photo
from core.bridge import Bridge

from typing import Mapping, Any, Dict
import asyncio
import logging

logger = logging.getLogger('irc')

class IRCProto(Proto):
    async def start(self, bridge: Bridge, out_port: OutPort, instance_cfg: Config) -> None:
        self.bridge = bridge
        self.out_port = out_port

        self.nick = instance_cfg['nick']
        self.host, self.port = instance_cfg['server']

        logger.info(f'Opening connection to {self.host}:{self.port}')
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        asyncio.create_task(self.client_loop())

        self.joined_channels = []

    async def write(self, data: str) -> None:
        self.writer.write((data + '\n').encode('utf-8'))
        await self.writer.drain()

    async def readline(self) -> str:
        return (await self.reader.readline()).decode('utf-8')

    def parse(self, msg: str) -> Dict[str, str]:
        if (msg.startswith('PING')): return ('', 'PING', msg.split(' :')[1])

        host, method, args = msg.split(' ', 2)
        if ' :' in msg:
            simple, trailing = args.rsplit(' :', 1)
            args_parsed = [*simple.split(), trailing]
        else:
            args_parsed = args.split()
        return (host[1:], method, args_parsed)

    async def client_loop(self):
        self.write(f'NICK {self.nick}')
        self.write(f'USER {self.nick} * * :Bridge')

        while (line := await self.readline()):
            host, method, args = self.parse(line)
            if (method == 'PING')
