import requests
import aiohttp
import logging
import core.bridge
from core.types import AttachmentHost
from typing import IO

logger = logging.getLogger('imgf.lt')

class ImagefaultAttachmentHost(AttachmentHost):
    def __init__(self) -> None:
        self.session = aiohttp.ClientSession()

    async def dispose(self) -> None:
        await self.session.close()

    async def put(self, data: IO) -> str:
        logger.info('starting data upload.')
        fd = aiohttp.FormData({'upload': data})
        async with self.session.post('https://imgf.lt/app_upload', data=fd) as resp:
            url = (await resp.content.readline()).decode('utf-8')
        logger.info(f'Got url back: {url}')
        return url

def init(bridge: 'core.bridge.Bridge') -> None:
    host = ImagefaultAttachmentHost()
    bridge.set_attachment_host(host)
    bridge.add_destructor(host.dispose)
