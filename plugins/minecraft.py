"""
* depends: imgflt
"""
from core.types import Proto, OutPort, Message, Attachment, Channel, Config, Photo, JoinMessage, PartMessage, ServiceMessage, UserRequest, UserList
from core.bridge import Bridge
from typing import Mapping, Any
import logging
import json
import inspect
import asyncio
import functools
import re

logger = logging.getLogger('minecraft')

CHANNEL_NAME = "chat"

SERVER_MESSAGE_RE = re.compile(r"\[[0-9:]+\] \[Server thread/[ A-Z]+\]: (.+)")

USERNAME = "[A-Za-z0-9_-]+"

def match(regex):
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(self, line, *args, **kwargs):
            if (re_match := re.match(regex, line)):
                await fn(self, line, re_match.groups(), *args, **kwargs)
        wrapper._is_match = True
        return wrapper
    return decorator

class MinecraftProto(Proto):
    """
    Protocol to interact with a Minecraft server over a pseudoterminal.
    """
    async def start(self, bridge: Bridge, out_port: OutPort, instance_cfg: Config) -> None:
        self.pty_reader = open(instance_cfg['pty_file'], 'r')
        self.pty_writer = open(instance_cfg['pty_file'], 'w')
        self.img_host = bridge.get_attachment_host()
        self.out_port = out_port
        asyncio.get_event_loop().add_reader(self.pty_reader, self.handle_recv)
        self.match_funcs = []
        for _, fn in inspect.getmembers(self, predicate=callable):
            if hasattr(fn, '_is_match'):
                self.match_funcs.append(fn)

    def handle_recv(self):
        line = self.pty_reader.readline().strip()
        logger.info(f"Read line from PTY: {line!r}")
        match = SERVER_MESSAGE_RE.match(line)
        if not match:
            return

        text = match.group(1)
        for fn in self.match_funcs:
            asyncio.create_task(fn(text))

    @match(f"({USERNAME}) (joined|left) the game")
    async def join_part(self, line, groups):
        logger.info(f"{groups[1].title()}: {groups[0]}")
        if groups[1] == "joined":
            msg = JoinMessage(groups[0], CHANNEL_NAME)
        else:
            msg = PartMessage(groups[0], CHANNEL_NAME)
        await self.out_port.put_message(msg)

    @match(f"[\\[<]({USERNAME})[>\\]] (.+)")
    async def message(self, line, groups):
        user, message = groups
        logger.info(f"Message from {user}: {message}")
        message = Message(user, message, CHANNEL_NAME)
        await self.out_port.put_message(message)

    @match(r"There are (\d+) of a max of (\d+) players online: (.+)")
    async def online(self, line, groups):
        _, _, users = groups
        ulist = UserList(users.split(', '), CHANNEL_NAME)
        await self.out_port.put_message(ulist)

    @match(f"{USERNAME} has made the advancement .+")
    async def advancement(self, line, groups):
        pass

    async def send_message(self, to_channel: Channel, message: Message):
        logger.info(f"Got message to send: {message}")
        fmt = [{'text': 'Bridge: ', 'color': 'blue'}]

        for attachment in message.attachments:
            if isinstance(attachment, Photo):
                url = await self.img_host.put(attachment.get())
                fmt.append({'text': '[IMG]', 'color': 'gold', 'clickEvent': {'action': 'open_url', 'value': url}})

        fmt.append({'text': f"{message.user}: {message.text}", 'color': 'white'})
        self.pty_writer.write("tellraw @a " + json.dumps(fmt) + "\n")
        self.pty_writer.flush()

    async def handle_service_message(self, to_channel: Channel, message: ServiceMessage):
        if isinstance(message, UserRequest):
            self.pty_writer.write("list\n")

def init(bridge: Bridge) -> None:
    bridge.add_protocol('minecraft', MinecraftProto)
