
from flask import Flask, Response, jsonify, request
import requests
import re
import threading
import math
import os
import time
from urllib.parse import urljoin

app = Flask(__name__)

# ==========================================================
# INSTELLINGEN
# ==========================================================

SOURCE_URL = "https://streams.pinguinradio.com/PinguinRadio320.mp3"

PORT = 5002

# Laat leeg om automatisch het adres uit het HTTP-verzoek
# te gebruiken. Je kunt hier eventueel een vast adres invullen:
# PUBLIC_BASE_URL = "http://192.168.1.35:5001"
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

# Wacht na een bronmetadata-wijziging voordat de nieuwe track wordt getoond.
TRACK_METADATA_DELAY_SECONDS = max(
    0.0,
    float(os.environ.get("TRACK_METADATA_DELAY_SECONDS", "10"))
)

ITUNES_URL = "https://itunes.apple.com/search"

USER_AGENT = "PinguinRadioProxy/2.0"

# ==========================================================
# GEDEELDE STATUS
# ==========================================================

current_track = "Pinguin Indie"
current_cover_version = 0

track_lock = threading.RLock()

# Albumhoezen en gebeurtenissen per trackversie.
cover_cache = {}
cover_events = {}

# ==========================================================
# TRACKINFORMATIE
# ==========================================================

def get_current_state():
    with track_lock:
        return current_track, current_cover_version


def split_artist_track(title):
    """
    Probeert artiest en titel uit StreamTitle te halen.
    Verwacht bijvoorbeeld: Radiohead - Karma Police.
    """

    if " - " in title:
        artist, track = title.split(" - ", 1)
        return artist.strip(), track.strip()

    return "", title.strip()


def set_current_track(title):
    """
    Werkt de huidige track bij.
    Bij een nieuwe track wordt een nieuwe coverzoekactie gestart.
    """

    global current_track, current_cover_version

    title = title.strip()

    with track_lock:
        if not title or title == current_track:
            return current_cover_version, False

        current_track = title
        current_cover_version += 1

        version = current_cover_version
        cover_events[version] = threading.Event()

        print("Nieuwe track:", title)

    start_cover_search(title, version)

    return version, True


# ==========================================================
# ICY-METADATA UITLEZEN
# ==========================================================

def parse_icy_metadata(data):
    """
    Leest StreamTitle uit het ICY-metadata-blok.
    """

    text = data.rstrip(b"\0").decode(
        "utf-8",
        errors="replace"
    )

    match = re.search(
        r"StreamTitle='(.*?)';",
        text,
        re.DOTALL | re.IGNORECASE
    )

    if match:
        return match.group(1).strip()

    return None


# ==========================================================
# STREAM READER
# ==========================================================

class StreamReader:
    """
    Leest exact het gevraagde aantal bytes uit de HTTP-stream.
    """

    def __init__(self, response):
        self.iterator = iter(
            response.iter_content(chunk_size=8192)
        )
        self.buffer = bytearray()

    def read(self, amount):
        while len(self.buffer) < amount:
            try:
                chunk = next(self.iterator)

                if chunk:
                    self.buffer.extend(chunk)

            except StopIteration:
                break

        result = bytes(self.buffer[:amount])
        del self.buffer[:amount]

        return result


def source_audio_segments(response, source_metaint):
    """
    Scheidt de audiobytes en ICY-metadata van de bronstream.
    """

    reader = StreamReader(response)

    while True:

        audio = reader.read(source_metaint)

        if not audio:
            break

        yield "audio", audio

        if len(audio) < source_metaint:
            break

        length_byte = reader.read(1)

        if len(length_byte) != 1:
            break

        metadata_length = length_byte[0] * 16

        metadata = b""

        if metadata_length:
            metadata = reader.read(metadata_length)

            if len(metadata) < metadata_length:
                break

        title = (
            parse_icy_metadata(metadata)
            if metadata
            else None
        )

        yield "metadata", title


# ==========================================================
# ALBUMHOEZEN OPZOEKEN VIA ITUNES
# ==========================================================

def start_cover_search(title, version):
    """
    Zoekt op de achtergrond naar een albumhoes.
    Hierdoor wordt de audiostream niet opgehouden door de zoekactie.
    """

    thread = threading.Thread(
        target=lookup_cover,
        args=(title, version),
        daemon=True
    )

    thread.start()


