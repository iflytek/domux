# Third-Party Licenses

This document inventories the third-party components that Domux depends on, together with a
license risk assessment.

It complements [`NOTICE`](NOTICE), which carries the attribution and pass-through obligations
required for redistribution. Where the two overlap (notably the Gemma model weights), `NOTICE`
is the authoritative statement.

- **Domux's own code** (training scripts, reward plugins, evaluation tooling) is licensed under
  the Apache License, Version 2.0 — see [`LICENSE`](LICENSE).
- **Domux's model weights** are a Gemma derivative and are **not** covered by Apache-2.0.
  See [Model weights](#model-weights) below.

**Scope of this inventory.** It covers the *direct*, first-order dependencies declared in this
repository. It does not enumerate transitive dependencies. Licenses were verified against
package metadata published on PyPI on **2026-08-26**; declared metadata can change between
releases, so re-verify when bumping a pin.

---

## Training

Declared in [`training/requirements.txt`](training/requirements.txt).

| Component | Constraint | License (as declared) | Project |
| --- | --- | --- | --- |
| `ms-swift[llm]` | `>=3.0.0` | Apache-2.0 | [modelscope/ms-swift](https://github.com/modelscope/ms-swift) |
| `torch` | `>=2.0.0` | `Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND MIT` | [pytorch/pytorch](https://github.com/pytorch/pytorch) |
| `transformers` | `>=4.40.0` | Apache-2.0 | [huggingface/transformers](https://github.com/huggingface/transformers) |
| `peft` | `>=0.10.0` | Apache-2.0 | [huggingface/peft](https://github.com/huggingface/peft) |

> `torch` declares a composite SPDX expression because the distributed wheels bundle
> third-party components under several licenses. The PyTorch project's own source license is
> BSD-3-Clause. Anyone **redistributing** PyTorch binaries (rather than installing them from
> PyPI) should also ship PyTorch's bundled third-party notices.

## Inference and deployment

Declared in the Quick Start / Deployment sections of [`README.md`](README.md).

| Component | Constraint | License (as declared) | Project |
| --- | --- | --- | --- |
| `vllm` | `==0.22.0` | Apache-2.0 | [vllm-project/vllm](https://github.com/vllm-project/vllm) |
| `sglang[all]` | `==0.5.12` | Apache-2.0 | [sgl-project/sglang](https://github.com/sgl-project/sglang) |

> These are alternative backends — a deployment normally installs one, not both. The `[all]`
> extra of `sglang` pulls in a substantially larger dependency set than the base package; audit
> it separately if you redistribute a bundled environment.

## Evaluation and tooling

| Component | Used by | License (as declared) | Project |
| --- | --- | --- | --- |
| `requests` | [`eval/run_eval.py`](eval/run_eval.py) | Apache-2.0 | [psf/requests](https://github.com/psf/requests) |
| `modelscope` | model download ([`README.md`](README.md)) | Apache-2.0 | [modelscope/modelscope](https://github.com/modelscope/modelscope) |

[`scripts/validate_cases.py`](scripts/validate_cases.py) uses only the Python standard library
and introduces no third-party dependency.

---

## Model weights

Domux (`Domux-Gemma-4-E2B-it`) is a fine-tuned derivative of Google's Gemma model.

| Component | Terms |
| --- | --- |
| Gemma / Domux model weights | [Gemma Terms of Use](https://ai.google.dev/gemma/terms) + [Gemma Prohibited Use Policy](https://ai.google.dev/gemma/prohibited_use_policy) |

The Gemma Terms of Use are **not** an OSI-approved open source license. They impose use
restrictions that must be passed through to downstream recipients. The full obligations —
providing the terms, displaying a "built from Gemma" notice, and passing through the
restrictions — are set out in [`NOTICE`](NOTICE).

"Gemma" is a trademark of Google LLC.

---

## Risk assessment

| # | Area | Risk | Assessment |
| --- | --- | --- | --- |
| 1 | Direct Python dependencies | **Low** | All are permissive (Apache-2.0, BSD, MIT). No copyleft (GPL/LGPL/AGPL) or source-available/non-commercial license appears in the direct dependency set. All are compatible with distributing this repository's code under Apache-2.0. |
| 2 | Model weights | **Medium — the principal obligation** | The weights are governed by the Gemma Terms of Use, not Apache-2.0. Redistribution requires passing through the terms and the Prohibited Use Policy. Users who assume "Apache-2.0 repository ⇒ Apache-2.0 weights" will be wrong; `NOTICE` and the model cards state the correct terms. |
| 3 | Transitive dependencies | **Unassessed** | Not enumerated here. `torch`, `vllm` and `sglang[all]` each pull in large trees. Anyone shipping a bundled environment or container image should run a full transitive scan rather than relying on this document. |
| 4 | Bundled binaries in `torch` | **Low** | Handled upstream by PyTorch's own notices; relevant only when redistributing PyTorch binaries. |
| 5 | Version drift | **Low, ongoing** | Training dependencies use `>=` constraints, so the resolved version — and in principle its declared license — can change over time. Re-verify when pinning for a release. |
| 6 | Datasets | **See dataset docs** | Evaluation data ships in [`eval/`](eval); its provenance and terms are described in [`eval/DATASET_README.md`](eval/DATASET_README.md). Contributed case studies must satisfy the data-source requirements in [`cases/README.md`](cases/README.md). |

**Summary.** The code-side license position is clean: every direct dependency is permissive and
Apache-2.0-compatible. The meaningful compliance obligation lies with the **model weights**,
which are Gemma-derived and carry use restrictions that must travel with any redistribution.

---

## Maintaining this document

When adding, removing, or re-pinning a dependency, update the tables above.

To regenerate a full inventory including transitive dependencies:

```bash
pip install pip-licenses
pip-licenses --format=markdown --with-urls --with-license-file
```

Verify anything the tooling reports as `UNKNOWN` against the project's own `LICENSE` file
rather than recording it as unknown.
