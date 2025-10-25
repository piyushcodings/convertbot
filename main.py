"""
Pyrogram Telegram Bot — Direct link -> HLS (.m3u8) converter
-------------------------------------------------------------
Usage:
1. Install requirements: pip install pyrogram tgcrypto aiohttp python-dotenv
2. System must have ffmpeg installed and available in PATH.
3. Set environment variables:
   - API_ID, API_HASH, BOT_TOKEN (for Pyrogram bot)
   - BASE_URL — public URL that points to the server's /hls route (e.g. https://abcd.ngrok.io/hls)
     If you don't have a public URL, run `ngrok http 8080` and use the generated http(s) URL + "/hls".
4. Run this script on a machine with a public IP or behind a tunnel (ngrok/Caddy/reverse proxy).

What it does:
- Accepts /convert <direct-video-url> [qualities]
  e.g. /convert https://example.com/video.mp4 360,480,720
- Uses ffmpeg to transcode/pack the incoming stream or file into HLS with the requested renditions
- Serves the generated HLS folder over an aiohttp static route (/hls)
- Replies with the public m3u8 master playlist URL when finished

Important notes & limitations:
- This script assumes you have enough CPU to transcode. Transcoding multiple renditions is CPU- and I/O-heavy.
- If the input URL already supports byte-range requests and good throughput, ffmpeg will stream from it; otherwise ffmpeg may fail.
- You must provide a publicly reachable BASE_URL for the returned links to work for others.

"""

import os
import asyncio
import shlex
import subprocess
import uuid
from pathlib import Path
from typing import List

from pyrogram import Client, filters
from pyrogram.types import Message
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.environ.get("API_ID", 23907288))
API_HASH = os.environ.get("API_HASH", "f9a47570ed19aebf8eb0f0a5ec1111e5")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8387124222:AAE0jNJRLsoWz887kUgPbAesemH8VfREz_M")
BASE_URL = os.environ.get("BASE_URL", "https://imgcapapi-a60f52761a9b.herokuapp.com")  # e.g. https://abcd.ngrok.io/hls

# Directory where hls jobs will be written
BASE_OUTPUT = Path("./hls_output")
BASE_OUTPUT.mkdir(parents=True, exist_ok=True)

app = web.Application()

# Serve the hls_output folder at route /hls
app.router.add_static('/hls', path=str(BASE_OUTPUT), show_index=True)

# Simple HTML index for root
async def index(request):
    return web.Response(text="Pyrogram HLS converter — /convert in Telegram bot. HLS files are served under /hls/", content_type='text/plain')

app.router.add_get('/', index)

# Helper: build ffmpeg command for multiple renditions
def build_ffmpeg_command(input_url: str, outdir: Path, qualities: List[int]):
    # qualities: list of vertical resolution integers like [360,480,720]
    # target bitrates (approx) — you can tweak mapping as needed
    bitrate_map = {240: '300k', 360: '600k', 480: '1000k', 720: '2000k', 1080: '3500k'}

    # Create outdir
    outdir.mkdir(parents=True, exist_ok=True)

    # Prepare per-variant settings
    vcodec_cmds = []
    stream_maps = []
    segment_template = str(outdir / 'stream_%v' / 'seg%03d.ts')
    playlist_template = str(outdir / 'stream_%v' / 'prog.m3u8')

    # We'll build ffmpeg args piece by piece
    cmd = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'warning', '-i', input_url]

    # Use one audio stream for all variants
    # For each quality, add mapping for video
    for idx, q in enumerate(qualities):
        br = bitrate_map.get(q, f"{q*4}k")  # fallback bitrate heuristic
        # map video
        cmd += ['-c:v:%d' % idx, 'libx264', '-b:v:%d' % idx, br, '-vf:%d' % idx, f"scale=-2:{q}", '-preset', 'veryfast', '-g', '48', '-keyint_min', '48']
        stream_maps.append(f'v:{idx},a:0')

    # Audio (single copy)
    cmd += ['-c:a', 'aac', '-b:a', '128k']

    # HLS options
    cmd += [
        '-f', 'hls',
        '-hls_time', '6',
        '-hls_playlist_type', 'vod',
        '-master_pl_name', 'master.m3u8',
        '-hls_segment_filename', segment_template,
    ]

    # var_stream_map requires mapping strings like "v:0,a:0 v:1,a:0"
    var_map = ' '.join(stream_maps)
    cmd += ['-var_stream_map', var_map, playlist_template]

    return cmd


