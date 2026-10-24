"""Pure functions for kk-reencoder model logic.

This module provides utilities for:
- Determining folder keys for video files in a library.
- Deciding whether to skip re-encoding based on configuration.
- Ordering folders for processing.
- Searching for optimal encoder quality settings.
- Selecting the best result from trials.
- Generating encoding reports.
- Parsing ffmpeg progress output.

All functions use only the standard library and perform no I/O or subprocess calls.
"""

import os
import math
import re
from typing import Any, Dict, List, Optional


def folder_key(root: str, path: str) -> str:
    """Return the title folder of a video file relative to the library root.

    Args:
        root: The absolute path to the library root.
        path: The absolute path to the video file.

    Returns:
        A string representing the folder key. If the file is directly in root,
        returns the filename without extension. If the last directory component
        is season-like (e.g., "Season 1", "S01"), it is dropped. Remaining parts
        are joined with '/'.
    """
    # Normalize paths to avoid issues with trailing slashes
    root = root.rstrip("/")
    path = path.rstrip("/")

    # Get the relative path from root
    if not path.startswith(root + "/") and path != root:
        # Should not happen in normal usage, but handle gracefully
        return os.path.basename(path).rsplit(".", 1)[0]

    rel_path = path[len(root) + 1:]  # Skip root + "/"

    if not rel_path:
        # File is exactly at root (shouldn't happen with valid paths, but handle)
        return os.path.basename(path).rsplit(".", 1)[0]

    parts = rel_path.split("/")
    
    # Remove the filename (last part) first
    if parts:
        parts = parts[:-1]
    
    # Check if the last part is season-like
    if parts:
        last_part = parts[-1]
        season_patterns = [
            r'^season\s*\d+$',
            r'^series\s*\d+$',
            r'^specials$',
            r'^extras$',
            r'^s\d{2}$',
        ]
        
        is_season_like = False
        for pattern in season_patterns:
            if re.match(pattern, last_part, re.IGNORECASE):
                is_season_like = True
                break
        
        if is_season_like:
            # Drop the season-like folder
            parts = parts[:-1]
    
    # If no parts left, return filename without extension
    if not parts:
        return os.path.basename(path).rsplit(".", 1)[0]
    
    # Join remaining parts with '/'
    return "/".join(parts)


