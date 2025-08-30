import unittest
import os
from pathlib import Path
from inspect import getsourcefile
from mutagen import File

from simpleQoC.qoc import parseUrl, downloadAudioFromUrl, checkBitrateFromFile, checkClippingFromFile, \
                    checkBitrateFromUrl, checkClippingFromUrl, QoCException, DOWNLOAD_DIR

TEST_DIR = Path(os.path.abspath(getsourcefile(lambda:0))).parent

TEST_URLS = {
    'almostClipping.mp3': "https://drive.google.com/file/d/1wI6ZGaTmWpYD07kG8lK8jZ-XIBL-Xm3e/view?usp=drive_link",
    'clipping1.mp3': "https://drive.google.com/file/d/159dl-ot3nee6dSE3geaE_e8acpQ0sbCM/view?usp=drive_link",
    'clipping2.mp3': "https://drive.google.com/file/d/1pDDCBzYbdjQYjLLgEAiXZ-0B6ABKqNXP/view?usp=drive_link",
    'clipping2inverted.wav': "https://drive.google.com/file/d/1Vi9CadpCogR-2JfhmVLsrAXVohYlRhm6/view?usp=drive_link",
    'clipping3.wav': "https://drive.google.com/file/d/1IYrnLtZciOZDoISlMBEVBjk-L9kWNOGM/view?usp=drive_link",
    'clipping4.wav': "https://drive.google.com/file/d/1Gb_vDqn4vM38hPfFqnpVakp5lXLLNcl0/view?usp=drive_link",
    'clipping5.ogg': "https://drive.google.com/file/d/17_8MPvNYP5Ky21wtCww7X2WUKs1gd1_P/view?usp=drive_link",
    'clipping6lowBitrate.mp4': "https://drive.google.com/file/d/1Af5Y8mgS92J89gdYBZeoQLQwnxcQ4W7n/view?usp=drive_link",
    'clipping16bit.flac': "https://drive.google.com/file/d/13S5B4z7GZ7gACe6eo7z7oOBX2cLbmGNk/view?usp=drive_link",
    'clipping24bit.flac': "https://drive.google.com/file/d/1WUIsi_Rl9gyeJUangq6wwe9sx1S08jMj/view?usp=drive_link",
    'clipping24bit.wav': "https://drive.google.com/file/d/1q2TNMA4d3BMQvtrrtx3ed2DbU66kR0dq/view?usp=drive_link",
    'clipping32bitfloat.wav': "https://drive.google.com/file/d/1Old48cHuVGglh6VZ_7XsJCTJxNnSwR0Q/view?usp=drive_link",
    'goodQuality.aiff': "https://drive.google.com/file/d/1tC2cr9klMVmUVGLWClIlRJfctuLFCqEc/view?usp=drive_link",
    'goodQuality.flac': "https://drive.google.com/file/d/1_k0I7mYstvXwMPQMf8CKlo8D5hdAiqkV/view?usp=drive_link",
    'goodQuality.mp2': "https://drive.google.com/file/d/1o139UD9e2sONqbS_WGMbuipnFrrDP6cq/view?usp=drive_link",
    'goodQuality.mp3': "https://drive.google.com/file/d/1ofQMUh1xtItM3a1VXATRovYL9nYVYets/view?usp=drive_link",
    'goodQuality.mp4': "https://drive.google.com/file/d/1-gGaLjie2lDfQVNMJOSuM1IfuythDr8i/view?usp=drive_link",
    'goodQuality.ogg': "https://drive.google.com/file/d/1bDrs-HE-OI9OoZEKD6CjNuK0K5mswNz-/view?usp=drive_link",
    'goodQuality.wav': "https://drive.google.com/file/d/1wF-YR1agyAmJASR5moE2PzjdLGY2XovV/view?usp=drive_link",
    'goodQuality.wmv': "https://drive.google.com/file/d/1J7keq03mIDVZ-LyOUIwucFMcINQBwZqi/view?usp=drive_link",
    'goodQualityMono.wav': "https://drive.google.com/file/d/1577f0dfDalMAlRe402u4v1tHol_dMwYt/view?usp=drive_link",
    'lowBitrate.m4a': "https://drive.google.com/file/d/1Uei3_qiWgglRXj-z2nfPPkc_aY9oH4VV/view?usp=drive_link",
    'lowBitrate.mp3': "https://drive.google.com/file/d/1LSDF5AY2JcS5_QjsHmWBKWCuABXxySOd/view?usp=drive_link",
    'lowBitrate.ogg': "https://drive.google.com/file/d/18IC3fwYRIH8Iwf_lFmTp_lKIx7vpGcHK/view?usp=drive_link",
}

#=======================================#
#           URL DOWNLOADING             #
#=======================================#

