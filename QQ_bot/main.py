"""Minimal QQ bot that replies with text only."""
# -*- coding: utf-8 -*-
import os
import random

import botpy
from botpy import logging
from botpy.ext.cog_yaml import read
from botpy.message import GroupMessage

config = read(os.path.join(os.path.dirname(__file__), "config.yaml"))

_log = logging.get_logger()


class MyClient(botpy.Client):
    async def on_ready(self):
        _log.info(f'robot "{self.robot.name}" is ready!')

    async def on_group_at_message_create(self, message: GroupMessage):
        content = message.content.strip()
        # 随机在文本后追加感叹号或问号进行回复
        responses = ["!", "?"]
        random_response = random.choice(responses)
        await message.reply(content=content + random_response)


if __name__ == "__main__":
    intents = botpy.Intents(public_messages=True)
    client = MyClient(intents=intents, is_sandbox=True)
    client.run(appid=config["appid"], secret=config["secret"]) 

