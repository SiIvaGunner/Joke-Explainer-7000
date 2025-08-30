import unittest
from unittest.mock import patch
import json
import sys
import os
from pathlib import Path
from inspect import getsourcefile

from simpleQoC.metadata import checkMetadata

TEST_DIR = Path(os.path.abspath(getsourcefile(lambda:0))).parent
DEBUG_MODE = False

class TestMetadata(unittest.TestCase):
    """
    Test suits for checkMetadata function
    """
    CHANNEL_NAME = "SiIvaGunner"

    def __init__(self, *args, **kwargs):
        super(TestMetadata, self).__init__(*args, **kwargs)

        with open(TEST_DIR / 'smb2jp.json', 'r', encoding='utf-8') as file:
            self.SMB2JP = json.load(file)
        with open(TEST_DIR / 'smbas.json', 'r', encoding='utf-8') as file:
            self.SMBAS = json.load(file)
        with open(TEST_DIR / 'ssbu.json', 'r', encoding='utf-8') as file:
            self.SSBU = json.load(file)


    def base_test(self, mock_details, mock_videos, details_ret, videos_ret, description, playlist, expected_msgs, advanced = True):
        mock_details.return_value = details_ret
        mock_videos.return_value = videos_ret

        code, msgs = checkMetadata(description, self.CHANNEL_NAME, playlist, None, advanced)
        for msg in msgs:
            if DEBUG_MODE: print(msg)
        
        self.assertEqual(code, len(expected_msgs) > 0)
        self.assertEqual(len(msgs), len(expected_msgs))
        for m in expected_msgs:
            self.assertTrue(m in msgs)


    @patch('simpleQoC.metadata.get_playlist_videos')
    @patch('simpleQoC.metadata.get_playlist_details')
    def test_metadata_ok(self, mock_details, mock_videos):
        self.base_test(
            mock_details, mock_videos,
            ("Super Mario Bros. 2 (JP)", self.CHANNEL_NAME), self.SMB2JP,
            "Castle (Beta Mix) - Super Mario Bros. 2 (JP)\n\nMusic: Castle (Beta Mix) \nComposer: Koji Kondo  \nPlaylist: https://www.youtube.com/playlist?list=PLL0CQjrcN8D1eRlsClwtcJM1V1xtZbU1S\nPlatform: Famicom Disk System\n\nPlease read the c",
            "PLL0CQjrcN8D1eRlsClwtcJM1V1xtZbU1S",
            []
        )


    @patch('simpleQoC.metadata.get_playlist_videos')
    @patch('simpleQoC.metadata.get_playlist_details')
    def test_metadata_issue(self, mock_details, mock_videos):
        self.base_test(
            mock_details, mock_videos,
            ("Super \u2588\u2588\u2588\u2588\u2588 3D All Stars", self.CHANNEL_NAME), self.SMBAS,
            "S\u258864 Super \u2588\u2588\u2588\u2588\u2588 64 Main Theme - Super \u2588\u2588\u2588\u2588\u2588 4D All Stars\nMusic:  S\u258864 Super \u2588\u2588\u2588\u2588\u2588 64 Main Theme (JP Version)\nComposed by: Koji Kondo\n\nArrangement: my cuh\nPlatlist: https://www.youtube.com/playlist?list=PLL0CQjrcN8D0RpfnKPuj8anigmMCnbJpF\ndummy line’s — lol\nPlatforms:Playstation 2, Xbox Series X/S",
            "PLL0CQjrcN8D0RpfnKPuj8anigmMCnbJpF",
            [
                "Double space detected in description.",
                "There is no **s** in Platform.",
                "Typographic apostrophe (``’``) detected. It is recommended that you use typewriter apostrophe(``'``) for consistency.",
                "Em dash (``—``) detected. It is recommended that you use hyphen (``-``) for consistency.",
                "The **S** in PlayStation should be capitalized.",
                "We commonly use Xbox Series X|S.",
                "Reboot metadata found. Change to regular metadata if this was not intentional.",
                "Missing space in ``Platforms`` line.",
                "Irregular line without : found in description: \"dummy l...s — lol\"",
                "``Arrangement`` field not present in any existing videos in playlist.",
                "``Platlist`` field not present in any existing videos in playlist.",
                "Title format does not match existing videos in playlist, or Music line is incorrect (e.g. missing mixname).",
            ]
        )


    @patch('simpleQoC.metadata.get_playlist_videos')
    @patch('simpleQoC.metadata.get_playlist_details')
    def test_metadata_issue_v2(self, mock_details, mock_videos):
        self.base_test(
            mock_details, mock_videos,
            ("Super \u2588\u2588\u2588\u2588\u2588 3D All Stars", self.CHANNEL_NAME), self.SMBAS,
            "Super \u2588\u2588\u2588\u2588\u2588 4D All Stars Music S\u258864 Super \u2588\u2588\u2588\u2588\u2588 64 Main Theme (JP Version)\n\nMusic: S\u258864 Super \u2588\u2588\u2588\u2588\u2588 64 Main Theme (JP Version)\nComposer: Koji Kondo\nPlaylist: https://www.youtube.com/playlist?list=PLL0CQjrcN8D0RpfnKPuj8anigmMCnbJpF\nPlatform: Nintendo Switch\r\n\r\nPlease read the channel description.",
            "PLL0CQjrcN8D0RpfnKPuj8anigmMCnbJpF",
            [
                "Game in title does not match playlist name (``Super \u2588\u2588\u2588\u2588\u2588 3D All Stars``) nor any existing videos in playlist.",
            ]
        )


    @patch('simpleQoC.metadata.get_playlist_videos')
    @patch('simpleQoC.metadata.get_playlist_details')
    def test_metadata_issue_v3(self, mock_details, mock_videos):
        self.base_test(
            mock_details, mock_videos,
            ("Super \u2588\u2588\u2588\u2588\u2588 3D All Stars", self.CHANNEL_NAME), self.SMBAS,
            "S\u258864 Super \u2588\u2588\u2588\u2588\u2588 64 Main Theme - Super \u2588\u2588\u2588\u2588\u2588 4D All Stars\nMusic:  S\u258864 Super \u2588\u2588\u2588\u2588\u2588 64 Main Theme (JP Version)\nComposed by: Koji Kondo\n\nArrangement: my cuh\nPlatlist: https://www.youtube.com/playlist?list=PLL0CQjrcN8D0RpfnKPuj8anigmMCnbJpF\ndummy line’s — lol\nPlatforms:Playstation 2, Xbox Series X/S",
            "PLL0CQjrcN8D0RpfnKPuj8anigmMCnbJpF",
            [
                "Double space detected in description.",
                "There is no **s** in Platform.",
                "Typographic apostrophe (``’``) detected. It is recommended that you use typewriter apostrophe(``'``) for consistency.",
                "Em dash (``—``) detected. It is recommended that you use hyphen (``-``) for consistency.",
                "The **S** in PlayStation should be capitalized.",
                "We commonly use Xbox Series X|S.",
            ],
            False
        )

    
    @patch('simpleQoC.metadata.get_playlist_videos')
    @patch('simpleQoC.metadata.get_playlist_details')
    def test_metadata_no_playlist(self, mock_details, mock_videos):
        self.base_test(
            mock_details, mock_videos,
            ("", ""), [],
            "Track - New Game\nMusic: Track\nPlaylist:\nPlatform: PC",
            "",
            []
        )
    

    @patch('simpleQoC.metadata.get_playlist_videos')
    @patch('simpleQoC.metadata.get_playlist_details')
    def test_metadata_announcement(self, mock_details, mock_videos):
        self.base_test(
            mock_details, mock_videos,
            ("Announcement", "not siivagunner"), self.SSBU,
            "A Decrypted Announcement [Installer Insanity Out Now!]\n\nInstaller Insanity features arranges from the classic works of keygen music. SGFR has scavenged through plenty of tunes from the depths and has given them a new twist - in celebration of demoscene music! Remember... SGFR does not condone piracy. We only condone music.\n\nGet it now for free: http://sgfr.highquality.rip/sgfr-0009/\nAlbum playlist: https://www.youtube.com/playlist?list=PLL0CQjrcN8D1-lL5iRUT2CNttzP8KxeP9",
            "PLL0CQjrcN8D1-lL5iRUT2CNttzP8KxeP9",
            []
        )


if __name__ == '__main__': 
    if len(sys.argv) > 1:
        print('DEBUG MODE ENABLED')
        DEBUG_MODE = True

    unittest.main()
