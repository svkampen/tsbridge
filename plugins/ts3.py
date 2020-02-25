from core.types import Proto, Message, OutPort, Config, Channel, Photo
from typing import Any, Mapping
from .server_query import ServerQueryClient, SQResult, SQError
from core.bridge import Bridge
import logging
import asyncio

logger = logging.getLogger('ts3')

class TS3Proto(Proto):
    async def start(self, bridge: Bridge, out_port: OutPort, instance_cfg: Config) -> None:
        logger.info('Starting construction of ts3 sq instance.')
        self.out_port = out_port
        self.img_host = bridge.get_attachment_host()
        sq_user, sq_password, sq_host, sq_port = map(instance_cfg.get, ['sq_user', 'sq_password', 'sq_host', 'sq_port'])

        assert sq_port is not None

        self.client = await ServerQueryClient.create(sq_user, sq_password, sq_host, int(sq_port))
        logger.info('Construction finished.')
        await self.client.request('servernotifyregister event=textchannel')
        asyncio.create_task(self.notify_waiter(), name='notify waiter (protocol)')

    async def notify_waiter(self) -> None:
        while True:
            notification: SQResult = await self.client.notify_queue.get()
            data = notification.kvs()
            assert 'notifytextmessage' in data
            message = Message(user=data['invokername'], text=data['msg'], channel='69')
            logger.info(f'Received message: {message}')
            await self.out_port.put_message(message)

    async def send_message(self, to_channel: Channel, message: Message) -> None:
        logger.info(f'Got message to send to TS3: {message} (to channel: {to_channel})')

        txt = message.text
        for attachment in message.attachments:
            if isinstance(attachment, Photo):
                url = await self.img_host.put(attachment.get())
                txt += f" [Contains photo: {url}]"

        txt = txt.replace(' ', r'\s').strip()

        try:
            await self.client.request(f'clientupdate client_nickname=Bridge-{message.user}')
        except SQError as e:
            if e.errno != 513: # nick already in use
                raise
        await self.client.request(f'sendtextmessage targetmode=2 target={to_channel} msg={txt}')

def init(bridge: Bridge) -> None:
    bridge.add_protocol('ts3-serverquery', TS3Proto)
