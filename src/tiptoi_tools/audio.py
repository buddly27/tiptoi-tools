import time

import miniaudio


class AudioPlaybackError(Exception):
    """Raised when audio playback fails."""


def play_audio(data: bytes, verbose: bool = False) -> None:
    """
    Play audio data using miniaudio.

    Args:
        data: Raw audio bytes (decrypted)
        verbose: If True, show additional playback info

    Raises:
        AudioPlaybackError: If playback fails
    """
    try:
        # Decode audio - miniaudio auto-detects format
        decoded = miniaudio.decode(data, output_format=miniaudio.SampleFormat.SIGNED16)

        duration = decoded.num_frames / decoded.sample_rate

        if verbose:
            print(
                f"    Audio: {decoded.sample_rate}Hz, "
                f"{decoded.nchannels}ch, {duration:.2f}s"
            )

        # Start playback device
        device = miniaudio.PlaybackDevice(
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=decoded.nchannels,
            sample_rate=decoded.sample_rate,
        )

        # Use stream_memory for proper playback
        stream = miniaudio.stream_memory(
            data,
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=decoded.nchannels,
            sample_rate=decoded.sample_rate,
        )

        device.start(stream)

        # Wait for playback to complete
        time.sleep(duration + 0.2)
        device.close()

    except miniaudio.DecodeError as e:
        raise AudioPlaybackError(f"Failed to decode audio: {e}") from None
    except miniaudio.MiniaudioError as e:
        raise AudioPlaybackError(f"Playback error: {e}") from None


def get_player_info() -> str:
    """Return information about the audio backend."""
    backends = miniaudio.get_enabled_backends()
    return f"miniaudio (backends: {', '.join(b.name for b in backends)})"