class TestDownload(unittest.TestCase):
    """
    Test suites for the downloadAudioFromUrl function
    """

    def __init__(self, *args, **kwargs):
        super(TestDownload, self).__init__(*args, **kwargs)
        if not os.path.exists(DOWNLOAD_DIR):
            os.mkdir(DOWNLOAD_DIR)

    def parseAndDownload(self, url):
        return downloadAudioFromUrl(parseUrl(url))

    # Successful downloads
    def testSuccessSiivaGunner(self):
        filepath = self.parseAndDownload("https://siiva-gunner.com/?id=vnrufKKnxu")
        self.assertEqual(filepath, DOWNLOAD_DIR / 'goodQuality.mp3')
        if filepath:
            os.remove(filepath)

    def testSuccessSiivaGunnerV2(self):
        filepath = self.parseAndDownload("https://11.22.33.44/?id=vnrufKKnxu")
        self.assertEqual(filepath, DOWNLOAD_DIR / 'goodQuality.mp3')
        if filepath:
            os.remove(filepath)

    def testSuccessSiivaGunnerV3(self):
        filepath = self.parseAndDownload("https://siiva-gunner.com/?id=aWHZuQtx3P")
        self.assertEqual(filepath, DOWNLOAD_DIR / 'video0.mp4')
        if filepath:
            os.remove(filepath)

    def testSuccessDrive(self):
        filepath = self.parseAndDownload("https://drive.google.com/file/d/1ofQMUh1xtItM3a1VXATRovYL9nYVYets/view?usp=sharing")
        self.assertEqual(filepath, DOWNLOAD_DIR / 'goodQuality.mp3')
        if filepath:
            os.remove(filepath)

    def testSuccessDriveV2(self):
        filepath = self.parseAndDownload("https://drive.google.com/file/d/1ofQMUh1xtItM3a1VXATRovYL9nYVYets")
        self.assertEqual(filepath, DOWNLOAD_DIR / 'goodQuality.mp3')
        if filepath:
            os.remove(filepath)

    def testSuccessDriveV3(self):
        filepath = self.parseAndDownload("https://drive.google.com/open?id=1ofQMUh1xtItM3a1VXATRovYL9nYVYets")
        self.assertEqual(filepath, DOWNLOAD_DIR / 'goodQuality.mp3')
        if filepath:
            os.remove(filepath)

    def testSuccessDriveV4(self):
        filepath = self.parseAndDownload("https://drive.google.com/uc?id=1ofQMUh1xtItM3a1VXATRovYL9nYVYets&export=download")
        self.assertEqual(filepath, DOWNLOAD_DIR / 'goodQuality.mp3')
        if filepath:
            os.remove(filepath)

    def testSuccessDropbox(self):
        filepath = self.parseAndDownload("https://www.dropbox.com/scl/fi/stkgm8qtw6m9oq5dbeg76/goodQuality.mp3?rlkey=wxhi0wu55a4d4tsppe6buu5by&st=vi8io7zw&dl=0")
        self.assertEqual(filepath, DOWNLOAD_DIR / 'goodQuality.mp3')
        if filepath:
            os.remove(filepath)

    # def testSuccessNeocities(self):
    #     filepath = self.parseAndDownload("https://livvy94.neocities.org/rips/IoG_Fanfare.mp3")
    #     self.assertEqual(filepath, DOWNLOAD_DIR / 'IoG_Fanfare.mp3')
    #     if filepath:
    #         os.remove(filepath)

    def testSuccessCatbox(self):
        filepath = self.parseAndDownload("https://files.catbox.moe/twp0nz.mp3")
        self.assertEqual(filepath, DOWNLOAD_DIR / 'twp0nz.mp3')
        if filepath:
            os.remove(filepath)
    
    # TODO: Try a large Google Drive link (should work)

    # Failed downloads
    def testFailureSiivaGunner(self):
        with self.assertRaises(QoCException):
            filepath = self.parseAndDownload("https://siiva-gunner.com/?id=vnrufKKnx")
            if filepath:
                os.remove(filepath)

    def testFailureDrive(self):
        with self.assertRaises(QoCException):
            filepath = self.parseAndDownload("https://drive.google.com/file/d/1ofQMUh1xtItM3a1VXATRovYL9nYVYet/view?usp=sharing")
            if filepath:
                os.remove(filepath)

#=======================================#
#           BITRATE CHECKING            #
#=======================================#

