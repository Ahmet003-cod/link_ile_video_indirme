import os
import re
import time
import tempfile
import uuid
import urllib.request
import urllib.error
import urllib.parse
import json
import subprocess
import shutil
import yt_dlp
import imageio_ffmpeg
from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='.')
CORS(app)

DOWNLOAD_DIR = os.path.join(tempfile.gettempdir(), 'link_video_downloads')
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Locate and ensure standard ffmpeg binary exists across Windows and Linux
FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
FFMPEG_DIR = os.path.dirname(FFMPEG_EXE)
ffmpeg_binary_name = 'ffmpeg.exe' if os.name == 'nt' else 'ffmpeg'
STANDARD_FFMPEG = os.path.join(FFMPEG_DIR, ffmpeg_binary_name)
if not os.path.exists(STANDARD_FFMPEG):
    try:
        shutil.copyfile(FFMPEG_EXE, STANDARD_FFMPEG)
        if os.name != 'nt':
            os.chmod(STANDARD_FFMPEG, 0o755)
    except Exception as e:
        print(f"Copy ffmpeg error: {e}")

print(f"FFmpeg yolu: {FFMPEG_EXE}")

COOKIE_COMPATIBLE_CLIENTS = ['web_embedded', 'tv_downgraded', 'tv', 'mweb', 'web']
YTDLP_CLIENT_SETS = [
    ['visionos', 'tv_downgraded', 'web_embedded', 'tv'],
    ['ios', 'mweb', 'web_safari', 'web'],
]
PYTUBEFIX_CLIENTS = ['VISION_OS', 'MWEB']
PIPED_INSTANCES = [
    'https://pipedapi.kavin.rocks',
    'https://pipedapi.adminforge.de',
    'https://api.piped.private.coffee',
    'https://pipedapi.tokhmi.xyz',
    'https://pipedapi.syncpundit.io',
]
INVIDIOUS_INSTANCES = [
    'https://yt.chocolatemoo53.com',
    'https://inv.nadeko.net',
    'https://invidious.nerdvpn.de',
    'https://yewtu.be',
    'https://invidious.tiekoetter.com',
]
HTTP_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
)


def cleanup_old_downloads(max_age_seconds=600):
    """Delete temporary downloaded files older than max_age_seconds."""
    now = time.time()
    try:
        for fname in os.listdir(DOWNLOAD_DIR):
            fpath = os.path.join(DOWNLOAD_DIR, fname)
            if os.path.isfile(fpath) and (now - os.path.getmtime(fpath)) > max_age_seconds:
                try:
                    os.remove(fpath)
                except OSError:
                    pass
    except OSError:
        pass


def extract_youtube_id(url):
    """Extract the 11-character YouTube video ID from common YouTube URL formats."""
    regex = r'(?:youtube\.com\/(?:watch\?v=|shorts\/|embed\/|live\/)|youtu\.be\/)([a-zA-Z0-9_-]{11})'
    match = re.search(regex, url)
    return match.group(1) if match else None


def sanitize_title(title):
    """Return a filesystem-safe download filename stem."""
    cleaned = re.sub(r'[\\/*?:"<>|]', '_', title or 'video')[:80].strip()
    return cleaned or 'video'


def get_node_exe():
    """Locate Node.js executable from nodejs_wheel or system PATH."""
    try:
        import nodejs_wheel.executable as nwe
        bin_dir = nwe.ROOT_DIR if os.name == 'nt' else os.path.join(nwe.ROOT_DIR, 'bin')
        node_name = 'node.exe' if os.name == 'nt' else 'node'
        candidate = os.path.join(bin_dir, node_name)
        if os.path.exists(candidate):
            return candidate
    except Exception:
        pass
    return shutil.which('node')


def get_cookies_file():
    """Return path to cookies file from YOUTUBE_COOKIES env var or local cookies.txt."""
    cookies_content = os.environ.get('YOUTUBE_COOKIES', '').strip()
    if cookies_content:
        c_path = os.path.join(DOWNLOAD_DIR, 'youtube_cookies.txt')
        try:
            if not cookies_content.startswith('# Netscape'):
                cookies_content = '# Netscape HTTP Cookie File\n' + cookies_content
            with open(c_path, 'w', encoding='utf-8') as f:
                f.write(cookies_content)
            return c_path
        except Exception as e:
            print(f"Cookies yazma hatası: {e}")
    if os.path.exists('cookies.txt'):
        return 'cookies.txt'
    return None


