import asyncio
import logging
import core.plugins
import core.logging
import core.link
import sys
from typing import Dict, Type, Set, Callable, List, Awaitable
from asyncio import CancelledError
from core.types import Proto, Bus, AttachmentHost, ServiceMessage, Metadata
from core.link import Link
from collections import defaultdict

logger = logging.getLogger('bridge')

class Bridge:
    protocols: Dict[str, Type[Proto]]
    instances: Dict[str, Proto]
    routes: Dict[str, Set[str]]
    destructors: List[Callable[[], Awaitable]]

    def __init__(self, config: Dict) -> None:
        self.protocols = {}
        self.destructors = []
        self.instances = {}
        self.links: List[Link] = []
        self.routes = defaultdict(set)
        self.bus = Bus()
        self.config = config

        plugin_loader = core.plugins.PluginLoader()
        self.plugins = plugin_loader.load_all()
        for plugin in self.plugins:
            if hasattr(plugin, 'init'):
                plugin.init(self)  # type: ignore

        for (a, b) in self.config['routes']:
            self.routes[a].add(b)
            self.routes[b].add(a)

    def add_destructor(self, fn: Callable[[], Awaitable]) -> None:
        self.destructors.append(fn)

    def add_protocol(self, name: str, proto: Type[Proto]) -> None:
        self.protocols[name] = proto

    def set_attachment_host(self, host: AttachmentHost) -> None:
        self.attachment_host = host

    def get_attachment_host(self) -> AttachmentHost:
        return self.attachment_host

    async def start(self) -> None:
        try:
            await self.construct()
            await self.run_loop()
        except CancelledError:
            await self.run_destructors()
            raise

    async def run_destructors(self) -> None:
        for destructor in self.destructors:
            await destructor()

    async def construct(self) -> None:
        self.loop = asyncio.get_event_loop()
        for name, cfg in self.config['links'].items():
            cfg['name'] = name
            link = Link()
            await link.start(self, cfg)
            self.links.append(link)

        for name, cfg in self.config['instances'].items():
            try:
                proto = self.protocols[cfg['proto']]
            except KeyError:
                logger.error(f'Unknown protocol: {cfg["proto"]}')
                sys.exit()

            logger.info(f'Constructing instance: {name}')

            proto_inst = proto()
            await proto_inst.start(self, self.bus.port_for(name), cfg)
            self.instances[name] = proto_inst


    async def run_loop(self) -> None:
        while True:
            meta, message = await self.bus.get_message()
            logger.info(f'Got message on bus from {meta.from_instance}: {message}')

            inst = meta.from_instance

            for link in self.links:
                if link.name == meta.from_link: continue
                await link.send_message(inst, message)

            for dest in self.routes[f'{inst}#{message.channel}']:
                inst, to_channel = dest.split('#')
                if inst in self.instances:
                    if isinstance(message, ServiceMessage):
                        await self.instances[inst].handle_service_message(to_channel, message)
                    else:
                        await self.instances[inst].send_message(to_channel, message)

