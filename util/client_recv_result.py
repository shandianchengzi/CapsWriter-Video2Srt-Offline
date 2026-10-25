import asyncio
import json

import keyboard
import websockets
from config import ClientConfig as Config
from util.client_cosmic import Cosmic
from util.client_console import console
from util.client_check_websocket import check_websocket
from util.client_rename_audio import rename_audio
from util.client_write_md import write_md
from util.client_type_result import type_result
from util.client_format_text import format_text


async def recv_result():
    if not await check_websocket():
        return
    console.print('[green]连接成功\n')
    try:
        while True:
            # 接收消息
            message = await Cosmic.websocket.recv()
            message = json.loads(message)
            text = message['text']
            delay = message['time_complete'] - message['time_submit']

            # 如果非最终结果，继续等待
            if not message['is_final']:
                continue

            # 格式化文本
            text = await format_text(text)
            if not text.startswith("[QUICKER] "):
                await type_result(text)

            file_audio = rename_audio(message['task_id'], text, message['time_start'])

            # 记录写入 md 文件
            if getattr(Config, "save_text", True):
                write_md(text, message['time_start'], file_audio)

            # 控制台输出
            console.print(f'    转录时延：{delay:.2f}s')
            console.print(f'    识别结果：[green]{text}')
            console.line()

    except websockets.ConnectionClosedError:
        console.print('[red]连接断开\n')
    except websockets.ConnectionClosedOK:
        console.print('[red]连接断开\n')
    except Exception as e:
        print(e)
    finally:
        return
