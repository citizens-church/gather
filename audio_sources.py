"""Livestream audio input. Resolve a public feed, then decode to mono PCM locally."""
import asyncio
import ipaddress
from pathlib import Path
import socket
import shutil
from urllib.parse import urlsplit

class SourceError(RuntimeError):
    pass

def validate_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise SourceError("Use a public HTTPS livestream or audio URL, without a username or password.")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise SourceError("The livestream address could not be reached.") from None
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise SourceError("Use a public livestream URL. Local devices use the sound desk input.")
    return parsed

def resolve(url):
    parsed = validate_url(url)
    youtube = parsed.hostname.lower() in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
    if youtube:
        import yt_dlp
        class Quiet:
            def debug(self, message): pass
            def warning(self, message): pass
            def error(self, message): pass
        try:
            with yt_dlp.YoutubeDL({"format": "bestaudio/best", "quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True, "socket_timeout": 15, "retries": 1, "logger": Quiet(), "js_runtimes": {"node": {"path": shutil.which("node") or "node"}}}) as downloader:
                info = downloader.extract_info(url, download=False)
            if not info or not info.get("url") or info.get("_type") in {"playlist", "multi_video"}:
                raise SourceError("Paste a specific live video link, rather than the channel page.")
            media = info["url"]
            validate_url(media)
            # Playback resumes at the live edge for an active stream, at zero for a recording.
            return media, info.get("title", "Church livestream")[:120], bool(info.get("is_live")), info.get("http_headers", {}), min(86400, max(0, float(info.get("start_time") or 0)))
        except SourceError:
            raise
        except Exception as exc:
            if "will begin" in str(exc) or "not yet started" in str(exc):
                raise SourceError("This livestream has not started yet. Reconnect when the service goes live.") from None
            raise SourceError("YouTube could not provide this feed. Try the direct HTTPS audio/HLS URL, or connect the sound desk. Private or restricted videos may be unavailable.") from None
    return url, "Direct livestream audio", True, {}, 0

class Livestream:
    def __init__(self, process, title):
        self.process = process
        self.title = title
        self.stopped = False
        self.buffer = b""

    @classmethod
    async def open(cls, url, start_seconds=None, realtime=True):
        last_error = None
        for attempt in range(2):
            try:
                source = await cls._open(url, start_seconds, realtime)
                try:
                    first = await asyncio.wait_for(source.receive(), timeout=15)
                    if not first.get("bytes"):
                        raise SourceError("The stream contains no playable audio.")
                    source.buffer = first["bytes"] + source.buffer
                    return source
                except BaseException:
                    await source.close()
                    raise
            except SourceError as exc:
                last_error = exc
                if "not started" in str(exc):
                    raise
        raise last_error

    @classmethod
    async def _open(cls, url, requested_start=None, realtime=True):
        media, title, live, headers, start_seconds = await asyncio.to_thread(resolve, url)
        if requested_start is not None and not live:
            start_seconds = requested_start
        args = ["ffmpeg", "-nostdin", "-v", "error"]
        if realtime:
            args += ["-re"]
        args += ["-rw_timeout", "15000000", "-protocol_whitelist", "https,tls,tcp,crypto,http"]
        if live and ".m3u8" in urlsplit(media).path:
            args += ["-live_start_index", "-1"]
        if headers.get("User-Agent"):
            args += ["-user_agent", str(headers["User-Agent"]).replace("\r", "").replace("\n", "")]
        if not live and start_seconds:
            args += ["-ss", str(start_seconds)]
        args += ["-i", media, "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1"]
        process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        source = cls(process, title)
        source.live = live
        source.start_seconds = 0 if live else start_seconds
        return source

    async def send_json(self, message):
        pass

    async def receive(self):
        if self.stopped:
            return {"type": "websocket.disconnect"}
        # read() is cancellation-safe: a .5s booth poll does not lose partially read PCM.
        while len(self.buffer) < 3200:
            data = await self.process.stdout.read(3200 - len(self.buffer))
            if not data:
                if self.buffer:
                    chunk, self.buffer = self.buffer, b""
                    return {"type": "websocket.receive", "bytes": chunk[:len(chunk)//2*2]}
                code = await self.process.wait()
                if code and not self.stopped:
                    raise SourceError("The livestream connection ended. Reconnect the feed or switch to the sound desk.")
                return {"type": "websocket.disconnect"}
            self.buffer += data
        chunk, self.buffer = self.buffer[:3200], self.buffer[3200:]
        return {"type": "websocket.receive", "bytes": chunk}

    async def close(self):
        self.stopped = True
        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 3)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
