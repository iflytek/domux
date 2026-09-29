"""Memory-efficient GGUF v2/v3 metadata inspection."""

from __future__ import annotations

import os
import struct
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, BinaryIO


class GGUFError(ValueError):
    """Raised when a file is not a supported or valid GGUF file."""


_SCALAR_FORMATS = {
    0: "B",
    1: "b",
    2: "H",
    3: "h",
    4: "I",
    5: "i",
    6: "f",
    7: "?",
    10: "Q",
    11: "q",
    12: "d",
}
_TYPE_NAMES = {
    0: "uint8",
    1: "int8",
    2: "uint16",
    3: "int16",
    4: "uint32",
    5: "int32",
    6: "float32",
    7: "bool",
    8: "string",
    9: "array",
    10: "uint64",
    11: "int64",
    12: "float64",
}
_FILE_TYPES = {
    0: "F32",
    1: "F16",
    2: "Q4_0",
    3: "Q4_1",
    7: "Q8_0",
    8: "Q5_0",
    9: "Q5_1",
    10: "Q2_K",
    11: "Q3_K_S",
    12: "Q3_K_M",
    13: "Q3_K_L",
    14: "Q4_K_S",
    15: "Q4_K_M",
    16: "Q5_K_S",
    17: "Q5_K_M",
    18: "Q6_K",
}
_TENSOR_TYPES = {
    0: "F32",
    1: "F16",
    2: "Q4_0",
    3: "Q4_1",
    6: "Q5_0",
    7: "Q5_1",
    8: "Q8_0",
    10: "Q2_K",
    11: "Q3_K",
    12: "Q4_K",
    13: "Q5_K",
    14: "Q6_K",
    30: "BF16",
}
_SUPPORTED_QUANTIZATIONS = {"Q4_0", "Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0"}
_MAX_STRING_BYTES = 64 * 1024 * 1024
_MAX_METADATA_PAIRS = 1_000_000
_MAX_DIMENSIONS = 16


@dataclass(frozen=True)
class GGUFMetadata:
    """Useful model metadata without loading tensor weights."""

    path: str
    file_size: int
    version: int
    tensor_count: int
    metadata_count: int
    architecture: str | None
    model_name: str | None
    quantization: str | None
    quantization_supported: bool | None
    metadata: dict[str, Any]
    tensor_types: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _Reader:
    def __init__(
        self,
        stream: BinaryIO,
        size: int,
        progress: Callable[[int, int], None] | None,
    ) -> None:
        self.stream = stream
        self.size = size
        self.progress = progress
        self._last_progress = -1

    def read(self, size: int) -> bytes:
        data = self.stream.read(size)
        if len(data) != size:
            raise GGUFError("Unexpected end of GGUF file")
        self._report()
        return data

    def unpack(self, fmt: str) -> Any:
        return struct.unpack("<" + fmt, self.read(struct.calcsize(fmt)))[0]

    def skip(self, size: int) -> None:
        if size < 0 or size > self.size - self.stream.tell():
            raise GGUFError("Unexpected end of GGUF file")
        self.stream.seek(size, os.SEEK_CUR)
        self._report()

    def string(self) -> str:
        length = self.unpack("Q")
        if length > _MAX_STRING_BYTES or length > self.size - self.stream.tell():
            raise GGUFError(f"Invalid GGUF string length: {length}")
        try:
            return self.read(length).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GGUFError("GGUF metadata contains invalid UTF-8") from exc

    def _report(self, force: bool = False) -> None:
        if not self.progress:
            return
        position = self.stream.tell()
        percent = 100 if not self.size else int(position * 100 / self.size)
        if force or percent != self._last_progress:
            self._last_progress = percent
            self.progress(position, self.size)


