from ..core.types import (
    Proto,
    OutPort,
    Message,
    Attachment,
    Channel,
    Config,
    Photo,
    JoinMessage,
    PartMessage,
    ServiceMessage,
    UserRequest,
    UserList,
    Metadata,
    MiscServiceMessage,
    AnyMessage,
)
from ..core.bridge import Bridge
from ..core.utils import load_cfg_value
from typing import Mapping, Any, Optional, Dict, Tuple
import asyncio
import logging
import struct
from enum import Enum
from . import mumble_proto
from dataclasses import dataclass
import ssl
import socket

logger = logging.getLogger("mumble")


class MumbleType(Enum):
    Version = 0
    UDPTunnel = 1
    Authenticate = 2
    Ping = 3
    Reject = 4
    ServerSync = 5
    ChannelRemove = 6
    ChannelState = 7
    UserRemove = 8
    UserState = 9
    BanList = 10
    TextMessage = 11
    PermissionDenied = 12
    ACL = 13
    QueryUsers = 14
    CryptSetup = 15
    ContextActionModify = 16
    ContextAction = 17
    UserList = 18
    VoiceTarget = 19
    PermissionQuery = 20
    CodecVersion = 21
    UserStats = 22
    RequestBlob = 23
    ServerConfig = 24
    SuggestConfig = 25
    Unhandled = 26


class MumbleMsg:
    def __init__(self, mumble_type: MumbleType, value: Any):
        self.mumble_type = mumble_type
        self.value = value

    @staticmethod
    def from_typed_buf(mumble_type: MumbleType, buf: bytes):
        val = None
        match mumble_type:
            case MumbleType.Version:
                val = mumble_proto.Version()
            case MumbleType.Authenticate:
                val = mumble_proto.Authenticate()
            case MumbleType.Ping:
                val = mumble_proto.Ping()
            case MumbleType.Reject:
                val = mumble_proto.Reject()
            case MumbleType.ServerSync:
                val = mumble_proto.ServerSync()
            case MumbleType.ChannelRemove:
                val = mumble_proto.ChannelRemove()
            case MumbleType.ChannelState:
                val = mumble_proto.ChannelState()
            case MumbleType.UserRemove:
                val = mumble_proto.UserRemove()
            case MumbleType.UserState:
                val = mumble_proto.UserState()
            case MumbleType.BanList:
                val = mumble_proto.BanList()
            case MumbleType.TextMessage:
                val = mumble_proto.TextMessage()
            case MumbleType.PermissionDenied:
                val = mumble_proto.PermissionDenied()
            case MumbleType.ACL:
                val = mumble_proto.ACL()
            case MumbleType.QueryUsers:
                val = mumble_proto.QueryUsers()
            case MumbleType.CryptSetup:
                val = mumble_proto.CryptSetup()
            case MumbleType.ContextActionModify:
                val = mumble_proto.ContextActionModify()
            case MumbleType.ContextAction:
                val = mumble_proto.ContextAction()
            case MumbleType.UserList:
                val = mumble_proto.UserList()
            case MumbleType.VoiceTarget:
                val = mumble_proto.VoiceTarget()
            case MumbleType.PermissionQuery:
                val = mumble_proto.PermissionQuery()
            case MumbleType.CodecVersion:
                val = mumble_proto.CodecVersion()
            case MumbleType.UserStats:
                val = mumble_proto.UserStats()
            case MumbleType.RequestBlob:
                val = mumble_proto.RequestBlob()
            case MumbleType.ServerConfig:
                val = mumble_proto.ServerConfig()
            case MumbleType.SuggestConfig:
                val = mumble_proto.SuggestConfig()
            case MumbleType.UDPTunnel:
                pass # voice data, we don't care about it
            case _:
                logger.warning(f"Unhandled MumbleType: {mumble_type!r}")
                mumble_type = MumbleType.Unhandled

        if val is not None:
            val.ParseFromString(buf)

        return MumbleMsg(mumble_type, val)

    def __repr__(self):
        return f"MumbleMsg({self.mumble_type}, {self.value})"


