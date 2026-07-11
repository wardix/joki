import os
import json
import subprocess
from joki.display import _Spinner


def handle_camera_capture(args):
    device = args.get("device", "/dev/video0")
    path = args.get("path", "/tmp/joki_cam.jpg")
    resolution = args.get("resolution", "640x480")
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    # Try fswebcam first, then ffmpeg
    r = subprocess.run(["fswebcam", "-d", device, "-r", resolution, path],
                       capture_output=True, text=True, timeout=15)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return f"Camera capture saved: {path} ({os.path.getsize(path)} bytes)"
    r2 = subprocess.run(["ffmpeg",
                         "-f",
                         "v4l2",
                         "-i",
                         device,
                         "-vframes",
                         "1",
                         "-s",
                         resolution,
                         "-y",
                         path],
                        capture_output=True,
                        text=True,
                        timeout=15)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return f"Camera capture saved: {path} ({os.path.getsize(path)} bytes)"
    return "Gagal capture kamera. Install fswebcam: sudo apt install fswebcam"


def handle_audio_info(args):
    path = args.get("path", "")
    r = subprocess.run(["ffprobe",
                        "-v",
                        "quiet",
                        "-print_format",
                        "json",
                        "-show_format",
                        "-show_streams",
                        path],
                       capture_output=True,
                       text=True,
                       timeout=30)
    if r.returncode != 0:
        return f"Error: {r.stderr or 'ffprobe not found. Install: sudo apt install ffmpeg'}"
    data = json.loads(r.stdout)
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    lines = [f"  File: {path}"]
    lines.append(f"  Duration: {fmt.get('duration', 'N/A')}s")
    lines.append(f"  Size: {fmt.get('size', 'N/A')} bytes")
    lines.append(f"  Bitrate: {fmt.get('bit_rate', 'N/A')} bps")
    for s in streams:
        if s.get("codec_type") == "audio":
            lines.append(f"  Codec: {s.get('codec_name', 'N/A')}")
            lines.append(f"  Sample Rate: {s.get('sample_rate', 'N/A')} Hz")
            lines.append(f"  Channels: {s.get('channels', 'N/A')}")
            lines.append(
                f"  Language: {s.get('tags', {}).get('language', 'N/A')}")
    return "\n".join(lines)


def handle_audio_transcribe(args):
    path = args.get("path", "")
    model_size = args.get("model", "base")
    language = args.get("language", "")
    if not os.path.exists(path):
        return f"File not found: {path}"
    try:
        import whisper
    except ImportError:
        return "whisper tidak terinstall. Install: pip install openai-whisper"
    with _Spinner(f"Transkripsi audio (model: {model_size})..."):
        model = whisper.load_model(model_size)
        opts = {"language": language} if language else {}
        result = model.transcribe(path, **opts)
    text = result.get("text", "").strip()
    detected = result.get("language", "")
    segments = result.get("segments", [])
    duration = segments[-1]["end"] if segments else 0
    info = f"  Bahasa: {detected.upper() if detected else 'auto'}"
    info += f"\n  Durasi: {duration:.1f}s" if duration else ""
    info += f"\n  Teks ({len(text)} chars):\n{text}"
    return info


def handle_video_info(args):
    path = args.get("path", "")
    r = subprocess.run(["ffprobe",
                        "-v",
                        "quiet",
                        "-print_format",
                        "json",
                        "-show_format",
                        "-show_streams",
                        path],
                       capture_output=True,
                       text=True,
                       timeout=30)
    if r.returncode != 0:
        return f"Error: {r.stderr or 'ffprobe not found. Install: sudo apt install ffmpeg'}"
    data = json.loads(r.stdout)
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    lines = [f"  File: {path}"]
    lines.append(f"  Duration: {fmt.get('duration', 'N/A')}s")
    lines.append(f"  Size: {fmt.get('size', 'N/A')} bytes")
    lines.append(f"  Bitrate: {fmt.get('bit_rate', 'N/A')} bps")
    for s in streams:
        codec_type = s.get("codec_type", "unknown")
        lines.append(f"  [{codec_type}]")
        lines.append(f"    Codec: {s.get('codec_name', 'N/A')}")
        if codec_type == "video":
            lines.append(
                f"    Resolution: {s.get('width', 'N/A')}x{s.get('height', 'N/A')}")
            rate = s.get('r_frame_rate', '0/1')
            if '/' in rate:
                try:
                    num, den = rate.split('/')
                    fps = float(int(num) / int(den)) if int(den) else 0.0
                except ValueError:
                    fps = 0.0
            else:
                try:
                    fps = float(rate)
                except ValueError:
                    fps = 0.0
            lines.append(f"    FPS: {fps:.2f}")
            lines.append(f"    Pixel Format: {s.get('pix_fmt', 'N/A')}")
        elif codec_type == "audio":
            lines.append(f"    Sample Rate: {s.get('sample_rate', 'N/A')} Hz")
            lines.append(f"    Channels: {s.get('channels', 'N/A')}")
    return "\n".join(lines)


def handle_video_extract(args):
    path = args.get("path", "")
    mode = args.get("mode", "")
    if not mode:
        return "Error: Parameter 'mode' wajib diisi. Contoh: video_extract(path=\"video.mp4\", mode=\"thumbnail\")"
    output_dir = args.get("output_dir", "/tmp/joki_video_extract")
    os.makedirs(output_dir, exist_ok=True)
    if mode == "thumbnail":
        out = os.path.join(output_dir, "thumbnail.jpg")
        r = subprocess.run(
            ["ffmpeg", "-i", path, "-vframes", "1", "-q:v", "2", "-y", out],
            capture_output=True, text=True, timeout=30
        )
        if os.path.exists(out):
            return f"Thumbnail saved: {out} ({os.path.getsize(out)} bytes)"
        return f"Error: {r.stderr}"
    elif mode == "timestamp":
        ts = args.get("timestamp", 0)
        out = os.path.join(output_dir, f"frame_{ts}s.jpg")
        r = subprocess.run(["ffmpeg",
                            "-ss",
                            str(ts),
                            "-i",
                            path,
                            "-vframes",
                            "1",
                            "-q:v",
                            "2",
                            "-y",
                            out],
                           capture_output=True,
                           text=True,
                           timeout=30)
        if os.path.exists(out):
            return f"Frame at {ts}s saved: {out} ({os.path.getsize(out)} bytes)"
        return f"Error: {r.stderr}"
    elif mode == "frames":
        fps = args.get("fps", 1)
        out_pattern = os.path.join(output_dir, "frame_%04d.jpg")
        r = subprocess.run(
            ["ffmpeg", "-i", path, "-vf", f"fps={fps}", "-q:v", "2", "-y", out_pattern],
            capture_output=True, text=True, timeout=60
        )
        count = len([f for f in os.listdir(output_dir)
                    if f.startswith("frame_")])
        return f"Extracted {count} frames to {output_dir}/ (fps={fps})"
    return f"Unknown mode: {mode}"
