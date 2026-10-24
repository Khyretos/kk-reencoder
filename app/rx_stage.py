"""Module for staging and managing re-encoded video files in kk-reencoder.

Provides utilities to stage re-encoded videos next to originals, compare frames,
and manage the lifecycle (confirm, undo, finalize, discard) ensuring no data loss.
"""

import csv
import hashlib
import io
import os
from typing import List, Optional


class StageError(Exception):
    """Raised when a stage operation is attempted on an invalid state."""
    pass


def _reencoder_dir(path: str) -> str:
    """Return the base directory for .reencoder files for a given path."""
    parent = os.path.dirname(path)
    name = os.path.basename(path)
    return os.path.join(parent, ".reencoder")


def _final_dir(path: str) -> str:
    """Return the base directory for final marker files for a given path."""
    parent = os.path.dirname(path)
    name = os.path.basename(path)
    return os.path.join(parent, ".reencoder-final")


def staged_path(path: str) -> str:
    """Path for the staged (re-encoded) copy.

    Args:
        path: Original file path.

    Returns:
        Path to <dir>/.reencoder/[reencoded] <name>.
    """
    dir_path = _reencoder_dir(path)
    name = os.path.basename(path)
    return os.path.join(dir_path, "[reencoded] " + name)


def kept_path(path: str) -> str:
    """Path for the kept (original) copy.

    Args:
        path: Original file path.

    Returns:
        Path to <dir>/.reencoder/[original] <name>.
    """
    dir_path = _reencoder_dir(path)
    name = os.path.basename(path)
    return os.path.join(dir_path, "[original] " + name)


def frames_dir(root: str, path: str) -> str:
    """Directory for comparison frames based on SHA1 of the file path.

    Args:
        root: Base directory for frames.
        path: File path to hash.

    Returns:
        Path to <root>/<first 16 hex chars of sha1>.
    """
    sha1 = hashlib.sha1(path.encode("utf-8")).hexdigest()
    prefix = sha1[:16]
    return os.path.join(root, prefix)


def state(path: str) -> str:
    """Determine the current state of a file's re-encode process.

    States: 'staged', 'confirmed', 'final', 'none'.

    Args:
        path: File path to check.

    Returns:
        Current state string.
    """
    staged_exists = os.path.exists(staged_path(path))
    kept_exists = os.path.exists(kept_path(path))
    
    # Check for final marker
    final_dir = _final_dir(path)
    final_marker_exists = os.path.exists(os.path.join(final_dir, os.path.basename(path)))
    
    if staged_exists and not kept_exists:
        return "staged"
    elif kept_exists:
        return "confirmed"
    elif final_marker_exists:
        return "final"
    else:
        # Check if path exists and nothing in .reencoder for it
        if os.path.exists(path):
            reencoder_dir = _reencoder_dir(path)
            if not os.path.exists(reencoder_dir) or not os.listdir(reencoder_dir):
                return "none"
        return "none"


def _swap(path: str, first: tuple, second: tuple) -> None:
    """Two renames on the same filesystem; when the second fails the first is moved back."""
    os.makedirs(_reencoder_dir(path), exist_ok=True)
    os.replace(*first)
    try:
        os.replace(*second)
    except OSError:
        os.replace(first[1], first[0])
        raise


def confirm(path: str) -> None:
    """Put the staged re-encode in place; the original moves aside as [original] <name>.

    Raises:
        StageError: If state is not 'staged'.
    """
    if state(path) != "staged":
        raise StageError(f"Cannot confirm: expected 'staged', got '{state(path)}'")
    # original aside first, so it is never overwritten
    _swap(path, (path, kept_path(path)), (staged_path(path), path))


def undo(path: str) -> None:
    """Swap back: the original returns, the re-encode goes back to staged.

    Raises:
        StageError: If state is not 'confirmed'.
    """
    if state(path) != "confirmed":
        raise StageError(f"Cannot undo: expected 'confirmed', got '{state(path)}'")
    _swap(path, (path, staged_path(path)), (kept_path(path), path))


