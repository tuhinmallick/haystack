import logging
from pathlib import Path
from typing import List, Optional, Dict, Any
from collections import defaultdict
import json
import copy

import pandas as pd

from haystack.lazy_imports import LazyImport
from haystack.nodes.file_converter.base import BaseConverter
from haystack.errors import HaystackError
from haystack.schema import Document

logger = logging.getLogger(__name__)

with LazyImport(
    message="Run 'pip install farm-haystack[file-conversion]' or 'pip install " "azure-ai-formrecognizer>=3.2.0b2'"
) as azure_import:
    from azure.ai.formrecognizer import DocumentAnalysisClient, AnalyzeResult
    from azure.core.credentials import AzureKeyCredential


class AzureConverter(BaseConverter):
    """
    File converter that makes use of Microsoft Azure's Form Recognizer service
    (https://azure.microsoft.com/en-us/services/form-recognizer/).
    This Converter extracts both text and tables.
    Supported file formats are: PDF, JPEG, PNG, BMP and TIFF.

    In order to be able to use this Converter, you need an active Azure account
    and a Form Recognizer or Cognitive Services resource.
    (Here you can find information on how to set this up:
    https://docs.microsoft.com/en-us/azure/applied-ai-services/form-recognizer/quickstarts/try-v3-python-sdk#prerequisites)

    """

    def __init__(
        self,
        endpoint: str,
        credential_key: str,
        model_id: str = "prebuilt-document",
        valid_languages: Optional[List[str]] = None,
        save_json: bool = False,
        preceding_context_len: int = 3,
        following_context_len: int = 3,
        merge_multiple_column_headers: bool = True,
        id_hash_keys: Optional[List[str]] = None,
        add_page_number: bool = True,
    ):
        """
        :param endpoint: Your Form Recognizer or Cognitive Services resource's endpoint.
        :param credential_key: Your Form Recognizer or Cognitive Services resource's subscription key.
        :param model_id: The identifier of the model you want to use to extract information out of your file.
                         Default: "prebuilt-document". General purpose models are "prebuilt-document"
                         and "prebuilt-layout".
                         List of available prebuilt models:
                         https://azuresdkdocs.blob.core.windows.net/$web/python/azure-ai-formrecognizer/3.2.0b1/index.html#documentanalysisclient
        :param valid_languages: Validate languages from a list of languages specified in the ISO 639-1
                                (https://en.wikipedia.org/wiki/ISO_639-1) format.
                                This option can be used to add test for encoding errors. If the extracted text is
                                not one of the valid languages, then it might likely be encoding error resulting
                                in garbled text.
        :param save_json: Whether to save the output of the Form Recognizer to a JSON file.
        :param preceding_context_len: Number of lines before a table to extract as preceding context (will be returned as part of meta data).
        :param following_context_len: Number of lines after a table to extract as subsequent context (will be returned as part of meta data).
        :param merge_multiple_column_headers: Some tables contain more than one row as a column header (i.e., column description).
                                              This parameter lets you choose, whether to merge multiple column header
                                              rows to a single row.
        :param id_hash_keys: Generate the document id from a custom list of strings that refer to the document's
            attributes. If you want to ensure you don't have duplicate documents in your DocumentStore but texts are
            not unique, you can modify the metadata and pass e.g. `"meta"` to this field (e.g. [`"content"`, `"meta"`]).
            In this case the id will be generated by using the content and the defined metadata.
        :param add_page_number: Adds the number of the page a table occurs in to the Document's meta field
                                `"page"`.
        """
        # ensure the required dependencies were actually imported
        azure_import.check()

        super().__init__(valid_languages=valid_languages, id_hash_keys=id_hash_keys)

        self.document_analysis_client = DocumentAnalysisClient(
            endpoint=endpoint, credential=AzureKeyCredential(credential_key)
        )
        self.model_id = model_id
        self.valid_languages = valid_languages
        self.save_json = save_json
        self.preceding_context_len = preceding_context_len
        self.following_context_len = following_context_len
        self.merge_multiple_column_headers = merge_multiple_column_headers
        self.add_page_number = add_page_number

    def convert(
        self,
        file_path: Path,
        meta: Optional[Dict[str, Any]] = None,
        remove_numeric_tables: Optional[bool] = None,
        valid_languages: Optional[List[str]] = None,
        encoding: Optional[str] = "utf-8",
        id_hash_keys: Optional[List[str]] = None,
        pages: Optional[str] = None,
        known_language: Optional[str] = None,
    ) -> List[Document]:
        """
        Extract text and tables from a PDF, JPEG, PNG, BMP or TIFF file using Azure's Form Recognizer service.

        :param file_path: Path to the file you want to convert.
        :param meta: Optional dictionary with metadata that shall be attached to all resulting documents.
                     Can be any custom keys and values.
        :param remove_numeric_tables: Not applicable.
        :param valid_languages: Validate languages from a list of languages specified in the ISO 639-1
                                (https://en.wikipedia.org/wiki/ISO_639-1) format.
                                This option can be used to add test for encoding errors. If the extracted text is
                                not one of the valid languages, then it might likely be encoding error resulting
                                in garbled text.
        :param encoding: Not applicable.
        :param id_hash_keys: Generate the document id from a custom list of strings that refer to the document's
            attributes. If you want to ensure you don't have duplicate documents in your DocumentStore but texts are
            not unique, you can modify the metadata and pass e.g. `"meta"` to this field (e.g. [`"content"`, `"meta"`]).
            In this case the id will be generated by using the content and the defined metadata.
        :param pages: Custom page numbers for multi-page documents(PDF/TIFF). Input the page numbers and/or ranges
                      of pages you want to get in the result. For a range of pages, use a hyphen,
                      like pages=”1-3, 5-6”. Separate each page number or range with a comma.
        :param known_language: Locale hint of the input document.
                               See supported locales here: https://aka.ms/azsdk/formrecognizer/supportedlocales.
        """
        if id_hash_keys is None:
            id_hash_keys = self.id_hash_keys

        if isinstance(file_path, str):
            file_path = Path(file_path)

        if valid_languages is None:
            valid_languages = self.valid_languages

        with open(file_path, "rb") as file:
            poller = self.document_analysis_client.begin_analyze_document(
                self.model_id, file, pages=pages, locale=known_language
            )
            result = poller.result()

        if self.save_json:
            with open(file_path.with_suffix(".json"), "w") as json_file:
                json.dump(result.to_dict(), json_file, indent=2)

        return self._convert_tables_and_text(
            result, meta, valid_languages, file_path, id_hash_keys
        )

    def convert_azure_json(
        self,
        file_path: Path,
        meta: Optional[Dict[str, Any]] = None,
        valid_languages: Optional[List[str]] = None,
        id_hash_keys: Optional[List[str]] = None,
    ) -> List[Document]:
        """
        Extract text and tables from the JSON output of Azure's Form Recognizer service.

        :param file_path: Path to the JSON-file you want to convert.
        :param meta: Optional dictionary with metadata that shall be attached to all resulting documents.
                     Can be any custom keys and values.
        :param valid_languages: Validate languages from a list of languages specified in the ISO 639-1
                                (https://en.wikipedia.org/wiki/ISO_639-1) format.
                                This option can be used to add test for encoding errors. If the extracted text is
                                not one of the valid languages, then it might likely be encoding error resulting
                                in garbled text.
        :param id_hash_keys: Generate the document id from a custom list of strings that refer to the document's
            attributes. If you want to ensure you don't have duplicate documents in your DocumentStore but texts are
            not unique, you can modify the metadata and pass e.g. `"meta"` to this field (e.g. [`"content"`, `"meta"`]).
            In this case the id will be generated by using the content and the defined metadata.
        """
        if id_hash_keys is None:
            id_hash_keys = self.id_hash_keys

        if valid_languages is None:
            valid_languages = self.valid_languages

        with open(file_path) as azure_file:
            azure_result = json.load(azure_file)
            azure_result = AnalyzeResult.from_dict(azure_result)

        return self._convert_tables_and_text(
            azure_result, meta, valid_languages, file_path, id_hash_keys
        )

    def _convert_tables_and_text(
        self,
        result: "AnalyzeResult",
        meta: Optional[Dict[str, Any]],
        valid_languages: Optional[List[str]],
        file_path: Path,
        id_hash_keys: Optional[List[str]] = None,
    ) -> List[Document]:
        tables = self._convert_tables(result, meta, id_hash_keys)
        text = self._convert_text(result, meta, id_hash_keys)
        docs = tables + [text]

        if valid_languages:
            file_text = text.content
            for table in tables:
                # Mainly needed for type checking
                if not isinstance(table.content, pd.DataFrame):
                    raise HaystackError("Document's content field must be of type 'pd.DataFrame'.")
                for _, row in table.content.iterrows():
                    for cell in row.values():
                        file_text += f" {cell}"
            if not self.validate_language(file_text, valid_languages):
                logger.warning(
                    "The language for %s is not one of %s. The file may not have "
                    "been decoded in the correct text format.",
                    file_path,
                    valid_languages,
                )

        return docs

    def _convert_tables(
        self, result: "AnalyzeResult", meta: Optional[Dict[str, Any]], id_hash_keys: Optional[List[str]] = None
    ) -> List[Document]:
        converted_tables: List[Document] = []

        if not result.tables:
            return converted_tables

        for table in result.tables:
            # Initialize table with empty cells
            table_list = [[""] * table.column_count for _ in range(table.row_count)]
            additional_column_header_rows = set()
            caption = ""
            row_idx_start = 0

            for idx, cell in enumerate(table.cells):
                # Remove ':selected:'/':unselected:' tags from cell's content
                cell.content = cell.content.replace(":selected:", "")
                cell.content = cell.content.replace(":unselected:", "")

                # Check if first row is a merged cell spanning whole table
                # -> exclude this row and use as a caption
                if idx == 0 and cell.column_span == table.column_count:
                    caption = cell.content
                    row_idx_start = 1
                    table_list.pop(0)
                    continue

                column_span = cell.column_span if cell.column_span else 0
                for c in range(column_span):
                    row_span = cell.row_span if cell.row_span else 0
                    for r in range(row_span):
                        if (
                            self.merge_multiple_column_headers
                            and cell.kind == "columnHeader"
                            and cell.row_index > row_idx_start
                        ):
                            # More than one row serves as column header
                            table_list[0][cell.column_index + c] += f"\n{cell.content}"
                            additional_column_header_rows.add(cell.row_index - row_idx_start)
                        else:
                            table_list[cell.row_index + r - row_idx_start][cell.column_index + c] = cell.content

            # Remove additional column header rows, as these got attached to the first row
            for row_idx in sorted(additional_column_header_rows, reverse=True):
                del table_list[row_idx]

            # Get preceding context of table
            if table.bounding_regions:
                table_beginning_page = next(
                    page for page in result.pages if page.page_number == table.bounding_regions[0].page_number
                )
            else:
                table_beginning_page = None
            table_start_offset = table.spans[0].offset
            if table_beginning_page and table_beginning_page.lines:
                preceding_lines = [
                    line.content for line in table_beginning_page.lines if line.spans[0].offset < table_start_offset
                ]
            else:
                preceding_lines = []
            preceding_context = "\n".join(preceding_lines[-self.preceding_context_len :]) + f"\n{caption}"
            preceding_context = preceding_context.strip()

            # Get following context
            if table.bounding_regions and len(table.bounding_regions) == 1:
                table_end_page = table_beginning_page
            elif table.bounding_regions:
                table_end_page = next(
                    page for page in result.pages if page.page_number == table.bounding_regions[-1].page_number
                )
            else:
                table_end_page = None

            table_end_offset = table_start_offset + table.spans[0].length
            if table_end_page and table_end_page.lines:
                following_lines = [
                    line.content for line in table_end_page.lines if line.spans[0].offset > table_end_offset
                ]
            else:
                following_lines = []
            following_context = "\n".join(following_lines[: self.following_context_len])

            table_meta = copy.deepcopy(meta)

            if isinstance(table_meta, dict):
                table_meta["preceding_context"] = preceding_context
                table_meta["following_context"] = following_context
            else:
                table_meta = {"preceding_context": preceding_context, "following_context": following_context}

            if self.add_page_number and table.bounding_regions:
                table_meta["page"] = table.bounding_regions[0].page_number

            table_df = pd.DataFrame(columns=table_list[0], data=table_list[1:])
            converted_tables.append(
                Document(content=table_df, content_type="table", meta=table_meta, id_hash_keys=id_hash_keys)
            )

        return converted_tables

    def _convert_text(
        self, result: "AnalyzeResult", meta: Optional[Dict[str, str]], id_hash_keys: Optional[List[str]] = None
    ) -> Document:
        text = ""
        table_spans_by_page = defaultdict(list)
        tables = result.tables if result.tables else []
        for table in tables:
            if not table.bounding_regions:
                continue
            table_spans_by_page[table.bounding_regions[0].page_number].append(table.spans[0])

        for page in result.pages:
            tables_on_page = table_spans_by_page[page.page_number]
            lines = page.lines if page.lines else []
            for line in lines:
                in_table = any(
                    t.offset <= line.spans[0].offset <= t.offset + t.length
                    for t in tables_on_page
                )
                if in_table:
                    continue
                text += f"{line.content}\n"
            text += "\f"

        return Document(content=text, meta=meta, id_hash_keys=id_hash_keys)