def get_base_ydl_opts(player_clients=None, use_cookies=False):
    """Build base yt-dlp options without hardcoding deprecated clients."""
    node_exe = get_node_exe()
    js_runtimes = {'node': {'path': node_exe}} if node_exe else {'node': {}}

    opts = {
        'quiet': True,
        'no_warnings': True,
        'ffmpeg_location': FFMPEG_EXE,
        'source_address': '0.0.0.0',
        'js_runtimes': js_runtimes,
        'geo_bypass': True,
        'socket_timeout': 10,
        'retries': 1,
        'extractor_retries': 1,
    }

    if player_clients:
        opts['extractor_args'] = {
            'youtube': {
                'player_client': player_clients,
            }
        }

    if use_cookies:
        cookies_file = get_cookies_file()
        if cookies_file and os.path.exists(cookies_file):
            opts['cookiefile'] = cookies_file

    return opts


def _remove_file_silently(path):
    """Best-effort temporary file deletion."""
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def _find_downloaded_file(prefix):
    """Find a downloaded file in DOWNLOAD_DIR matching the given filename prefix."""
    for fname in os.listdir(DOWNLOAD_DIR):
        if fname.startswith(prefix):
            fpath = os.path.join(DOWNLOAD_DIR, fname)
            if os.path.isfile(fpath) and os.path.getsize(fpath) > 0:
                return fpath
    return None


def _convert_to_mp3(input_path, output_path):
    """Convert an audio/video file to 192kbps MP3 using FFmpeg."""
    cmd = [
        FFMPEG_EXE, '-y', '-i', input_path,
        '-vn', '-acodec', 'libmp3lame', '-ab', '192k', output_path,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)


def _mux_video_audio_mp4(video_path, audio_path, output_path):
    """Mux separate video and audio streams into an MP4 container using FFmpeg."""
    cmd = [
        FFMPEG_EXE, '-y', '-i', video_path, '-i', audio_path,
        '-c:v', 'copy', '-c:a', 'aac', '-movflags', '+faststart', output_path,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)


