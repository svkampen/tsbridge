from telethon import TelegramClient, events
from pprint import pprint
from core.types import Proto, OutPort, Message, Attachment, Channel, Config, Photo, JoinMessage, PartMessage, ServiceMessage, UserRequest, UserList, Metadata, MiscServiceMessage
from core.bridge import Bridge
from typing import Mapping, Any
import asyncio
from io import BytesIO
import logging
import telethon

logger = logging.getLogger('telegram')

class TelegramProto(Proto):
    async def start(self, bridge: Bridge, out_port: OutPort, instance_cfg: Config) -> None:
        self.client = TelegramClient('bot', instance_cfg['api_id'],
                                            instance_cfg['api_hash'])

        await self.client.start(bot_token=instance_cfg['bot_token'])

        logger.info('Started TG client.')

        self.client.add_event_handler(self.message_handler)
        self.out_port = out_port

    async def send_user_request(self, from_channel: Channel) -> None:
        await self.out_port.put_message(UserRequest(from_channel))

    async def get_reply_message(self, event: events.NewMessage) -> Message:
        reply = await event.get_reply_message()
        reply_sender = await reply.get_sender()

        message = Message(user=reply_sender.first_name, text=reply.message,
                          channel=reply.chat_id, attachments=[])

        return message

    @events.register(events.NewMessage)
    async def message_handler(self, event: events.NewMessage) -> None:
        sender: telethon.types.User = await event.get_sender()

        reply_message = await self.get_reply_message(event)

        attachments = []

        if event.photo is not None:
            logger.info('Getting photo from TG message...')
            data = BytesIO()
            res = await event.download_media(file=data)
            logger.info('Photo downloaded.')
            if res:
                data.seek(0)
                attachments.append(Photo(data))
        elif event.raw_text == '':
            return

        if event.text == '.online':
            return await self.send_user_request(event.chat_id)

        message = Message(user=sender.first_name, text=event.raw_text,
                          channel=event.chat_id, reply_to=reply_message,
                          attachments=attachments)

        if 'tasks' in event.raw_text:
            # debugging level: 100%
            pprint(asyncio.all_tasks())

        logger.info(f'Received message from Telegram: {message}')

        await self.out_port.put_message(message)

    async def handle_service_message(self, to_channel: Channel, message: ServiceMessage, meta: Metadata) -> None:
        to_channel = int(to_channel)
        if isinstance(message, JoinMessage):
            await self.client.send_message(to_channel, message=f"[{meta.from_instance.upper()}] {message.user} joined.")
        elif isinstance(message, PartMessage):
            await self.client.send_message(to_channel, message=f"[{meta.from_instance.upper()}] {message.user} left.", silent=True)
        elif isinstance(message, UserList):
            ulist = "Nobody is online" if not message.users else "Users:\n%s" % ('\n'.join(message.users))
            await self.client.send_message(to_channel, message=f"[{meta.from_instance.upper()}] {ulist}.")
        elif isinstance(message, MiscServiceMessage):
            await self.client.send_message(to_channel, message=message.text)

    async def send_message(self, to_channel: Channel, message: Message, meta: Metadata) -> None:
        logger.info(f'Sending message to channel {to_channel}: {message}')
        formatted = f'[{meta.from_instance.upper()}] {message.user}: {message.text}'.strip()
        to_channel = int(to_channel)
        await self.client.send_message(to_channel, message=formatted)

def init(bridge: Bridge) -> None:
    bridge.add_protocol('telegram', TelegramProto)
