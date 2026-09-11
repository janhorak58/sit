import json
import struct
import wave

import pytest

from transcriber import recorder as recorder_module
from transcriber.errors import AppError

# A Bluetooth headset in A2DP: pactl publishes the input source, but the
# active profile has no capture device, so the source only returns silence.
BT_SOURCE = {
    "name": "bluez_input.2C:BE:EE:87:71:4B",
    "description": "Nothing Ear (a)",
    "properties": {"device.name": "bluez_card.2C_BE_EE_87_71_4B"},
}
BT_CARD = {
    "name": "bluez_card.2C_BE_EE_87_71_4B",
    "active_profile": "a2dp-sink",
    "profiles": {
        "off": {"sinks": 0, "sources": 0, "available": True},
        "a2dp-sink": {"sinks": 1, "sources": 0, "available": True},
        "headset-head-unit-cvsd": {"sinks": 1, "sources": 1, "available": True},
        "headset-head-unit": {"sinks": 1, "sources": 1, "available": True},
    },
}
BUILTIN_SOURCE = {
    "name": "alsa_input.pci-0000_c4_00.6.HiFi__Mic1__source",
    "description": "Digital Microphone",
    "properties": {"device.name": "alsa_card.pci-0000_c4_00.6"},
}
BUILTIN_CARD = {
    "name": "alsa_card.pci-0000_c4_00.6",
    "active_profile": "HiFi",
    "profiles": {"HiFi": {"sinks": 1, "sources": 2, "available": True}},
}
SPEAKERS_SINK = {
    "name": "alsa_output.pci-0000_c4_00.6.HiFi__Speaker__sink",
    "description": "Built-in Speakers",
    "properties": {},
}
HEADPHONES_SINK = {
    "name": "bluez_output.2C_BE_EE_87_71_4B.1",
    "description": "Nothing Ear (a)",
    "properties": {},
}


def fake_pactl(monkeypatch, sources, cards, calls=None):
    def pactl(*args):
        if calls is not None:
            calls.append(args)
        if args[:1] == ("get-default-source",):
            return sources[0]["name"] if sources else ""
        if args[:1] == ("get-default-sink",):
            return SPEAKERS_SINK["name"]
        if args == ("-f", "json", "list", "sinks"):
            return json.dumps([SPEAKERS_SINK, HEADPHONES_SINK])
        if args == ("-f", "json", "list", "sources"):
            return json.dumps(sources)
        if args == ("-f", "json", "list", "cards"):
            return json.dumps(cards)
        if args[:1] == ("set-card-profile",):
            card = next(c for c in cards if c["name"] == args[1])
            card["active_profile"] = args[2]
            return ""
        if args[:1] == ("load-module",):
            return "42"
        return ""

    monkeypatch.setattr(recorder_module, "pactl", pactl)


def test_microphone_without_capture_profile_is_unusable_with_the_manual_fix(monkeypatch):
    fake_pactl(monkeypatch, [BT_SOURCE, BUILTIN_SOURCE], [dict(BT_CARD), BUILTIN_CARD])

    devices = {mic["id"]: mic for mic in recorder_module.microphones()}

    # A2DP publishes the source but captures silence, and the recorder no
    # longer switches the profile itself, so it must not be selectable.
    assert devices[BT_SOURCE["name"]]["available"] is False
    assert "headset mode" in devices[BT_SOURCE["name"]]["note"]
    assert devices[BUILTIN_SOURCE["name"]]["available"] is True
    assert devices[BUILTIN_SOURCE["name"]]["note"] is None


def test_working_bluetooth_microphone_warns_about_narrowband_quality(monkeypatch):
    card = dict(BT_CARD, active_profile="headset-head-unit")
    fake_pactl(monkeypatch, [BT_SOURCE], [card])

    device, = recorder_module.microphones()

    assert device["available"] is True
    assert device["bluetooth"] is True
    assert "narrowband" in device["note"]


def test_microphone_with_no_capture_profile_at_all_is_unusable(monkeypatch):
    card = dict(BT_CARD, profiles={"a2dp-sink": {"sinks": 1, "sources": 0, "available": True}})
    fake_pactl(monkeypatch, [BT_SOURCE], [card])

    device, = recorder_module.microphones()

    assert device["available"] is False
    assert "no microphone" in device["note"]


