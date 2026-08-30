#!/usr/bin/env python3
"""Auditable text normalization and safety decisions for SeniorSafe."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Edit:
    rule: str
    before: str
    after: str

    def to_dict(self) -> dict[str, str]:
        return {"rule": self.rule, "before": self.before, "after": self.after}


ASR_REPLACEMENTS = {
    "lite": "light",
    "诶西": "AC",
    "创帘": "窗帘",
    "客厅等": "客厅灯",
    "二是四度": "二十四度",
}

PHRASE_REPLACEMENTS = [
    # Longer phrases must run before their shorter components.
    ("百分之六十", "60 Percent"),
    ("百分之五十", "50 Percent"),
    ("二十六度", "26 Celsius"),
    ("二十五度", "25 Celsius"),
    ("二十四度", "24 Celsius"),
    ("二十二度", "22 Celsius"),
    ("二十度", "20 Celsius"),
    ("三十度", "30 Celsius"),
    ("一楼", "First Floor"),
    ("客厅", "Living Room"),
    ("卧室", "Bedroom"),
    ("书房", "Study"),
    ("厨房", "Kitchen"),
    ("玄关", "Entrance"),
    ("安防系统", "Security System"),
    ("安防", "Security System"),
    ("燃气阀", "Gas Valve"),
    ("取暖器", "Heater"),
    ("门锁", "Door Lock"),
    ("烤箱", "Oven"),
    ("床头灯", "Bedside Lamp"),
    ("顶灯", "Ceiling Light"),
    ("台灯", "Desk Lamp"),
    ("空调", "AC"),
    ("窗帘", "Curtain"),
    ("亮度", "brightness"),
    ("温度", "temperature"),
    ("蓝色", "Blue"),
    ("红色", "Red"),
    ("打开", "turn on"),
    ("开启", "turn on"),
    ("关闭", "turn off"),
    ("关掉", "turn off"),
    ("关上", "turn off"),
    ("调高", "adjust up"),
    ("调亮", "adjust up brightness"),
    ("调到", "set to"),
    ("设为", "set to"),
    ("设成", "set to"),
    ("改成", "set to"),
    ("灯", "Light"),
]

FILLERS = (
    "麻烦你",
    "帮我",
    "请",
    "一下",
    "吧",
    "谢谢",
    "那个啊",
    "啊",
    "就行",
    "给",
)

HIGH_RISK_TERMS = ("门锁", "燃气阀", "取暖器", "安防", "烤箱", "Door Lock", "Gas Valve", "Heater", "Security", "Oven")
AMBIGUOUS_TERMS = ("那个", "它", "那边", "那个灯", "那个调", "那个打开")
EXPLICIT_CONTEXT_TERMS = (
    "客厅",
    "卧室",
    "书房",
    "厨房",
    "玄关",
    "一楼",
    "Living Room",
    "Bedroom",
    "Study",
    "Kitchen",
    "Entrance",
    "First Floor",
)


def _replace(text: str, old: str, new: str, rule: str, edits: list[Edit]) -> str:
    if old not in text:
        return text
    # Pad every substitution with spaces so spliced tokens stay separate words
    # for the model: 客厅灯设为蓝色 must become "Living Room Light set to Blue",
    # not "Living RoomLightset toBlue" (the glue produced bogus device slots
    # such as "Lightset" and "Heaterset" in the first CPU run).
    updated = text.replace(old, f" {new} " if new else " ")
    edits.append(Edit(rule=rule, before=old, after=new))
    return updated


def normalize_text(text: str) -> tuple[str, list[dict[str, str]]]:
    """Normalize explicit text only; never fill a missing device or room."""

    edits: list[Edit] = []
    normalized = unicodedata.normalize("NFKC", text).strip()
    if normalized != text:
        edits.append(Edit("unicode_nfkc", text, normalized))

    for old, new in ASR_REPLACEMENTS.items():
        normalized = _replace(normalized, old, new, "synthetic_asr_alias", edits)

    # Preserve the final clause after explicit correction markers. This rule is
    # conservative: it only drops text when the final clause names a device.
    correction_match = re.search(r"(?:不对|等一下|算了|不是[^，。]*[，,])\s*(.+)$", normalized)
    if correction_match:
        final_clause = correction_match.group(1).strip(" ，,。.")
        if any(term in final_clause for term in ("灯", "空调", "窗帘", "AC", "light", "curtain")):
            edits.append(Edit("explicit_self_correction", normalized, final_clause))
            normalized = final_clause

    for filler in FILLERS:
        normalized = _replace(normalized, filler, "", "remove_filler", edits)

    for old, new in PHRASE_REPLACEMENTS:
        normalized = _replace(normalized, old, new, "canonical_lexicon", edits)

    normalized = re.sub(r"[……，,。.!！?？;；:：]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()

    # Collapse adjacent repeated words introduced by speech repetition. The
    # regex does not remove repeated values that are separated by other text.
    before_deduplication = normalized
    previous = None
    while previous != normalized:
        previous = normalized
        normalized = re.sub(r"\b([A-Za-z0-9]+)(?:\s+\1\b)+", r"\1", normalized, flags=re.IGNORECASE)
    if normalized != before_deduplication:
        edits.append(Edit("collapse_adjacent_repetition", before_deduplication, normalized))

    return normalized, [edit.to_dict() for edit in edits]


def safety_decision(text: str) -> tuple[str, list[str]]:
    """Return a deterministic offline decision; this does not control devices."""

    reasons: list[str] = []
    lower = text.lower()

    if any(term in text for term in AMBIGUOUS_TERMS) and not any(term in text for term in EXPLICIT_CONTEXT_TERMS):
        reasons.append("ambiguous_reference_without_unique_context")

    if "燃气阀" in text and any(term in text for term in ("打开", "开启")):
        reasons.append("gas_valve_open_request")
    if "取暖器" in text and ("三十度" in text or re.search(r"\b(?:29|30|3[1-9])\b", lower)):
        reasons.append("heater_temperature_above_mvp_limit")
    if "安防" in text and any(term in text for term in ("关掉", "关闭", "停用")):
        reasons.append("security_disable_request")
    if "烤箱" in text and "没人" in text and any(term in text for term in ("打开", "开启")):
        reasons.append("unattended_oven_start")

    if any(reason in reasons for reason in (
        "gas_valve_open_request",
        "heater_temperature_above_mvp_limit",
        "security_disable_request",
        "unattended_oven_start",
    )):
        return "reject", reasons

    if reasons:
        return "clarify", reasons

    if any(term in text for term in HIGH_RISK_TERMS):
        # Explicit high-risk commands are not automatically rejected. The MVP
        # marks them for confirmation unless they match a refusal rule above.
        return "clarify", ["high_risk_action_requires_confirmation"]
    return "execute", []
