import os
import asyncio
import uuid
import subprocess
import threading
from pyrogram import Client, filters
from pyrogram.types import Message
from flask import Flask, send_from_directory

# --- Configuration (Set these as Heroku Config Vars) ---
API_ID = int(os.environ.get("API_ID", 23907288))
API_HASH = os.environ.get("API_HASH", "f9a47570ed19aebf8eb0f0a5ec1111e5")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8387124222:AAE0jNJRLsoWz887kUgPbAesemH8VfREz_M")
BASE_URL = os.environ.get("BASE_URL", "https://imgcapapi-a60f52761a9b.herokuapp.com")  # e.g. https://abcd.ngrok.io/hls

PORT = int(os.environ.get("PORT", 8080))

# This is the public URL where your HLS files will be accessed
# Heroku's main URL will be used for this.
APP_URL = os.environ.get("HEROKU_APP_URL")  # Get this from your Heroku settings if needed


# Directory where FFmpeg will store the segments/playlists
HLS_STORAGE_DIR = "hls_files"
os.makedirs(HLS_STORAGE_DIR, exist_ok=True)

# Adaptive Streaming Qualities (Same as before)
QUALITIES = {
    "1080p": {"resolution": "1920x1080", "video_bitrate": "5000k", "audio_bitrate": "192k"},
    "720p": {"resolution": "1280x720", "video_bitrate": "2500k", "audio_bitrate": "128k"},
    "480p": {"resolution": "854x480", "video_bitrate": "1000k", "audio_bitrate": "96k"},
    "360p": {"resolution": "640x360", "video_bitrate": "500k", "audio_bitrate": "64k"},
}

# --- Flask Web Server Setup (To serve the HLS files) ---
web_app = Flask(__name__)

@web_app.route('/hls/<path:filename>')
def serve_hls_files(filename):
    """Serves the M3U8 and .ts files from the local storage directory."""
    # Ensure M3U8 files are served with the correct MIME type
    if filename.endswith(".m3u8"):
        mimetype = 'application/x-mpegURL'
    elif filename.endswith(".ts"):
        mimetype = 'video/MP2T'
    else:
        mimetype = None
        
    return send_from_directory(HLS_STORAGE_DIR, filename, mimetype=mimetype)

# --- Pyrogram Bot Setup (The Handler) ---
# ... (create_hls_command function remains the same as before, 
# but it should use HLS_STORAGE_DIR and BASE_URL) ...

def create_hls_command(input_url, output_path, file_id):
    """Generates the FFmpeg command."""
    
    cmd = [
        "ffmpeg", 
        "-i", input_url,
        "-y", # Overwrite output files
    ]

    stream_map = ""
    for name, q in QUALITIES.items():
        # Ensure output is to the specified output_path
        cmd.extend([
            "-map", "0:v:0", "-map", "0:a:0",
            "-c:v", "libx264", "-crf", "23",
            "-c:a", "aac", "-ar", "48000",
            "-vf", f"scale=w=min(iw\\, {q['resolution'].split('x')[0]}):h={q['resolution'].split('x')[1]}:force_original_aspect_ratio=decrease,pad=w={q['resolution'].split('x')[0]}:h={q['resolution'].split('x')[1]}:-1:-1:color=black",
            "-b:v", q['video_bitrate'], "-maxrate", q['video_bitrate'], 
            "-b:a", q['audio_bitrate'],
            "-hls_segment_filename", f"{output_path}/{file_id}_{name}_%03d.ts",
            f"{output_path}/{file_id}_{name}.m3u8",
        ])
        stream_map += f"v:0,a:0,name:{file_id}_{name} "
        
    # Master Playlist setup
    cmd.extend([
        "-f", "hls", 
        "-hls_list_size", "0", 
        "-hls_time", "10",
        "-master_pl_name", f"{file_id}_master.m3u8",
        "-var_stream_map", stream_map.strip(),
        "-hls_base_url", BASE_URL, # The base URL for the web server
        f"{output_path}/%v.m3u8" # Output template for individual quality playlists
    ])
    
    return cmd

# Pyrogram Bot Client
bot_app = Client("HLS_Converter_Bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@bot_app.on_message(filters.text & filters.private)
async def process_link(client: Client, message: Message):
    url = message.text.strip()
    
    if not url.startswith("http"):
        await message.reply_text("Please send a valid HTTP link.")
        return

    file_id = str(uuid.uuid4())
    output_path = HLS_STORAGE_DIR # Use the shared HLS directory
    
    initial_msg = await message.reply_text(f"🎬 Conversion started for: `{url}`")

    try:
        # 1. Create FFmpeg command
        ffmpeg_cmd = create_hls_command(url, output_path, file_id)
        
        # 2. Execute FFmpeg command
        # This part must be handled carefully to prevent blocking the web server
        
        process = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_message = stderr.decode(errors='ignore')
            await initial_msg.edit_text(f"❌ Conversion Failed. Error: `{error_message[:500]}`")
            return

        final_m3u8_url = f"{BASE_URL}{file_id}_master.m3u8"

        await initial_msg.edit_text(
            "✅ **HLS Conversion Complete!**\n\n"
            "Here is your **M3U8 Master Playlist URL** (Adaptive Quality):\n"
            f"`{final_m3u8_url}`\n\n"
            f"⚠️ **Warning:** Files may be deleted when the Heroku dyno restarts."
        )

    except Exception as e:
        await initial_msg.edit_text(f"❌ An unexpected error occurred: `{e}`")

# --- Main Runner ---
def run_bot():
    """Runs the Pyrogram bot in a separate thread/process."""
    bot_app.run()

if __name__ == "__main__":
    # Start the Pyrogram bot in a separate thread
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.start()
    
    # Start the Flask web server on the Heroku web dyno
    print(f"Starting Flask web server on port {PORT}...")
    # NOTE: In Heroku, you MUST bind to 0.0.0.0 and the assigned PORT
    web_app.run(host="0.0.0.0", port=PORT, threaded=True) 


