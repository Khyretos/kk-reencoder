"""Module for parsing ffprobe JSON and building ffmpeg argument lists.

This module provides utilities to inspect video files via ffprobe output
and construct command-line arguments for various AV1 encoding backends
(qsv, vaapi, nvenc, svt) without executing any commands itself.
"""

import json
import os
import subprocess
from typing import Any, Dict, List, Optional


def parse_probe(probe: Dict[str, Any], size: int) -> Optional[Dict[str, Any]]:
    """Parse ffprobe JSON output and file size into a video info dict.

    Args:
        probe: Dictionary containing 'streams' and 'format' keys from ffprobe.
        size: File size in bytes.

    Returns:
        Dict with codec details or None if no valid video stream exists.
    """
    streams = probe.get("streams", [])
    fmt = probe.get("format", {})

    # Filter out cover art (attached_pic == 1) and collect video/audio/subtitle streams
    video_streams = [s for s in streams if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    sub_streams = [s for s in streams if s.get("codec_type") == "subtitle"]

    if not video_streams:
        return None

    # Use the first valid video stream (excluding cover art)
    v = video_streams[0]
    codec = v.get("codec_name", "")
    width = v.get("width", 0)
    height = v.get("height", 0)
    pix_fmt = v.get("pix_fmt", "")
    
    # Ten-bit check: pix_fmt contains '10' or '12'
    ten_bit = "10" in pix_fmt or "12" in pix_fmt
    
    # HDR check: color_transfer is smpte2084 or arib-std-b67
    hdr = v.get("color_transfer", "") in ("smpte2084", "arib-std-b67")
    
    # FPS from avg_frame_rate 'a/b' -> float(a)/float(b)
    avg_fps = v.get("avg_frame_rate", "0/1")
    try:
        a, b = avg_fps.split("/")
        fps = float(a) / float(b) if b else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    
    # Duration
    duration_str = fmt.get("duration", "0")
    try:
        duration_s = float(duration_str)
    except (ValueError, TypeError):
        duration_s = 0.0
    
    # Bitrate: prefer format bit_rate, else calculate from size/duration
    bitrate_kbps = None
    if fmt.get("bit_rate"):
        try:
            bitrate_kbps = int(fmt["bit_rate"]) // 1000
        except (ValueError, TypeError):
            pass
    elif duration_s > 0 and size > 0:
        bitrate_kbps = (size * 8) // duration_s // 1000
    
    # video_index is the position of the chosen stream among video streams that are not cover art
    video_index = len(video_streams) - 1

    return {
        "codec": codec,
        "width": width,
        "height": height,
        "pix_fmt": pix_fmt,
        "ten_bit": ten_bit,
        "hdr": hdr,
        "fps": fps,
        "duration_s": duration_s,
        "bitrate_kbps": bitrate_kbps,
        "size": size,
        "audio": len(audio_streams),
        "subs": len(sub_streams),
        "video_index": video_index,
    }


def probe(path: str) -> Optional[Dict[str, Any]]:
    """Run ffprobe on a file and return parsed info.

    Args:
        path: Path to the video file.

    Returns:
        Parsed probe dict or None on failure.
    """
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        return parse_probe(data, os.path.getsize(path))
    except Exception:
        return None


def encoder_args(encoder: str, q: int, device: Optional[str]) -> List[str]:
    """Build video encoder arguments for a given backend.

    Args:
        encoder: One of 'qsv', 'vaapi', 'nvenc', 'svt'.
        q: Quality value (CRF or global_quality).
        device: Device path (used by qsv and vaapi).

    Returns:
        List of ffmpeg arguments starting with '-c:v ...'.

    Raises:
        ValueError: If encoder name is not recognized.
    """
    if encoder == "qsv":
        return [
            "-c:v", "av1_qsv",
            "-preset", "veryslow",
            "-global_quality", str(q),
            "-extbrc", "1",
            "-look_ahead_depth", "40",
            "-pix_fmt", "p010le"
        ]
    elif encoder == "vaapi":
        return [
            "-vf", "format=p010le,hwupload",
            "-c:v", "av1_vaapi",
            "-rc_mode", "ICQ",
            "-global_quality", str(q)
        ]
    elif encoder == "nvenc":
        return [
            "-c:v", "av1_nvenc",
            "-preset", "p7",
            "-tune", "hq",
            "-rc", "vbr",
            "-cq", str(q),
            "-b:v", "0",
            "-pix_fmt", "p010le"
        ]
    elif encoder == "svt":
        return [
            "-c:v", "libsvtav1",
            "-preset", "6",
            "-crf", str(q),
            "-pix_fmt", "yuv420p10le"
        ]
    else:
        raise ValueError(f"Unknown encoder: {encoder}")


def hw_init_args(encoder: str, device: Optional[str]) -> List[str]:
    """Build hardware initialization arguments.

    Args:
        encoder: Encoder name ('qsv', 'vaapi', etc.).
        device: Device path (required for qsv and vaapi).

    Returns:
        List of init args or empty list.
    """
    if encoder == "qsv":
        return ["-init_hw_device", f"qsv=hw:{device}"]
    elif encoder == "vaapi":
        return ["-vaapi_device", device]
    else:
        return []


def sample_points(duration_s: float, count: int, length: int) -> List[int]:
    """Calculate start seconds for sample points.

    Args:
        duration_s: Total duration in seconds.
        count: Number of samples.
        length: Length of each sample in seconds.

    Returns:
        List of integer start times, or [0] if duration too short.
    """
    if duration_s < (count + 1) * length:
        return [0]
    
    points = []
    for k in range(1, count + 1):
        center = duration_s * k / (count + 1)
        start = int(center - length / 2)
        points.append(start)
    
    return points


def sample_cmd(src: str, dst: str, start: int, length: int, video_index: int) -> List[str]:
    """Build command to extract a sample by seeking and copying.

    Args:
        src: Source file path.
        dst: Destination file path.
        start: Start time in seconds.
        length: Sample duration in seconds.
        video_index: Video stream index (0-based).

    Returns:
        List of ffmpeg arguments.
    """
    return [
        "ffmpeg",
        "-ss", str(start),
        "-i", src,
        "-t", str(length),
        "-map", f"0:v:{video_index}",
        "-c", "copy",
        "-an",
        "-sn",
        "-y",
        "-nostdin",
        "-v", "error",
        dst
    ]


def reference_cmd(sample: str, ref: str) -> List[str]:
    """Build command to create a lossless ffv1 reference.

    Args:
        sample: Source file path.
        ref: Destination reference file path.

    Returns:
        List of ffmpeg arguments.
    """
    return [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-v", "error",
        "-i", sample,
        "-c:v", "ffv1",
        ref
    ]


def with_filter(enc_args: List[str], filter: Optional[str]) -> List[str]:
    """Encoder args with the named filter in front of their own -vf chain (one -vf in total)."""
    extra = filter_args(filter)
    if not extra:
        return list(enc_args)
    if "-vf" in enc_args:
        i = enc_args.index("-vf")
        return enc_args[:i + 1] + [extra[1] + "," + enc_args[i + 1]] + enc_args[i + 2:]
    return extra + list(enc_args)


def sample_encode_cmd(ref: str, dst: str, encoder: str, q: int, device: Optional[str], filter: Optional[str] = None) -> List[str]:
    """Build command to encode a reference sample.

    Args:
        ref: Reference file path.
        dst: Destination encoded file path.
        encoder: Encoder name.
        q: Quality value.
        device: Device path.

    Returns:
        List of ffmpeg arguments.
    """
    args = ["ffmpeg", "-nostdin", "-v", "error"] + hw_init_args(encoder, device) + ["-i", ref] + with_filter(encoder_args(encoder, q, device), filter)
    args.extend(["-y", dst])
    return args


FILTERS = {"deband": "deband=1thr=0.02:2thr=0.02:3thr=0.02:4thr=0.02:range=16"}


def filter_args(name: Optional[str]) -> List[str]:
    """The -vf pair of a named filter ([] for None); unknown names raise ValueError."""
    if name is None:
        return []
    if name not in FILTERS:
        raise ValueError(f"unknown filter: {name}")
    return ["-vf", FILTERS[name]]


def encode_cmd(src: str, dst: str, encoder: str, q: int, device: Optional[str], video_index: int, filter: Optional[str] = None) -> List[str]:
    """Build full encoding command preserving all streams.

    Args:
        src: Source file path.
        dst: Destination file path.
        encoder: Encoder name.
        q: Quality value.
        device: Device path.
        video_index: Video stream index.

    Returns:
        List of ffmpeg arguments.
    """
    args = ["ffmpeg", "-nostdin", "-v", "error"]
    
    # Hardware init if needed
    if encoder in ("qsv", "vaapi"):
        args.extend(hw_init_args(encoder, device))
    
    args.extend(["-i", src])
    
    # Map video stream
    args.extend(["-map", f"0:v:{video_index}"])
    
    # Map audio and subtitle streams conditionally
    args.extend(["-map", "0:a?"])
    args.extend(["-map", "0:s?"])
    
    # Map time track only for mkv
    if dst.endswith(".mkv"):
        args.extend(["-map", "0:t?"])
    
    # Copy metadata and chapters
    args.extend(["-map_metadata", "0"])
    args.extend(["-map_chapters", "0"])
    
    # Stream copy for non-encoded streams
    args.extend(["-c", "copy"])
    
    # Add encoder-specific arguments
    enc_args = with_filter(encoder_args(encoder, q, device), filter)
    # Replace -c:v with -c:v:0 and adjust pix_fmt/vf if present
    new_enc_args = []
    for arg in enc_args:
        if arg == "-c:v":
            new_enc_args.append("-c:v:0")
        elif arg == "-pix_fmt":
            new_enc_args.append("-pix_fmt:v:0")
        else:
            new_enc_args.append(arg)
    
    # Handle -vf specially (keep as is, but it's already applied to video stream 0 via context)
    # Actually, for hw upload we need to ensure vf applies to the right stream
    # But per spec, -vf stays as is in encoder_args
    args.extend(new_enc_args)
    
    # Add mov_text subtitle codec for mp4/m4v
    if dst.endswith((".mp4", ".m4v")):
        args.extend(["-c:s", "mov_text"])
    
    # Progress output
    args.extend(["-progress", "pipe:1"])
    args.extend(["-nostats"])
    args.extend(["-y"])
    args.append(dst)
    
    return args


def vmaf_cmd(encoded: str, ref: str, threads: int, log: Optional[str] = None) -> List[str]:
    """Build VMAF comparison command.

    Args:
        encoded: Encoded file path.
        ref: Reference file path.
        threads: Number of threads for VMAF analysis.
        log: Optional path to write VMAF CSV log.

    Returns:
        List of ffmpeg arguments.
    """
    lavfi = f"[0:v]setpts=PTS-STARTPTS,format=yuv420p[d];[1:v]setpts=PTS-STARTPTS,format=yuv420p[r];[d][r]libvmaf=n_threads={threads}:n_subsample=4"
    if log is not None:
        lavfi += ":log_fmt=csv:log_path=" + log
    return [
        "ffmpeg",
        "-nostdin",
        "-i", encoded,
        "-i", ref,
        "-lavfi", lavfi,
        "-f", "null", "-"
    ]


def parse_vmaf(text: str) -> Optional[float]:
    """Extract VMAF score from ffmpeg stderr text.

    Args:
        text: Stderr output from vmaf_cmd.

    Returns:
        Float score or None if not found.
    """
    for line in text.splitlines():
        if "VMAF score:" in line:
            try:
                parts = line.split("VMAF score:")
                return float(parts[1].strip())
            except (ValueError, IndexError):
                continue
    return None


def source_reference_cmd(src: str, ref: str, start: int, length: int, video_index: int,
                         filter: Optional[str] = None) -> List[str]:
    """Lossless ffv1 reference decoded straight from the source (accurate seek), so a sample cut by
    stream copy that starts between keyframes cannot break the decode. With a filter (deband) the
    reference is the filtered picture: VMAF then measures only the compression loss, not the
    intended clean-up."""
    return (["ffmpeg", "-nostdin", "-v", "error", "-ss", str(start), "-i", src, "-t", str(length),
             "-map", f"0:v:{video_index}", "-an", "-sn"] + filter_args(filter)
            + ["-c:v", "ffv1", "-y", ref])