def finalize(path: str) -> None:
    """Finalize by deleting the original and creating a marker.

    Args:
        path: Original file path.

    Raises:
        StageError: If state is not 'confirmed'.
    """
    if state(path) != "confirmed":
        raise StageError(f"Cannot finalize: expected 'confirmed', got '{state(path)}'")
    
    # Delete the kept original
    os.remove(kept_path(path))
    
    # Create final marker
    final_dir = _final_dir(path)
    os.makedirs(final_dir, exist_ok=True)
    marker_path = os.path.join(final_dir, os.path.basename(path))
    with open(marker_path, "w") as f:
        pass  # Empty file
    
    # Remove .reencoder if empty
    reencoder_dir = _reencoder_dir(path)
    if os.path.exists(reencoder_dir) and not os.listdir(reencoder_dir):
        os.rmdir(reencoder_dir)


def discard(path: str) -> None:
    """Discard the staged copy.

    Args:
        path: Original file path.

    Raises:
        StageError: If state is not 'staged'.
    """
    if state(path) != "staged":
        raise StageError(f"Cannot discard: expected 'staged', got '{state(path)}'")
    
    staged = staged_path(path)
    os.remove(staged)
    
    # Remove .reencoder if empty
    reencoder_dir = _reencoder_dir(path)
    if os.path.exists(reencoder_dir) and not os.listdir(reencoder_dir):
        os.rmdir(reencoder_dir)


def frame_times(duration: float, worst: List[float], n: int = 5) -> List[float]:
    """Generate timestamps for comparison frames.

    Args:
        duration: Total video duration in seconds.
        worst: List of worst frame times (at most 2).
        n: Number of frames to return.

    Returns:
        Sorted list of timestamps.
    """
    candidates = []
    
    # Priority 1: Worst times (at most 2)
    for t in worst:
        if 0 <= t < duration:
            candidates.append(t)
    
    # Priority 2: Centres of thirds
    thirds = [duration / 6, duration / 2, duration * 5 / 6]
    for t in thirds:
        if 0 <= t < duration:
            candidates.append(t)
    
    # Priority 3: Fillers
    fillers = [duration / 3, duration * 2 / 3]
    for t in fillers:
        if 0 <= t < duration:
            candidates.append(t)
    
    # Priority 4: duration*k/(n+1) for k=1..n
    for k in range(1, n + 1):
        t = duration * k / (n + 1)
        if 0 <= t < duration:
            candidates.append(t)
    
    # Walk candidates and keep one only when at least 5s away from every kept time
    kept = []
    for t in sorted(candidates):
        if len(kept) >= n:
            break
        # Check distance from all kept times
        valid = True
        for kt in kept:
            if abs(t - kt) < 5.0:
                valid = False
                break
        if valid:
            kept.append(t)
    
    return sorted(kept)


# HDR (PQ/HLG) frames tone-mapped to an ordinary picture, so the JPEGs for Compare do not look washed out
TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,"
           "zscale=t=bt709:m=bt709:r=tv,format=yuv420p,")


def frame_cmd(src: str, t: float, out: str, hdr: bool = False) -> List[str]:
    """Generate ffmpeg command for extracting a single frame.

    Args:
        src: Source video path.
        t: Timestamp in seconds.
        out: Output image path.

    Returns:
        List of command arguments.
    """
    # Format time with 3 decimals
    t_str = f"{t:.3f}"
    
    return [
        "ffmpeg", "-nostdin", "-v", "error",
        "-ss", t_str, "-i", src,
        "-frames:v", "1",
        "-vf", (TONEMAP if hdr else "") + "scale=-2:'min(1080,ih)'", "-q:v", "3", "-y", out
    ]


def worst_time(csv_text: str, start: float, fps: float) -> Optional[float]:
    """Parse libvmaf CSV log to find the worst frame time.

    Args:
        csv_text: CSV log text from libvmaf.
        start: Start time of the sample.
        fps: Frames per second.

    Returns:
        Worst frame time (start + Frame/fps), or None if no data.
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    
    # Check if vmaf column exists
    if not reader.fieldnames or "vmaf" not in reader.fieldnames:
        return None
    
    worst_frame = None
    worst_vmaf = float("inf")
    
    for row in reader:
        try:
            frame = int(row["Frame"])
            vmaf = float(row["vmaf"])
            if vmaf < worst_vmaf:
                worst_vmaf = vmaf
                worst_frame = frame
        except (ValueError, KeyError):
            continue
    
    if worst_frame is None:
        return None
    
    return start + worst_frame / fps
