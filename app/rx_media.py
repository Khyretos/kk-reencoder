"""Module for parsing ffprobe JSON and building ffmpeg argument lists.

This module provides utilities to inspect video files via ffprobe output
and construct command-line arguments for various AV1 encoding backends
(qsv, vaapi, nvenc, svt) without executing any commands itself.
"""

import json
import os
import subprocess
from typing import Any, Dict, List, Optional


def stream_duration(st: Dict[str, Any]) -> Optional[float]:
    """A stream's own length in seconds: its duration field, or Matroska's DURATION tag (hh:mm:ss.fffffffff)."""
    try:
        if st.get("duration") not in (None, "", "N/A"):
            return float(st["duration"])
    except (TypeError, ValueError):
        pass
    tag = (st.get("tags") or {}).get("DURATION") or (st.get("tags") or {}).get("duration")
    if not tag:
        return None
    try:
        h, m, sec = tag.split(":")
        return int(h) * 3600 + int(m) * 60 + float(sec)
    except ValueError:
        return None


def video_packets_after(path: str, t: float, video_index: int = 0, span: float = 30.0) -> bool:
    """True when the file has video packets at or after t seconds (seeking, so a cut-off file says False).
    Used to tell an AV1 copy that is short because the original is cut off (a download that stopped: nothing
    left to read) from one that really lost video."""
    try:
        r = subprocess.run(
            ["nice", "-n", "19", "ionice", "-c", "3", "ffprobe", "-v", "error", "-select_streams", f"v:{video_index}",
             "-show_entries", "packet=pts_time", "-of", "csv=p=0", "-read_intervals", f"{max(0.0, t):.3f}%+{span:g}",
             path],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    except Exception:
        return True  # cannot tell: assume there is more, so nothing short is ever kept by mistake
    if r.returncode != 0:
        return True
    for line in r.stdout.splitlines():
        try:
            if float(line.strip().strip(",")) >= t - 0.5:
                return True
        except ValueError:
            continue
    return False


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
    # Dolby Vision profile (None without a configuration record): profile 5 has no HDR10 picture underneath
    dv_profile = None
    for sd in v.get("side_data_list") or []:
        if sd.get("side_data_type") == "DOVI configuration record":
            dv_profile = sd.get("dv_profile")
    
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
    # the container's duration can be wrong (Black Clover / SPY x FAMILY episodes say 3600 s and are 24 min long;
    # a Gen V episode's video stops after 9.5 min while its audio runs 48 min): the video stream's own length is
    # what the samples and the length check must go by
    v_dur = stream_duration(v)
    if v_dur and (duration_s <= 0 or v_dur < duration_s - 2):
        duration_s = v_dur
    
    # Bitrate: prefer format bit_rate, else calculate from size/duration
    bitrate_kbps = None
    if fmt.get("bit_rate"):
        try:
            bitrate_kbps = int(fmt["bit_rate"]) // 1000
        except (ValueError, TypeError):
            pass
    elif duration_s > 0 and size > 0:
        bitrate_kbps = (size * 8) // duration_s // 1000
    
    # video_index is the position of the chosen stream among video streams that are not cover art (the first one)
    video_index = 0

    return {
        "codec": codec,
        "width": width,
        "height": height,
        "pix_fmt": pix_fmt,
        "ten_bit": ten_bit,
        "hdr": hdr,
        # colour tags the AV1 copy must carry to look the same (HDR10 / HLG); None where the source has none
        "colour": {"primaries": v.get("color_primaries"), "transfer": v.get("color_transfer"),
                   "space": v.get("color_space")} if hdr else None,
        "dv_profile": dv_profile,
        "fps": fps,
        "duration_s": duration_s,
        "bitrate_kbps": bitrate_kbps,
        "size": size,
        "audio": len(audio_streams),
        "subs": len(sub_streams),
        # subtitle streams ffmpeg does not know (codec "none"): copying them fails the whole encode, so they are left out
        "bad_subs": [i for i, st in enumerate(sub_streams) if not st.get("codec_name") or st.get("codec_name") == "none"],
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
            ["nice", "-n", "19", "ionice", "-c", "3", "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",  # tags in old files are not always UTF-8; that must not make the file unreadable
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
        # AVI (packed B-frames) and some old files carry packets without timestamps, which a Matroska
        # sample cannot hold ("Can't write packet with unknown timestamp"): let ffmpeg fill them in
        "-fflags", "+genpts",
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


def hdr_args(colour: Optional[Dict[str, Any]], dv_profile: Optional[int]) -> List[str]:
    """Output options that keep an HDR picture HDR: the colour tags written into the AV1 stream (ffmpeg passes the
    mastering display and light level side data on by itself), and for Dolby Vision the configuration record dropped,
    so players show the HDR10 picture underneath instead of looking for Dolby Vision data that is not there."""
    args: List[str] = []
    if colour:
        for opt, key in (("-color_primaries:v:0", "primaries"), ("-color_trc:v:0", "transfer"),
                         ("-colorspace:v:0", "space")):
            if colour.get(key):
                args += [opt, colour[key]]
    if dv_profile is not None:
        args += ["-bsf:v:0", "dovi_rpu=strip=1"]
    return args


def encode_cmd(src: str, dst: str, encoder: str, q: int, device: Optional[str], video_index: int, filter: Optional[str] = None,
               hw_decode: bool = False, colour: Optional[Dict[str, Any]] = None,
               dv_profile: Optional[int] = None, bad_subs: Optional[List[int]] = None) -> List[str]:
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
    
    # qsv without a CPU filter: decode, 10-bit conversion and encode all on the GPU (about 5x faster)
    gpu_path = hw_decode and encoder == "qsv" and not filter
    if gpu_path:
        args.extend(["-hwaccel", "qsv", "-hwaccel_output_format", "qsv"])
    if src.lower().endswith((".avi", ".wmv")):
        # packed B-frames / packets without timestamps (as for the samples): without them the AV1 copy of
        # Knights of the Zodiac S05 came out "0 s long"
        args.extend(["-fflags", "+genpts"])
    args.extend(["-i", src])

    # Map video stream
    args.extend(["-map", f"0:v:{video_index}"])
    
    # Map audio and subtitle streams conditionally
    args.extend(["-map", "0:a?"])
    args.extend(["-map", "0:s?"])
    for i in bad_subs or []:
        args.extend(["-map", f"-0:s:{i}"])
    
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
    if gpu_path:
        # frames stay in GPU memory: convert with vpp_qsv instead of a CPU pixel format
        i = enc_args.index("-pix_fmt")
        enc_args = enc_args[:i] + ["-vf", "vpp_qsv=format=p010"] + enc_args[i + 2:]
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
    args.extend(hdr_args(colour, dv_profile))
    
    # MPEG-TS cannot carry AV1 properly (and "The Wretched (2019).ts" is Matroska inside): the copy is Matroska
    if dst.lower().endswith(".ts"):
        args.extend(["-f", "matroska"])

    # Add mov_text subtitle codec for mp4/m4v
    if dst.endswith((".mp4", ".m4v")):
        args.extend(["-c:s", "mov_text"])
    
    # Progress output
    args.extend(["-progress", "pipe:1"])
    args.extend(["-nostats"])
    args.extend(["-y"])
    args.append(dst)
    
    return args


VMAF_MAX_HEIGHT = 1080  # VMAF's default model is made for 1080p; 4K is scored at 1080p (4x less memory and work)
# every 4th frame is scored; every 8th saved some CPU but moved the scores up by 0.3-1.1 on real episodes, so the
# search would have picked smaller, slightly worse encodes
VMAF_SUBSAMPLE = 4


# the kk-vmafx base image puts an ffmpeg with libvmaf_sycl (VMAF on an Intel Arc GPU) here; RX_VMAF_FFMPEG overrides
VMAF_GPU_FFMPEG = os.environ.get("RX_VMAF_FFMPEG", "/opt/vmafx/bin/ffmpeg")


def vmaf_cmd(encoded: str, ref: str, threads: int, log: Optional[str] = None, height: Optional[int] = None,
             gpu: bool = False, subsample: Optional[int] = None, qsv_device: Optional[str] = None) -> List[str]:
    """Build VMAF comparison command.

    Args:
        encoded: Encoded file path.
        ref: Reference file path.
        threads: Number of threads for VMAF analysis.
        log: Optional path to write VMAF CSV log.
        gpu: score with libvmaf_sycl on the GPU (VMAF_GPU_FFMPEG). It always scores every frame: its n_subsample
            above 1 is broken (scores ~99.9 for anything), and every frame on the GPU still costs less CPU than every
            4th frame on the CPU.
        subsample: CPU only, score every n-th frame (default VMAF_SUBSAMPLE).
        qsv_device: with gpu, the A580 also decodes the AV1 encode (an av1_qsv encode is 10-bit p010): same scores to
            the last digit, ~3-4 s less CPU per 20 s 1080p sample than dav1d on the CPU.

    Returns:
        List of ffmpeg arguments.
    """
    # 4K pairs held at full size (10-bit, two decoders, libvmaf's buffers) used 1.4-1.7 GB per check and the kernel
    # killed them at the container's memory limit: above 1080p both pictures are scaled down first
    scale = f"scale=-2:{VMAF_MAX_HEIGHT}:flags=bicubic," if height and height > VMAF_MAX_HEIGHT else ""
    vmaf = "libvmaf_sycl" if gpu else "libvmaf"
    n = 1 if gpu else (subsample or VMAF_SUBSAMPLE)
    hw_in = gpu and qsv_device
    download = "hwdownload,format=p010le," if hw_in else ""
    lavfi = (f"[0:v]{download}setpts=PTS-STARTPTS,{scale}format=yuv420p[d];[1:v]setpts=PTS-STARTPTS,{scale}format=yuv420p[r];"
             f"[d][r]{vmaf}=n_threads={threads}:n_subsample={n}")
    if log is not None:
        lavfi += ":log_fmt=csv:log_path=" + log
    return [
        VMAF_GPU_FFMPEG if gpu else "ffmpeg",
        "-nostdin",
        "-nostats",  # the score is logged at info level; progress lines only buried the real error
    ] + (["-init_hw_device", f"qsv=hw:{qsv_device}", "-hwaccel", "qsv", "-hwaccel_output_format", "qsv",
          "-c:v", "av1_qsv"] if hw_in else []) + [
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
                         filter: Optional[str] = None, ten_bit: bool = False) -> List[str]:
    """Lossless reference decoded straight from the source (accurate seek), so a sample cut by
    stream copy that starts between keyframes cannot break the decode. With a filter (deband) the
    reference is the filtered picture: VMAF then measures only the compression loss, not the
    intended clean-up. It is written once per sample and read back by every trial encode and every
    VMAF run, so the codec matters: ffv1 took ~24 s of CPU to write and ~21 s to read per 20 s of
    1080p. Ut Video (8-bit sources) reads in ~2 s; it has no 10-bit mode, so 10-bit sources get
    FFV Huffyuv, which keeps all 10 bits and reads in ~6 s. Both are lossless, so scores and the
    chosen quality stay the same."""
    codec = ["-c:v", "ffvhuff"] if ten_bit else ["-c:v", "utvideo", "-pix_fmt", "yuv420p"]
    return (["ffmpeg", "-nostdin", "-v", "error", "-ss", str(start), "-i", src, "-t", str(length),
             "-map", f"0:v:{video_index}", "-an", "-sn"] + filter_args(filter)
            + codec + ["-y", ref])
