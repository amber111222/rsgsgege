"""Volna catalogue server. Deezer metadata and standard YouTube video playback.

No audio extraction, media proxying, account cookies or subscription bypass.
"""
import concurrent.futures
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent / 'public'
ALLOWED_ORIGINS = set(filter(None, os.getenv('ALLOWED_ORIGINS', 'https://volna-music-free-261028.mossycrumb514.chatgpt.site').split(',')))
CACHE = {}
LOCK = threading.Lock()
RESOLVER_SLOTS = threading.BoundedSemaphore(2)
GENRES = {'Electronic': 106, 'Hip-Hop/Rap': 116, 'Pop': 132, 'Jazz': 129, 'Alternative': 85, 'Ambient': 98}


def cached(key, ttl, loader):
    with LOCK:
        item = CACHE.get(key)
        if item and item[0] > time.monotonic():
            return item[1]
    result = loader()
    with LOCK:
        if len(CACHE) > 1000:
            CACHE.clear()
        CACHE[key] = (time.monotonic() + ttl, result)
    return result


def deezer(path, **params):
    url = 'https://api.deezer.com/' + path + ('?' + urllib.parse.urlencode(params) if params else '')
    def load():
        request = urllib.request.Request(url, headers={'User-Agent': 'Volna/4.0', 'Accept': 'application/json'})
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.load(response)
        if data.get('error'):
            raise ValueError('Запись не найдена в Deezer')
        return data
    return cached(url, 600, load)


def number(value):
    match = re.fullmatch(r'(?:dz-)?([1-9][0-9]{0,14})', str(value))
    if not match:
        raise ValueError('Некорректный ID каталога')
    return match[1]


def artist_record(a):
    return {'id': 'dz-' + number(a['id']), 'name': a.get('name', ''), 'handle': '',
            'is_verified': False, 'source': 'deezer', 'bio': '', 'track_count': 0,
            'album_count': a.get('nb_album', 0), 'follower_count': a.get('nb_fan', 0),
            'profile_picture': {'480x480': a.get('picture_big') or a.get('picture_medium', ''),
                                '150x150': a.get('picture_medium') or a.get('picture', '')},
            'link': a.get('link', '')}


def track_record(t):
    a = t.get('artist') or {}
    if not a.get('id'):
        return None
    album = t.get('album') or {}
    return {'id': 'dz-' + number(t['id']), 'title': t.get('title', ''), 'source': 'deezer',
            'user': artist_record(a), 'duration': t.get('duration', 0), 'genre': 'Deezer',
            'artwork': {'480x480': album.get('cover_big') or album.get('cover_medium', '')},
            'permalink': t.get('link', ''), 'album': album.get('title', ''),
            'is_streamable': True, 'is_stream_gated': False,
            'playback_provider': 'youtube', 'playback_requires_match': True}


def tracks(data):
    return [row for t in data.get('data', []) if (row := track_record(t))]


def artist_catalogue(artist_id, limit, offset):
    """Enumerate the artist's real albums; quoted Cyrillic artist searches return no data."""
    ident = number(artist_id)
    rows = {}
    def include(items, album=None):
        for item in items:
            if str((item.get('artist') or {}).get('id')) != ident:
                continue
            if album and not item.get('album'):
                item = dict(item, album=album)
            rows.setdefault(str(item['id']), item)
    include(deezer('artist/' + ident + '/top', limit=100).get('data', []))
    wanted = offset + limit
    album_offset = 0
    exhausted = False
    while len(rows) <= wanted and album_offset < 1000:
        albums = deezer('artist/' + ident + '/albums', limit=100, index=album_offset)
        batch = albums.get('data', [])
        for start in range(0, len(batch), 4):
            group = batch[start:start + 4]
            def load(album):
                data = deezer('album/' + number(album['id']) + '/tracks', limit=100)
                return album, data.get('data', [])
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                for album, items in pool.map(load, group):
                    include(items, album)
            if len(rows) > wanted:
                break
        if len(rows) > wanted:
            break
        if not albums.get('next'):
            exhausted = True
            break
        album_offset += 100
    page = list(rows.values())[offset:offset + limit]
    return {'data': tracks({'data': page}), 'has_more': len(rows) > wanted or not exhausted}


