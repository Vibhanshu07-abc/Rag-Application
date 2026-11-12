from langchain_core.documents import Document

from backend.app.core.exceptions import DocumentParsingError
from backend.app.core.logging import get_logger
from backend.app.ingestion.parsers.base import BaseDocumentParser


logger = get_logger(__name__)


class DOCXDocumentParser(BaseDocumentParser):
    """Normalize loaded DOCX documents and validate extracted text."""

    def parse(self, documents: list[Document]) -> list[Document]:
        if not documents:
            raise DocumentParsingError(
                message="No loaded DOCX documents were provided."
            )

        logger.info(
            "docx_parsing_started",
            document_count=len(documents),
        )

        parsed_documents: list[Document] = []
        empty_document_count = 0

        for position, document in enumerate(documents, start=1):
            if not isinstance(document, Document):
                raise DocumentParsingError(
                    message="An invalid document object was provided.",
                    details={"position": position},
                )

            text, is_empty = self.normalize_text(document.page_content)

            if is_empty:
                empty_document_count += 1

            metadata = dict(document.metadata)
            metadata.update(
                {
                    "parser": "docx2txt",
                    "content_length": len(text),
                    "is_empty": is_empty,
                }
            )

            parsed_documents.append(
                Document(
                    page_content=text,
                    metadata=metadata,
                )
            )

        if empty_document_count == len(parsed_documents):
            raise DocumentParsingError(
                message="No extractable text was found in the DOCX file.",
                details={
                    "document_count": len(parsed_documents),
                    "empty_document_count": empty_document_count,
                },
            )

        logger.info(
            "docx_parsing_completed",
            document_count=len(parsed_documents),
            empty_document_count=empty_document_count,
        )

        return parsed_documents