async def run_ffmpeg(cmd: List[str], message: Message):
    # Run ffmpeg as subprocess and stream output to Telegram so user sees progress
    # We show only limited logs to avoid flooding
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

    # Relay stderr lines to Telegram periodically
    last_report = 0
    report_msg = None
    while True:
        line = await proc.stderr.readline()
        if not line:
            break
        text = line.decode(errors='ignore').strip()
        # send small updates every ~3 seconds
        now = asyncio.get_event_loop().time()
        if now - last_report > 3:
            try:
                if report_msg is None:
                    report_msg = await message.reply_text(f'FFmpeg: {text[:400]}')
                else:
                    await report_msg.edit_text(f'FFmpeg: {text[:400]}')
            except Exception:
                pass
            last_report = now

    await proc.wait()
    return proc.returncode


# Pyrogram client
bot = Client("hls_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@bot.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, message: Message):
    await message.reply_text("Hello! Send /convert <direct-video-url> [qualities]
Example: /convert https://example.com/video.mp4 360,480,720")


@bot.on_message(filters.command("convert") & filters.private)
async def convert_cmd(client: Client, message: Message):
    """Usage: /convert <url> [comma-separated qualities]
    """
    if len(message.command) < 2:
        await message.reply_text("Usage: /convert <direct-video-url> [qualities]. Example: /convert https://... 360,480,720")
        return

    input_url = message.command[1]
    qualities_raw = '480,720'
    if len(message.command) >= 3:
        qualities_raw = message.command[2]

    try:
        qualities = [int(q.strip()) for q in qualities_raw.split(',') if q.strip()]
        qualities = sorted(list(set(qualities)))
    except Exception:
        await message.reply_text("Could not parse qualities. Provide comma separated numbers. e.g. 360,480,720")
        return

    job_id = uuid.uuid4().hex[:10]
    outdir = BASE_OUTPUT / job_id
    outdir.mkdir(parents=True, exist_ok=True)

    await message.reply_text(f"Starting conversion job {job_id}. This may take time depending on input and your server CPU.")

    cmd = build_ffmpeg_command(input_url, outdir, qualities)

    # Start ffmpeg
    try:
        rc = await run_ffmpeg(cmd, message)
    except Exception as e:
        await message.reply_text(f"FFmpeg failed to start: {e}")
        return

    if rc != 0:
        await message.reply_text(f"Conversion finished with error code {rc}. Check server logs.")
        return

    # On success, reply with master playlist URL
    master_path = f"/hls/{job_id}/master.m3u8"
    if BASE_URL:
        # Ensure no trailing slash
        base = BASE_URL.rstrip('/')
        master_url = f"{base}/{job_id}/master.m3u8" if not base.endswith('/hls') else f"{base}/{job_id}/master.m3u8"
    else:
        # If BASE_URL not set, attempt to craft a local URL (may not be reachable externally)
        master_url = f"http://{get_local_host_port()}/hls/{job_id}/master.m3u8"

    await message.reply_text(f"Conversion finished ✅\nMaster playlist: {master_url}\nPlay using VLC or any HLS-capable player.")


def get_local_host_port():
    # default when BASE_URL not provided
    return '127.0.0.1:8080'


async def start_webserver():
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    print('Web server started on http://0.0.0.0:8080 — HLS files served at /hls')


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.create_task(start_webserver())
    bot.run()
