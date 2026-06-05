"""Local proxy adapters for browser automation.

Chromium/Playwright cannot use username/password authentication with SOCKS5
proxies directly. This module exposes a local no-auth SOCKS5 endpoint and
authenticates to the upstream SOCKS5 proxy on behalf of the browser.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import socket
import struct
import threading
from urllib.parse import unquote, urlsplit

logger = logging.getLogger(__name__)

_BRIDGES: dict[str, "AuthenticatedSocks5Bridge"] = {}
_LOCK = threading.RLock()


def mask_proxy_url(proxy_url: str | None) -> str:
    parsed = urlsplit(str(proxy_url or ""))
    if not parsed.scheme or not parsed.hostname or not (parsed.username or parsed.password):
        return str(proxy_url or "")
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://***@{host}{port}"


def needs_socks5_auth_bridge(proxy_url: str | None) -> bool:
    parsed = urlsplit(str(proxy_url or ""))
    return parsed.scheme.lower().startswith("socks") and bool(parsed.hostname) and bool(parsed.username or parsed.password)


def get_playwright_proxy_url(proxy_url: str | None) -> str:
    """Return a Playwright-safe proxy URL for browser launch."""
    proxy_url = str(proxy_url or "").strip()
    if not needs_socks5_auth_bridge(proxy_url):
        return proxy_url
    with _LOCK:
        bridge = _BRIDGES.get(proxy_url)
        if bridge is None:
            bridge = AuthenticatedSocks5Bridge(proxy_url)
            bridge.start()
            _BRIDGES[proxy_url] = bridge
        return bridge.local_url


async def _relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


class AuthenticatedSocks5Bridge:
    def __init__(self, upstream_url: str):
        parsed = urlsplit(upstream_url)
        if not parsed.hostname or not parsed.port:
            raise ValueError(f"invalid upstream SOCKS5 proxy: {mask_proxy_url(upstream_url)}")
        self.upstream_url = upstream_url
        self.upstream_host = parsed.hostname
        self.upstream_port = parsed.port
        self.username = unquote(parsed.username or "")
        self.password = unquote(parsed.password or "")
        self.local_url = ""
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: asyncio.base_events.Server | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None

    def start(self) -> str:
        if self.local_url:
            return self.local_url
        self._loop = asyncio.new_event_loop()
        ready = self._ready

        async def _run() -> None:
            try:
                self._server = await asyncio.start_server(self._handle_client, "127.0.0.1", 0)
                sock = next(iter(self._server.sockets or []), None)
                if sock is None:
                    raise RuntimeError("SOCKS5 bridge failed to bind")
                self.local_url = f"socks5://127.0.0.1:{sock.getsockname()[1]}"
                logger.info("[ProxyBridge] %s -> %s", self.local_url, mask_proxy_url(self.upstream_url))
                ready.set()
                await self._server.serve_forever()
            except BaseException as exc:  # noqa: BLE001
                self._error = exc
                ready.set()
                raise

        def _thread_main() -> None:
            assert self._loop is not None
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(_run())

        self._thread = threading.Thread(target=_thread_main, name="proxy-bridge", daemon=True)
        self._thread.start()
        if not ready.wait(timeout=10):
            raise RuntimeError("SOCKS5 bridge did not become ready")
        if self._error is not None:
            raise RuntimeError(f"SOCKS5 bridge failed: {self._error}") from self._error
        return self.local_url

    def stop(self) -> None:
        loop = self._loop
        server = self._server
        if loop and server and loop.is_running():
            loop.call_soon_threadsafe(server.close)

    async def _handle_client(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
        upstream_writer = None
        try:
            header = await asyncio.wait_for(client_reader.readexactly(2), timeout=30)
            version, nmethods = struct.unpack("!BB", header)
            if version != 5:
                return
            await client_reader.readexactly(nmethods)
            client_writer.write(b"\x05\x00")
            await client_writer.drain()

            request = await asyncio.wait_for(client_reader.readexactly(4), timeout=30)
            version, command, _reserved, atyp = struct.unpack("!BBBB", request)
            if version != 5 or command != 1:
                client_writer.write(b"\x05\x07\x00\x01" + b"\x00" * 6)
                await client_writer.drain()
                return

            addr_payload = await self._read_addr_payload(client_reader, atyp)
            port_payload = await client_reader.readexactly(2)

            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(self.upstream_host, self.upstream_port),
                timeout=30,
            )
            await self._authenticate_upstream(upstream_reader, upstream_writer)
            upstream_writer.write(b"\x05\x01\x00" + bytes([atyp]) + addr_payload + port_payload)
            await upstream_writer.drain()

            reply = await asyncio.wait_for(upstream_reader.readexactly(4), timeout=30)
            _ver, status, _rsv, reply_atyp = struct.unpack("!BBBB", reply)
            await self._read_addr_payload(upstream_reader, reply_atyp)
            await upstream_reader.readexactly(2)
            if status != 0:
                client_writer.write(b"\x05\x05\x00\x01" + b"\x00" * 6)
                await client_writer.drain()
                return

            client_writer.write(b"\x05\x00\x00\x01" + b"\x00" * 6)
            await client_writer.drain()
            await asyncio.gather(_relay(client_reader, upstream_writer), _relay(upstream_reader, client_writer))
        except Exception:
            pass
        finally:
            try:
                client_writer.close()
            except Exception:
                pass
            if upstream_writer is not None:
                try:
                    upstream_writer.close()
                except Exception:
                    pass

    @staticmethod
    async def _read_addr_payload(reader: asyncio.StreamReader, atyp: int) -> bytes:
        if atyp == 1:
            return await reader.readexactly(4)
        if atyp == 3:
            size = await reader.readexactly(1)
            return size + await reader.readexactly(size[0])
        if atyp == 4:
            return await reader.readexactly(16)
        raise ValueError(f"unsupported SOCKS5 address type: {atyp}")

    async def _authenticate_upstream(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        username = self.username.encode()
        password = self.password.encode()
        if len(username) > 255 or len(password) > 255:
            raise ValueError("SOCKS5 username/password must be <= 255 bytes")

        writer.write(b"\x05\x01\x02")
        await writer.drain()
        method_reply = await asyncio.wait_for(reader.readexactly(2), timeout=30)
        if method_reply != b"\x05\x02":
            raise RuntimeError("upstream SOCKS5 proxy did not accept username/password auth")

        writer.write(b"\x01" + bytes([len(username)]) + username + bytes([len(password)]) + password)
        await writer.drain()
        auth_reply = await asyncio.wait_for(reader.readexactly(2), timeout=30)
        if auth_reply != b"\x01\x00":
            raise RuntimeError("upstream SOCKS5 proxy authentication failed")


def _stop_all() -> None:
    with _LOCK:
        for bridge in list(_BRIDGES.values()):
            bridge.stop()


atexit.register(_stop_all)
