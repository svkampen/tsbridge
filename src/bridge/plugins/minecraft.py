"""
* after: imgflt
"""
from ..core.types import Proto, OutPort, Message, Attachment, Channel, Config, Photo, JoinMessage, PartMessage, ServiceMessage, UserRequest, UserList, Metadata, MiscServiceMessage
from ..core.bridge import Bridge
from typing import Mapping, Any, Callable, Coroutine, Sequence, List, Dict, Optional, Awaitable
import logging
import json
import inspect
import asyncio
import functools
import re
import io

logger = logging.getLogger('minecraft')

CHANNEL_NAME = "chat"

SERVER_MESSAGE_RE = re.compile(r"\[[0-9:]+\] \[Server thread/[ A-Z]+\]: (.+)")

USERNAME = "[A-Za-z0-9_-]+"

DEATH_MESSAGES = '(?:' + '|'.join("was shot by;was pummeled by;was pricked to death;walked into a cactus whilst trying to escape;drowned;drowned whilst trying to escape;experienced kinetic energy;experienced kinetic energy whilst trying to escape;blew up;was blown up by;was blown up by;hit the ground too hard;hit the ground too hard whilst trying to escape;fell from a high place;fell off a ladder;fell off some vines;fell off some weeping vines;fell off some twisting vines;fell off scaffolding;fell while climbing;was squashed by;went up in flames;walked into fire whilst fighting;burned to death;was burnt to a crisp whilst fighting;went off with a bang;went off with a bang due to a firework fired from;tried to swim in lava;tried to swim in lava to escape;was struck by lightning;was struck by lightning whilst fighting;discovered the floor was lava;walked into danger zone due to;was killed by magic;was killed by;was slain by;was fireballed by;was stung to death;was shot by a skull from;starved to death;suffocated in a wall;suffocated in a wall whilst fighting;was squished too much;was squashed by;was poked to death by a sweet berry bush;was poked to death by a sweet berry bush whilst trying to escape;was killed trying to hurt;was impaled by;fell out of the world;didn't want to live in the same world as;withered away".split(';')) + ')'

Groups = Sequence[str]

MatchFn = Callable[..., Awaitable[Any]]

def match(regex: str) -> Callable[..., MatchFn]:
    def decorator(fn: MatchFn) -> MatchFn:
        @functools.wraps(fn)
        async def wrapper(self: 'MinecraftProto', line: str) -> None:
            re_match = re.match(regex, line)
            if re_match:
                await fn(self, line, re_match.groups())
        wrapper._is_match = True  # type: ignore
        return wrapper
    return decorator

