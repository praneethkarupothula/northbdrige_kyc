"""Transient Pydantic validation of local OCR output; raw text is never persisted here."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Coordinate = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Confidence = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]


class OcrWordSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    text: str
    bbox: tuple[Coordinate, Coordinate, Coordinate, Coordinate]
    conf: Confidence | None

    @model_validator(mode="after")
    def ordered_bounds(self):
        if self.bbox[0] > self.bbox[2] or self.bbox[1] > self.bbox[3]:
            raise ValueError("Invalid OCR bounds")
        return self


class OcrLineSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    page: int = Field(ge=1)
    method: Literal["pdf_text", "tesseract", "tesseract_reocr"]
    words: list[OcrWordSchema] = Field(min_length=1)


class OcrPageSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    page: int = Field(ge=1)
    method: Literal["pdf_text", "tesseract", "tesseract_reocr"]
    lines: list[OcrLineSchema]
    mean_conf: Confidence | None
    alnum: int = Field(ge=0)

    @model_validator(mode="after")
    def matching_pages(self):
        if any(line.page != self.page for line in self.lines):
            raise ValueError("OCR line/page mismatch")
        return self


class OcrResultSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    pages_total: int = Field(ge=1)
    pages: list[OcrPageSchema] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_pages(self):
        ids = [page.page for page in self.pages]
        if len(ids) != len(set(ids)) or any(i > self.pages_total for i in ids):
            raise ValueError("Invalid OCR page references")
        return self
