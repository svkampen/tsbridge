from . import plugins
import asyncio
import logging
import sys
import itertools
from typing import Dict, Type, Set, Callable, List, Awaitable, Optional
from asyncio import CancelledError
from .types import Proto, Bus, AttachmentHost, ServiceMessage, Metadata, Config
from .link import Link
from collections import defaultdict


logger = logging.getLogger("bridge")


class Bridge:
    protocols: Dict[str, Type[Proto]]
    instances: Dict[str, Proto]
    routes: Dict[str, Set[str]]
    destructors: List[Callable[[], Awaitable]]
    attachment_host: Optional[AttachmentHost]

    def __init__(self, config: Dict) -> None:
        self.protocols = {}
        self.destructors = []
        self.instances = {}
        self.run_handles: set[asyncio.Task] = set()
        self.links: List[Link] = []
        self.routes = defaultdict(set)
        self.bus = Bus()
        self.config = config
        self.attachment_host = None

        plugin_loader = plugins.PluginLoader()
        self.plugins = plugin_loader.load_all()
        for plugin in self.plugins:
            if hasattr(plugin, "init"):
                plugin.init(self)  # type: ignore

        for l in self.config["routes"]:
            for x, y in itertools.product(l, l):
                if x == y:
                    continue
                self.routes[x].add(y)

    def add_destructor(self, fn: Callable[[], Awaitable]) -> None:
        self.destructors.append(fn)

    def add_protocol(self, name: str, proto: type[Proto]) -> None:
        self.protocols[name] = proto

    def set_attachment_host(self, host: AttachmentHost) -> None:
        self.attachment_host = host

    def get_attachment_host(self) -> Optional[AttachmentHost]:
        return self.attachment_host

    async def start(self) -> None:
        try:
            await self.construct()
            mb_task = asyncio.create_task(self.message_broker())
            wd_task = asyncio.create_task(self.instance_watchdog())
            await asyncio.gather(mb_task, wd_task)
        except CancelledError:
            await self.run_destructors()
            raise

    async def run_destructors(self) -> None:
        for destructor in self.destructors:
            await destructor()

    def _construct_instance(
        self, name: str, cfg: Config, proto: type[Proto], delay_secs: int = 0
    ) -> Proto:
        logger.info(f"Constructing instance: {name}")
        proto_inst = proto()

        async def _wrapper() -> None:
            await asyncio.sleep(delay_secs)
            await proto_inst.start(self, self.bus.port_for(name), cfg)

        self.run_handles.add(self.loop.create_task(name=name, coro=_wrapper()))
        self.instances[name] = proto_inst
        return proto_inst

    async def construct(self) -> None:
        self.loop = asyncio.get_event_loop()
        for name, cfg in self.config.get("links", {}).items():
            cfg["name"] = name
            link = Link()
            await link.start(self, cfg)
            self.links.append(link)

        for name, cfg in self.config["instances"].items():
            cfg["name"] = name
            try:
                proto = self.protocols[cfg["proto"]]
            except KeyError:
                logger.error(f'Unknown protocol: {cfg["proto"]}')
                sys.exit()

            self._construct_instance(name, cfg, proto)

    async def instance_watchdog(self) -> None:
        while True:
            done, pending = await asyncio.wait(
                self.run_handles, return_when=asyncio.FIRST_EXCEPTION
            )

            for task in done:
                if ex := task.exception():
                    inst_name = task.get_name()
                    logger.error(
                        f"Instance {inst_name} task raised an exception, restarting it...",
                        exc_info=ex,
                    )
                    self.run_handles.remove(task)
                    old_inst = self.instances.pop(inst_name)

                    for name, cfg in self.config["instances"].items():
                        if name != inst_name:
                            continue

                        proto = self.protocols[cfg["proto"]]
                        new_inst = self._construct_instance(
                            name, cfg, proto, delay_secs=10
                        )

                        # preserve any messages waiting in the in_queue
                        new_inst.in_queue = old_inst.in_queue

    async def message_broker(self) -> None:
        while True:
            meta, message = await self.bus.get_message()
            logger.info(f"Got message on bus from {meta.from_instance}: {message}")

            inst = meta.from_instance

            for link in self.links:
                if link.name == meta.from_link:
                    continue
                await link.send_message(meta, message)

            for dest in self.routes[f"{inst}#{message.channel}"]:
                inst, to_channel = dest.split("#")
                if inst in self.instances:
                    await self.instances[inst].in_queue.put((to_channel, message, meta))
