import os
import re
import tempfile
import uuid
import urllib.request
import json
import yt_dlp
import imageio_ffmpeg
import shutil
from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='.')
CORS(app)

DOWNLOAD_DIR = os.path.join(tempfile.gettempdir(), 'link_video_downloads')
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Locate and ensure standard ffmpeg.exe exists
FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
FFMPEG_DIR = os.path.dirname(FFMPEG_EXE)
STANDARD_FFMPEG = os.path.join(FFMPEG_DIR, 'ffmpeg.exe')
if not os.path.exists(STANDARD_FFMPEG):
    try:
        shutil.copyfile(FFMPEG_EXE, STANDARD_FFMPEG)
    except Exception as e:
        print(f"Copy ffmpeg error: {e}")

print(f"FFmpeg dizini: {FFMPEG_DIR}")

def extract_youtube_id(url):
    regex = r'(?:youtube\.com\/(?:watch\?v=|shorts\/|embed\/|live\/)|youtu\.be\/)([a-zA-Z0-9_-]{11})'
    match = re.search(regex, url)
    return match.group(1) if match else None

def get_base_ydl_opts():
    opts = {
        'quiet': True,
        'no_warnings': True,
        'ffmpeg_location': FFMPEG_DIR,
        'extractor_args': {
            'youtube': {
                'player_client': ['android']
            }
        }
    }
    return opts

@app.route('/')
def serve_index():
    return send_from_directory('.', 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    if os.path.exists(path):
        return send_from_directory('.', path)
    return jsonify({'error': 'Not found'}), 404

@app.route('/api/analyze', methods=['POST'])
def analyze_url():
    data = request.get_json(silent=True) or {}
    url = data.get('url', '').strip()
    if not url:
        return jsonify({'success': False, 'error': 'Lütfen geçerli bir URL girin.'}), 400

    yt_id = extract_youtube_id(url)
    
    # 1. Fetch metadata via oEmbed
    if yt_id:
        try:
            oembed_url = f"https://noembed.com/embed?url=https://www.youtube.com/watch?v={yt_id}"
            req = urllib.request.Request(oembed_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                oembed_data = json.loads(response.read().decode('utf-8'))
                
            title = oembed_data.get('title', 'YouTube Videosu')
            uploader = oembed_data.get('author_name', 'YouTube')
            thumbnail = f"https://i.ytimg.com/vi/{yt_id}/hqdefault.jpg"

            return jsonify({
                'success': True,
                'type': 'social_video',
                'title': title,
                'thumbnail': thumbnail,
                'duration': 0,
                'uploader': uploader,
                'description': '',
                'qualities': [
                    {'format_id': 'best_video', 'resolution': '🎬 En Yüksek Kalite Video (HD MP4)', 'height': 1080, 'ext': 'mp4', 'size': '', 'type': 'video'},
                    {'format_id': 'audio_mp3', 'resolution': '🎵 Sadece Müzik / Ses (MP3)', 'height': 0, 'ext': 'mp3', 'size': '', 'type': 'audio'}
                ],
                'original_url': url,
                'video_id': yt_id
            })
        except Exception as e:
            print(f"oEmbed error: {e}")

    # 2. For non-youtube links, use yt-dlp
    ydl_opts = get_base_ydl_opts()
    ydl_opts.update({'extract_flat': False, 'skip_download': True})

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return jsonify({
                'success': True,
                'type': 'social_video',
                'title': info.get('title', 'Video'),
                'thumbnail': info.get('thumbnail', ''),
                'duration': info.get('duration', 0),
                'uploader': info.get('uploader', ''),
                'description': '',
                'qualities': [
                    {'format_id': 'best_video', 'resolution': '🎬 En Yüksek Kalite Video', 'height': 1080, 'ext': 'mp4', 'size': '', 'type': 'video'},
                    {'format_id': 'audio_mp3', 'resolution': '🎵 Sadece Müzik / Ses (MP3)', 'height': 0, 'ext': 'mp3', 'size': '', 'type': 'audio'}
                ],
                'original_url': url
            })
    except Exception as e:
        return jsonify({'success': False, 'error': f'Video yüklenemedi: {str(e)}'}), 400

@app.route('/api/download', methods=['GET'])
def download_media():
    url = request.args.get('url', '').strip()
    format_id = request.args.get('format', 'best_video')

    if not url:
        return "URL parametresi eksik", 400

    unique_id = str(uuid.uuid4())[:8]
    output_template = os.path.join(DOWNLOAD_DIR, f'media_{unique_id}.%(ext)s')

    ydl_opts = get_base_ydl_opts()
    ydl_opts.update({'outtmpl': output_template})

    if format_id == 'audio_mp3':
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}],
        })
    else:
        ydl_opts.update({
            'format': 'best[ext=mp4]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best',
            'merge_output_format': 'mp4'
        })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'video')
            safe_title = re.sub(r'[\\/*?:"<>|]', '_', title)[:80].strip()

        found_file = None
        for fname in os.listdir(DOWNLOAD_DIR):
            if fname.startswith(f'media_{unique_id}'):
                found_file = os.path.join(DOWNLOAD_DIR, fname)
                break

        if found_file and os.path.exists(found_file):
            ext = os.path.splitext(found_file)[1]
            return send_file(
                found_file, 
                as_attachment=True, 
                download_name=f"{safe_title}{ext}"
            )
        else:
            return "İndirilen dosya bulunamadı.", 500

    except Exception as e:
        print(f"Direct download error: {e}")
        return f"İndirme sırasında hata oluştu: {str(e)}", 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5500))
    print(f"Sunucu başlatıldı: http://localhost:{port} (FFmpeg: {FFMPEG_DIR})")
    app.run(host='0.0.0.0', port=port, debug=False)