def resolve_video(track_id):
    ident = number(track_id)
    def resolve():
        import yt_dlp
        t = deezer('track/' + ident)
        query = (t['artist']['name'] + ' ' + t['title'] + ' official audio')[:240]
        api_key = os.getenv('YOUTUBE_API_KEY', '')
        if api_key:
            url = 'https://www.googleapis.com/youtube/v3/search?' + urllib.parse.urlencode({
                'key': api_key, 'part': 'snippet', 'type': 'video', 'videoEmbeddable': 'true',
                'maxResults': 5, 'q': query})
            with urllib.request.urlopen(url, timeout=15) as response:
                result = json.load(response)
            for item in result.get('items', []):
                video_id = item.get('id', {}).get('videoId', '')
                if re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
                    return {'videoId': video_id, 'title': item['snippet']['title'],
                            'channel': item['snippet']['channelTitle'], 'provider': 'youtube',
                            'watchUrl': 'https://www.youtube.com/watch?v=' + video_id,
                            'match': 'search', 'adsMayAppear': True}
            raise ValueError('Запись не найдена на YouTube')
        if not RESOLVER_SLOTS.acquire(timeout=1):
            raise RuntimeError('Поиск занят. Попробуй через несколько секунд.')
        try:
            with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True, 'extract_flat': True,
                                  'skip_download': True, 'noplaylist': True,
                                  'socket_timeout': 12, 'retries': 1}) as ydl:
                data = ydl.extract_info('ytsearch5:' + query, download=False)
            entries = [e for e in data.get('entries', []) if e and re.fullmatch(r'[A-Za-z0-9_-]{11}', e.get('id', ''))]
            if not entries:
                raise ValueError('Запись не найдена на YouTube')
            def score(e):
                name = (e.get('title') or '').casefold()
                channel = (e.get('channel') or e.get('uploader') or '').casefold()
                duration = e.get('duration') or t.get('duration', 0)
                return (40 * ('topic' in channel) + 20 * ('official' in name)
                        - min(abs(duration - t.get('duration', duration)), 200)
                        - 80 * any(x in name for x in ('cover', 'karaoke', 'reaction', 'nightcore', 'sped up', 'live')))
            best = max(entries, key=score)
            return {'videoId': best['id'], 'title': best.get('title', ''),
                    'channel': best.get('channel') or best.get('uploader', ''),
                    'duration': best.get('duration'), 'watchUrl': 'https://www.youtube.com/watch?v=' + best['id'],
                    'provider': 'youtube', 'match': 'search', 'adsMayAppear': True}
        finally:
            RESOLVER_SLOTS.release()
    return cached('yt:' + ident, 3600, resolve)


def route(path, query):
    limit = max(1, min(int(query.get('limit', ['50'])[0]), 100))
    offset = max(0, min(int(query.get('offset', ['0'])[0]), 5000))
    term = query.get('query', [''])[0].strip()[:200]
    if path == '/health':
        return {'status': 'ok', 'catalog': 'deezer', 'playback': 'youtube-embed', 'version': 4}
    if path == '/api/v1/tracks/search':
        result = deezer('search', q=term, limit=limit, index=offset) if term else {'data': []}
        return {'data': tracks(result), 'has_more': bool(result.get('next'))}
    if path == '/api/v1/tracks/trending':
        genre = GENRES.get(query.get('genre', [''])[0], 0)
        result = deezer('chart/' + str(genre) + '/tracks', limit=limit, index=offset)
        return {'data': tracks(result), 'has_more': bool(result.get('next'))}
    if path == '/api/v1/users/search':
        data = deezer('search/artist', q=term, limit=min(limit, 12), index=offset)
        return {'data': [artist_record(a) for a in data.get('data', [])]}
    match = re.fullmatch(r'/api/v1/tracks/(dz-\d+)(/stream)?', path)
    if match:
        return resolve_video(match[1]) if match[2] else {'data': track_record(deezer('track/' + number(match[1])))}
    match = re.fullmatch(r'/api/v1/users/(dz-\d+)(/tracks)?', path)
    if match:
        a = deezer('artist/' + number(match[1]))
        if not match[2]:
            return {'data': artist_record(a)}
        return artist_catalogue(a['id'], limit, offset)
    raise LookupError('Неизвестный маршрут')


class Handler(BaseHTTPRequestHandler):
    server_version = 'Volna/4'

    def cors(self):
        origin = self.headers.get('Origin', '')
        if origin in ALLOWED_ORIGINS:
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        if not (parsed.path.startswith('/api/') or parsed.path == '/health'):
            return self.serve_static(parsed.path)
        try:
            result = route(parsed.path, urllib.parse.parse_qs(parsed.query))
            self.json_response(200, result)
        except LookupError as error:
            self.json_response(404, {'error': str(error)})
        except (ValueError, TypeError):
            self.json_response(400, {'error': 'Неверный запрос или запись не найдена'})
        except Exception:
            # Never disclose signed URLs, subprocess output or upstream credentials.
            self.json_response(502, {'error': 'Музыкальный источник временно недоступен. Попробуй позже.'})

    def json_response(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.cors()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_static(self, name):
        import mimetypes
        target = (ROOT / urllib.parse.unquote(name).lstrip('/')).resolve()
        if not target.is_relative_to(ROOT) or not target.is_file():
            if name == '/':
                target = ROOT / 'index.html'
            else:
                self.send_error(404)
                return
        if not target.is_file():
            self.send_error(404)
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', mimetypes.guess_type(target)[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    server = ThreadingHTTPServer((os.getenv('HOST', '0.0.0.0'), int(os.getenv('PORT', '4175'))), Handler)
    print('Volna server ready', flush=True)
    server.serve_forever()
