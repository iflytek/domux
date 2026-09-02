from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


CASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CASE_DIR))

from run_model import (  # noqa: E402
    MODEL_REVISION,
    atomic_write_pair,
    canonical_json,
    git_blob_sha1,
    sha256_file,
    sha256_text,
    snapshot_manifest,
    validate_paths,
)


class SnapshotManifestTests(unittest.TestCase):
    def _fixture(
        self,
        root: Path,
    ) -> tuple[Path, dict[str, tuple[int, str]], dict[str, bytes]]:
        snapshot = root / "snapshot"
        snapshot.mkdir()
        payloads = {
            "config.json": b'{"model":"synthetic"}\n',
            "weights.bin": b"small synthetic lfs payload\n",
        }
        expected_files = {
            "config.json": (len(payloads["config.json"]), ""),
            "weights.bin": (
                len(payloads["weights.bin"]),
                hashlib.sha256(payloads["weights.bin"]).hexdigest(),
            ),
        }
        for name, payload in payloads.items():
            path = snapshot / name
            path.write_bytes(payload)
        expected_files["config.json"] = (
            len(payloads["config.json"]),
            git_blob_sha1(snapshot / "config.json"),
        )
        metadata_root = snapshot / ".cache" / "huggingface" / "download"
        metadata_root.mkdir(parents=True)
        for name, (_size, etag) in expected_files.items():
            (metadata_root / f"{name}.metadata").write_text(
                f"{MODEL_REVISION}\n{etag}\nsynthetic-timestamp\n",
                encoding="utf-8",
            )
        return snapshot, expected_files, payloads

    def test_snapshot_manifest_verifies_revision_etag_size_and_content_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            snapshot, expected_files, payloads = self._fixture(Path(temporary))

            entries, manifest_sha256 = snapshot_manifest(
                snapshot,
                expected_files=expected_files,
                expected_manifest_sha256=None,
            )

            self.assertEqual([entry["name"] for entry in entries], sorted(payloads))
            for entry in entries:
                name = str(entry["name"])
                expected_size, expected_etag = expected_files[name]
                self.assertEqual(entry["size_bytes"], expected_size)
                self.assertEqual(entry["hub_etag"], expected_etag)
                self.assertEqual(entry["sha256"], sha256_file(snapshot / name))
            self.assertEqual(manifest_sha256, sha256_text(canonical_json(entries)))

    def test_snapshot_manifest_rejects_revision_etag_size_and_hash_mismatches(self) -> None:
        cases = ("revision", "etag", "size", "hash")
        for mutation in cases:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                snapshot, expected_files, payloads = self._fixture(Path(temporary))
                if mutation == "revision":
                    metadata = snapshot / ".cache/huggingface/download/config.json.metadata"
                    lines = metadata.read_text(encoding="utf-8").splitlines()
                    lines[0] = "0" * 40
                    metadata.write_text("\n".join(lines) + "\n", encoding="utf-8")
                    message = "revision metadata mismatch"
                elif mutation == "etag":
                    metadata = snapshot / ".cache/huggingface/download/config.json.metadata"
                    lines = metadata.read_text(encoding="utf-8").splitlines()
                    lines[1] = "0" * 40
                    metadata.write_text("\n".join(lines) + "\n", encoding="utf-8")
                    message = "Hub etag mismatch"
                elif mutation == "size":
                    (snapshot / "config.json").write_bytes(payloads["config.json"] + b"x")
                    message = "size mismatch"
                else:
                    original = payloads["weights.bin"]
                    replacement = bytes([original[0] ^ 1]) + original[1:]
                    self.assertEqual(len(replacement), len(original))
                    (snapshot / "weights.bin").write_bytes(replacement)
                    message = "LFS digest mismatch"

                with self.assertRaisesRegex(RuntimeError, message):
                    snapshot_manifest(
                        snapshot,
                        expected_files=expected_files,
                        expected_manifest_sha256=None,
                    )

    def test_snapshot_manifest_rejects_missing_extra_and_symlinked_root_files(self) -> None:
        for mutation in ("missing", "extra", "symlink"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                snapshot, expected_files, _payloads = self._fixture(Path(temporary))
                if mutation == "missing":
                    (snapshot / "config.json").unlink()
                    message = "missing=.*config.json"
                elif mutation == "extra":
                    (snapshot / "unregistered.txt").write_text("extra", encoding="utf-8")
                    message = "extra=.*unregistered.txt"
                else:
                    target = snapshot / "config-target.json"
                    target.write_bytes((snapshot / "config.json").read_bytes())
                    (snapshot / "config.json").unlink()
                    (snapshot / "config.json").symlink_to(target.name)
                    expected_files = {
                        **expected_files,
                        "config-target.json": (
                            target.stat().st_size,
                            git_blob_sha1(target),
                        ),
                    }
                    metadata = snapshot / ".cache/huggingface/download/config-target.json.metadata"
                    metadata.write_text(
                        f"{MODEL_REVISION}\n{expected_files['config-target.json'][1]}\n",
                        encoding="utf-8",
                    )
                    message = "must not be a symlink"

                with self.assertRaisesRegex(RuntimeError, message):
                    snapshot_manifest(
                        snapshot,
                        expected_files=expected_files,
                        expected_manifest_sha256=None,
                    )


class PathValidationTests(unittest.TestCase):
    def test_validate_paths_rejects_aliases_and_snapshot_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            dataset = root / "dataset.jsonl"
            dataset.write_text("{}\n", encoding="utf-8")
            output = root / "raw.jsonl"
            metadata = root / "metadata.json"

            cases = (
                (dataset, metadata, "output equals dataset"),
                (output, output, "output equals metadata"),
                (snapshot / "raw.jsonl", metadata, "evidence inside snapshot"),
                (output, snapshot / "metadata.json", "metadata inside snapshot"),
            )
            for candidate_output, candidate_metadata, label in cases:
                with self.subTest(case=label), self.assertRaises(ValueError):
                    validate_paths(
                        snapshot,
                        dataset,
                        candidate_output,
                        candidate_metadata,
                    )


class AtomicWritePairTests(unittest.TestCase):
    @staticmethod
    def _staging_files(root: Path) -> list[Path]:
        return sorted(path for path in root.rglob("*.tmp") if path.is_file())

    def test_atomic_write_pair_publishes_both_exact_byte_sequences(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "evidence" / "raw.jsonl"
            metadata = root / "metadata" / "run.json"
            raw_bytes = b'{"raw":"evidence"}\n'
            metadata_bytes = b'{"raw_evidence_sha256":"synthetic"}\n'

            atomic_write_pair(output, raw_bytes, metadata, metadata_bytes)

            self.assertEqual(output.read_bytes(), raw_bytes)
            self.assertEqual(metadata.read_bytes(), metadata_bytes)
            self.assertEqual(self._staging_files(root), [])

    def test_atomic_write_pair_never_overwrites_existing_artifacts_or_leaves_staging(self) -> None:
        for existing in ("evidence", "metadata", "both"):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                output = root / "raw.jsonl"
                metadata = root / "metadata.json"
                if existing in {"evidence", "both"}:
                    output.write_bytes(b"existing raw")
                if existing in {"metadata", "both"}:
                    metadata.write_bytes(b"existing metadata")

                with self.assertRaises(FileExistsError):
                    atomic_write_pair(
                        output,
                        b"replacement raw",
                        metadata,
                        b"replacement metadata",
                    )

                if existing in {"evidence", "both"}:
                    self.assertEqual(output.read_bytes(), b"existing raw")
                else:
                    self.assertFalse(output.exists())
                if existing in {"metadata", "both"}:
                    self.assertEqual(metadata.read_bytes(), b"existing metadata")
                else:
                    self.assertFalse(metadata.exists())
                self.assertEqual(self._staging_files(root), [])


if __name__ == "__main__":
    unittest.main()
