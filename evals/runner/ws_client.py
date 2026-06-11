"""Async WebSocket client for CMS live-state endpoints.

Wire format: pure JSON message events.
CMS message types: subscribe, telemetry, status, error, unsubscribe.

Auth: JWT passed as Authorization: Bearer <token> header.

Client-side timeout: 50s (5s safety margin under AWS API Gateway 55s hard limit).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

# Client-side timeout — 5s safety margin under AWS API Gateway 55s hard limit
CLIENT_TIMEOUT_S = 50.0


async def connect_and_subscribe(
    url: str,
    subscribe_msg: dict[str, Any],
    duration_ms: int,
    jwt: str | None = None,
) -> list[dict[str, Any]]:
    """Connect to a CMS WebSocket endpoint, subscribe, and capture events.

    Args:
        url: WebSocket URL (wss://...).
        subscribe_msg: The subscribe message to send after connecting.
        duration_ms: How long to capture events (milliseconds).
        jwt: Optional JWT for Authorization header.

    Returns:
        List of received JSON event dicts.

    Raises:
        RuntimeError: On connection failure (URL logged, credentials never logged).
    """
    import websockets

    headers: dict[str, str] = {}
    if jwt:
        headers["Authorization"] = f"Bearer {jwt}"

    duration_s = min(duration_ms / 1000.0, CLIENT_TIMEOUT_S)
    events: list[dict[str, Any]] = []

    try:
        # Use the modern asyncio client which accepts additional_headers correctly.
        # The bare websockets.connect alias in websockets==13.1 routes to the
        # legacy client whose API does NOT accept additional_headers, leaking
        # the kwarg into asyncio.BaseEventLoop.create_connection() and raising
        # TypeError. Importing from websockets.asyncio.client.connect avoids
        # that path.
        from websockets.asyncio.client import connect as ws_connect
        async with ws_connect(url, additional_headers=headers) as ws:
            # Send subscribe message
            await ws.send(json.dumps(subscribe_msg))

            deadline = time.monotonic() + duration_s
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                except websockets.exceptions.ConnectionClosed:
                    break

                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    event = {"type": "raw", "data": raw}

                events.append(event)

                if event.get("type") == "error":
                    break

    except Exception as exc:
        # Log URL for debugging but never log credentials
        raise RuntimeError(
            f"WebSocket connection failed for {url!r}: {type(exc).__name__}: {exc}"
        ) from exc

    return events
