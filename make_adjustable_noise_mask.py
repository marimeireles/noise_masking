"""
A Python script to generate noise using SOX based on audio statistics.
The generated noise will adjust its volume based on the system's volume setting.
"""

import numpy as np
import os
import platform
import signal
import shutil
import subprocess
import sys
import time
from datetime import datetime


if sys.platform.startswith("linux"):
    import pulsectl

# Global variable to store the noise process (if any)
noise_process = None
t = time.localtime()
time_str = f"{t.tm_year}_{t.tm_mon}_{t.tm_mday}_{t.tm_hour}_{t.tm_min}"

def signal_handler(sig, frame):
    raise KeyboardInterrupt

signal.signal(signal.SIGINT, signal_handler)

def get_system_volume():
    result = subprocess.run(["amixer", "sget", "Master"], stdout=subprocess.PIPE)
    output = result.stdout.decode()
    volume = int(output.split("[")[1].split("%")[0])
    is_muted = "off" in output
    return volume, is_muted


def set_system_volume(volume):
    """Set the system volume. Volume should be an integer between 0 and 100."""
    result = subprocess.run(["amixer", "sget", "Master"], stdout=subprocess.PIPE)
    output = result.stdout.decode()
    volume = int(output.split("[")[1].split("%")[0])
    is_muted = "off" in output
    return volume, is_muted


def record_audio(duration=10):
    subprocess.run(f"sox -d data/input.wav trim 0 {duration}", shell=True)
    # copy as a record for the future
    time_str = str(datetime.now().timestamp())
    shutil.copy("data/input.wav", f"data/input_{time_str}.wav")


# Function to record audio using 'arecord'
def record_audio_osx(duration=10):
    print(f"Recording {duration} seconds of audio...")
    filename = f"data/input.wav"
    subprocess.run(f"sox -d {filename} trim 0 {duration}", shell=True)
    print(f"Audio recorded and saved as {filename}")


# Function to generate a spectrogram from an audio file using SOX
def generate_spectrogram():
    print("Generating spectrogram...")
    subprocess.run(
        f"sox data/input.wav -n spectrogram -o data/spectrum.png", shell=True
    )


# Function to fetch audio statistics using SOX
def fetch_audio_stats():
    print("Fetching audio statistics...")
    subprocess.run(
        "sox data/input.wav -n stat -freq 2>&1 | sed -n -e :a -e '1,15!{P;N;D;};N;ba' > data/data.txt",
        shell=True,
    )


def db_to_linear(dB):
    """Convert dB value to linear scale factor"""
    return 10 ** (dB / 20)


def get_new_volume(volume_percentage, is_muted):
    volume_adjustment = 0
    return 0 if is_muted else (volume_percentage / 100.0)


def set_volume(volume_percentage, is_muted, pulse, sox_sink_input):
    new_volume = get_new_volume(volume_percentage, is_muted)

    new_volume_info = pulsectl.PulseVolumeInfo(
        new_volume, channels=len(sox_sink_input.volume.values)
    )

    pulse.volume_set(sox_sink_input, new_volume_info)


def identify_os():
    if sys.platform.startswith("darwin"):
        return "OS X (macOS)"
    elif sys.platform.startswith("linux"):
        return "Linux"
    else:
        return "Unsupported OS, feel free to open an issue here https://github.com/morganrivers/noise_masking"


def play_noise_osx(mean, standard_deviation, volume_dB):
    """Play noise using sox with specified parameters and wait until termination."""
    global noise_process
    volume_adjustment = volume_dB  # Use the calculated volume or set a preferred level
    command = f"play -n synth noise band {mean} {standard_deviation} vol {volume_adjustment}dB"
    print("Playing noise. Press Ctrl+C to stop.")
    # Launch the noise process in its own process group so we can kill it later
    noise_process = subprocess.Popen(command, shell=True, preexec_fn=os.setsid)
    try:
        # Wait for the process to complete (it normally runs indefinitely)
        noise_process.wait()
    except KeyboardInterrupt:
        # If interrupted, kill the entire process group of noise_process
        try:
            os.killpg(os.getpgid(noise_process.pid), signal.SIGTERM)
        except Exception as e:
            print("Error terminating noise process:", e)
        raise

