import asyncio
import sys
from ..core.bridge import Bridge
from ..core.types import OutPort, Channel, Message, Proto, Config, Metadata
from ..core.utils import wait_forever


class Console(Proto):
    async def start(
        self, bridge: Bridge, out_port: OutPort, instance_cfg: Config
    ) -> None:
        self.out_port = out_port
        loop = asyncio.get_event_loop()
        loop.add_reader(sys.stdin, self.console_handler)

        await wait_forever()

    async def _handle_message(
        self, to_channel: Channel, message: Message, meta: Metadata
    ) -> None:
        print(f"[{to_channel}] {message}")

    def console_handler(self) -> None:
        data = sys.stdin.readline().strip()
        msg = Message(user="console", text=data, channel="console")
        asyncio.create_task(self.out_port.put_message(msg))


def init(bridge: Bridge) -> None:
    bridge.add_protocol("console", Console)
