"""
* depends: imgflt
"""
from ..core.types import Proto, OutPort, Message, Attachment, Channel, Config, Photo, JoinMessage, PartMessage, ServiceMessage, UserRequest, UserList, Metadata, MiscServiceMessage
from ..core.bridge import Bridge
from typing import Mapping, Any, Callable, Coroutine, Sequence, List, Dict, Optional
from .ptyproto import PtyProto
import logging
import json
import shlex
import asyncio
import re
import io

CHANNEL_NAME = "chat"

USERNAME = "[A-Za-z0-9_- \#]+"

Groups = Sequence[str]

class OpenTTDProto(PtyProto):
    """
    Protocol to interact with an OpenTTD server over a pseudoterminal.
    """
    async def start(self, bridge: Bridge, out_port: OutPort, instance_cfg: Config) -> None:
        self.logger = logging.getLogger('openttd')
        self.register_match_functions()
        await super().start(bridge, out_port, instance_cfg)

        self.img_host = bridge.get_attachment_host()

        # Number of entities of a given type which are online.
        self.online: Dict[str, int] = {}

        # Number of responses to the `clients` command we've gotten:
        self.clients_responses_got = 0

        # Temporary list of client data received from the `clients` command.
        self.clients: List[str] = []


    @PtyProto.match(f"\\[All\\] ([^:]+): (.+)")
    async def message(self, line: str, groups: Groups) -> None:
        user, message = groups

        self.logger.info(f"Message from {user}: {message}")
        msg = Message(user, message, CHANNEL_NAME)
        await self.out_port.put_message(msg)


    @PtyProto.match(r"Current/maximum (.+):\s+(\d+)/(\d+)")
    async def info_response(self, line: str, groups: Groups) -> None:
        type, online, _ = groups
        if (type == 'spectators'):
            # this is the end of the info command.
            self.pty.write(b"clients\n")
            self.pty.flush()

        self.online[type] = int(online)
        self.clients_responses_got = 0


    @PtyProto.match(r"^Client #\d+  name: '(.+)'  company: \d+  IP: [a-z0-9.]+$")
    async def clients_response(self, line: str, groups: Groups) -> None:
        self.clients_responses_got += 1
        self.clients.append(groups[0])
        if self.clients_responses_got == self.online.get('clients', float('inf')):
            ulist = UserList(self.clients, CHANNEL_NAME)
            await self.out_port.put_message(ulist)


    @PtyProto.match(r"^\*\*\* (.+) has (joined|left) the game \((?:Client \#\d+|leaving)\)$")
    async def join_part(self, line: str, groups: Groups) -> None:
        name, type = groups
        self.logger.info(f"{groups[1].title()}: {groups[0]}")
        msg: ServiceMessage
        if (type == "joined"):
            msg = JoinMessage(name, CHANNEL_NAME)
        else:
            msg = PartMessage(name, CHANNEL_NAME)
        await self.out_port.put_message(msg)


    async def send_message(self, to_channel: Channel, message: Message, meta: Metadata) -> None:
        self.logger.info(f"Got message to send: {message}")
        from_name = meta.from_instance.upper()

        escaped_text = message.text.replace('"', r'\"').replace('\n', ' ').replace('\r','').replace('\t',' ')
        output = f"[{from_name}] {message.user}: {escaped_text}"

        self.pty.write((f'say "{output}"\n').encode('utf-8'))
        self.pty.flush()

    async def handle_service_message(self, to_channel: Channel, message: ServiceMessage, meta: Metadata) -> None:
        from_name = meta.from_instance.upper()
        if isinstance(message, UserRequest):
            self.pty.write(b"info\n")
        if isinstance(message, JoinMessage):
            output = f'[{meta.from_instance.upper()}] {message.user} joined.'
            self.pty.write((f'say "{output}"\n').encode('utf-8'))
        if isinstance(message, PartMessage):
            output = f'[{meta.from_instance.upper()}] {message.user} left.'
            self.pty.write((f'say "{output}"\n').encode('utf-8'))
        self.pty.flush()

def init(bridge: Bridge) -> None:
    bridge.add_protocol('openttd', OpenTTDProto)
