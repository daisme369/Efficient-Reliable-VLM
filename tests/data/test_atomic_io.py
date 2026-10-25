"""Exercise a real Windows file lease at the JSON replacement boundary."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from cxr_research.utils.io import write_json


@unittest.skipUnless(os.name == "nt", "Windows file-sharing regression")
class AtomicIOTests(unittest.TestCase):
    def test_replacement_survives_a_brief_reader_lease_and_preserves_valid_json(self):
        scratch = ROOT / ".test-tmp"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as folder:
            path = Path(folder) / "status.json"
            path.write_text('{"status":"old"}')
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                           wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            kernel.CreateFileW.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            # The reader allows reads, but Windows must reject deletion/replacement.
            handle = kernel.CreateFileW(str(path), 0x80000000, 0x1, None, 3, 0x80, None)
            self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
            timer = threading.Timer(0.12, kernel.CloseHandle, args=(handle,))
            timer.start()
            try:
                write_json(path, {"status": "new", "rows": 3})
            finally:
                timer.join()
            self.assertEqual(json.loads(path.read_text()), {"status": "new", "rows": 3})


if __name__ == "__main__":
    unittest.main()
