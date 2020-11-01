"""
Inter-bridge links.
"""
import core.types
import core.bridge
from core.types import Proto, Message, OutPort, Config, Channel, ServiceMessage, JoinMessage, PartMessage, Metadata, AnyMessage, Metadata
from typing import Tuple, Optional, Dict
import struct
import pickle
import logging
import asyncio
from asyncio import StreamReader, StreamWriter
import socket
import ssl
from dataclasses import dataclass

logger = logging.getLogger('link')

Success = bool

@dataclass
class SSLConfig:
    cert: str # Path to SSL certificate
    key: str # Path to SSL certificate private key
    peer_hostname: str # Hostname of the remote end of the link

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

        ssl = instance_cfg.get('ssl', False)
        self.ssl_config: Optional[SSLConfig] = self.get_ssl_config() if ssl else None
        if ssl and self.ssl_config is None:
            logger.error("unable to get ssl config, link failed.")
            return

        asyncio.create_task(self.try_connect())

    def get_ssl_config(self) -> Optional[SSLConfig]:
        cert, key, peer_hostname = [self.instance_cfg.get(x) for x in ('ssl_cert', 'ssl_key', 'peer_hostname')]
        if not all((cert, key, peer_hostname)):
            logger.error("ssl: configuration options missing, need ssl_cert (path to certificate), ssl_key (path to certificate private key, peer_hostname (hostname of other end of link))")
            return None

        return SSLConfig(cert, key, peer_hostname)  # type: ignore

    def get_ssl_context(self, purpose: ssl.Purpose) -> Optional[ssl.SSLContext]:
        """
        Get an SSL context for the given purpose, with certificates loaded according to the given configuration.

        This might fail, if the certificates are unavailable or invalid. In that case, return None.
        """
        assert self.ssl_config

        ssl_ctx = ssl.create_default_context(purpose=purpose)
        ssl_ctx.verify_mode = ssl.VerifyMode.CERT_REQUIRED
        try:
            ssl_ctx.load_cert_chain(self.ssl_config.cert, self.ssl_config.key)
        except FileNotFoundError as e:
            logger.error(f"ssl: unable to load certificate chain, file not found: {e}")
            return None
        except ssl.SSLError as e:
            logger.error(f"ssl: unable to load certificate chain: {e}")
            return None
        return ssl_ctx

    def get_ssl_server_context(self) -> Optional[ssl.SSLContext]:
        """ Get an SSL context suitable for server use. """
        ctx = self.get_ssl_context(ssl.Purpose.CLIENT_AUTH)

        if ctx:
            # Verify client certificates using the default CA store.
            ctx.load_verify_locations(capath='/etc/ssl/certs')

        return ctx

    def get_ssl_client_context(self) -> Optional[ssl.SSLContext]:
        """ Get an SSL context suitable for client use. """
        return self.get_ssl_context(ssl.Purpose.SERVER_AUTH)

    async def try_connect(self) -> None:
        ssl_ctx: Optional[ssl.SSLContext] = None

        try:
            logger.info("trying to connect to remote host..")
            extra_kwargs: Dict = {}
            if self.ssl_config:
                ssl_ctx = self.get_ssl_client_context()
                extra_kwargs['ssl'] = ssl_ctx
                extra_kwargs['server_hostname'] = self.ssl_config.peer_hostname
                if not ssl_ctx:
                    logger.error("unable to construct ssl context, link failed.")
                    return None

            reader, writer = await asyncio.open_connection(
                    self.remote_host, self.remote_port, **extra_kwargs)
            asyncio.create_task(self.handle_connection(reader, writer))
        except (socket.timeout, ConnectionRefusedError, OSError) as e:
            logger.info(f"got exception: {e!r}; starting server.")

            if self.instance_cfg.get('ssl') and (ssl_ctx := self.get_ssl_server_context()) is None:
                return

            self.server = await asyncio.start_server(self.handle_connection, host=self.local_host, port=self.local_port, reuse_address=True, ssl=ssl_ctx)

    def ssl_verify_hostname(self, peer_certificate: Dict, expected_hostname: str) -> Success:
        """ Verify that the hostname of our SSL peer is what we expect. """
        try:
            ssl.match_hostname(peer_certificate, expected_hostname)
        except ssl.CertificateError as e:
            logger.error(f"ssl: hostname verification failed: {e}")
            return False
        logger.info(f"ssl: hostname verification succeeded.")
        return True

    async def handle_connection(self, reader: StreamReader, writer: StreamWriter) -> None:
        logger.info(f"received connection: {(reader, writer)}")
        self.reader, self.writer = reader, writer

        if self.ssl_config:
            self.ssl_verify_hostname(writer.get_extra_info('peercert'), self.ssl_config.peer_hostname)

        try:
            while True:
                logger.debug("reading...")
                size, *_ = struct.unpack('!I', await self.reader.readexactly(4))
                logger.debug(f"got size: {size}")
                meta, message = pickle.loads(await self.reader.readexactly(size))
                meta.from_link = self.name
                await self.bridge.bus.queue.put((meta, message))
        except (asyncio.IncompleteReadError, ConnectionResetError) as e:
            logger.warn(f"got exception trying to read: {e!r}; trying to restart link.")
            try:
                if hasattr(self, 'server'):
                    self.server.close()
                    await self.server.wait_closed()
                self.writer.close()
                await self.writer.wait_closed()
            except:
                pass
            await asyncio.sleep(5)
            asyncio.create_task(self.start(self.bridge, self.instance_cfg))

    async def send_message(self, meta: Metadata, message: AnyMessage) -> None:
        if not self.writer:
            return

        try:
            logger.info(f"Sending message {message}")
            data = pickle.dumps((meta, message))
            size = len(data)
            size_encoded = struct.pack('!I', size)
            self.writer.write(size_encoded)
            self.writer.write(data)
            await self.writer.drain()
        except ConnectionResetError:
            pass