class MinecraftProto(Proto):
    """
    Protocol to interact with a Minecraft server over a pseudoterminal.
    """
    async def start(self, bridge: Bridge, out_port: OutPort, instance_cfg: Config) -> None:
        self.pty = io.FileIO(instance_cfg['pty_file'], 'r+')
        self.config: Config = instance_cfg
        if 'user_map' not in self.config:
            self.config['user_map'] = {}
        self.img_host = bridge.get_attachment_host()
        self.out_port = out_port
        asyncio.get_event_loop().add_reader(self.pty, self.handle_recv)
        self.match_funcs: List[MatchFn] = []
        self.user_map: Dict[str, str] = self.config['user_map']
        for _, fn in inspect.getmembers(self, predicate=callable):
            if hasattr(fn, '_is_match'):
                self.match_funcs.append(fn)  # type: ignore

        self.buffer = ""

    def handle_recv(self) -> None:
        # mypy will complain, but read should never return None here
        # as we've just been informed data /is/ available.
        data = self.pty.read(8192).decode('utf-8')  # type: ignore
        self.buffer += data

        *lines, self.buffer = self.buffer.split('\n')

        async def handle_matches(lines: List[str]) -> None:
            for line in lines:
                logger.info(f"Read line from PTY: {line!r}")
                match = SERVER_MESSAGE_RE.match(line)
                if not match:
                    continue

                text = match.group(1)
                for fn in self.match_funcs:
                    await fn(text)

        asyncio.create_task(handle_matches(lines))


    @match(f"({USERNAME}) (joined|left) the game")
    async def join_part(self, line: str, groups: Groups) -> None:
        logger.info(f"{groups[1].title()}: {groups[0]}")
        user, action = groups
        user_mapped = self.user_map.get(user, user)
        msg: ServiceMessage
        if groups[1] == "joined":
            msg = JoinMessage(user_mapped, CHANNEL_NAME)
        else:
            msg = PartMessage(user_mapped, CHANNEL_NAME)
        await self.out_port.put_message(msg)

    @match(f"[\\[<]({USERNAME})[>\\]] (.+)")
    async def message(self, line: str, groups: Groups) -> None:
        user, message = groups
        if message.startswith('.nickname '):
            self.user_map[user] = message.split('.nickname ')[1]
            return

        user_mapped = self.user_map.get(user, user)
        logger.info(f"Message from {user_mapped}: {message}")
        msg = Message(user_mapped, message, CHANNEL_NAME)
        await self.out_port.put_message(msg)

    @match(r"There are (\d+) of a max of (\d+) players online: (.*)")
    async def online(self, line: str, groups: Groups) -> None:
        ulist: UserList
        _, _, users = groups

        if users:
            users_mapped = [self.user_map.get(user, user) for user in users.split(', ')]
            ulist = UserList(users_mapped, CHANNEL_NAME)
        else:
            ulist = UserList([], CHANNEL_NAME)
        await self.out_port.put_message(ulist)

    @match(f"({USERNAME})( {DEATH_MESSAGES}.*)")
    async def death(self, line:str, groups: Groups) -> None:
        name, rest = groups
        name_mapped = self.user_map.get(name, name)
        msg = MiscServiceMessage(f"{name_mapped}{rest}", CHANNEL_NAME)
        await self.out_port.put_message(msg)

    @match(f"{USERNAME} has made the advancement .+")
    async def advancement(self, line: str, groups: Groups) -> None:
        pass

    async def generate_msg_json(self, from_instance: str, message: Message, handle_attachments: bool = True) -> Any:
        fmt: List[Dict[str, Any]] = [{'text': f'[{from_instance}] ', 'color': 'blue'}]

        for attachment in message.attachments:
            if isinstance(attachment, Photo) and self.img_host:
                url = await self.img_host.put(attachment.get()) if handle_attachments else ''
                fmt.append({'text': '[IMG] ', 'color': 'gold', 'clickEvent': {'action': 'open_url', 'value': url}})

        if message.reply_to is not None:
            # don't handle attachments in replies as clickEvent doesn't work in tooltips anyway.
            origin = (message.reply_to_origin or '?').upper()
            if isinstance(message.reply_to, Message):
                reply_fmt = await self.generate_msg_json(origin, message.reply_to, False)
                fmt.append({'text': '[REPLY] ', 'color': 'dark_red', 'hoverEvent': {'action': 'show_text', 'contents': reply_fmt}})

        fmt.append({'text': f"{message.user}: {message.text}", 'color': 'white'})
        return fmt


    async def send_message(self, to_channel: Channel, message: Message, meta: Metadata) -> None:
        logger.info(f"Got message to send: {message}")
        from_name = meta.from_instance.upper()

        fmt = await self.generate_msg_json(from_name, message)

        self.pty.write(("tellraw @a " + json.dumps(fmt) + "\n").encode('utf-8'))
        self.pty.flush()

    async def handle_service_message(self, to_channel: Channel, message: ServiceMessage, meta: Metadata) -> None:
        from_name = meta.from_instance.upper()
        fmt: List[Dict[str, Any]] = [{'text': f'[{from_name}] ', 'color': 'blue'}]
        if isinstance(message, UserRequest):
            self.pty.write(b"list\n")
        if isinstance(message, JoinMessage):
            fmt.append({'text': f'{message.user} joined.', 'color': 'green'})
            self.pty.write(("tellraw @a " + json.dumps(fmt) + '\n').encode('utf-8'))
        if isinstance(message, PartMessage):
            fmt.append({'text': f'{message.user} left.', 'color': 'red'})
            self.pty.write(("tellraw @a " + json.dumps(fmt) + '\n').encode('utf-8'))

def init(bridge: Bridge) -> None:
    bridge.add_protocol('minecraft', MinecraftProto)
