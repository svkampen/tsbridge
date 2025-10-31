from pprint import pprint
from ..core.types import *
from ..core.bridge import Bridge
from ..core.utils import load_cfg_value, wait_reraise
from typing import Optional, Dict, Tuple
import asyncio
from io import StringIO
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Message as TGMessage
import pathlib
import html
import functools

logger = logging.getLogger("telegram")

_esc = functools.partial(html.escape, quote=False)


class TelegramProto(Proto):
    async def start(
        self, bridge: Bridge, out_port: OutPort, instance_cfg: Config
    ) -> None:
        bot_token = load_cfg_value(instance_cfg["bot_token"])

        self.dispatcher = Dispatcher()
        self.bot = Bot(
            bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )

        self.bot_id = instance_cfg["bot_id"]
        self.cfg = instance_cfg
        self.paused = False

        logger.info("Started TG client.")

        self.dispatcher.message()(self.message_handler)
        self.out_port = out_port

        self.message_cache: Dict[int, Tuple[AnyMessage, Metadata]] = {}

        message_loop_task = asyncio.create_task(self.message_loop())
        bot_poll_task = asyncio.create_task(
            self.dispatcher.start_polling(self.bot, handle_signals=False)
        )

        try:
            await wait_reraise({message_loop_task, bot_poll_task})
        except BaseException as e:
            await self.dispatcher.stop_polling()
            raise e

    async def send_user_request(self, from_channel: Channel) -> None:
        await self.out_port.put_message(UserRequest(from_channel))

    async def message_handler(self, msg: TGMessage) -> None:
        sender = msg.from_user

        if not sender:
            return

        # todo handle reply somehow
        reply = msg.reply_to_message

        attachments = []

        text = msg.text or ""
        if text == ".unpause":
            self.paused = False

        if text == ".pause":
            self.paused = True

        if self.paused:
            return

        if msg.sticker is not None:
            if msg.sticker.emoji:
                text = f"[ Sticker with emoji: {msg.sticker.emoji} ]"
            else:
                text = f"[ Sticker without emoji >:( ]"
        elif msg.photo is not None:
            logger.info("Getting photo from TG message...")
            data = await self.bot.download(msg.photo[0].file_id)
            logger.info("Photo downloaded.")
            if data:
                data.seek(0)
                attachments.append(Photo(data))
        elif msg.text == "":
            return

        if msg.text == ".online":
            return await self.send_user_request(msg.chat.id)
        if msg.text == ".tasks":
            return await self.send_debug_info(msg.chat.id)

        message = Message(
            user=sender.first_name,
            text=text,
            channel=msg.chat.id,
            attachments=attachments,
        )

        logger.info(f"Received message from Telegram: {message}")

        await self.out_port.put_message(message)

    async def send_debug_info(self, to_chat: Channel) -> None:
        tasks = asyncio.all_tasks()
        output = ""
        for n, task in enumerate(tasks):
            (stack_frame, *rest) = task.get_stack(limit=1)
            function_name = stack_frame.f_code.co_name
            function_lineno = stack_frame.f_lineno
            function_filename = stack_frame.f_code.co_filename
            filename = pathlib.Path(function_filename).parts[-1]

            val = f"<b>Task {n}</b> (name: {_esc(task.get_name())})\n"
            val += f"In {function_name}, {filename}:{function_lineno}.\n\n"

            if (len(output) + len(val)) < 4000:
                output += val
            else:
                break
        await self.bot.send_message(chat_id=to_chat, text=output)

    async def _handle_service_message(
        self, to_channel: Channel, message: ServiceMessage, meta: Metadata
    ) -> None:
        to_channel = int(to_channel)
        msg: Optional[TGMessage] = None
        match message:
            case JoinMessage():
                msg = await self.bot.send_message(
                    chat_id=to_channel,
                    text=_esc(f"[{meta.from_instance.upper()}] {message.user} joined."),
                )
            case PartMessage():
                msg = await self.bot.send_message(
                    chat_id=to_channel,
                    text=_esc(f"[{meta.from_instance.upper()}] {message.user} left."),
                    disable_notification=True,
                )
            case UserList():
                ulist = (
                    "Nobody is online"
                    if not message.users
                    else "Users:\n%s" % ("\n".join(message.users))
                )
                msg = await self.bot.send_message(
                    to_channel,
                    text=_esc(f"[{meta.from_instance.upper()}] {ulist}."),
                )
            case MiscServiceMessage():
                msg = await self.bot.send_message(to_channel, text=_esc(message.text))

        if msg:
            self.message_cache[msg.message_id] = (message, meta)

    async def _handle_message(
        self, to_channel: Channel, message: Message, meta: Metadata
    ) -> None:
        logger.info(f"Sending message to channel {to_channel}: {message}")
        formatted = _esc(
            f"[{meta.from_instance.upper()}] {message.user}: {message.text}".strip()
        )
        to_channel = int(to_channel)
        if "DJ Smerlemex" in formatted:
            msg = await self.bot.send_message(
                to_channel, text=formatted, disable_notification=True
            )
        else:
            msg = await self.bot.send_message(to_channel, text=formatted)
        self.message_cache[msg.message_id] = (message, meta)


def init(bridge: Bridge) -> None:
    bridge.add_protocol("telegram", TelegramProto)