def lookup_cover(title, version):
    """
    Zoekt de track op in de Apple iTunes Search API.
    Downloadt vervolgens de gevonden albumhoes.
    """

    try:

        artist, track = split_artist_track(title)

        query = f"{artist} {track}".strip()

        print("Zoeken naar albumhoes:", query)

        result = requests.get(
            ITUNES_URL,
            params={
                "term": query,
                "entity": "song",
                "limit": 5,
                "country": "nl"
            },
            headers={
                "User-Agent": USER_AGENT
            },
            timeout=(4, 8)
        )

        result.raise_for_status()

        results = result.json().get("results", [])

        if not results:
            print("Geen albumhoes gevonden:", title)
            return

        # iTunes sorteert de zoekresultaten op relevantie.
        artwork_url = results[0].get("artworkUrl100")

        if not artwork_url:
            print("Geen artwork-URL gevonden:", title)
            return

        # Probeer een grotere afbeelding op te halen.
        artwork_url = artwork_url.replace(
            "100x100bb",
            "600x600bb"
        )

        print("Albumhoes gevonden:", artwork_url)

        image_response = requests.get(
            artwork_url,
            headers={
                "User-Agent": USER_AGENT
            },
            timeout=(4, 10)
        )

        image_response.raise_for_status()

        content_type = image_response.headers.get(
            "Content-Type",
            "image/jpeg"
        )

        if not content_type.lower().startswith("image/"):
            print("Ongeldig afbeeldingstype:", content_type)
            return

        image_data = image_response.content

        if not image_data:
            return

        with track_lock:
            cover_cache[version] = (
                image_data,
                content_type
            )

        print("Albumhoes opgeslagen voor:", title)

    except requests.RequestException as error:

        print("Fout bij ophalen albumhoes:", error)

    except Exception as error:

        print("Onverwachte fout bij albumhoes:", error)

    finally:

        with track_lock:
            event = cover_events.get(version)

            if event:
                event.set()


# ==========================================================
# ICY-METADATA MAKEN
# ==========================================================

def make_icy_metadata(title, cover_url=None):
    """
    Maakt een ICY-metadata-blok met StreamTitle en StreamUrl.
    """

    safe_title = title.replace("'", "’")

    metadata_text = (
        f"StreamTitle='{safe_title}';"
    )

    if cover_url:
        metadata_text += (
            f"StreamUrl='{cover_url}';"
        )

    metadata = metadata_text.encode("utf-8")

    # Maximaal 255 blokken van 16 bytes.
    metadata = metadata[:4080]

    blocks = math.ceil(len(metadata) / 16)

    padding = blocks * 16 - len(metadata)

    return (
        bytes([blocks])
        + metadata
        + (b"\0" * padding)
    )


# ==========================================================
# COVER-URL
# ==========================================================

def get_public_base_url():
    """
    Bepaalt het adres waarop de WiiM de cover kan ophalen.
    """

    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL

    return request.host_url.rstrip("/")


def get_cover_url(base_url, version):
    return (
        f"{base_url}/cover.jpg?v={version}"
    )


# ==========================================================
# STREAM NAAR DE WIIM
# ==========================================================

def update_and_stream(
    upstream,
    source_metaint,
    send_icy,
    base_url
):
    """
    Stuurt audio ongewijzigd door.
    Voegt op iedere ICY-grens de actuele metadata toe.
    """

    pending_title = None
    publish_title_at = None

    for event_type, data in source_audio_segments(
        upstream,
        source_metaint
    ):

        if event_type == "audio":

            if (
                pending_title is not None
                and time.monotonic() >= publish_title_at
            ):
                set_current_track(pending_title)
                pending_title = None
                publish_title_at = None

            yield data

        elif event_type == "metadata":

            if data:
                pending_title = data
                publish_title_at = (
                    time.monotonic()
                    + TRACK_METADATA_DELAY_SECONDS
                )

            title, version = get_current_state()

            if send_icy:

                cover_url = get_cover_url(
                    base_url,
                    version
                )

                yield make_icy_metadata(
                    title,
                    cover_url
                )


# ==========================================================
# WEBPAGINA
# ==========================================================

