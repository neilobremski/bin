"""Stage a local file for a browser upload, proving its bytes are really there.

The edition folders live in OneDrive. macOS File Provider can leave a file
"dataless" (cloud-only): it lists with its full size, but `shutil.copy2`
(clonefile underneath) and even shell `cat` hand back 0 bytes, and so does any
other process that reads it without asking File Provider to fetch it first.
A plain `open(path, 'rb').read()` from Python does hydrate it. On 2026-10-01 a
header went up to Givebacks as a 0-byte image for exactly this reason.

So an upload never hands the browser the original path. It reads the bytes
here, writes them to a private temp copy, checks the copy against the size the
file system reports, and uploads the copy. A file that still reads short is
refused by name instead of being uploaded empty.
"""
import os
import tempfile


class StageError(Exception):
    """The file could not be staged with all of its bytes."""


def read_all(path):
    """Every byte of `path`, refusing an empty or short read.

    `os.path.getsize` reports the logical size even for a cloud-only file, so
    it is the yardstick the read is held to.
    """
    expected = os.path.getsize(path)
    with open(path, "rb") as f:
        data = f.read()
    if not data:
        raise StageError(
            f"{path} read as 0 bytes (the file system says {expected}). If it "
            "lives in OneDrive it may be cloud-only: open it once in Finder or "
            "mark it 'Always Keep on This Device', then retry.")
    if len(data) != expected:
        raise StageError(
            f"{path} read {len(data)} bytes but the file system says "
            f"{expected}; refusing to upload a partial file.")
    return data


def stage(path, name=None):
    """Copy `path` into a fresh temp dir by its bytes. Returns (staged, size).

    `name` is the file name to upload under (S3 keeps it); default is the
    source's own name.
    """
    data = read_all(path)
    staged = os.path.join(tempfile.mkdtemp(prefix="b3t-upload-"),
                          name or os.path.basename(path))
    with open(staged, "wb") as f:
        f.write(data)
    written = os.path.getsize(staged)
    if written != len(data):
        raise StageError(f"staged copy {staged} holds {written} bytes, "
                         f"expected {len(data)}.")
    return staged, len(data)
