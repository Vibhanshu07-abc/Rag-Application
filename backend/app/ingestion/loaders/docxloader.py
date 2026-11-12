from collections.abc import Iterable
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

from langchain_community.document_loaders import Docx2txtLoader
from langchain_core.documents import Document

from backend.app.core.exceptions import (
    AppError,
    DocumentNotFoundError,
    DocumentParsingError,
    UnsupportedDocumentTypeError,
)
from backend.app.core.logging import get_logger
from backend.app.ingestion.loaders.base import BaseDocumentLoader


logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DocxLoadFailure:
    source: str
    code: str
    message: str


@dataclass(slots=True)
class DocxBatchLoadResult:
    documents: list[Document] = field(default_factory=list)
    loaded_sources: list[str] = field(default_factory=list)
    failures: list[DocxLoadFailure] = field(default_factory=list)


class DOCXDocumentLoader(BaseDocumentLoader):
    """Load one or multiple DOCX files as LangChain Documents."""

    def load(self, source: Path) -> list[Document]:
        """Load one DOCX file."""

        source = source.expanduser().resolve()

        if not source.exists() or not source.is_file():
            raise DocumentNotFoundError(details={"path": str(source)})

        if source.suffix.lower() != ".docx":
            raise UnsupportedDocumentTypeError(
                details={
                    "path": str(source),
                    "extension": source.suffix,
                }
            )

        logger.info("docx_loading_started", filename=source.name)

        try:
            file_hash = self._calculate_sha256(source)
            documents = Docx2txtLoader(str(source)).load()

            if not documents or not any(
                document.page_content.strip()
                for document in documents
            ):
                raise DocumentParsingError(
                    message="No text could be extracted from the DOCX file.",
                    details={"path": str(source)},
                )

        except AppError:
            raise

        except Exception as exc:
            raise DocumentParsingError(
                details={
                    "path": str(source),
                    "error_type": type(exc).__name__,
                }
            ) from exc

        for document in documents:
            document.metadata.update(
                {
                    "document_id": file_hash,
                    "page_id": f"{file_hash}:1",
                    "source": str(source),
                    "filename": source.name,
                    "file_type": "docx",
                    "file_hash": file_hash,
                    "file_size_bytes": source.stat().st_size,
                }
            )

        logger.info(
            "docx_loading_completed",
            filename=source.name,
            document_id=file_hash,
        )

        return documents

    def load_many(
        self,
        sources: Iterable[Path],
        *,
        continue_on_error: bool = False,
    ) -> DocxBatchLoadResult:

        unique_sources = sorted(
            {
                Path(source).expanduser().resolve()
                for source in sources
            },
            key=lambda path: str(path).lower(),
        )

        if not unique_sources:
            raise DocumentNotFoundError(
                message="No DOCX files were provided."
            )

        result = DocxBatchLoadResult()

        for source in unique_sources:
            try:
                documents = self.load(source)

                result.documents.extend(documents)
                result.loaded_sources.append(str(source))

            except AppError as exc:
                logger.error(
                    "docx_loading_failed",
                    source=str(source),
                    error_code=exc.code,
                    error_message=exc.message,
                )

                result.failures.append(
                    DocxLoadFailure(
                        source=str(source),
                        code=exc.code,
                        message=exc.message,
                    )
                )

                if not continue_on_error:
                    raise

        logger.info(
            "docx_batch_loading_completed",
            loaded_file_count=len(result.loaded_sources),
            failed_file_count=len(result.failures),
        )

        return result

    def load_directory(
        self,
        directory: Path,
        *,
        recursive: bool = False,
        continue_on_error: bool = False,
    ) -> DocxBatchLoadResult:

        directory = directory.expanduser().resolve()

        if not directory.exists() or not directory.is_dir():
            raise DocumentNotFoundError(
                message="The DOCX directory was not found.",
                details={"path": str(directory)},
            )

        pattern = "**/*" if recursive else "*"

        docx_files = [
            path
            for path in directory.glob(pattern)
            if path.is_file() and path.suffix.lower() == ".docx"
        ]

        if not docx_files:
            raise DocumentNotFoundError(
                message="No DOCX files were found in the directory.",
                details={"path": str(directory)},
            )

        return self.load_many(
            docx_files,
            continue_on_error=continue_on_error,
        )

    @staticmethod
    def _calculate_sha256(source: Path) -> str:
        """Generate a stable SHA-256 hash."""

        digest = sha256()

        with source.open("rb") as file:
            while chunk := file.read(1024 * 1024):
                digest.update(chunk)

        return digest.hexdigest()
