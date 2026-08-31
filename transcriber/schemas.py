"""Request bodies for the JSON API."""

from pydantic import BaseModel


class YoutubeReq(BaseModel):
    url: str
    folder: str = ""
    filename: str = ""


class StopReq(BaseModel):
    folder: str = ""
    filename: str = ""


class TranscribeReq(BaseModel):
    path: str
    language: str = "cs"
    num_speakers: int | None = None


class MkdirReq(BaseModel):
    folder: str


class MoveReq(BaseModel):
    folder: str = ""
    name: str
    to_folder: str = ""
    to_name: str
    wav_path: str | None = None


class DeleteReq(BaseModel):
    path: str
