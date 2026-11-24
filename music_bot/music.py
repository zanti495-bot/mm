import difflib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp

logger = logging.getLogger(__name__)
SEARCH_PREFIXES = ("ytsearch1:", "scsearch1:")


@dataclass(frozen=True)
class Track:
    title: str
    artist: str | None
    duration: float | None
    url: str


class TrackNotFoundError(Exception):
    pass


class AudioFileNotCreatedError(Exception):
    pass


def _ydl_options(**options: object) -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 2,
        **options,
    }


def search_track(query: str) -> Track:
    with yt_dlp.YoutubeDL(_ydl_options()) as ydl:
        parsed_url = urlparse(query)
        if parsed_url.scheme in {"http", "https"} and parsed_url.netloc:
            result = ydl.extract_info(query, download=False)
            entries = result.get("entries") if result else None
            entry = next((item for item in entries or [] if item), result)
            if not entry:
                raise TrackNotFoundError
            return _track_from_entry(entry, query)

        candidates: list[Track] = []
        errors: list[yt_dlp.utils.DownloadError] = []
        for prefix in SEARCH_PREFIXES:
            try:
                result = ydl.extract_info(f"{prefix}{query}", download=False)
            except yt_dlp.utils.DownloadError as error:
                logger.warning("Search failed for %s: %s", prefix, error)
                errors.append(error)
                continue

            entries = result.get("entries") if result else None
            entry = next((item for item in entries or [] if item), None)
            if entry:
                candidates.append(_track_from_entry(entry, query))

    if candidates:
        normalized_query = _normalize(query)
        return max(
            candidates,
            key=lambda track: difflib.SequenceMatcher(
                None,
                normalized_query,
                _normalize(f"{track.title} {track.artist or ''}"),
            ).ratio(),
        )
    if errors:
        raise errors[-1]
    raise TrackNotFoundError


def _track_from_entry(entry: dict, query: str) -> Track:
    url = entry.get("webpage_url") or entry.get("original_url")
    if not url:
        raise TrackNotFoundError

    duration = entry.get("duration")
    if not isinstance(duration, (int, float)):
        duration = None

    artist = (
        entry.get("artist")
        or entry.get("creator")
        or entry.get("uploader")
        or entry.get("channel")
    )
    return Track(
        title=entry.get("title") or query,
        artist=artist,
        duration=duration,
        url=url,
    )


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9а-яё]+", " ", value.casefold()).strip()


def download_track(track: Track, directory: str) -> Path:
    options = _ydl_options(
        format="bestaudio/best",
        outtmpl=str(Path(directory) / "%(id)s.%(ext)s"),
        postprocessors=[
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "0",
            }
        ],
    )
    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([track.url])

    audio_files = list(Path(directory).glob("*.mp3"))
    if len(audio_files) != 1:
        raise AudioFileNotCreatedError
    return audio_files[0]