@app.route("/",methods=['GET'])
def index():

    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Pinguin Radio Proxy</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {
                font-family: Arial, sans-serif;
                max-width: 600px;
                margin: 40px auto;
                padding: 20px;
                background: #151515;
                color: white;
            }
            a { color: #80caff; }
            img {
                width: 240px;
                max-width: 100%;
                border-radius: 12px;
            }
        </style>
    </head>
    <body>
        <h1>Pinguin Radio Proxy</h1>
        <p>Status: actief</p>
        <p><a href="/radio">Luister naar de stream</a></p>
        <p><a href="/metadata">Metadata</a></p>
        <p><a href="/api/nowplaying">Now Playing API</a></p>
        <p><a href="/cover.jpg">Huidige albumhoes</a></p>
        <div id="nowplaying">Metadata laden...</div>

        <script>
        async function update() {
            try {
                const response = await fetch('/api/nowplaying');
                const data = await response.json();

                document.getElementById('nowplaying').innerHTML =
                    '<h2>' + data.title + '</h2>' +
                    '<img src="' + data.cover_url + '" alt="Albumhoes">';
            } catch (e) {
                document.getElementById('nowplaying').innerText =
                    'Metadata niet beschikbaar';
            }
        }

        update();
        setInterval(update, 10000);
        </script>
    </body>
    </html>
    """


# ==========================================================
# METADATA API
# ==========================================================

@app.route("/metadata",methods=['GET'])
def metadata():

    title, version = get_current_state()

    return jsonify({
        "station": "Pinguin Indie",
        "title": title,
        "cover_version": version
    })


@app.route("/api/nowplaying",methods=['GET'])
def nowplaying():

    title, version = get_current_state()

    artist, track = split_artist_track(title)

    base_url = get_public_base_url()

    return jsonify({
        "station": "Pinguin Indie",
        "artist": artist,
        "title": track,
        "stream_title": title,
        "cover_url": get_cover_url(base_url, version)
    })


# ==========================================================
# ALBUMHOES ENDPOINT
# ==========================================================

@app.route("/cover.jpg",methods=['GET'])
def cover():

    try:
        version = int(
            request.args.get(
                "v",
                get_current_state()[1]
            )
        )
    except (ValueError, TypeError):
        version = get_current_state()[1]

    with track_lock:
        event = cover_events.get(version)
        image = cover_cache.get(version)

    # Geef de achtergrondzoekactie maximaal 8 seconden.
    if image is None and event is not None:
        event.wait(timeout=8)

        with track_lock:
            image = cover_cache.get(version)

    if image is None:
        return (
            "Albumhoes nog niet beschikbaar",
            404
        )

    image_data, content_type = image

    response = Response(
        image_data,
        content_type=content_type
    )

    response.headers["Cache-Control"] = "public, max-age=3600"

    return response


# ==========================================================
# RADIO STREAM ENDPOINT
# ==========================================================

@app.route("/radio", methods=['GET'])
def radio():

    try:

        upstream = requests.get(
            SOURCE_URL,
            headers={
                "Icy-MetaData": "1",
                "User-Agent": USER_AGENT,
                "Accept-Encoding": "identity"
            },
            stream=True,
            timeout=(10, 60)
        )

        upstream.raise_for_status()

    except requests.RequestException as error:

        return (
            f"Kan Pinguin Radio niet bereiken: {error}",
            502
        )

    source_metaint = int(
        upstream.headers.get("icy-metaint", "0") or 0
    )

    send_icy = (
        request.headers.get("Icy-MetaData", "0") == "1"
    )

    base_url = get_public_base_url()

    print("----------------------------------")
    print("Pinguin Radio Proxy")
    print("Bronstatus:", upstream.status_code)
    print("Bron ICY-meta-interval:", source_metaint)
    print("WiiM vraagt metadata:", send_icy)
    print("WiiM-adres:", request.remote_addr)
    print("----------------------------------")

    if source_metaint > 0:

        body = update_and_stream(
            upstream,
            source_metaint,
            send_icy,
            base_url
        )

    else:

        print("WAARSCHUWING: bron levert geen ICY-meta-interval")

        body = upstream.iter_content(
            chunk_size=8192
        )

    response = Response(
        body,
        content_type="audio/mpeg"
    )

    response.headers["Cache-Control"] = "no-cache, no-store"
    response.headers["Connection"] = "close"
    response.headers["icy-name"] = "Pinguin Indie"
    response.headers["icy-description"] = "Pinguin Indie Radio"
    response.headers["icy-url"] = "https://pinguinradio.com"
    response.headers["icy-pub"] = "0"

    if send_icy and source_metaint > 0:

        response.headers["icy-metaint"] = str(
            source_metaint
        )

    response.call_on_close(upstream.close)

    return response


# ==========================================================
# START
# ==========================================================

if __name__ == "__main__":

    print("")
    print("====================================")
    print("     PINGUIN RADIO PROXY 2.0")
    print("====================================")
    print("")
    print(f"Stream:   http://localhost:{PORT}/radio")
    print(f"Metadata: http://localhost:{PORT}/metadata")
    print(f"Now playing: http://localhost:{PORT}/api/nowplaying")
    print(f"Cover:    http://localhost:{PORT}/cover.jpg")
    print("")

    app.run(
        host="0.0.0.0",
        port=PORT,
        threaded=True,
        debug=False
    )
