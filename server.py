import requests
import os
import warnings
import time
import io
import threading
import wave
import json
from collections import deque
from typing import Optional

warnings.filterwarnings("ignore")
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "hide"
import pygame

from mcp.server.fastmcp import FastMCP

# Create MCP server instance
mcp = FastMCP("Speak Server")

# --- Playback state ---
_queue = deque()  # Items: {"id": int, "source": str, "text": str, "audio": bytes, "volume": float, "duration": float}
_queue_lock = threading.Lock()
_now_playing = None  # Current item being played
_stop_event = threading.Event()
_next_id = 0
_id_lock = threading.Lock()


def _next_playback_id():
    global _next_id
    with _id_lock:
        _next_id += 1
        return _next_id


def _estimate_duration(audio_bytes: bytes) -> float:
    """Estimate WAV duration in seconds from raw bytes."""
    try:
        buf = io.BytesIO(audio_bytes)
        with wave.open(buf, 'rb') as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return 0.0


def _playback_worker():
    """Background worker that processes the playback queue."""
    global _now_playing

    if not pygame.mixer.get_init():
        pygame.mixer.init()

    while True:
        # Wait for items
        item = None
        while item is None:
            with _queue_lock:
                if _queue:
                    item = _queue.popleft()
            if item is None:
                time.sleep(0.05)

        _now_playing = item
        _stop_event.clear()

        temp_file = None
        try:
            pygame.mixer.music.set_volume(item["volume"])

            try:
                audio_data = io.BytesIO(item["audio"])
                pygame.mixer.music.load(audio_data)
            except Exception:
                temp_file = f"speak_{int(time.time())}.wav"
                with open(temp_file, "wb") as f:
                    f.write(item["audio"])
                pygame.mixer.music.load(temp_file)

            pygame.mixer.music.play()

            while pygame.mixer.music.get_busy():
                if _stop_event.is_set():
                    pygame.mixer.music.stop()
                    break
                pygame.time.wait(50)

            if not _stop_event.is_set():
                # Let the OS audio buffer drain
                time.sleep(0.3)

        except Exception:
            pass
        finally:
            _now_playing = None
            if temp_file:
                try:
                    os.remove(temp_file)
                except Exception:
                    pass


# Start the playback worker
_worker = threading.Thread(target=_playback_worker, daemon=True)
_worker.start()


@mcp.tool()
def speak(
    text: str,
    source: Optional[str] = None,
    speaker_id: Optional[int] = 0,
    length_scale: Optional[float] = 1.1,
    noise_scale: Optional[float] = 0.667,
    noise_w_scale: Optional[float] = 0.333,
    volume: Optional[float] = 0.15
) -> str:
    """
    Convert text to speech and play it through the speakers.
    Returns immediately with queue position. Audio plays in background.

    Args:
        text: The text to convert to speech
        source: Short identifier for the caller (e.g. "TL/coding-agents-config/fix-auth").
                When provided, spoken as a brief prefix before the text.
        speaker_id: Voice speaker ID (default: 0)
        length_scale: Speech speed control (default: 1.1, lower = faster)
        noise_scale: Voice variation control (default: 0.667)
        noise_w_scale: Pronunciation variation control (default: 0.333)
        volume: Volume level from 0.01 to 1.00 (default: 0.15)

    Returns:
        JSON with playback_id, queue_position, estimated_wait_seconds
    """
    try:
        volume = max(0.01, min(1.00, volume or 0.15))

        # Prepend source prefix to spoken text
        spoken_text = text
        if source:
            spoken_text = f"From {source}: {text}"

        data = {
            "text": spoken_text,
            "speaker_id": speaker_id,
            "length_scale": length_scale,
            "noise_scale": noise_scale,
            "noise_w_scale": noise_w_scale
        }

        response = requests.post(
            "http://localhost:5001",
            headers={"Content-Type": "application/json"},
            json=data,
            timeout=30
        )

        if response.status_code != 200:
            return f"TTS service error: HTTP {response.status_code}"

        playback_id = _next_playback_id()
        duration = _estimate_duration(response.content)

        item = {
            "id": playback_id,
            "source": source or "",
            "text": text,
            "audio": response.content,
            "volume": volume,
            "duration": duration,
        }

        with _queue_lock:
            _queue.append(item)
            queue_pos = len(_queue)
            # Estimate wait: sum durations of items ahead + currently playing remainder
            wait = sum(q["duration"] for q in _queue) - duration
            if _now_playing:
                wait += _now_playing["duration"] * 0.5  # rough estimate of remaining

        return json.dumps({
            "playback_id": playback_id,
            "queue_position": queue_pos,
            "estimated_wait_seconds": round(wait, 1),
            "audio_duration_seconds": round(duration, 1),
            "text": text,
        })

    except requests.exceptions.ConnectionError:
        return "Error: TTS service not available at localhost:5001"
    except requests.exceptions.Timeout:
        return "Error: TTS service request timed out"
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def speech_status() -> str:
    """
    Check current playback status and queue depth.

    Returns:
        JSON with now_playing info, queue depth, and queued items summary
    """
    with _queue_lock:
        queued = [{"id": q["id"], "source": q["source"], "text": q["text"][:60], "duration": round(q["duration"], 1)} for q in _queue]

    playing = None
    if _now_playing:
        playing = {
            "id": _now_playing["id"],
            "source": _now_playing["source"],
            "text": _now_playing["text"][:60],
            "duration": round(_now_playing["duration"], 1),
        }

    return json.dumps({
        "now_playing": playing,
        "queue_depth": len(queued),
        "queued": queued,
    })


@mcp.tool()
def speech_stop(clear_queue: Optional[bool] = True) -> str:
    """
    Stop current playback and optionally clear the queue.

    Args:
        clear_queue: If True (default), also clears all queued items.
                     If False, only stops the current item and continues with next.

    Returns:
        JSON with what was stopped and how many items were cleared
    """
    stopped = None
    cleared = 0

    if _now_playing:
        stopped = {"id": _now_playing["id"], "text": _now_playing["text"][:60]}
    _stop_event.set()

    if clear_queue:
        with _queue_lock:
            cleared = len(_queue)
            _queue.clear()

    return json.dumps({
        "stopped": stopped,
        "cleared_count": cleared,
    })


if __name__ == "__main__":
    mcp.run(transport='stdio')
