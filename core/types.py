""" Defines Abstract Base Classes for use in the bridge. """
import abc
import asyncio
from dataclasses import dataclass, field
from typing import IO, Sequence, List, Mapping, Any, Union, Iterable, Tuple
from io import BytesIO

Channel = Union[str, int]
User = str
Config = Mapping[str, Any]

class Attachment(abc.ABC):
    @abc.abstractmethod
    def get(self) -> IO:
        pass

class Photo(Attachment):
    def __init__(self, data: BytesIO):
        self.data = data

    def get(self) -> IO:
        return self.data

class AttachmentHost(abc.ABC):
    @abc.abstractmethod
    async def put(self, data: IO) -> str:
        """ Put given data on a server and return a link to it. """
        pass

@dataclass
class Message:
    user: User
    text: str
    channel: Channel
    attachments: Sequence[Attachment] = field(default_factory=list)

class ServiceMessage:
    channel: Channel

AnyMessage = Union[Message, ServiceMessage]

@dataclass
class JoinMessage(ServiceMessage):
    user: User
    channel: Channel

@dataclass
class UserRequest(ServiceMessage):
    channel: Channel

@dataclass
class UserList(ServiceMessage):
    users: List[User]
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
    queue: asyncio.Queue

    def __init__(self) -> None:
        self.queue = asyncio.Queue()

    def port_for(self, instance_name: str) -> OutPort:
        async def put_message(_: OutPort, message: AnyMessage) -> None:
            meta = Metadata(from_link='local', from_instance=instance_name)
            await self.queue.put((meta, message))

        return type('outport', (OutPort,), {'put_message': put_message})()

    async def get_message(self) -> Tuple[Metadata, AnyMessage]:
        return await self.queue.get()

class Proto(abc.ABC):
    @abc.abstractmethod
    async def send_message(self, to_channel: Channel, message: Message) -> None:
        pass

    @abc.abstractmethod
    async def start(self, bridge: 'core.bridge.Bridge', out_port: OutPort, instance_cfg: Config) -> None:
        pass

    async def handle_service_message(self, to_channel: Channel, message: ServiceMessage) -> None:
        pass

import core.bridge
