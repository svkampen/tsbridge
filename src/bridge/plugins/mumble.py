from ..core.types import *
from ..core.bridge import Bridge
from typing import Any, Dict
import asyncio
import logging
import struct
from enum import Enum
from . import mumble_proto
from dataclasses import dataclass
import ssl

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


@dataclass
class MumbleUser:
    name: str
    mute: bool
    deaf: bool


class MumbleMsg:
    def __init__(self, mumble_type: MumbleType, value: Any):
        self.mumble_type = mumble_type
        self.value = value

    @staticmethod
    def from_typed_buf(mumble_type: MumbleType, buf: bytes) -> "MumbleMsg":
        val: Any = None
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
                pass  # voice data, we don't care about it
            case _:
                logger.warning(f"Unhandled MumbleType: {mumble_type!r}")
                mumble_type = MumbleType.Unhandled

        if val is not None:
            val.ParseFromString(buf)

        return MumbleMsg(mumble_type, val)

    def __repr__(self) -> str:
        return f"MumbleMsg({self.mumble_type}, {self.value})"


class MumbleProto(Proto):
    async def start(
        self, bridge: Bridge, out_port: OutPort, instance_cfg: Config
    ) -> None:
        self.out_port: OutPort = out_port
        self.host: str = instance_cfg["host"]
        self.port: int = instance_cfg["port"]
        self.ssl_cert: str = instance_cfg["ssl_cert"]
        self.ssl_key: str = instance_cfg["ssl_key"]
        self.username: str = instance_cfg["username"]

        self.ssl_ctx = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
        self.ssl_ctx.load_cert_chain(self.ssl_cert, self.ssl_key)

        await self.connect()
        await self.message_loop()

    async def connect(self) -> None:
        self.reader, self.writer = await asyncio.open_connection(
            self.host, self.port, ssl=self.ssl_ctx
        )

        self.running = True
        self.restarting = asyncio.Event()

        self.send_queue: asyncio.Queue = asyncio.Queue()
        self.recv_queue: asyncio.Queue = asyncio.Queue()

        self.read_handle = asyncio.create_task(self.read_task(), name="mumble: read")
        self.send_handle = asyncio.create_task(self.send_task(), name="mumble: send")
        self.restart_handle = asyncio.create_task(
            self.restart_task(), name="mumble: restart"
        )

        self.user_map: Dict[int, MumbleUser] = {}
        self.session_id = 0

        # whether we are still in the process of connecting.
        # this influences whether to send join messages for UserState updates-
        # when we join, we get a flurry of UserStates for the currently connected
        # users, and we shouldn't treat these as if people have just joined.
        # after we have received ServerSync, we can be sure we're done connecting.
        self.connecting = True

        await self.send_version()
        await self.send_auth()

    async def send_version(self) -> None:
        ver = mumble_proto.Version()
        ver.release = "TSBridge"
        ver.os = "Linux"
        ver.version_v2 = 0x0001000400000000
        await self.send_queue.put(MumbleMsg(MumbleType.Version, ver))

    async def send_auth(self) -> None:
        auth = mumble_proto.Authenticate()
        auth.client_type = 1
        auth.opus = True
        auth.username = self.username
        await self.send_queue.put(MumbleMsg(MumbleType.Authenticate, auth))

    async def send_task(self) -> None:
        while self.running:
            try:
                async with asyncio.timeout(15):
                    msg = await self.send_queue.get()
            except TimeoutError:
                msg = MumbleMsg(MumbleType.Ping, mumble_proto.Ping())

            if msg.mumble_type != MumbleType.Ping:
                logger.info(f"Sending message: {msg!r}")

            tag = msg.mumble_type
            data = msg.value.SerializeToString()
            try:
                self.writer.write(struct.pack("!HI", tag.value, len(data)))
                self.writer.write(data)
                await self.writer.drain()
            except:
                self.restarting.set()
                return

    async def read_task(self) -> None:
        while self.running:
            try:
                raw_ty = await self.reader.readexactly(2)
                mumble_type = MumbleType(struct.unpack("!H", raw_ty)[0])
                length = struct.unpack("!I", await self.reader.readexactly(4))[0]
                buf = await self.reader.readexactly(length)
                msg = MumbleMsg.from_typed_buf(mumble_type, buf)
            except:
                self.restarting.set()
                return

            if mumble_type not in (MumbleType.UDPTunnel, MumbleType.Ping):
                logger.info(f"Received message: {msg!r}")

            match msg.mumble_type:
                case MumbleType.ServerSync:
                    # done connecting
                    self.connecting = False
                case MumbleType.UserState:
                    ustate: mumble_proto.UserState = msg.value
                    user = self.user_map.get(ustate.session) or MumbleUser(
                        name="", mute=False, deaf=False
                    )
                    if ustate.session not in self.user_map and not self.connecting:
                        assert ustate.HasField("name")
                        await self.out_port.put_message(JoinMessage(ustate.name, 0))

                    if ustate.HasField("name"):
                        user.name = ustate.name
                        if ustate.name == self.username:
                            self.session_id = ustate.session
                    if ustate.HasField("self_mute"):
                        user.mute = ustate.self_mute
                    if ustate.HasField("self_deaf"):
                        user.deaf = ustate.self_deaf

                    self.user_map[ustate.session] = user
                case MumbleType.UserRemove:
                    uremove: mumble_proto.UserRemove = msg.value
                    del_user = self.user_map.get(uremove.session)
                    if del_user:
                        await self.out_port.put_message(PartMessage(del_user.name, 0))
                        del self.user_map[uremove.session]
                case MumbleType.TextMessage:
                    text_msg: mumble_proto.TextMessage = msg.value
                    message = Message(
                        user=self.user_map[text_msg.actor].name,
                        text=text_msg.message,
                        channel=0,
                    )
                    logger.info(f"Received message: {message}")
                    await self.out_port.put_message(message)
                case _:
                    pass

    async def restart_task(self) -> None:
        await self.restarting.wait()
        logger.warning("Restart event tripped!")
        self.running = False
        await self.send_handle
        await self.read_handle
        asyncio.create_task(self.connect())

    async def _handle_message(
        self, to_channel: Channel, message: Message, meta: Metadata
    ) -> None:
        text_msg = mumble_proto.TextMessage()
        text_msg.actor = self.session_id
        text_msg.channel_id.append(0)
        formatted = (
            f"[{meta.from_instance.upper()}] {message.user}: {message.text}".strip()
        )
        text_msg.message = formatted

        await self.send_queue.put(MumbleMsg(MumbleType.TextMessage, text_msg))

    async def _handle_service_message(
        self, to_channel: Channel, message: ServiceMessage, meta: Metadata
    ) -> None:
        if isinstance(message, UserRequest):
            users = []
            for user in self.user_map.values():
                if user.name == self.username:
                    continue
                fmt = user.name
                if user.mute:
                    fmt += " [mic muted]"
                if user.deaf:
                    fmt += " [speakers muted]"

                users.append(fmt)

            await self.out_port.put_message(UserList(users, 0))


def init(bridge: Bridge) -> None:
    bridge.add_protocol("mumble", MumbleProto)
