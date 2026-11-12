from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from backend.app.core.config import settings
from backend.app.core.exceptions import InvalidUploadError


class DocumentFileStorage:
    """Stores accepted uploads until the independent worker consumes them."""

    def __init__(self) -> None:
        self.root = Path(settings.chroma_persist_directory).parent / "queued_uploads"

    async def save(self, file: UploadFile, filename: str) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        destination = self.root / f"{uuid4()}_{filename}"
        total_bytes = 0
        try:
            with destination.open("wb") as stream:
                while chunk := await file.read(1024 * 1024):
                    total_bytes += len(chunk)
                    if total_bytes > settings.upload_max_bytes:
                        raise InvalidUploadError(
                            message="The uploaded document is too large.",
                            details={"maximum_bytes": settings.upload_max_bytes, "received_bytes": total_bytes},
                        )
                    stream.write(chunk)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        if total_bytes == 0:
            destination.unlink(missing_ok=True)
            raise InvalidUploadError(message="The uploaded document is empty.")
        return destination
