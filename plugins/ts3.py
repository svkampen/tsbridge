from core.types import Proto, Message, OutPort, Config, Channel, Photo, ServiceMessage, JoinMessage, PartMessage, UserRequest, UserList, Metadata
from typing import Any, Mapping, Dict
from .server_query import ServerQueryClient, SQResult, SQError
from core.bridge import Bridge
import requests
import logging
import asyncio
import re

logger = logging.getLogger('ts3')
URL_RE = re.compile('\[URL\]([^[]*)\[/URL\]')

class TS3Proto(Proto):
    async def start(self, bridge: Bridge, out_port: OutPort, instance_cfg: Config) -> None:
        logger.info('Starting construction of ts3 sq instance.')
        self.out_port = out_port
        self.img_host = bridge.get_attachment_host()
        sq_user, sq_password, sq_host, sq_port = map(instance_cfg.get, ['sq_user', 'sq_password', 'sq_host', 'sq_port'])

        self.clid_users: Dict[int, str] = {}
        self.blacklisted_users = instance_cfg.get('blacklisted_users', [])

        assert sq_port is not None

        self.client = await ServerQueryClient.create(sq_user, sq_password, sq_host, int(sq_port))
        logger.info('Construction finished.')
        clients = await self.client.request('clientlist')
        for client in map(SQResult.kvs, clients.split('|')):
            if client['client_type'] != '0': continue
            self.clid_users[int(client['clid'])] = client['client_nickname']
        logger.info(f"Current client list: {self.clid_users}")
        await self.client.request('servernotifyregister event=textchannel')
        for chid in instance_cfg['notify_channels']:
            await self.client.request(f'servernotifyregister event=channel id={chid}')
        asyncio.create_task(self.notify_waiter())

    async def notify_waiter(self) -> None:
        while True:
            notification: SQResult = await self.client.notify_queue.get()
            data = notification.kvs()
            if ('notifytextmessage' in data):
                await self.handle_notify_text(data)
            elif ('notifycliententerview' in data):
                await self.handle_notify_join(data)
            elif ('notifyclientleftview' in data):
                await self.handle_notify_part(data)

    async def handle_notify_join(self, data: Dict) -> None:
        """ Handle a join notification """
        if (data['client_type'] != '0'): return

        name = data['client_nickname']
        clid = int(data['clid'])
        channel_id = int(data['ctid'])
        self.clid_users[clid] = name
        await self.out_port.put_message(JoinMessage(name, channel_id))

    async def handle_notify_part(self, data: Dict) -> None:
        clid = int(data['clid'])
        if (clid in self.clid_users):
            name = self.clid_users[clid]
            channel_id = int(data['cfid'])
            del self.clid_users[clid]
            await self.out_port.put_message(PartMessage(name, channel_id))
        else:
            logger.warning(f"Unable to find client with id {clid} in user list! (maybe a SQ client).")

    async def handle_notify_text(self, data: Dict) -> None:
        """ Handle a text notification """
        message = Message(user=data['invokername'], text=data['msg'], channel='46')
        if (message.user in self.blacklisted_users): return
        if (message.text.startswith('!')): return

        logger.info(f'Received message: {message}')
        if ('URL' in message.text):
            message.text = self.url_strip(message.text)
        await self.out_port.put_message(message)

    def url_strip(self, message: str) -> str:
        message = URL_RE.sub(r'\1', message)
        message = message.replace(r'\/', '/')
        return message

    async def send_message(self, to_channel: Channel, message: Message, meta: Metadata) -> None:
        logger.info(f'Got message to send to TS3: {message} (to channel: {to_channel})')

        txt = message.text
        for attachment in message.attachments:
            if isinstance(attachment, Photo):
                url = await self.img_host.put(attachment.get())
                txt += f" [ Contains photo: {url} ]"

        txt = ServerQueryClient.escape(txt).strip()

        try:
            await self.client.request(f'clientupdate client_nickname=Bridge-{message.user}')
        except SQError as e:
            if e.errno != 513: # nick already in use
                raise
        await self.client.request(f'sendtextmessage targetmode=2 target={to_channel} msg={txt}')

    async def handle_service_message(self, to_channel: Channel, message: ServiceMessage, meta: Metadata) -> None:
        if isinstance(message, UserRequest):
            data = requests.get('https://ts.segfault.party/json').json()
            users = data['people']
            in_muted_q = data['input_muted']
            out_muted_q = data['output_muted']

            for n, val in enumerate(out_muted_q):
                if val:
                    users[n] += " [speakers muted]"

            for n, val in enumerate(in_muted_q):
                if val:
                    users[n] += " [mic muted]"

            await self.out_port.put_message(UserList(users, 46))

def init(bridge: Bridge) -> None:
    bridge.add_protocol('ts3-serverquery', TS3Proto)
