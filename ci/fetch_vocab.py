#!/usr/bin/env python3
"""
Download only the vocabulary of a Vosk model, without the 1-2 GB zip.

  python3 ci/fetch_vocab.py <model name> <dest dir>

Reads the zip at https://alphacephei.com/vosk/models/<model name>.zip with HTTP range requests,
and extracts graph/words.txt (big models) or graph/Gr.fst (small models), the two files
vocab_check.py reads, into <dest dir>/<model name>/graph/.
"""
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

MODEL_URL = "https://alphacephei.com/vosk/models/{}.zip"
VOCABULARY_FILES = ("graph/words.txt", "graph/Gr.fst")
# zipfile reads in small steps: fetch larger blocks and serve its reads from them
BLOCK = 1 << 20


class HttpRangeFile:
    """Read-only, seekable view of a remote file, for zipfile."""

    def __init__(self, url):
        self.url = url
        request = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(request, timeout=60) as resp:
            if resp.headers.get("Accept-Ranges") != "bytes":
                raise OSError(f"{url}: the server does not accept range requests")
            self.size = int(resp.headers["Content-Length"])
        self.pos = 0
        self.block_start = 0
        self.block = b""

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=0):
        self.pos = {0: offset, 1: self.pos + offset, 2: self.size + offset}[whence]
        return self.pos

    def fetch(self, start, end):
        request = urllib.request.Request(self.url, headers={"Range": f"bytes={start}-{end - 1}"})
        with urllib.request.urlopen(request, timeout=60) as resp:
            if resp.status != 206:
                raise OSError(f"{self.url}: range request answered {resp.status}")
            return resp.read()

    def read(self, n=-1):
        end = self.size if n is None or n < 0 else min(self.size, self.pos + n)
        if self.pos >= end:
            return b""
        if not (self.block_start <= self.pos and end <= self.block_start + len(self.block)):
            self.block_start = self.pos
            self.block = self.fetch(self.pos, max(end, min(self.size, self.pos + BLOCK)))
        data = self.block[self.pos - self.block_start:end - self.block_start]
        self.pos += len(data)
        return data


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    model, dest = sys.argv[1], Path(sys.argv[2])
    with zipfile.ZipFile(HttpRangeFile(MODEL_URL.format(model))) as z:
        for name in z.namelist():
            # members sit under a top folder, normally named after the model
            relative = name.split("/", 1)[-1]
            if relative in VOCABULARY_FILES:
                target = dest / model / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                # Renamed only once complete: CI caches the folder, a cut download must not land there
                partial = target.with_name(target.name + ".part")
                with z.open(name) as src, open(partial, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                partial.rename(target)
                print(f"{model}: {relative} ({target.stat().st_size} bytes)")
                return
    print(f"{model}: neither {' nor '.join(VOCABULARY_FILES)} in the zip")
    sys.exit(1)


if __name__ == "__main__":
    main()
