"""MoltBot/OpenClaw WebSocket client for Python."""

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

import websockets
from websockets.client import WebSocketClientProtocol


@dataclass
class MoltBotConfig:
    """Configuration for MoltBot connection."""

    gateway_url: str = "ws://127.0.0.1:18789"
    device_id: str = field(default_factory=lambda: f"python-scraper-{uuid.uuid4().hex[:8]}")
    device_name: str = "Python E-commerce Scraper"
    auth_token: str | None = None


class MoltBotClient:
    """WebSocket client for MoltBot/OpenClaw Gateway."""

    def __init__(self, config: MoltBotConfig | None = None):
        self.config = config or MoltBotConfig()
        self.ws: WebSocketClientProtocol | None = None
        self.connected = False
        self._request_id = 0
        self._pending_requests: dict[int, asyncio.Future] = {}
        self._event_handlers: dict[str, list[Callable]] = {}
        self._receive_task: asyncio.Task | None = None

    async def connect(self) -> bool:
        """Connect to MoltBot Gateway."""
        try:
            self.ws = await websockets.connect(self.config.gateway_url)

            # Send connect frame (required first message)
            connect_msg = {
                "type": "req",
                "id": self._next_id(),
                "method": "connect",
                "params": {
                    "device": {
                        "id": self.config.device_id,
                        "name": self.config.device_name,
                        "type": "client",
                    },
                },
            }

            if self.config.auth_token:
                connect_msg["params"]["auth"] = {"token": self.config.auth_token}

            await self.ws.send(json.dumps(connect_msg))

            # Wait for connect response
            response = await self.ws.recv()
            data = json.loads(response)

            if data.get("type") == "res" and data.get("ok"):
                self.connected = True
                self._receive_task = asyncio.create_task(self._receive_loop())
                return True
            else:
                error = data.get("error", "Unknown error")
                raise ConnectionError(f"MoltBot connection failed: {error}")

        except Exception as e:
            self.connected = False
            raise ConnectionError(f"Failed to connect to MoltBot Gateway: {e}")

    async def disconnect(self):
        """Disconnect from MoltBot Gateway."""
        self.connected = False
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        if self.ws:
            await self.ws.close()

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _receive_loop(self):
        """Background task to receive messages."""
        try:
            async for message in self.ws:
                data = json.loads(message)
                msg_type = data.get("type")

                if msg_type == "res":
                    # Response to a request
                    req_id = data.get("id")
                    if req_id in self._pending_requests:
                        future = self._pending_requests.pop(req_id)
                        if data.get("ok"):
                            future.set_result(data.get("payload"))
                        else:
                            future.set_exception(Exception(data.get("error", "Unknown error")))

                elif msg_type == "event":
                    # Server-push event
                    event_name = data.get("event")
                    if event_name in self._event_handlers:
                        for handler in self._event_handlers[event_name]:
                            try:
                                await handler(data.get("payload"))
                            except Exception:
                                pass

        except websockets.exceptions.ConnectionClosed:
            self.connected = False
        except asyncio.CancelledError:
            pass

    async def request(self, method: str, params: dict | None = None, timeout: float = 30.0) -> Any:
        """Send a request and wait for response."""
        if not self.connected:
            raise ConnectionError("Not connected to MoltBot Gateway")

        req_id = self._next_id()
        msg = {
            "type": "req",
            "id": req_id,
            "method": method,
            "params": params or {},
        }

        future = asyncio.get_event_loop().create_future()
        self._pending_requests[req_id] = future

        await self.ws.send(json.dumps(msg))

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending_requests.pop(req_id, None)
            raise TimeoutError(f"Request '{method}' timed out after {timeout}s")

    def on_event(self, event_name: str, handler: Callable):
        """Register an event handler."""
        if event_name not in self._event_handlers:
            self._event_handlers[event_name] = []
        self._event_handlers[event_name].append(handler)

    # High-level methods

    async def health(self) -> dict:
        """Check gateway health."""
        return await self.request("health")

    async def status(self) -> dict:
        """Get gateway status."""
        return await self.request("status")

    async def list_sessions(self) -> list:
        """List active sessions."""
        return await self.request("sessions_list")

    async def send_message(self, session_id: str, message: str) -> dict:
        """Send a message to a session."""
        return await self.request("sessions_send", {
            "sessionId": session_id,
            "message": message,
            "idempotencyKey": uuid.uuid4().hex,
        })

    async def invoke_agent(self, prompt: str, tools: list[str] | None = None) -> dict:
        """Invoke an agent with a prompt."""
        params = {
            "prompt": prompt,
            "idempotencyKey": uuid.uuid4().hex,
        }
        if tools:
            params["tools"] = tools
        return await self.request("agent", params, timeout=120.0)

    async def invoke_node(self, command: str, params: dict | None = None) -> dict:
        """Invoke a node command."""
        return await self.request("node.invoke", {
            "command": command,
            "params": params or {},
        })
