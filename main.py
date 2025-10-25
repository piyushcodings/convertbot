import os
import uuid
import subprocess
import requests
import boto3
from pyrogram import Client, filters

# -------------------
# Pyrogram Bot Setup
# -------------------
API_ID = int(os.environ.get("API_ID", 23907288))
API_HASH = os.environ.get("API_HASH", "f9a47570ed19aebf8eb0f0a5ec1111e5")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8387124222:AAE0jNJRLsoWz887kUgPbAesemH8VfREz_M")

app = Client(
    "hls_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# -------------------
# Cloudflare R2 Setup (S3-compatible)
# -------------------
R2_ACCESS_KEY = "37d92a0cc181b5cada8805126ecf0cc1"
R2_SECRET_KEY = "813cdf431e0a5cbe1b98a103b522ffaae10b82319941518cf0e5d02bf98fff39"
R2_ENDPOINT = "https://c924773969fa9cd80ba2bf5bae7cfb00.r2.cloudflarestorage.com"
R2_BUCKET = "reeva-erp"
R2_PUBLIC_URL = f"https://{R2_BUCKET}.r2.cloudflarestorage.com"

r2_client = boto3.client(
    "s3",
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY,
    endpoint_url=R2_ENDPOINT,
    region_name="auto"
)

# -------------------
# /convert command
# -------------------
@app.on_message(filters.command("convert") & filters.private)
async def convert_to_hls(client, message):
    if len(message.command) < 2:
        await message.reply_text("Usage: /convert <video-url>")
        return

    video_url = message.command[1]
    job_id = str(uuid.uuid4())
    os.makedirs(job_id, exist_ok=True)
    local_file = f"{job_id}/video.mp4"

    progress_msg = await message.reply_text("Downloading video...")

    # --------- Step 1: Download the video ----------
    try:
        with requests.get(video_url, stream=True, timeout=600) as r:
            r.raise_for_status()
            with open(local_file, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024*1024):
                    if chunk:
                        f.write(chunk)
        await progress_msg.edit_text("✅ Video downloaded. Starting HLS conversion...")
    except Exception as e:
        await progress_msg.edit_text(f"❌ Download failed: {e}")
        return

    # --------- Step 2: Convert to HLS ----------
    qualities = {
        "360p": "640:360",
        "480p": "854:480",
        "720p": "1280:720",
        "1080p": "1920:1080"
    }

    try:
        for idx, (q, res) in enumerate(qualities.items(), 1):
            await progress_msg.edit_text(f"Converting {q} ({idx}/{len(qualities)})...")
            q_dir = os.path.join(job_id, q)
            os.makedirs(q_dir, exist_ok=True)

            cmd = [
                "ffmpeg",
                "-i", local_file,
                "-vf", f"scale={res}",
                "-c:a", "aac",
                "-c:v", "h264",
                "-hls_time", "10",
                "-hls_list_size", "0",
                f"{q_dir}/{q}.m3u8"
            ]
            subprocess.run(cmd, check=True)

        await progress_msg.edit_text("Uploading HLS files to Cloudflare R2...")

        # --------- Step 3: Upload to R2 ----------
        for root, dirs, files in os.walk(job_id):
            for file in files:
                local_path = os.path.join(root, file)
                r2_path = os.path.relpath(local_path, job_id)
                r2_client.upload_file(local_path, R2_BUCKET, f"{job_id}/{r2_path}", ExtraArgs={"ACL": "public-read"})

        # --------- Step 4: Create master playlist ----------
        master_file = os.path.join(job_id, "master.m3u8")
        with open(master_file, "w") as f:
            f.write("#EXTM3U\n#EXT-X-VERSION:3\n")
            for q in qualities.keys():
                f.write(f"#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION={qualities[q]}\n")
                f.write(f"{R2_PUBLIC_URL}/{job_id}/{q}/{q}.m3u8\n")

        # Upload master playlist
        r2_client.upload_file(master_file, R2_BUCKET, f"{job_id}/master.m3u8", ExtraArgs={"ACL": "public-read"})

        master_url = f"{R2_PUBLIC_URL}/{job_id}/master.m3u8"
        await progress_msg.edit_text(f"✅ Conversion completed!\nMaster URL:\n{master_url}")

    except Exception as e:
        await progress_msg.edit_text(f"❌ Conversion failed: {e}")

app.run()