def skip_reason(info: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[str]:
    """Determine if a file should be skipped based on configuration.

    Args:
        info: Dictionary containing file info ('codec', 'hdr', 'height', 'bitrate_kbps').
        cfg: Configuration dictionary with 'codecs', 'skip_hdr', 'max_height', 'min_kbps'.

    Returns:
        None if the file should be considered, otherwise a short reason string.
        Checks in order: AV1 codec -> HDR -> codec not selected -> resolution too high -> bitrate already low.
    """
    # Check if already AV1
    if info.get('codec') == 'av1':
        return 'already AV1'
    
    # Check HDR
    if info.get('hdr', False) and cfg.get('skip_hdr', False):
        return 'HDR'
    
    # Check if codec is not in allowed list
    if info.get('codec') not in cfg.get('codecs', []):
        return 'codec not selected'
    
    # Check resolution
    max_height = cfg.get('max_height', float('inf'))
    height = info.get('height', 0)
    if height > max_height:
        return 'resolution too high'
    
    # Check bitrate
    bitrate = info.get('bitrate_kbps')
    if bitrate is not None:
        # Determine band based on height
        if height < 700:
            band = 'sd'
        elif height < 1000:
            band = '720'
        elif height < 1800:
            band = '1080'
        else:
            band = '2160'
        
        min_kbps = cfg.get('min_kbps', {}).get(band, float('inf'))
        if bitrate < min_kbps:
            return 'bitrate already low'
    
    return None


# skip reasons that depend on the settings (not on the file alone): a later scan looks at these files again
SETTING_REASONS = ("HDR", "codec not selected", "resolution too high", "bitrate already low")
# skip reasons from the quality search: a change of target VMAF or size limit puts these files back in the queue
QUALITY_REASON_PREFIXES = ("no smaller encode", "encode not smaller", "never reached VMAF", "at VMAF",
                           "the whole AV1 file")


def is_quality_reason(reason: Optional[str]) -> bool:
    """True for a skip that came from the quality search (worth trying again with other settings)."""
    return bool(reason) and reason.startswith(QUALITY_REASON_PREFIXES)


def search_skip_reason(trials: List[Dict[str, Any]], target: float, max_ratio: float) -> str:
    """Why a search found nothing to keep, with the measured numbers, in words people can act on."""
    if not trials:
        return "no smaller encode at target quality"
    good = [t for t in trials if t["vmaf"] >= target]
    if not good:
        best = max(trials, key=lambda t: t["vmaf"])
        return (f"never reached VMAF {target:g}: best was {best['vmaf']:.1f} at the highest quality tried "
                f"(q {best['q']})")
    smallest = min(good, key=lambda t: t["ratio"])
    return (f"at VMAF {target:g} the AV1 copy would be {smallest['ratio'] * 100:.0f}% of the original size; "
            f"the size limit is {max_ratio * 100:.0f}%")


def order_folders(folders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort folders by encode flag, then priority, then name.

    Folders marked for encoding come first. Within each group, sort by
    priority descending, then name case-insensitive ascending.

    Only includes folders that are not paused and have pending > 0.

    Args:
        folders: List of dicts with 'name', 'priority', 'paused', 'pending',
                 and optionally 'encode' (bool).

    Returns:
        Sorted list of folders meeting the criteria.
    """
    filtered = [f for f in folders if not f.get('paused', True) and f.get('pending', 0) > 0]
    
    def sort_key(f):
        # encode=True comes before encode=False or missing
        encode_flag = f.get('encode', False)
        # Sort by: encode (False first), priority (desc), name (asc)
        return (not encode_flag, -f['priority'], f['name'].lower())
    
    return sorted(filtered, key=sort_key)


def next_quality(trials: List[Dict[str, Any]], target: float, lo: int, hi: int, start: int) -> Optional[int]:
    """Find the next quality value to try in an encoder search.

    Lower q means better quality and bigger files.
    
    Args:
        trials: List of dicts with 'q' and 'vmaf'.
        target: Target VMAF score.
        lo: Lower bound of search range.
        hi: Upper bound of search range.
        start: Initial quality to try if no trials exist.

    Returns:
        Next quality to try (int), or None when search is finished.
    """
    if not trials:
        return start
    
    # Separate good (vmaf >= target) and bad (vmaf < target) trials
    good = [t['q'] for t in trials if t['vmaf'] >= target]
    bad = [t['q'] for t in trials if t['vmaf'] < target]
    
    # Sort them
    good.sort()
    bad.sort()
    
    # Case 1: Both good and bad exist
    if good and bad:
        best_good = good[-1]  # Highest q (lowest quality) that meets target
        worst_bad = bad[0]    # Lowest q (best quality) that fails target
        
        # Check if adjacent
        if worst_bad - best_good <= 1:
            return None
        
        # Midpoint strictly between them
        midpoint = (best_good + worst_bad) // 2
        # Ensure it's strictly inside (lo, hi) and not already tried
        if midpoint <= best_good or midpoint >= worst_bad:
            return None
        
        # Verify it hasn't been tried
        if midpoint in [t['q'] for t in trials]:
            # Try the other side
            if midpoint - 1 > best_good:
                return midpoint - 1
            elif midpoint + 1 < worst_bad:
                return midpoint + 1
            return None
        
        return midpoint
    
    # Case 2: Only bad trials (all failed to meet target)
    if bad:
        worst_bad = bad[0]
        # Try lower (better quality), halfway between lo and bad
        if worst_bad == lo:
            return None
        
        # Halfway between lo and worst_bad
        new_q = (lo + worst_bad) // 2
        # Ensure strictly inside (lo, worst_bad)
        if new_q <= lo or new_q >= worst_bad:
            return None
        
        # Verify not tried
        if new_q in [t['q'] for t in trials]:
            return None
        
        return new_q
    
    # Case 3: Only good trials (all met target)
    if good:
        best_good = good[-1]  # Highest q (lowest quality) that meets target
        # Try higher (worse quality), halfway between good and hi
        if best_good == hi:
            return None
        
        # Halfway between best_good and hi
        new_q = (best_good + hi) // 2
        # Ensure strictly inside (best_good, hi)
        if new_q <= best_good or new_q >= hi:
            return None
        
        # Verify not tried
        if new_q in [t['q'] for t in trials]:
            return None
        
        return new_q
    
    return None


def pick_result(trials: List[Dict[str, Any]], target: float, max_ratio: float) -> Optional[Dict[str, Any]]:
    """Select the best result from trials that meet quality and size constraints.

    Args:
        trials: List of dicts with 'q', 'vmaf', 'ratio'.
        target: Minimum VMAF score required.
        max_ratio: Maximum allowed ratio (new/original bytes).

    Returns:
        The trial dict with highest q that passes both filters, or None.
    """
    candidates = [
        t for t in trials 
        if t['vmaf'] >= target and t['ratio'] <= max_ratio
    ]
    
    if not candidates:
        return None
    
    # Return the one with highest q
    return max(candidates, key=lambda t: t['q'])


def folder_report(
    library: str,
    folder: str,
    files: List[Dict[str, Any]],
    generated: str
) -> Dict[str, Any]:
    """Generate the encoding report dictionary.

    Args:
        library: Library name.
        folder: Folder name.
        files: List of file info dicts.
        generated: Timestamp when report was generated.

    Returns:
        Report dict with 'library', 'folder', 'generated', 'totals', 'files'.
    """
    # Sort files by name
    sorted_files = sorted(files, key=lambda f: f['name'])
    
    # Calculate totals
    totals = {
        'files': len(files),
        'encoded': sum(1 for f in files if f['status'] == 'encoded'),
        'skipped': sum(1 for f in files if f['status'] == 'skipped'),
        'predicted': sum(1 for f in files if f['status'] == 'predicted'),
        'failed': sum(1 for f in files if f['status'] == 'failed'),
        'staged': sum(1 for f in files if f['status'] == 'staged'),
        'confirmed': sum(1 for f in files if f['status'] == 'confirmed'),
        'original_bytes': sum(f['original_bytes'] for f in files),
        'current_bytes': 0,
        'extra_bytes': 0,
        'saved_bytes': 0,
        'projected_bytes': 0,
    }
    
    # Process each file
    processed_files = []
    for f in sorted_files:
        original = f['original_bytes']
        new_bytes = f.get('new_bytes')
        
        # current_bytes: encoded AND confirmed rows at new_bytes, all others at original_bytes
        if f['status'] in ('encoded', 'confirmed'):
            current = new_bytes if new_bytes is not None else original
        else:
            current = original
        
        totals['current_bytes'] += current
        
        # extra_bytes: staged rows add new_bytes, confirmed rows add original_bytes
        if f['status'] == 'staged':
            totals['extra_bytes'] += new_bytes if new_bytes is not None else 0
        elif f['status'] == 'confirmed':
            totals['extra_bytes'] += original
        
        # projected_bytes: counts encoded, confirmed, staged and predicted rows that have new_bytes at new_bytes
        if f['status'] in ('encoded', 'confirmed', 'staged', 'predicted') and new_bytes is not None:
            totals['projected_bytes'] += new_bytes
        else:
            totals['projected_bytes'] += current
        
        # Add saved_pct
        saved_pct = None
        if original > 0 and new_bytes is not None:
            saved_pct = round((original - new_bytes) / original * 100, 1)
        
        file_entry = dict(f)
        file_entry['saved_pct'] = saved_pct
        processed_files.append(file_entry)
    
    # net saving: only what is really freed (kept originals and staged copies still use disk)
    totals['saved_bytes'] = totals['original_bytes'] - totals['current_bytes'] - totals['extra_bytes']
    # Calculate saved_pct for totals
    if totals['original_bytes'] > 0:
        totals['saved_pct'] = round(totals['saved_bytes'] / totals['original_bytes'] * 100, 1)
    else:
        totals['saved_pct'] = 0.0
    
    return {
        'library': library,
        'folder': folder,
        'generated': generated,
        'totals': totals,
        'files': processed_files,
    }


def parse_progress(lines: List[str], duration_s: float) -> Dict[str, Any]:
    """Parse ffmpeg -progress output lines into structured data.

    Args:
        lines: List of key=value strings from ffmpeg progress.
        duration_s: Total duration in seconds.

    Returns:
        Dict with 'frame', 'fps', 'percent', 'eta_s', 'done'.
    """
    result = {
        'frame': 0,
        'fps': 0.0,
        'percent': 0.0,
        'eta_s': None,
        'done': False,
    }
    
    out_time_us = None
    speed = None
    progress_status = None
    
    for line in lines:
        if '=' not in line:
            continue
        
        key, _, value = line.partition('=')
        key = key.strip()
        value = value.strip()
        
        if key == 'frame':
            result['frame'] = int(value)
        elif key == 'fps':
            result['fps'] = float(value)
        elif key == 'out_time_us':
            out_time_us = int(value)
        elif key == 'speed':
            speed = value
        elif key == 'progress':
            progress_status = value
    
    # Calculate percent from out_time_us against duration_s
    if out_time_us is not None and duration_s > 0:
        percent = (out_time_us / (duration_s * 1_000_000)) * 100
        result['percent'] = min(percent, 100.0)
    elif progress_status == 'end':
        result['percent'] = 100.0
    
    # Calculate eta_s
    if progress_status == 'end':
        result['done'] = True
        result['eta_s'] = None
    elif speed is not None and speed != 'N/A' and speed != '0x':
        try:
            speed_val = float(speed.replace('x', ''))
            if speed_val > 0:
                if out_time_us is not None and duration_s > 0:
                    elapsed_s = out_time_us / 1_000_000
                    remaining_s = duration_s - elapsed_s
                    if remaining_s > 0:
                        result['eta_s'] = remaining_s / speed_val
        except (ValueError, TypeError):
            pass
    
    return result
