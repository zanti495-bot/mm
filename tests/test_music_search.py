import unittest
from unittest.mock import MagicMock, patch

import yt_dlp

from music_bot.music import search_track


class MusicSearchTests(unittest.TestCase):
    def test_youtube_error_falls_back_to_soundcloud(self) -> None:
        ydl = MagicMock()
        ydl.__enter__.return_value = ydl
        ydl.extract_info.side_effect = [
            yt_dlp.utils.DownloadError("YouTube blocked the request"),
            {
                "entries": [
                    {
                        "title": "Song",
                        "artist": "Artist",
                        "duration": 180,
                        "webpage_url": "https://soundcloud.com/artist/song",
                    }
                ]
            },
        ]

        with patch("music_bot.music.yt_dlp.YoutubeDL", return_value=ydl):
            track = search_track("Song Artist")

        self.assertEqual(track.url, "https://soundcloud.com/artist/song")
        self.assertEqual(
            [call.args[0] for call in ydl.extract_info.call_args_list],
            ["ytsearch1:Song Artist", "scsearch1:Song Artist"],
        )


if __name__ == "__main__":
    unittest.main()
