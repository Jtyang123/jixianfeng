from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import datetime
from uuid import uuid4


class A2AError(RuntimeError):
    pass


class A2ABus:
    """轻量 Agent-to-Agent 消息总线，内存队列与数据库双写。"""

    def __init__(self, db, run_id: str, event_sink=None, enabled: bool = True) -> None:
        self.db = db
        self.run_id = run_id
        self.event_sink = event_sink
        self.enabled = enabled
        self._queues: dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)
        self._pending: dict[str, asyncio.Future] = {}

    def _emit(self, message: dict) -> None:
        if self.event_sink:
            self.event_sink(message)

    async def send(
        self,
        sender: str,
        receiver: str,
        message_type: str,
        content: dict,
        reply_to: str = "",
        correlation_id: str = "",
    ) -> dict:
        if not self.enabled:
            raise A2AError("A2A 消息总线未启用")
        if message_type not in {"request", "reply", "broadcast"}:
            raise A2AError(f"不支持的 A2A 消息类型：{message_type}")
        message = {
            "message_id": f"A2A-{uuid4().hex[:12].upper()}",
            "run_id": self.run_id,
            "sender": sender,
            "receiver": receiver,
            "message_type": message_type,
            "content": content,
            "reply_to": reply_to,
            "correlation_id": correlation_id or f"CORR-{uuid4().hex[:12].upper()}",
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "status": "queued",
        }
        try:
            self.db.create_a2a_message(message)
        except Exception as exc:
            raise A2AError(f"A2A 消息持久化失败：{exc}") from exc
        if message_type == "broadcast":
            targets = [key for key in self._queues if key != sender] or ["*"]
            for target in targets:
                await self._queues[target].put(message)
        else:
            await self._queues[receiver].put(message)
        self.db.update_a2a_message_status(message["message_id"], "delivered")
        message = {**message, "status": "delivered"}
        self._emit(message)
        if message_type == "reply":
            pending = self._pending.get(message["correlation_id"])
            if pending is not None and not pending.done():
                pending.set_result(message)
        return message

    async def request(self, sender: str, receiver: str, content: dict, timeout: float = 2.0) -> dict | None:
        correlation_id = f"CORR-{uuid4().hex[:12].upper()}"
        future = asyncio.get_running_loop().create_future()
        self._pending[correlation_id] = future
        request_message = await self.send(
            sender, receiver, "request", content, correlation_id=correlation_id
        )
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self.db.update_a2a_message_status(request_message["message_id"], "timeout")
            return None
        finally:
            self._pending.pop(correlation_id, None)

    async def reply(self, request_message: dict, sender: str, content: dict) -> dict:
        return await self.send(
            sender,
            request_message["sender"],
            "reply",
            content,
            reply_to=request_message["message_id"],
            correlation_id=request_message["correlation_id"],
        )

    async def broadcast(self, sender: str, content: dict) -> dict:
        return await self.send(sender, "*", "broadcast", content)

    async def receive(self, receiver: str, timeout: float = 1.0) -> dict | None:
        try:
            return await asyncio.wait_for(self._queues[receiver].get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def history(self, limit: int = 100) -> list[dict]:
        return self.db.list_a2a_messages(self.run_id, limit=limit)