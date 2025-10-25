import os
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
import uuid

# --- Configuration (Set these as Heroku Config Vars) ---
API_ID = int(os.environ.get("API_ID", 23907288))
API_HASH = os.environ.get("API_HASH", "f9a47570ed19aebf8eb0f0a5ec1111e5")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8387124222:AAE0jNJRLsoWz887kUgPbAesemH8VfREz_M")
BASE_URL = os.environ.get("BASE_URL", "https://imgcapapi-a60f52761a9b.herokuapp.com")  # e.g. https://abcd.ngrok.io/hls

app = Client("HLS_Converter_Bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Adaptive Streaming Resolutions/Bitrates
# You can customize these qualities. Ensure the highest quality is included.
QUALITIES = {
    "1080p": {"resolution": "1920x1080", "video_bitrate": "5000k", "audio_bitrate": "192k"},
    "720p": {"resolution": "1280x720", "video_bitrate": "2500k", "audio_bitrate": "128k"},
    "480p": {"resolution": "854x480", "video_bitrate": "1000k", "audio_bitrate": "96k"},
    "360p": {"resolution": "640x360", "video_bitrate": "500k", "audio_bitrate": "64k"},
}

def create_hls_command(input_url, output_folder, file_id):
    """
    Generates the FFmpeg command for Adaptive Bitrate HLS streaming.
    The -var_stream_map is crucial for the master playlist.
    """
    
    # Base command structure
    cmd = [
        "ffmpeg", 
        "-i", input_url,
        "-y", # Overwrite output files
    ]

    # Stream maps and output setup for each quality
    stream_map = ""
    for name, q in QUALITIES.items():
        # -vf: Scale resolution, -b:v: Video bitrate, -b:a: Audio bitrate
        cmd.extend([
            "-map", "0:v:0", "-map", "0:a:0",
            "-c:v", "libx264", "-crf", "23",
            "-c:a", "aac", "-ar", "48000",
            "-vf", f"scale={q['resolution']}:force_original_aspect_ratio=decrease,pad={q['resolution']}:-1:-1:color=black",
            "-b:v", q['video_bitrate'], "-maxrate", q['video_bitrate'], 
            "-b:a", q['audio_bitrate'],
            "-hls_segment_filename", f"{output_folder}/{name}_%03d.ts",
            f"{output_folder}/{name}.m3u8",
        ])
        stream_map += f"v:0,a:0,name:{name} "
        
    # Master Playlist setup
    cmd.extend([
        "-f", "hls", 
        "-hls_list_size", "0", 
        "-hls_time", "10",
        "-master_pl_name", "master.m3u8",
        "-var_stream_map", stream_map.strip(),
        "-hls_base_url", f"{BASE_URL}{file_id}/", # Base URL for client to access segments
        f"{output_folder}/%v.m3u8" # Output template for individual quality playlists
    ])
    
    return cmd

@app.on_message(filters.text & filters.private)
async def process_link(client: Client, message: Message):
    url = message.text.strip()
    
    if not url.startswith("http"):
        await message.reply_text("Please send a valid direct video link starting with `http`.")
        return

    # Use a unique ID for the conversion job and folder
    file_id = str(uuid.uuid4())
    output_folder = f"./{file_id}"
    os.makedirs(output_folder, exist_ok=True)
    
    initial_msg = await message.reply_text(f"🎬 Starting HLS conversion for: `{url}`\n\nThis will take time, as the video is being re-encoded into multiple qualities (Adaptive Streaming).")

    try:
        # 1. Create FFmpeg command
        ffmpeg_cmd = create_hls_command(url, output_folder, file_id)
        
        # 2. Execute FFmpeg command
        process = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        
        # Monitor the process (optional, for progress updates)
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_message = stderr.decode(errors='ignore')
            await initial_msg.edit_text(f"❌ FFmpeg Conversion Failed (Return Code: {process.returncode}).\n\nError Output:\n`{error_message[:1000]}`")
            return

        # 3. Handle File Upload (The CRITICAL step for Heroku)
        # --- IMPORTANT PLACEHOLDER ---
        # The following block must be replaced with code that:
        # a) Uploads all files in the output_folder (master.m3u8, all *.m3u8, all *.ts) 
        #    to your public cloud storage (e.g., S3).
        # b) Ensures the uploaded files are publicly accessible via the BASE_URL.
        
        # await upload_to_s3(output_folder, file_id) # Example function call
        # -----------------------------
        
        final_m3u8_url = f"{BASE_URL}{file_id}/master.m3u8"

        await initial_msg.edit_text(
            "✅ **HLS Conversion Complete!**\n\n"
            "Here is your **M3U8 Master Playlist URL** (Adaptive Quality):\n"
            f"`{final_m3u8_url}`\n\n"
            f"**Note:** Video segments are hosted on your external storage at `{BASE_URL}{file_id}/`."
        )

    except Exception as e:
        await initial_msg.edit_text(f"❌ An unexpected error occurred: `{e}`")

    finally:
        # 4. Cleanup (Delete local files after upload)
        await asyncio.to_thread(lambda: os.system(f"rm -rf {output_folder}"))


if __name__ == "__main__":
    app.run()
       