def play_and_adjust_volume(mean, standard_deviation, initial_volume_dB):
    global noise_process
    with pulsectl.Pulse("volume-adjuster") as pulse:
        volume_percentage, is_muted = get_system_volume()  # Get initial system volume

        time.sleep(0.2)  # small pause

        # Check if the SOX process exists in PulseAudio
        sox_sink_input = next(
            (
                si
                for si in pulse.sink_input_list()
                if si.proplist.get("application.name") == "ALSA plug-in [sox]"
            ),
            None,
        )

        # If the SOX process isn't already playing, start it
        if not sox_sink_input:
            reduced_volume = initial_volume_dB - 20
            command = (
                f"play -n trim 0.0 2.0 : synth noise band {mean} {standard_deviation} "
                f"vol {reduced_volume}dB > /dev/null 2>&1"
            )
            # Launch with its own process group.
            noise_process = subprocess.Popen(command, shell=True, preexec_fn=os.setsid)
            time.sleep(0.2)
            sox_sink_input = next(
                (
                    si
                    for si in pulse.sink_input_list()
                    if si.proplist.get("application.name") == "ALSA plug-in [sox]"
                ),
                None,
            )
            if not sox_sink_input:
                print("Couldn't find sox stream in PulseAudio.")
                return

        # Set the playback volume based on the system volume initially.
        set_volume(volume_percentage, is_muted, pulse, sox_sink_input)

        # Continuously adjust playback volume until interrupted.
        try:
            while True:
                volume_percentage, is_muted = get_system_volume()
                set_volume(volume_percentage, is_muted, pulse, sox_sink_input)
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("Exiting volume-adjust loop.")
            # Kill the noise process if it's running.
            if noise_process:
                try:
                    os.killpg(os.getpgid(noise_process.pid), signal.SIGTERM)
                except Exception as e:
                    print("Error terminating noise process:", e)
            raise

def main():
    try:
        # (OS detection, directory creation, recording, spectrogram, etc.)
        if not os.path.exists("data"):
            os.makedirs("data")
        if os.path.isfile("data/data.txt"):
            while True:
                user_input = input("Record new audio or use the old one? [r/o]\n")
                if user_input == "r":
                    if sys.platform.startswith("darwin"):
                        record_audio_osx()
                    elif sys.platform.startswith("linux"):
                        record_audio()
                    break
                elif user_input == "o":
                    print("Using old audio...")
                    break
                else:
                    print('Type "r" for record or "o" for old. Please try again.')
        else:
            record_audio()

        generate_spectrogram()
        fetch_audio_stats()

        frequency, amplitude = np.loadtxt("data/data.txt", unpack=True)
        mean_amplitude = np.mean(amplitude)
        volume_dB = 10 * np.log10(mean_amplitude)
        if np.sum(amplitude) == 0:
            raise ValueError("Error: No audio input signal.")
        mean = np.average(frequency, weights=amplitude)
        standard_deviation = np.sqrt(np.average((frequency - mean) ** 2, weights=amplitude))

        print("\nMean Frequency:", mean)
        print("Standard Deviation:", standard_deviation)
        print("Volume (dB):", volume_dB)
        print("Press Ctrl+C to exit gracefully.")

        if sys.platform.startswith("darwin"):
            play_noise_osx(mean, standard_deviation, volume_dB)
        elif sys.platform.startswith("linux"):
            play_and_adjust_volume(mean, standard_deviation, volume_dB)
    except KeyboardInterrupt:
        print("\nExiting gracefully...")
        # As a fallback, try to kill any leftover noise processes.
        if noise_process:
            try:
                os.killpg(os.getpgid(noise_process.pid), signal.SIGTERM)
            except Exception as e:
                print("Error terminating noise process during exit:", e)
        sys.exit(0)

if __name__ == "__main__":
    main()