from abc import ABC, abstractmethod

from langchain_core.documents import Document


class BaseDocumentParser(ABC):
    """Common interface for document parsers."""

    @staticmethod
    def normalize_text(raw_text: str | None) -> tuple[str, bool]:
        """Normalize extracted document text into clean string and is_empty flag."""
        text = (
            (raw_text or "")
            .replace("\x00", "")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .strip()
        )
        return text, not bool(text)

    @abstractmethod
    def parse(self, documents: list[Document]) -> list[Document]:
        """Parse loaded documents and return normalized documents."""
        raise NotImplementedError
    
# uv run python -c "from backend.app.ingestion.parsers.base import BaseDocumentParser; print('BaseDocumentParser imported successfully')"