import struct
import tempfile
import unittest
from pathlib import Path

from domux_runtime.gguf import GGUFError, inspect_gguf

FILE_TYPES = {
    "Q4_0": 2,
    "Q4_K_M": 15,
    "Q5_K_M": 17,
    "Q6_K": 18,
    "Q8_0": 7,
}


def _string(value):
    encoded = value.encode("utf-8")
    return struct.pack("<Q", len(encoded)) + encoded


def _write_fixture(path, version=3, quantization="Q4_K_M"):
    metadata = (
        _string("general.architecture")
        + struct.pack("<I", 8)
        + _string("gemma")
        + _string("general.name")
        + struct.pack("<I", 8)
        + _string("Domux test")
        + _string("general.file_type")
        + struct.pack("<II", 4, FILE_TYPES[quantization])
        + _string("general.languages")
        + struct.pack("<IIQ", 9, 8, 2)
        + _string("en")
        + _string("zh")
    )
    tensor = (
        _string("token_embd.weight")
        + struct.pack("<I", 2)
        + struct.pack("<QQ", 16, 32)
        + struct.pack("<I", 12)
        + struct.pack("<Q", 0)
    )
    path.write_bytes(
        b"GGUF"
        + struct.pack("<IQQ", version, 1, 4)
        + metadata
        + tensor
        + b"tensor-weights-are-never-read"
    )


class TestGGUF(unittest.TestCase):
    def test_parses_v2_and_v3_supported_quantizations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for version in (2, 3):
                for quantization in FILE_TYPES:
                    path = root / f"{version}-{quantization}.gguf"
                    _write_fixture(path, version, quantization)
                    result = inspect_gguf(path)
                    self.assertEqual(result.version, version)
                    self.assertEqual(result.quantization, quantization)
                    self.assertTrue(result.quantization_supported)
                    self.assertEqual(result.architecture, "gemma")
                    self.assertEqual(result.metadata["general.languages"], ["en", "zh"])
                    self.assertEqual(result.tensor_types, {"Q4_K": 1})

    def test_rejects_bad_magic_and_unsupported_version(self):
        with tempfile.TemporaryDirectory() as directory:
            bad_magic = Path(directory) / "bad.gguf"
            bad_magic.write_bytes(b"NOPE")
            with self.assertRaisesRegex(GGUFError, "magic"):
                inspect_gguf(bad_magic)

            bad_version = Path(directory) / "v1.gguf"
            bad_version.write_bytes(b"GGUF" + struct.pack("<IQQ", 1, 0, 0))
            with self.assertRaisesRegex(GGUFError, "version 1"):
                inspect_gguf(bad_version)

    def test_progress_is_reported_without_reading_weights(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.gguf"
            _write_fixture(path)
            result = inspect_gguf(
                path, progress=lambda current, total: calls.append((current, total))
            )
            self.assertTrue(calls)
            self.assertLess(calls[-1][0], result.file_size)
            self.assertEqual(calls[-1][1], result.file_size)


if __name__ == "__main__":
    unittest.main()
