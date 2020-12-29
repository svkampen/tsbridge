from telethon import TelegramClient, events
from pprint import pprint
from core.types import Proto, OutPort, Message, Attachment, Channel, Config, Photo, JoinMessage, PartMessage, ServiceMessage, UserRequest, UserList, Metadata, MiscServiceMessage, AnyMessage
from core.bridge import Bridge
from typing import Mapping, Any, Optional, Dict, Tuple
import asyncio
import telethon
from io import BytesIO
import logging
import telethon

logger = logging.getLogger('telegram')

class TelegramProto(Proto):
    async def start(self, bridge: Bridge, out_port: OutPort, instance_cfg: Config) -> None:
        self.client = TelegramClient('bot', instance_cfg['api_id'],
                                            instance_cfg['api_hash'])

        await self.client.start(bot_token=instance_cfg['bot_token'])

        self.bot_id = instance_cfg['bot_id']
        self.cfg = instance_cfg
        self.paused = False

        logger.info('Started TG client.')

        self.client.add_event_handler(self.message_handler)
        self.out_port = out_port

        self.message_cache: Dict[int, Tuple[AnyMessage, Metadata]] = {}

    async def send_user_request(self, from_channel: Channel) -> None:
        await self.out_port.put_message(UserRequest(from_channel))

    async def get_reply_message(self, event: events.NewMessage) -> Optional[Tuple[AnyMessage, str]]:
        reply = await event.get_reply_message()
        if not reply:
            return None

        reply_sender = await reply.get_sender()

        (orig_message, orig_meta) = self.message_cache.get(reply.id, (None, None))

        if orig_message and orig_meta:
            return (orig_message, orig_meta.from_instance)

        message = Message(user=reply_sender.first_name, text=reply.message,
                          channel=reply.chat_id, attachments=[])

        return (message, self.cfg['name'])

    @events.register(events.NewMessage)
    async def message_handler(self, event: events.NewMessage) -> None:
        sender: telethon.types.User = await event.get_sender()

        reply = await self.get_reply_message(event)
        (reply_message, reply_message_origin) = reply if reply else (None, None)

        attachments = []

        text = event.raw_text
        if text == '.unpause':
            self.paused = False

        if text == '.pause':
            self.paused = True

        if self.paused:
            return

        if event.sticker is not None:
            for attr in event.sticker.attributes:
                if isinstance(attr, telethon.types.DocumentAttributeSticker):
                    text = f'[ Sticker with emoji: {attr.alt} ]' if attr.alt else "[ Sticker without emoji >:( ]"
        elif event.photo is not None:
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

        message = Message(user=sender.first_name, text=text,
                          channel=event.chat_id, reply_to=reply_message,
                          reply_to_origin=reply_message_origin,
                          attachments=attachments)

        if 'tasks' in event.raw_text:
            # debugging level: 100%
            pprint(asyncio.all_tasks())

        logger.info(f'Received message from Telegram: {message}')

        await self.out_port.put_message(message)

    async def handle_service_message(self, to_channel: Channel, message: ServiceMessage, meta: Metadata) -> None:
        to_channel = int(to_channel)
        msg: Optional[telethon.types.Message] = None
        if isinstance(message, JoinMessage):
            msg = await self.client.send_message(to_channel, message=f"[{meta.from_instance.upper()}] {message.user} joined.")
        elif isinstance(message, PartMessage):
            msg = await self.client.send_message(to_channel, message=f"[{meta.from_instance.upper()}] {message.user} left.", silent=True)
        elif isinstance(message, UserList):
            ulist = "Nobody is online" if not message.users else "Users:\n%s" % ('\n'.join(message.users))
            msg = await self.client.send_message(to_channel, message=f"[{meta.from_instance.upper()}] {ulist}.")
        elif isinstance(message, MiscServiceMessage):
            msg = await self.client.send_message(to_channel, message=message.text)

        if msg:
            self.message_cache[msg.id] = (message, meta)

    async def send_message(self, to_channel: Channel, message: Message, meta: Metadata) -> None:
        logger.info(f'Sending message to channel {to_channel}: {message}')
        formatted = f'[{meta.from_instance.upper()}] {message.user}: {message.text}'.strip()
        to_channel = int(to_channel)
        msg = await self.client.send_message(to_channel, message=formatted)
        self.message_cache[msg.id] = (message, meta)

def init(bridge: Bridge) -> None:
    bridge.add_protocol('telegram', TelegramProto)