def _read_value(reader: _Reader, value_type: int, preview_limit: int) -> Any:
    if value_type in _SCALAR_FORMATS:
        return reader.unpack(_SCALAR_FORMATS[value_type])
    if value_type == 8:
        return reader.string()
    if value_type != 9:
        raise GGUFError(f"Unknown GGUF metadata value type: {value_type}")

    element_type = reader.unpack("I")
    if element_type == 9:
        raise GGUFError("Nested GGUF metadata arrays are not supported by the format")
    length = reader.unpack("Q")
    scalar_format = _SCALAR_FORMATS.get(element_type)
    if scalar_format and length > preview_limit:
        preview = [
            _read_value(reader, element_type, preview_limit)
            for _ in range(preview_limit)
        ]
        reader.skip((length - preview_limit) * struct.calcsize(scalar_format))
        return {
            "type": _TYPE_NAMES.get(element_type, str(element_type)),
            "length": length,
            "preview": preview,
        }

    preview = []
    for index in range(length):
        value = _read_value(reader, element_type, preview_limit)
        if index < preview_limit:
            preview.append(value)
    if length <= preview_limit:
        return preview
    return {
        "type": _TYPE_NAMES.get(element_type, str(element_type)),
        "length": length,
        "preview": preview,
    }


def _read_tensor_types(reader: _Reader, tensor_count: int) -> Counter[str]:
    types: Counter[str] = Counter()
    for _ in range(tensor_count):
        reader.string()
        dimensions = reader.unpack("I")
        if dimensions > _MAX_DIMENSIONS:
            raise GGUFError(f"Invalid tensor dimension count: {dimensions}")
        for _ in range(dimensions):
            reader.unpack("Q")
        tensor_type = reader.unpack("I")
        reader.unpack("Q")
        types[_TENSOR_TYPES.get(tensor_type, f"TYPE_{tensor_type}")] += 1
    return types


def inspect_gguf(
    path: str | os.PathLike[str],
    *,
    preview_limit: int = 16,
    progress: Callable[[int, int], None] | None = None,
) -> GGUFMetadata:
    """Inspect GGUF v2/v3 metadata while keeping peak memory bounded."""
    source = Path(path).expanduser().resolve()
    if source.suffix.lower() != ".gguf":
        raise GGUFError("Expected a .gguf file")
    if not source.is_file():
        raise GGUFError(f"GGUF file not found: {source}")
    if preview_limit < 0:
        raise ValueError("preview_limit must be non-negative")

    size = source.stat().st_size
    with source.open("rb") as stream:
        reader = _Reader(stream, size, progress)
        if reader.read(4) != b"GGUF":
            raise GGUFError("Invalid GGUF magic; expected 'GGUF'")
        version = reader.unpack("I")
        if version not in (2, 3):
            raise GGUFError(f"Unsupported GGUF version {version}; expected v2 or v3")
        tensor_count = reader.unpack("Q")
        metadata_count = reader.unpack("Q")
        if metadata_count > _MAX_METADATA_PAIRS:
            raise GGUFError(f"Unreasonable GGUF metadata count: {metadata_count}")
        if tensor_count > size // 24:
            raise GGUFError(f"Unreasonable GGUF tensor count: {tensor_count}")

        metadata = {}
        for _ in range(metadata_count):
            key = reader.string()
            value_type = reader.unpack("I")
            metadata[key] = _read_value(reader, value_type, preview_limit)

        tensor_types = _read_tensor_types(reader, tensor_count)
        reader._report(force=True)

    file_type = metadata.get("general.file_type")
    quantization = _FILE_TYPES.get(file_type) if isinstance(file_type, int) else None
    if quantization is None:
        quantized = [
            name
            for name, count in tensor_types.items()
            if count and name.startswith(("Q", "IQ", "TQ", "MXFP"))
        ]
        if len(quantized) == 1:
            quantization = quantized[0]

    return GGUFMetadata(
        path=str(source),
        file_size=size,
        version=version,
        tensor_count=tensor_count,
        metadata_count=metadata_count,
        architecture=metadata.get("general.architecture"),
        model_name=metadata.get("general.name"),
        quantization=quantization,
        quantization_supported=(
            quantization in _SUPPORTED_QUANTIZATIONS if quantization else None
        ),
        metadata=metadata,
        tensor_types=dict(sorted(tensor_types.items())),
    )