def _download_stream_to_file(stream_url, dest_path, timeout=25):
    """Stream a remote media URL directly to disk and reject HTML/empty responses."""
    req = urllib.request.Request(stream_url, headers={'User-Agent': HTTP_USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content_type = (resp.headers.get('Content-Type') or '').lower()
        if 'text/html' in content_type or 'application/json' in content_type:
            raise ValueError(f"Beklenmeyen içerik türü: {content_type}")

        first_chunk = resp.read(65536)
        if not first_chunk:
            raise ValueError("Boş medya yanıtı.")
        stripped_head = first_chunk.lstrip()[:15].lower()
        if stripped_head.startswith(b'<!doctype') or stripped_head.startswith(b'<html'):
            raise ValueError("Medya yerine HTML hata sayfası döndü.")

        with open(dest_path, 'wb') as out_f:
            out_f.write(first_chunk)
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                out_f.write(chunk)

    if os.path.getsize(dest_path) < 1024:
        _remove_file_silently(dest_path)
        raise ValueError("İndirilen dosya çok küçük veya bozuk.")


def _fetch_json(url, timeout=10):
    """Fetch and decode JSON from an external API endpoint."""
    req = urllib.request.Request(url, headers={'User-Agent': HTTP_USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _download_via_ytdlp(url, format_id, unique_id, is_youtube):
    """Stage 1: Attempt download using yt-dlp with YouTube client rotation."""
    output_template = os.path.join(DOWNLOAD_DIR, f'media_{unique_id}.%(ext)s')

    if is_youtube:
        attempts = [(clients, False) for clients in YTDLP_CLIENT_SETS]
        if get_cookies_file():
            attempts.append((COOKIE_COMPATIBLE_CLIENTS, True))
    else:
        attempts = [(None, False)]

    last_error = None
    for clients, use_cookies in attempts:
        # Clean partial files from any prior failed attempt
        for fname in os.listdir(DOWNLOAD_DIR):
            if fname.startswith(f'media_{unique_id}'):
                _remove_file_silently(os.path.join(DOWNLOAD_DIR, fname))

        ydl_opts = get_base_ydl_opts(player_clients=clients, use_cookies=use_cookies)
        ydl_opts['outtmpl'] = output_template

        if format_id == 'audio_mp3':
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            })
        else:
            ydl_opts.update({
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bestvideo+bestaudio/best',
                'merge_output_format': 'mp4',
            })

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                title = info.get('title', 'video')

            found_file = _find_downloaded_file(f'media_{unique_id}')
            if found_file:
                return found_file, sanitize_title(title)
            last_error = RuntimeError("İndirilen dosya bulunamadı.")
        except Exception as e:
            last_error = e
            print(f"yt-dlp attempt failed (clients={clients}, cookies={use_cookies}): {e}")

    raise last_error or RuntimeError("yt-dlp indirme başarısız.")


def _download_via_pytubefix(url, format_id, unique_id):
    """Stage 2: Fallback to pytubefix across multiple Innertube clients."""
    from pytubefix import YouTube

    last_error = None
    for client_name in PYTUBEFIX_CLIENTS:
        raw_files = []
        try:
            yt = YouTube(url, client=client_name)
            safe_title = sanitize_title(yt.title)

            if format_id == 'audio_mp3':
                audio_stream = (
                    yt.streams.filter(only_audio=True).order_by('abr').desc().first()
                    or yt.streams.get_audio_only()
                )
                if not audio_stream:
                    raise RuntimeError("Uygun ses akışı bulunamadı.")

                raw_name = f"raw_{unique_id}_audio.{audio_stream.subtype or 'm4a'}"
                raw_path = audio_stream.download(
                    output_path=DOWNLOAD_DIR, filename=raw_name, skip_existing=False, timeout=60
                )
                raw_files.append(raw_path)
                out_path = os.path.join(DOWNLOAD_DIR, f"media_{unique_id}.mp3")
                _convert_to_mp3(raw_path, out_path)
                return out_path, safe_title

            # Video mode: prefer HD progressive stream; mux adaptive HD if progressive is low-res
            prog_stream = (
                yt.streams.filter(progressive=True, file_extension='mp4')
                .order_by('resolution').desc().first()
            )
            prog_height = int(re.sub(r'\D', '', prog_stream.resolution or '0') or 0) if prog_stream else 0

            if prog_stream and prog_height >= 720:
                out_path = prog_stream.download(
                    output_path=DOWNLOAD_DIR, filename=f"media_{unique_id}.mp4",
                    skip_existing=False, timeout=60
                )
                return out_path, safe_title

            vid_stream = (
                yt.streams.filter(adaptive=True, only_video=True, file_extension='mp4')
                .order_by('resolution').desc().first()
            )
            aud_stream = (
                yt.streams.filter(only_audio=True).order_by('abr').desc().first()
                or yt.streams.get_audio_only()
            )

            if vid_stream and aud_stream:
                try:
                    raw_v = vid_stream.download(
                        output_path=DOWNLOAD_DIR,
                        filename=f"raw_{unique_id}_v.{vid_stream.subtype or 'mp4'}",
                        skip_existing=False, timeout=60
                    )
                    raw_files.append(raw_v)
                    raw_a = aud_stream.download(
                        output_path=DOWNLOAD_DIR,
                        filename=f"raw_{unique_id}_a.{aud_stream.subtype or 'm4a'}",
                        skip_existing=False, timeout=60
                    )
                    raw_files.append(raw_a)
                    out_path = os.path.join(DOWNLOAD_DIR, f"media_{unique_id}.mp4")
                    _mux_video_audio_mp4(raw_v, raw_a, out_path)
                    return out_path, safe_title
                except Exception as adaptive_err:
                    print(f"pytubefix adaptive failed ({client_name}): {adaptive_err}")
                    for rf in raw_files:
                        _remove_file_silently(rf)
                    raw_files.clear()

            if prog_stream:
                out_path = prog_stream.download(
                    output_path=DOWNLOAD_DIR, filename=f"media_{unique_id}.mp4",
                    skip_existing=False, timeout=60
                )
                return out_path, safe_title

            raise RuntimeError("Uygun video akışı bulunamadı.")
        except Exception as e:
            last_error = e
            print(f"pytubefix attempt failed (client={client_name}): {e}")
        finally:
            for rf in raw_files:
                _remove_file_silently(rf)

    raise last_error or RuntimeError("pytubefix indirme başarısız.")


def _download_via_piped(yt_id, format_id, unique_id):
    """Try downloading a proxied stream through public Piped API instances."""
    last_error = None
    for base_url in PIPED_INSTANCES:
        raw_path = None
        try:
            data = _fetch_json(f"{base_url}/streams/{yt_id}", timeout=10)
            safe_title = sanitize_title(data.get('title', 'video'))

            if format_id == 'audio_mp3':
                audio_streams = sorted(
                    [s for s in data.get('audioStreams', []) if s.get('url')],
                    key=lambda s: s.get('bitrate', 0),
                    reverse=True,
                )
                if not audio_streams:
                    raise ValueError("Piped ses akışı bulunamadı.")
                raw_path = os.path.join(DOWNLOAD_DIR, f"raw_{unique_id}_piped.m4a")
                _download_stream_to_file(audio_streams[0]['url'], raw_path, timeout=60)
                out_path = os.path.join(DOWNLOAD_DIR, f"media_{unique_id}.mp3")
                _convert_to_mp3(raw_path, out_path)
                return out_path, safe_title

            muxed_streams = sorted(
                [
                    s for s in data.get('videoStreams', [])
                    if s.get('url') and not s.get('videoOnly')
                    and 'mp4' in (s.get('mimeType') or '').lower()
                ],
                key=lambda s: s.get('height') or 0,
                reverse=True,
            )
            if muxed_streams:
                out_path = os.path.join(DOWNLOAD_DIR, f"media_{unique_id}.mp4")
                _download_stream_to_file(muxed_streams[0]['url'], out_path, timeout=60)
                return out_path, safe_title

            raise ValueError("Piped birleştirilmiş MP4 akışı bulunamadı.")
        except Exception as e:
            last_error = e
            print(f"Piped attempt failed ({base_url}): {e}")
        finally:
            _remove_file_silently(raw_path)

    raise last_error or RuntimeError("Piped indirme başarısız.")


def _get_invidious_instances():
    """Return known healthy Invidious instances plus dynamic ones from api.invidious.io."""
    seen = list(INVIDIOUS_INSTANCES)
    try:
        data = _fetch_json('https://api.invidious.io/instances.json?sort_by=health', timeout=6)
        if isinstance(data, list):
            for _, info in data:
                if isinstance(info, dict) and info.get('type') == 'https' and info.get('api'):
                    uri = (info.get('uri') or '').rstrip('/')
                    if uri and uri not in seen:
                        seen.append(uri)
    except Exception:
        pass
    return seen


def _normalize_invidious_url(stream_url, base_url):
    """Ensure an Invidious stream URL is proxied through base_url."""
    if not stream_url:
        return ''
    if stream_url.startswith('/'):
        stream_url = f"{base_url}{stream_url}"
    elif 'googlevideo.com' in stream_url:
        parsed_base = urllib.parse.urlparse(base_url)
        parsed_stream = urllib.parse.urlparse(stream_url)
        stream_url = urllib.parse.urlunparse((
            parsed_base.scheme,
            parsed_base.netloc,
            parsed_stream.path,
            parsed_stream.params,
            parsed_stream.query,
            parsed_stream.fragment,
        ))
    if 'local=' not in stream_url:
        sep = '&' if '?' in stream_url else '?'
        stream_url = f"{stream_url}{sep}local=true"
    return stream_url


def _pick_invidious_audio_url(adaptive_formats, base_url, yt_id):
    """Select the best proxied audio stream URL from Invidious adaptiveFormats."""
    by_itag = {str(f.get('itag')): f for f in adaptive_formats if f.get('url')}
    for itag in ('140', '139', '251', '250', '249'):
        if itag in by_itag:
            return _normalize_invidious_url(by_itag[itag]['url'], base_url)
    for f in adaptive_formats:
        if f.get('url') and 'audio/' in (f.get('type') or '').lower():
            return _normalize_invidious_url(f['url'], base_url)
    return f"{base_url}/latest_version?id={yt_id}&itag=140&local=true"


def _pick_invidious_video_url(adaptive_formats, base_url):
    """Select the best H.264 MP4 video stream URL from Invidious adaptiveFormats."""
    h264_by_itag = {
        str(f.get('itag')): f
        for f in adaptive_formats
        if f.get('url')
        and 'video/mp4' in (f.get('type') or '').lower()
        and 'avc1' in (f.get('type') or '').lower()
    }
    for itag in ('136', '137', '135', '134', '133', '160'):
        if itag in h264_by_itag:
            return _normalize_invidious_url(h264_by_itag[itag]['url'], base_url)
    return None


def _download_via_invidious(yt_id, format_id, unique_id):
    """Try downloading a proxied stream through public Invidious instances."""
    last_error = None
    for base_url in _get_invidious_instances():
        raw_files = []
        try:
            meta = {}
            try:
                meta = _fetch_json(f"{base_url}/api/v1/videos/{yt_id}?local=true", timeout=8)
            except Exception:
                pass
            safe_title = sanitize_title(meta.get('title', 'video'))
            adaptive_formats = meta.get('adaptiveFormats') or []
            format_streams = meta.get('formatStreams') or []

            if format_id == 'audio_mp3':
                audio_url = _pick_invidious_audio_url(adaptive_formats, base_url, yt_id)
                raw_a = os.path.join(DOWNLOAD_DIR, f"raw_{unique_id}_inv.m4a")
                raw_files.append(raw_a)
                _download_stream_to_file(audio_url, raw_a, timeout=25)
                out_path = os.path.join(DOWNLOAD_DIR, f"media_{unique_id}.mp3")
                _convert_to_mp3(raw_a, out_path)
                return out_path, safe_title

            # Video 1: Try adaptive HD H.264 video + audio muxing (works for Shorts & HD videos)
            video_url = _pick_invidious_video_url(adaptive_formats, base_url)
            if video_url:
                try:
                    audio_url = _pick_invidious_audio_url(adaptive_formats, base_url, yt_id)
                    raw_v = os.path.join(DOWNLOAD_DIR, f"raw_{unique_id}_inv_v.mp4")
                    raw_a = os.path.join(DOWNLOAD_DIR, f"raw_{unique_id}_inv_a.m4a")
                    raw_files.extend([raw_v, raw_a])
                    _download_stream_to_file(video_url, raw_v, timeout=30)
                    _download_stream_to_file(audio_url, raw_a, timeout=25)
                    out_path = os.path.join(DOWNLOAD_DIR, f"media_{unique_id}.mp4")
                    _mux_video_audio_mp4(raw_v, raw_a, out_path)
                    return out_path, safe_title
                except Exception as mux_err:
                    print(f"Invidious adaptive mux failed ({base_url}): {mux_err}")
                    for rf in raw_files:
                        _remove_file_silently(rf)
                    raw_files.clear()

            # Video 2: Fallback to progressive formatStreams or itag=18
            prog_candidates = [
                _normalize_invidious_url(fs.get('url'), base_url)
                for fs in format_streams if fs.get('url')
            ]
            prog_candidates.append(f"{base_url}/latest_version?id={yt_id}&itag=18&local=true")

            out_path = os.path.join(DOWNLOAD_DIR, f"media_{unique_id}.mp4")
            for prog_url in prog_candidates:
                try:
                    _download_stream_to_file(prog_url, out_path, timeout=25)
                    return out_path, safe_title
                except Exception:
                    _remove_file_silently(out_path)

            raise ValueError("Invidious video akışı alınamadı.")
        except Exception as e:
            last_error = e
            print(f"Invidious attempt failed ({base_url}): {e}")
        finally:
            for rf in raw_files:
                _remove_file_silently(rf)

    raise last_error or RuntimeError("Invidious indirme başarısız.")


def _download_via_proxy_fallback(yt_id, format_id, unique_id):
    """Stage 3: Download via Invidious or Piped when datacenter IP is blocked."""
    errors = []
    for fn in (_download_via_invidious, _download_via_piped):
        try:
            return fn(yt_id, format_id, unique_id)
        except Exception as e:
            errors.append(str(e))
    raise RuntimeError(" | ".join(errors))


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

    # 1. Fetch YouTube metadata via oEmbed
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

    # 2. For non-youtube links (or oEmbed fallback), use yt-dlp
    ydl_opts = get_base_ydl_opts(
        player_clients=YTDLP_CLIENT_SETS[0] if yt_id else None,
        use_cookies=False,
    )
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

    cleanup_old_downloads()

    unique_id = str(uuid.uuid4())[:8]
    yt_id = extract_youtube_id(url)
    errors = []

    if yt_id:
        is_cloud_without_cookies = bool(os.environ.get('RENDER')) and not get_cookies_file()
        if is_cloud_without_cookies:
            stages = [
                ('proxy', lambda: _download_via_proxy_fallback(yt_id, format_id, unique_id)),
                ('yt-dlp', lambda: _download_via_ytdlp(url, format_id, unique_id, True)),
                ('pytubefix', lambda: _download_via_pytubefix(url, format_id, unique_id)),
            ]
        else:
            stages = [
                ('yt-dlp', lambda: _download_via_ytdlp(url, format_id, unique_id, True)),
                ('pytubefix', lambda: _download_via_pytubefix(url, format_id, unique_id)),
                ('proxy', lambda: _download_via_proxy_fallback(yt_id, format_id, unique_id)),
            ]
    else:
        stages = [
            ('yt-dlp', lambda: _download_via_ytdlp(url, format_id, unique_id, False)),
        ]

    for stage_name, stage_fn in stages:
        try:
            found_file, safe_title = stage_fn()
            ext = os.path.splitext(found_file)[1]
            return send_file(found_file, as_attachment=True, download_name=f"{safe_title}{ext}")
        except Exception as e:
            errors.append(f"{stage_name}: {e}")

    error_summary = " | ".join(errors)
    print(f"All download stages failed: {error_summary}")
    return f"İndirme sırasında hata oluştu: {error_summary}", 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5500))
    print(f"Sunucu başlatıldı: http://localhost:{port} (FFmpeg: {FFMPEG_DIR})")
    app.run(host='0.0.0.0', port=port, debug=False)