class MumbleProto(Proto):
    async def start(
        self, bridge: Bridge, out_port: OutPort, instance_cfg: Config
    ) -> None:
        self.out_port = out_port
        self.host = instance_cfg["host"]
        self.port = instance_cfg["port"]
        self.ssl_cert = instance_cfg["ssl_cert"]
        self.ssl_key = instance_cfg["ssl_key"]
        self.username = instance_cfg["username"]

        self.ssl_ctx = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
        self.ssl_ctx.load_cert_chain(self.ssl_cert, self.ssl_key)

        self.reader, self.writer = await asyncio.open_connection(
            self.host, self.port, ssl=self.ssl_ctx
        )

        self.send_queue = asyncio.Queue()
        self.recv_queue = asyncio.Queue()

        self.read_handle = asyncio.create_task(self.read_task(), name="mumble: read")
        self.send_handle = asyncio.create_task(self.send_task(), name="mumble: send")

        self.user_map: Dict[int, str] = {}
        self.session_id = 0

        await self.send_version()
        await self.send_auth()

    async def send_version(self):
        ver = mumble_proto.Version()
        ver.release = "TSBridge"
        ver.os = "Linux"
        ver.version_v2 = 0x0001000400000000
        await self.send_queue.put(MumbleMsg(MumbleType.Version, ver))

    async def send_auth(self):
        auth = mumble_proto.Authenticate()
        auth.client_type = 1
        auth.opus = True
        auth.username = self.username
        await self.send_queue.put(MumbleMsg(MumbleType.Authenticate, auth))

    async def send_task(self):
        while True:
            try:
                async with asyncio.timeout(15):
                    msg = await self.send_queue.get()
            except TimeoutError:
                msg = MumbleMsg(MumbleType.Ping, mumble_proto.Ping())

            logger.info(f"Sending message: {msg!r}")

            tag = msg.mumble_type
            data = msg.value.SerializeToString()
            self.writer.write(struct.pack("!HI", tag.value, len(data)))
            self.writer.write(data)
            await self.writer.drain()

    async def read_task(self):
        while True:
            raw_ty = await self.reader.readexactly(2)
            mumble_type = MumbleType(struct.unpack("!H", raw_ty)[0])
            length = struct.unpack("!I", await self.reader.readexactly(4))[0]
            buf = await self.reader.readexactly(length)
            msg = MumbleMsg.from_typed_buf(mumble_type, buf)

            if mumble_type not in (MumbleType.UDPTunnel, MumbleType.Ping):
               logger.info(f"Received message: {msg!r}")

            match msg.mumble_type:
                case MumbleType.UserState:
                    ustate: mumble_proto.UserState = msg.value
                    self.user_map[ustate.session] = ustate.name
                    if ustate.name == self.username:
                        self.session_id = ustate.session
                case MumbleType.TextMessage:
                    text_msg: mumble_proto.TextMessage = msg.value
                    message = Message(
                        user=self.user_map[text_msg.actor],
                        text=text_msg.message,
                        channel=0,
                    )
                    logger.info(f"Received message: {message}")
                    await self.out_port.put_message(message)
                case _:
                    pass

    async def send_message(self, to_channel: Channel, message: Message, meta: Metadata):
        text_msg = mumble_proto.TextMessage()
        text_msg.actor = self.session_id
        text_msg.channel_id.append(0)
        formatted = (
            f"[{meta.from_instance.upper()}] {message.user}: {message.text}".strip()
        )
        text_msg.message = formatted

        await self.send_queue.put(MumbleMsg(MumbleType.TextMessage, text_msg))

    async def handle_service_message(
        self, to_channel: Channel, message: ServiceMessage, meta: Metadata
    ):
        pass


def init(bridge: Bridge) -> None:
    bridge.add_protocol("mumble", MumbleProto)
