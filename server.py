import os
import re
import tempfile
import uuid
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

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'skip_download': True,
        'ffmpeg_location': FFMPEG_DIR
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            title = info.get('title', 'Video')
            thumbnail = info.get('thumbnail', '')
            duration = info.get('duration', 0)
            uploader = info.get('uploader', info.get('channel', ''))
            description = info.get('description', '')
            if description and len(description) > 150:
                description = description[:150] + '...'

            # Format list
            formats_raw = info.get('formats', [])
            available_qualities = []
            seen_heights = set()

            for f in formats_raw:
                height = f.get('height')
                vcodec = f.get('vcodec', 'none')
                if height and vcodec != 'none' and height not in seen_heights:
                    seen_heights.add(height)
                    filesize = f.get('filesize') or f.get('filesize_approx') or 0
                    filesize_str = f"{round(filesize / (1024*1024), 1)} MB" if filesize else ""
                    available_qualities.append({
                        'format_id': str(f.get('format_id')),
                        'resolution': f"{height}p Video",
                        'height': height,
                        'ext': 'mp4',
                        'size': filesize_str,
                        'type': 'video'
                    })

            # Sort descending
            available_qualities.sort(key=lambda x: x['height'], reverse=True)

            # Best Quality option
            quality_options = [{
                'format_id': 'best_video',
                'resolution': '🎬 En Yüksek Kalite Video (HD/4K)',
                'height': 9999,
                'ext': 'mp4',
                'size': '',
                'type': 'video'
            }] + available_qualities

            # Audio MP3 option
            quality_options.append({
                'format_id': 'audio_mp3',
                'resolution': '🎵 Sadece Müzik / Ses (MP3)',
                'height': 0,
                'ext': 'mp3',
                'size': '',
                'type': 'audio'
            })

            return jsonify({
                'success': True,
                'type': 'social_video',
                'title': title,
                'thumbnail': thumbnail,
                'duration': duration,
                'uploader': uploader,
                'description': description,
                'qualities': quality_options,
                'original_url': url
            })
    except Exception as e:
        error_msg = str(e)
        return jsonify({
            'success': False,
            'error': f'Video bilgisi alınamadı: {error_msg}'
        }), 400

@app.route('/api/download', methods=['GET'])
def download_media():
    url = request.args.get('url', '').strip()
    format_id = request.args.get('format', 'best_video')

    if not url:
        return "URL parametresi eksik", 400

    unique_id = str(uuid.uuid4())[:8]
    output_template = os.path.join(DOWNLOAD_DIR, f'media_{unique_id}.%(ext)s')

    ydl_opts = {
        'outtmpl': output_template,
        'quiet': True,
        'no_warnings': True,
        'ffmpeg_location': FFMPEG_DIR
    }

    # Audio only (MP3)
    if format_id == 'audio_mp3':
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        })
    # Best Video (with audio merged)
    elif format_id == 'best_video':
        ydl_opts.update({
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best',
            'merge_output_format': 'mp4'
        })
    # Specific video format id (merged with best audio)
    else:
        ydl_opts.update({
            'format': f'{format_id}+bestaudio[ext=m4a]/{format_id}+bestaudio/best',
            'merge_output_format': 'mp4'
        })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'video')
            safe_title = re.sub(r'[\\/*?:"<>|]', '_', title)[:80].strip()

        # Find the downloaded file
        found_file = None
        for fname in os.listdir(DOWNLOAD_DIR):
            if fname.startswith(f'media_{unique_id}'):
                found_file = os.path.join(DOWNLOAD_DIR, fname)
                break

        if not found_file or not os.path.exists(found_file):
            return "İndirilen dosya bulunamadı", 500

        ext = os.path.splitext(found_file)[1]
        download_name = f"{safe_title}{ext}"

        return send_file(
            found_file,
            as_attachment=True,
            download_name=download_name
        )

    except Exception as e:
        return f"İndirme sırasında hata oluştu: {str(e)}", 500

if __name__ == '__main__':
    print(f"Sunucu başlatıldı: http://localhost:5500 (FFmpeg: {FFMPEG_DIR})")
    app.run(host='0.0.0.0', port=5500, debug=False)
