"""Optional on-demand Quicker WebSocket; does not affect normal dictation."""
import asyncio
import json
import re
import websockets

ACTION_ID = re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")


async def _request(action_id, settings):
    uri = f"ws://{settings['quicker_host']}:{settings['quicker_port']}/ws"
    async with websockets.connect(uri, open_timeout=2, close_timeout=2) as ws:
        password = settings.get("quicker_password", "")
        if password:
            await ws.send(json.dumps({"messageType": 5, "data": password, "serial": 0}))
            response = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            if response.get("messageType") != 6 or not response.get("isSuccess"):
                raise RuntimeError("Quicker WebSocket 认证失败")
        if action_id:
            await ws.send(json.dumps({
                "messageType": 2, "operation": "action", "data": "CapsWriterLite",
                "action": action_id, "serial": 1,
            }, ensure_ascii=False))


def connect_quicker(settings, action_id=None):
    if settings.get("quicker_host") not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("仅允许连接本机 Quicker")
    if not 1 <= int(settings.get("quicker_port", 0)) <= 65535:
        raise ValueError("Quicker 端口无效")
    if action_id and not ACTION_ID.fullmatch(action_id):
        raise ValueError("Quicker 动作 ID 必须是 UUID 格式")
    asyncio.run(_request(action_id, settings))
