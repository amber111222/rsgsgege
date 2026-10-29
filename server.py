"""Volna catalogue server. Deezer metadata and standard YouTube video playback.

No audio extraction, media proxying, account cookies or subscription bypass.
"""
import concurrent.futures
import html
import json
import os
import re
import threading
import time
import unicodedata
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
CYRILLIC_LATIN = str.maketrans(dict(zip(
    'абвгдеёжзийклмнопрстуфхцчшщъыьэюяіїє',
    ['a', 'b', 'v', 'g', 'd', 'e', 'e', 'zh', 'z', 'i', 'i', 'k', 'l', 'm', 'n', 'o',
     'p', 'r', 's', 't', 'u', 'f', 'kh', 'ts', 'ch', 'sh', 'shch', '', 'y', '', 'e', 'yu', 'ya', 'i', 'i', 'e'])))
SPOKEN_WORDS = {'interview', 'interviews', 'intervyu', 'interviu', 'podcast', 'podcasts', 'podkast', 'podkasty'}


def words(value):
    """Comparable whole words for catalogue Cyrillic and Latin video metadata."""
    text = html.unescape(str(value or '')).casefold().translate(CYRILLIC_LATIN)
    text = ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))
    return re.findall(r'[a-z0-9]+', text)


def is_spoken_title(title):
    return bool(set(words(title)) & SPOKEN_WORDS)


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
    if not a.get('id') or is_spoken_title(t.get('title', '')):
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
            if is_spoken_title(item.get('title', '')):
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


def title_words(value):
    # Collaborators and mastering labels vary between the album and video title.
    text = html.unescape(str(value or ''))
    text = re.sub(r'\([^)]*(?:feat\.?|ft\.?|with\b|remaster)[^)]*\)', '', text, flags=re.I)
    text = re.split(r'\s+(?:feat\.?|ft\.?)\s+', text, maxsplit=1, flags=re.I)[0]
    text = re.sub(r'\s*[-–—]\s*(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?\s*$', '', text, flags=re.I)
    return set(words(text))


def video_candidates(track, entries):
    """Accept metadata matches, rather than silently playing the closest search hit.

    Search snippets are not proof that a video is the requested song. Require the
    primary artist, song words and a plausible duration; never accept interviews
    even if they happen to mention the artist and song. No media is downloaded.
    """
    expected_title = title_words(track.get('title'))
    expected_artist = set(words((track.get('artist') or {}).get('name'))) - {'and'}
    expected_duration = track.get('duration') or 0
    if not expected_title or not expected_artist or not isinstance(expected_duration, (int, float)) or expected_duration <= 0:
        return []
    tolerance = min(45, max(12, expected_duration * .15))
    variants = {'cover', 'karaoke', 'reaction', 'reaktsiya', 'nightcore', 'sped', 'slowed', 'live', 'remix', 'instrumental', 'akustika', 'acoustic'}
    expected_variants = set(words(track.get('title')))
    matches = []
    for entry in entries:
        video_id = entry.get('id', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
            continue
        title = entry.get('title') or ''
        channel = entry.get('channel') or entry.get('uploader') or ''
        title_tokens = set(words(title))
        channel_tokens = set(words(channel))
        duration = entry.get('duration')
        if (is_spoken_title(title) or (title_tokens & variants) - expected_variants
                or entry.get('is_live') or entry.get('live_status') in {'is_live', 'is_upcoming'}
                or entry.get('availability') in {'private', 'premium_only', 'subscriber_only', 'needs_auth'}):
            continue
        if not expected_title.issubset(title_tokens) or not expected_artist.issubset(title_tokens | channel_tokens):
            continue
        if not isinstance(duration, (int, float)) or duration <= 0 or abs(duration - expected_duration) > tolerance:
            continue
        score = (25 * ('topic' in channel_tokens) + 10 * expected_artist.issubset(channel_tokens)
                 + 5 * ('official' in title_tokens) - abs(duration - expected_duration))
        matches.append((score, {'videoId': video_id, 'title': title, 'channel': channel,
                                'duration': duration, 'watchUrl': 'https://www.youtube.com/watch?v=' + video_id}))
    ranked = []
    seen = set()
    for _, candidate in sorted(matches, key=lambda item: item[0], reverse=True):
        if candidate['videoId'] not in seen:
            ranked.append(candidate)
            seen.add(candidate['videoId'])
        if len(ranked) == 3:
            break
    return ranked


def video_result(track, entries):
    candidates = video_candidates(track, entries)
    if not candidates:
        raise LookupError('Не удалось надёжно найти музыкальную запись. Выбери видео вручную или открой поиск YouTube.')
    return dict(candidates[0], candidates=candidates, provider='youtube', match='metadata', adsMayAppear=True)


def iso_duration(value):
    match = re.fullmatch(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?', str(value))
    return sum(float(n or 0) * unit for n, unit in zip(match.groups(), (3600, 60, 1))) if match else 0


def resolve_video(track_id):
    ident = number(track_id)
    def resolve():
        t = deezer('track/' + ident)
        if is_spoken_title(t.get('title', '')):
            raise LookupError('Эта запись относится к интервью или подкасту. Выбери музыкальный трек.')
        query = (t['artist']['name'] + ' ' + t['title'] + ' official audio')[:240]
        api_key = os.getenv('YOUTUBE_API_KEY', '')
        if api_key:
            url = 'https://www.googleapis.com/youtube/v3/search?' + urllib.parse.urlencode({
                'key': api_key, 'part': 'snippet', 'type': 'video', 'videoEmbeddable': 'true',
                'maxResults': 5, 'q': query})
            with urllib.request.urlopen(url, timeout=15) as response:
                result = json.load(response)
            ids = [item.get('id', {}).get('videoId', '') for item in result.get('items', [])]
            ids = list(dict.fromkeys(ident for ident in ids if re.fullmatch(r'[A-Za-z0-9_-]{11}', ident)))
            if not ids:
                return video_result(t, [])
            details_url = 'https://www.googleapis.com/youtube/v3/videos?' + urllib.parse.urlencode({
                'key': api_key, 'part': 'snippet,contentDetails,status', 'id': ','.join(ids)})
            with urllib.request.urlopen(details_url, timeout=15) as response:
                details = json.load(response)
            entries = [{'id': item.get('id', ''), 'title': item.get('snippet', {}).get('title', ''),
                        'channel': item.get('snippet', {}).get('channelTitle', ''),
                        'duration': iso_duration(item.get('contentDetails', {}).get('duration', ''))}
                       for item in details.get('items', [])
                       if item.get('status', {}).get('embeddable') is True
                       and item.get('status', {}).get('privacyStatus') == 'public'
                       and item.get('snippet', {}).get('liveBroadcastContent', 'none') == 'none']
            return video_result(t, entries)
        if not RESOLVER_SLOTS.acquire(timeout=1):
            raise RuntimeError('Поиск занят. Попробуй через несколько секунд.')
        try:
            import yt_dlp
            with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True, 'extract_flat': True,
                                  'skip_download': True, 'noplaylist': True,
                                  'socket_timeout': 12, 'retries': 1}) as ydl:
                data = ydl.extract_info('ytsearch5:' + query, download=False)
            entries = [e for e in data.get('entries', []) if e and re.fullmatch(r'[A-Za-z0-9_-]{11}', e.get('id', ''))]
            return video_result(t, entries)
        finally:
            RESOLVER_SLOTS.release()
    return cached('yt:matched-v2:' + ident, 3600, resolve)


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