class BaseTestBitrate:
    """
    Test suites for the checkBitrate functions
    """
    # Helper strings and functions
    BITRATE_OK_MSG = "Bitrate is OK."
    BITRATE_LOSSLESS_MSG = "Lossless file is OK."
    BITRATE_LOW_MSG = "Please re-render at 320kbps."

    def checkBitrate(self, filename: str):
        pass

    # Lossless
    def testBitrateFLAC(self):
        check, msg = self.checkBitrate('goodQuality.flac')
        self.assertTrue(check)
        self.assertEqual(msg, self.BITRATE_LOSSLESS_MSG)

    def testBitrateWAV(self):
        check, msg = self.checkBitrate('goodQuality.wav')
        self.assertTrue(check)
        self.assertEqual(msg, self.BITRATE_LOSSLESS_MSG)

    # Bitrate OK
    def testBitrateAIFF(self):
        check, msg = self.checkBitrate('goodQuality.aiff')
        self.assertTrue(check)
        self.assertEqual(msg, self.BITRATE_OK_MSG)

    def testBitrateMP2(self):
        check, msg = self.checkBitrate('goodQuality.mp2')
        self.assertTrue(check)
        self.assertEqual(msg, self.BITRATE_OK_MSG)

    def testBitrateMP3(self):
        check, msg = self.checkBitrate('goodQuality.mp3')
        self.assertTrue(check)
        self.assertEqual(msg, self.BITRATE_OK_MSG)

    def testLowBitrateMP4(self):
        check, msg = self.checkBitrate('goodQuality.mp4')
        self.assertTrue(check)
        self.assertEqual(msg, self.BITRATE_OK_MSG)

    def testBitrateOGG(self):
        check, msg = self.checkBitrate('goodQuality.ogg')
        self.assertTrue(check)
        self.assertEqual(msg, self.BITRATE_OK_MSG)

    # Low bitrate
    def testLowBitrateMP3(self):
        check, msg = self.checkBitrate('lowBitrate.mp3')
        self.assertFalse(check)
        self.assertIn(self.BITRATE_LOW_MSG, msg)
    
    def testLowBitrateMP4(self):
        check, msg = self.checkBitrate('clipping6lowBitrate.mp4')
        self.assertFalse(check)
        self.assertIn(self.BITRATE_LOW_MSG, msg)
    
    def testLowBitrateM4A(self):
        check, msg = self.checkBitrate('lowBitrate.m4a')
        self.assertFalse(check)
        self.assertIn(self.BITRATE_LOW_MSG, msg)

    def testLowBitrateOGG(self):
        check, msg = self.checkBitrate('lowBitrate.ogg')
        self.assertFalse(check)
        self.assertIn(self.BITRATE_LOW_MSG, msg)

# Use inheritance to reuse the same test functions
class TestBitrateFromFile(unittest.TestCase, BaseTestBitrate):
    def checkBitrate(self, filename: str):
        return checkBitrateFromFile(File(TEST_DIR / filename))

class TestBitrateFromUrl(unittest.TestCase, BaseTestBitrate):
    def checkBitrate(self, filename: str):
        return checkBitrateFromUrl(parseUrl(TEST_URLS[filename]))

#=======================================#
#           CLIPPING CHECKING           #
#=======================================#