def test_recording_never_switches_card_profiles(monkeypatch, tmp_path):
    card = dict(BT_CARD)
    calls, ffmpeg = [], []
    fake_pactl(monkeypatch, [BT_SOURCE, BUILTIN_SOURCE], [card, BUILTIN_CARD], calls)
    monkeypatch.setattr(
        recorder_module.subprocess, "Popen",
        lambda args, **kwargs: (ffmpeg.extend(args), DummyProcess())[1],
    )
    monkeypatch.setattr(recorder_module.subprocess, "run", lambda *a, **k: None)

    recorder = recorder_module.Recorder(tmp_path / "current.wav")

    with pytest.raises(AppError, match="headset mode"):
        recorder.start(BT_SOURCE["name"])

    recorder.start(BUILTIN_SOURCE["name"])

    assert not [call for call in calls if call[:1] == ("set-card-profile",)]
    assert card["active_profile"] == "a2dp-sink"
    assert ["-f", "pulse", "-i", BUILTIN_SOURCE["name"]] == ffmpeg[ffmpeg.index("-f"):ffmpeg.index("-f") + 4]
    # The two capture sources must stay on their own channels, never summed.
    assert any("[mic][sys]amerge=inputs=2" in arg for arg in ffmpeg)
    assert ["-ac", "2"] == ffmpeg[ffmpeg.index("-ac"):ffmpeg.index("-ac") + 2]


def test_recording_takes_system_audio_from_the_chosen_output(monkeypatch, tmp_path):
    calls, ffmpeg = [], []
    fake_pactl(monkeypatch, [BUILTIN_SOURCE], [BUILTIN_CARD], calls)
    monkeypatch.setattr(
        recorder_module.subprocess, "Popen",
        lambda args, **kwargs: (ffmpeg.extend(args), DummyProcess())[1],
    )
    monkeypatch.setattr(recorder_module.subprocess, "run", lambda *a, **k: None)
    recorder = recorder_module.Recorder(tmp_path / "current.wav")

    recorder.start(BUILTIN_SOURCE["name"], HEADPHONES_SINK["name"])

    loopback, = [call for call in calls if call[:2] == ("load-module", "module-loopback")]
    assert f"source={HEADPHONES_SINK['name']}.monitor" in loopback


def test_recording_refuses_an_output_that_is_gone(monkeypatch, tmp_path):
    fake_pactl(monkeypatch, [BUILTIN_SOURCE], [BUILTIN_CARD])
    monkeypatch.setattr(
        recorder_module.subprocess, "Popen",
        lambda args, **kwargs: DummyProcess(),
    )
    recorder = recorder_module.Recorder(tmp_path / "current.wav")

    with pytest.raises(AppError, match="output is not available"):
        recorder.start(BUILTIN_SOURCE["name"], "alsa_output.unplugged")


def test_outputs_list_marks_the_default_sink(monkeypatch):
    fake_pactl(monkeypatch, [BUILTIN_SOURCE], [BUILTIN_CARD])

    sinks = {sink["id"]: sink for sink in recorder_module.outputs()}

    assert sinks[SPEAKERS_SINK["name"]]["default"] is True
    assert sinks[HEADPHONES_SINK["name"]]["default"] is False
    assert sinks[HEADPHONES_SINK["name"]]["label"] == HEADPHONES_SINK["description"]


def test_recording_refuses_a_microphone_that_is_gone(monkeypatch, tmp_path):
    fake_pactl(monkeypatch, [BUILTIN_SOURCE], [BUILTIN_CARD])
    monkeypatch.setattr(
        recorder_module.subprocess,
        "Popen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ffmpeg started")),
    )
    monkeypatch.setattr(recorder_module.subprocess, "run", lambda *a, **k: None)

    recorder = recorder_module.Recorder(tmp_path / "current.wav")

    with pytest.raises(AppError, match="not available"):
        recorder.start("alsa_input.unplugged")


class DummyProcess:
    returncode = None

    def poll(self):
        return None

    def send_signal(self, _signal):
        self.returncode = 0

    def wait(self, timeout=None):
        return 0


def write_wav(path, samples):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def test_capture_levels_separate_silence_from_speech(tmp_path):
    silent = tmp_path / "silent.wav"
    loud = tmp_path / "loud.wav"
    write_wav(silent, [0] * 16000)
    write_wav(loud, [0] * 8000 + [16000, -16000] * 4000)

    quiet = recorder_module.capture_levels(silent)
    speech = recorder_module.capture_levels(loud)

    assert quiet["peak"] == 0.0
    assert len(quiet["bars"]) == recorder_module.LEVEL_BARS
    assert speech["peak"] == pytest.approx(0.488, abs=0.01)
    assert max(speech["bars"]) == speech["peak"]

def test_capture_levels_ignore_constant_laptop_microphone_bias(tmp_path):
    biased = tmp_path / "biased.wav"
    write_wav(biased, [8000] * 16000)

    assert recorder_module.capture_levels(biased)["peak"] == 0.0


def test_capture_levels_tolerate_a_missing_or_empty_capture(tmp_path):
    assert recorder_module.capture_levels(tmp_path / "nope.wav")["peak"] == 0.0
    header_only = tmp_path / "header.wav"
    write_wav(header_only, [])
    assert recorder_module.capture_levels(header_only)["peak"] == 0.0
