"""Request bodies for the JSON API."""

from pydantic import BaseModel



class StartReq(BaseModel):
    language: str = "cs"
    live: bool = False
    microphone: str = ""
    # Sink whose monitor is recorded as the system-audio track; "" = default sink.
    output: str = ""


class StopReq(BaseModel):
    folder: str = ""
    filename: str = ""


class TranscribeReq(BaseModel):
    path: str
    language: str = "cs"
    num_speakers: int | None = None


class DiarizeReq(BaseModel):
    path: str
    num_speakers: int | None = None


class MkdirReq(BaseModel):
    folder: str


class FolderRenameReq(BaseModel):
    path: str
    name: str


class MoveReq(BaseModel):
    folder: str = ""
    name: str
    to_folder: str = ""
    to_name: str
    wav_path: str | None = None


class DeleteReq(BaseModel):
    path: str


class SuggestReq(BaseModel):
    project: str


class SummaryReq(BaseModel):
    path: str
    project: str = ""
    preferences: dict | None = None


class SegmentEdit(BaseModel):
    text: str
    speaker: str | None = None


class UpdateTranscriptReq(BaseModel):
    path: str
    segments: list[SegmentEdit]


class RenameSpeakersReq(BaseModel):
    path: str
    names: dict[str, str]

class PreferencesReq(BaseModel):
    preferences: dict


class ConnectionsReq(BaseModel):
    connections: dict