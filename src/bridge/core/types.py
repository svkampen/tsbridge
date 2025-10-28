"""Defines Abstract Base Classes for use in the bridge."""

import abc
import asyncio
import io
from dataclasses import dataclass, field
from typing import IO, BinaryIO, Sequence, Any, Union, Optional, TYPE_CHECKING
from collections.abc import Awaitable

if TYPE_CHECKING:
    from .bridge import Bridge

Channel = Union[str, int]
User = str
Config = dict[str, Any]


class Attachment(abc.ABC):
    @abc.abstractmethod
    def get(self) -> IO:
        pass


class Photo(Attachment):
    def __init__(self, data: BinaryIO):
        self.data = data

    def get(self) -> IO:
        return self.data


class AttachmentHost(abc.ABC):
    @abc.abstractmethod
    async def put(self, data: IO) -> str:
        """Put given data on a server and return a link to it."""
        pass


@dataclass
class Message:
    """A message sent in a given channel, optionally as a reply to another and optionally containing attachments."""

    user: User
    text: str
    channel: Channel
    reply_to: Optional["AnyMessage"] = field(default=None)
    reply_to_origin: Optional[str] = field(default=None)
    attachments: Sequence[Attachment] = field(default_factory=list)


class ServiceMessage:
    channel: Channel


AnyMessage = Union[Message, ServiceMessage]


@dataclass
class MiscServiceMessage(ServiceMessage):
    text: str
    channel: Channel


@dataclass
class JoinMessage(ServiceMessage):
    user: User
    channel: Channel


@dataclass
class UserRequest(ServiceMessage):
    channel: Channel


@dataclass
class UserList(ServiceMessage):
    users: list[User]
    channel: Channel


@dataclass
class PartMessage(ServiceMessage):
    user: User
    channel: Channel


class OutPort(abc.ABC):
    @abc.abstractmethod
    async def put_message(self, message: AnyMessage) -> None:
        pass


@dataclass
class Metadata:
    from_link: str
    from_instance: str


class Bus:
    queue: asyncio.Queue[tuple[Metadata, AnyMessage]]

    def __init__(self) -> None:
        self.queue = asyncio.Queue()

    def port_for(self, instance_name: str) -> OutPort:
        async def put_message(_: OutPort, message: AnyMessage) -> None:
            meta = Metadata(from_link="local", from_instance=instance_name)
            await self.queue.put((meta, message))

        return type("outport", (OutPort,), {"put_message": put_message})()

    async def get_message(self) -> tuple[Metadata, AnyMessage]:
        return await self.queue.get()


class Proto(abc.ABC):
    in_queue: asyncio.Queue[tuple[Channel, AnyMessage, Metadata]]

    def __init__(self) -> None:
        self.in_queue = asyncio.Queue()

    @abc.abstractmethod
    async def start(
        self, bridge: "Bridge", out_port: OutPort, instance_cfg: Config
    ) -> None:
        pass

    @abc.abstractmethod
    async def _handle_message(
        self, to_channel: Channel, message: Message, meta: Metadata
    ) -> None:
        pass

    async def _handle_service_message(
        self, to_channel: Channel, message: ServiceMessage, meta: Metadata
    ) -> None:
        pass

    async def message_loop(self) -> None:
        while True:
            (chan, msg, meta) = await self.in_queue.get()
            if isinstance(msg, ServiceMessage):
                await self._handle_service_message(chan, msg, meta)
            else:
                await self._handle_message(chan, msg, meta)
