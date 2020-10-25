"""
Inter-bridge links.
"""
import core.types
import core.bridge
from core.types import Proto, Message, OutPort, Config, Channel, ServiceMessage, JoinMessage, PartMessage, Metadata, AnyMessage, Metadata
from typing import Tuple, Optional
import struct
import pickle
import logging
import asyncio
from asyncio import StreamReader, StreamWriter
import socket

logger = logging.getLogger('link')

class Link:
    async def start(self, bridge: 'core.bridge.Bridge', instance_cfg: Config) -> None:
        self.bridge = bridge
        self.instance_cfg = instance_cfg
        self.name = instance_cfg['name']
        self.reader: Optional[StreamReader] = None
        self.writer: Optional[StreamWriter] = None

        remote = instance_cfg['remote']
        self.remote_host, self.remote_port = remote.split(':')
        self.remote_port = int(self.remote_port)

        local = instance_cfg['local']
        self.local_host, self.local_port = local.split(':')
        self.local_port = int(self.local_port)

        logger.info(f'starting link {self.name!r} ({remote} <-> {local})')
        asyncio.create_task(self.try_connect())

    def shutdown_sockets(self) -> None:
        def shutdown_socket(sock: socket.socket) -> None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
                sock.close()
            except OSError as e:
                if e.errno == 107:
                    # transport endpoint not connected
                    pass
                else:
                    raise

        if hasattr(self, 'server'):
            if self.server.sockets:
                for sock in self.server.sockets:
                    shutdown_socket(sock)
        if hasattr(self, '_sock'):
            shutdown_socket(self._sock)

    async def try_connect(self) -> None:
        self._sock = socket.socket()

        try:
            logger.info("trying to connect to remote host..")
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            self._sock.bind((self.local_host, self.local_port))
            self._sock.settimeout(10)
            self._sock.connect((self.remote_host, self.remote_port))
            reader, writer = await asyncio.open_connection(sock=self._sock)
            asyncio.create_task(self.handle_connection(reader, writer))
        except (socket.timeout, ConnectionRefusedError):
            logger.info("timed out waiting to connect to remote host, starting server...")
            self.server = await asyncio.start_server(self.handle_connection, host=self.local_host, port=self.local_port, reuse_address=True, reuse_port=True)
        except OSError as e:
            logger.info(f"got exception: {e}, sleeping and trying to reconnect later")
            await asyncio.sleep(10)
            await self.try_connect()

    async def handle_connection(self, reader: StreamReader, writer: StreamWriter) -> None:
        logger.info(f"received connection: {(reader, writer)}")
        self.reader, self.writer = reader, writer

        try:
            while True:
                logger.debug("reading...")
                size, *_ = struct.unpack('!I', await self.reader.readexactly(4))
                logger.debug(f"got size: {size}")
                meta, message = pickle.loads(await self.reader.readexactly(size))
                meta.from_link = self.name
                await self.bridge.bus.queue.put((meta, message))
        except asyncio.IncompleteReadError:
            logger.warn(f"read was incomplete, assuming other end died. restarting link...")
            if hasattr(self, 'server'):
                self.server.close()
                await self.server.wait_closed()
            self.writer.close()
            await self.writer.wait_closed()
            await asyncio.sleep(5)
            asyncio.create_task(self.start(self.bridge, self.instance_cfg))

    async def send_message(self, meta: Metadata, message: AnyMessage) -> None:
        if not self.writer:
            return

        logger.info(f"Sending message {message}")
        data = pickle.dumps((meta, message))
        size = len(data)
        size_encoded = struct.pack('!I', size)
        self.writer.write(size_encoded)
        self.writer.write(data)
        await self.writer.drain()