class BaseTestClipping:
    """
    Test suites for the checkClipping functions
    """
    # Helper strings and functions
    CLIPPING_OK_MSG = "The rip is not clipping."
    CLIPPING_HEAVY_MSG = "The rip is heavily clipping."
    CLIPPING_REDUCED_MSG = " Post-render volume reduction detected, please lower the volume before rendering."

    def CLIPPING_MSG(self, clips): 
        return "The rip is clipping at: " + ", ".join(clips) + "."
    
    def checkClipping(self, filename: str):
        pass
    
    def clip(self, t, n):
        return '{:.2f} sec ({} samples)'.format(t, n)

    # No clipping
    def testNoClippingMP3(self):
        check, msg = self.checkClipping('goodQuality.mp3')
        self.assertTrue(check)
        self.assertEqual(msg, self.CLIPPING_OK_MSG)
    
    def testNoClippingFLAC(self):
        check, msg = self.checkClipping('goodQuality.flac')
        self.assertTrue(check)
        self.assertEqual(msg, self.CLIPPING_OK_MSG)
    
    def testNoClippingWAV(self):
        check, msg = self.checkClipping('goodQuality.wav')
        self.assertTrue(check)
        self.assertEqual(msg, self.CLIPPING_OK_MSG)

    def testNoClippingWAVMono(self):
        check, msg = self.checkClipping('goodQualityMono.wav')
        self.assertTrue(check)
        self.assertEqual(msg, self.CLIPPING_OK_MSG)

    def testAlmostClippingMP3(self):
        check, msg = self.checkClipping('almostClipping.mp3')
        self.assertTrue(check)
        self.assertEqual(msg, self.CLIPPING_OK_MSG)

    # Clipping
    def testClipping1(self):
        check, msg = self.checkClipping('clipping1.mp3')
        self.assertFalse(check)
        self.assertEqual(msg, self.CLIPPING_HEAVY_MSG)
    
    def testClipping2(self):
        check, msg = self.checkClipping('clipping2.mp3')
        self.assertFalse(check)
        self.assertEqual(msg, self.CLIPPING_MSG([
            self.clip(12.13, 4), 
            self.clip(13.01, 3),
        ]))
    
    def testClipping2inverted(self):
        check, msg = self.checkClipping('clipping2inverted.wav')
        self.assertFalse(check)
        self.assertEqual(msg, self.CLIPPING_MSG([
            self.clip(12.13, 3),
        ]))
    
    def testClipping3(self):
        check, msg = self.checkClipping('clipping3.wav')
        self.assertFalse(check)
        self.assertEqual(msg, self.CLIPPING_HEAVY_MSG + self.CLIPPING_REDUCED_MSG)

    def testClipping4(self):
        check, msg = self.checkClipping('clipping4.wav')
        self.assertFalse(check)
        self.assertEqual(msg, self.CLIPPING_HEAVY_MSG)

    def testClipping5(self):
        check, msg = self.checkClipping('clipping5.ogg')
        self.assertFalse(check)
        self.assertEqual(msg, self.CLIPPING_MSG([
            self.clip(6.23, 4),
            self.clip(7.18, 5),
            self.clip(7.97, 4), 
            self.clip(9.82, 3),
            self.clip(12.04, 5), 
            self.clip(13.01, 3),
        ]))

    def testClipping6(self):
        check, msg = self.checkClipping('clipping6lowBitrate.mp4')
        self.assertFalse(check)
        self.assertEqual(msg, self.CLIPPING_HEAVY_MSG)

    def testClipping24bitFLAC(self):
        check, msg = self.checkClipping('clipping24bit.flac')
        self.assertFalse(check)
        self.assertEqual(msg, "Detected large gradient. Please verify clipping in Audacity.")

    def testClipping24bitWAV(self):
        check, msg = self.checkClipping('clipping24bit.wav')
        self.assertFalse(check)
        self.assertEqual(msg, self.CLIPPING_HEAVY_MSG)

    def testClipping32bitWAV(self):
        check, msg = self.checkClipping('clipping32bitfloat.wav')
        self.assertFalse(check)
        self.assertEqual(msg, self.CLIPPING_MSG([
            self.clip(12.99, 6),
            self.clip(12.99, 7),
            self.clip(13.00, 6), 
            self.clip(13.01, 8),
            self.clip(13.01, 4),
            self.clip(13.01, 4), 
            self.clip(13.02, 10),
        ]))

# Use inheritance to reuse the same test functions
class TestClippingFromFile(unittest.TestCase, BaseTestClipping):
    def checkClipping(self, filename: str):
        return checkClippingFromFile(File(TEST_DIR / filename), TEST_DIR / filename)

class TestClippingFromUrl(unittest.TestCase, BaseTestClipping):
    def checkClipping(self, filename: str):
        return checkClippingFromUrl(parseUrl(TEST_URLS[filename]))

#=======================================#
#            Main Function              #
#=======================================#

from simpleQoC.qoc import performQoC

class TestOverall(unittest.TestCase):
    """
    A few test cases to make sure all functions work together fine
    """
    def testOverall(self):
        check, msg = performQoC("https://siiva-gunner.com/?id=vnrufKKnxu")
        self.assertEqual(check, 0)
        self.assertEqual(msg, "- Bitrate is OK.\n- The rip is not clipping.\n-No video streams detected")

    def testOverallV2(self):
        check, msg = performQoC(TEST_URLS['clipping5.ogg'])
        self.assertEqual(check, 1)
        self.assertIn("Please re-render at 320kbps", msg)
        self.assertIn("The rip is clipping", msg)

    def testOverallV3(self):
        check, msg = performQoC(TEST_URLS['clipping3.wav'])
        self.assertEqual(check, 1)
        self.assertIn("Lossless file is OK", msg)
        self.assertIn("The rip is heavily clipping. Post-render volume reduction detected, please lower the volume before rendering.", msg)

    def testOverallV4(self):
        check, msg = performQoC(TEST_URLS['lowBitrate.mp3'])
        self.assertEqual(check, 1)
        self.assertIn("Please re-render at 320kbps", msg)
        self.assertIn("The rip is not clipping", msg)

    def testOverallV5(self):
        check, msg = performQoC("https://siiva-gunner.com/?id=vnrufKKnx")
        self.assertEqual(check, -1)


import simpleQoC
import sys

if __name__ == '__main__':
    if len(sys.argv) > 1:
        print('DEBUG MODE ENABLED')
        simpleQoC.DEBUG_MODE = True
    
    unittest.main()