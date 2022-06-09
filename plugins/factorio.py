"""
* depends: imgflt
"""
from core.types import Proto, OutPort, Message, Attachment, Channel, Config, Photo, JoinMessage, PartMessage, ServiceMessage, UserRequest, UserList, Metadata, MiscServiceMessage
from core.bridge import Bridge
from typing import Mapping, Any, Callable, Coroutine, Sequence, List, Dict, Optional, Set
from .ptyproto import PtyProto
import logging
import json
import shlex
import asyncio
import re
import io

CHANNEL_NAME = "chat"

Groups = Sequence[str]

date = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"

class FactorioProto(PtyProto):
    """
    Protocol to interact with an OpenTTD server over a pseudoterminal.
    """
    async def start(self, bridge: Bridge, out_port: OutPort, instance_cfg: Config) -> None:
        self.register_match_functions()
        self.logger = logging.getLogger('factorio')
        await super().start(bridge, out_port, instance_cfg)

    @PtyProto.match(f"{date} \\[(LEAVE|JOIN)\\] ([A-z]+) (?:joined|left) the game")
    async def leave_join(self, line: str, groups: Groups) -> None:
        type, name = groups
        msg: ServiceMessage
        if (type == "JOIN"):
            msg = JoinMessage(name, CHANNEL_NAME)
        else:
            msg = PartMessage(name, CHANNEL_NAME)

        await self.out_port.put_message(msg)

    @PtyProto.match(f"{date} \\[KICK\\] ([A-z]+) was kicked")
    async def kick(self, line: str, groups: Groups) -> None:
        await self.out_port.put_message(PartMessage(groups[0], CHANNEL_NAME))

    @PtyProto.match(f"{date} \\[CHAT\\] ([A-z]+): (.*)")
    async def chat_message(self, line: str, groups: Groups) -> None:
        user, message = groups
        msg = Message(user, message, CHANNEL_NAME)
        await self.out_port.put_message(msg)

    async def send_message(self, to_channel: Channel, message: Message, meta: Metadata) -> None:
        self.logger.info(f"Got message to send: {message}")
        from_name = meta.from_instance.upper()

        escaped_text = message.text.replace('\n', ' ').replace('\r','').replace('\t',' ')
        output = f"[{from_name}] {message.user}: {escaped_text}"

        self.pty.write((f'{output}\n').encode('utf-8'))
        self.pty.flush()

    async def determine_users(self) -> None:
        self.pty.write(b"/players\n")

    @PtyProto.match(r"Players \((\d+)\):")
    async def players_response(self, line: str, groups: Groups) -> None:
        self.num_players_expected = int(groups[0])
        self.num_players_got = 0
        self.clients: Set[str] = set()

    @PtyProto.match("^  ([A-z]+)( \\(online\\))?")
    async def player_response(self, line: str, groups: Groups) -> None:
        self.num_players_got += 1
        if groups[1]:
            self.clients.add(groups[0])

        if self.num_players_got == self.num_players_expected:
            ulist = UserList([*self.clients], CHANNEL_NAME)
            await self.out_port.put_message(ulist)

    async def handle_service_message(self, to_channel: Channel, message: ServiceMessage, meta: Metadata) -> None:
        from_name = meta.from_instance.upper()
        if isinstance(message, UserRequest):
            await self.determine_users()
        if isinstance(message, JoinMessage):
            output = f'[{meta.from_instance.upper()}] {message.user} joined.'
            self.pty.write((f'{output}\n').encode('utf-8'))
        if isinstance(message, PartMessage):
            output = f'[{meta.from_instance.upper()}] {message.user} left.'
            self.pty.write((f'{output}\n').encode('utf-8'))
        self.pty.flush()

def init(bridge: Bridge) -> None:
    bridge.add_protocol('factorio', FactorioProto)
