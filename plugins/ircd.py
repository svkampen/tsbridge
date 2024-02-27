from core.types import Proto, OutPort, Message, Attachment, Channel, Config, Photo, Metadata
from core.bridge import Bridge
from typing import Mapping, Any, Dict, Tuple, List, Optional
from dataclasses import dataclass
import asyncio
import logging
import time
import string

logger = logging.getLogger('ircd')


def itime() -> int:
    return int(time.time())


@dataclass
class User:
    nick: str
    hopcount: int
    connect_ts: int
    user: str
    host: str
    uid: str
    svstamp: str
    usermodes: str
    vhost: str
    clkhost: str
    ip: str
    real: str


class UserFactory:
    def __init__(self, sid: str) -> None:
        self.sid = sid
        self.uid_part = 0

    def _gen_uid(self) -> str:
        output = self.sid
        uid = self.uid_part
        self.uid_part += 1
        for _ in range(5):
            output += string.ascii_uppercase[uid % 26]
            uid //= 26
        return output

    def user(self, nick: str, user: str = "user", host: str = "bridge.segfault.party", modes: str = "+iwB") -> User:
        return User(nick, 1, itime(), user, host, self._gen_uid(), '*', modes, '*', host, '*', 'Some User')


class IRCDProto(Proto):
    """ An implementation of the UnrealIRCd server protocol. """
    async def start(self, bridge: Bridge, out_port: OutPort, instance_cfg: Config) -> None:
        self.bridge = bridge
        self.out_port = out_port

        self.cfg = instance_cfg
        self.host, self.port = self.cfg['host'], self.cfg['port']

        logger.info(f'Opening connection to {self.host}:{self.port}')
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port, ssl=True)
        asyncio.create_task(self.client_loop())

        self.uf = UserFactory(self.cfg['sid'])
        self.users = {}

        b = self.uf.user("Bridge")
        self.users[b.uid] = b

    async def write(self, data: str) -> None:
        logger.debug(f"<<< {data!r}")
        self.writer.write((data + '\r\n').encode('utf-8'))
        await self.writer.drain()

    async def readline(self) -> str:
        line = (await self.reader.readline()).decode('utf-8')
        logger.debug(f">>> {line!r}")
        return line

    def parse(self, msg: str) -> Tuple[str, str, List[str]]:
        tr_start = msg.find(' :') + 1
        # just split if no trailing, else split before trailing and add on trailing
        tokens = msg.split() if not tr_start else [*msg[:tr_start].split(), msg[tr_start+1:]]

        host = tokens.pop(0) if msg.startswith(':') else ""
        method = tokens.pop(0)
        args = tokens

        return (host, method, args)

    def find_user(self, name_or_uid: str) -> Optional[User]:
        if name_or_uid in self.users:
            return self.users[name_or_uid]

        for user in self.users.values():
            if user.nick == name_or_uid:
                return user

        return None

    async def add_user(self, *args: Any, **kwargs: Any) -> User:
        """ Add a user with the given parameters to the server. """
        user = self.uf.user(*args, **kwargs)
        self.users[user.uid] = user
        await self.send_user(user)
        await self.join_channel(user, "#bridge")
        return user

    async def join_channel(self, user: User, channel: str) -> None:
        """ Make a user join a channel. """
        await self.write(f"SJOIN {itime()} {channel} +nt :{user.uid}")

    async def send_user(self, u: User) -> None:
        """ Announce a user on the link. """
        await self.write(f"UID {u.nick} {u.hopcount} {u.connect_ts} {u.user} {u.host} {u.uid} {u.svstamp} {u.usermodes} {u.vhost} {u.clkhost} {u.ip} :{u.real}")

    async def send_users(self) -> None:
        """ Announce all users on the link. """
        for u in self.users.values():
            await self.send_user(u)

    async def send_channels(self) -> None:
        """ Announce all channels on the link. """
        users = self.users.keys()
        await self.write(f"SJOIN {itime()} #bridge +nt :{' '.join(users)}")

    async def start_link(self) -> None:
        """ Start the link. """
        await self.write(f"PASS :{self.cfg['pass']}")
        await self.write(f"PROTOCTL EAUTH={self.cfg['linkname']},,,Bridge-0.1 SID={self.cfg['sid']}")
        await self.write(f"PROTOCTL NOQUIT NICKv2 SJOIN SJOIN2 SJ3 CLK TKLEXT TKLEXT2 NICKIP ESVID MLOCK EXTSWHOIS TS={itime()}")
        await self.write(f"PROTOCTL VHP VL")
        await self.write(f"SERVER {self.cfg['linkname']} 1 :Bridge")
        await self.send_users()
        await self.send_channels()
        await self.write(f"EOS")

    async def client_loop(self) -> None:
        await self.start_link()

        while (line := await self.readline()):
            line = line.strip()
            host, method, args = self.parse(line)
            if (method == 'PING'):
                await self.write(f"PONG :{args[-1]}")
            if (method == 'PRIVMSG'):
                user = host[1:]
                msg = Message(user=user, text=args[-1], channel='bridge')
                await self.out_port.put_message(msg)

    @staticmethod
    def nick_filter(s: str) -> str:
        return ''.join(c for c in s if c in (string.ascii_letters + string.digits + '_'))

    async def send_message(self, to_channel: Channel, message: Message, meta: Metadata) -> None:
        nick = f"{meta.from_instance}-{self.nick_filter(message.user)}"
        user = self.find_user(nick) or await self.add_user(nick)

        await self.write(f":{user.uid} PRIVMSG #bridge :{message.text}")

def init(bridge: Bridge) -> None:
    bridge.add_protocol('ircd', IRCDProto)
