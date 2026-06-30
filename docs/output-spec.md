# Domux Output Command Specification

> 🌐 **中文版**: [output-spec.zh.md](output-spec.zh.md)

This document defines the structured control-command format produced by the Domux model. It is the shared contract for parsers, downstream executors, and the evaluation suite.

---

## 📑 Table of Contents

- [1. Output Format](#1-output-format)
  - [1.1 Single Intent](#11-single-intent)
  - [1.2 Multiple Intents](#12-multiple-intents)
- [2. Field Overview](#2-field-overview)
- [3. Field Definitions](#3-field-definitions)
  - [3.1 `action` — Action](#31-action--action)
  - [3.2 `device` — Device](#32-device--device)
  - [3.3 `attribute` — Attribute](#33-attribute--attribute)
  - [3.4 `value` — Value](#34-value--value)
  - [3.5 `unit` — Unit](#35-unit--unit)
  - [3.6 `room` — Room](#36-room--room)
  - [3.7 `floor` — Floor](#37-floor--floor)
- [4. Placeholder Convention](#4-placeholder-convention)
- [5. End-to-End Examples](#5-end-to-end-examples)
- [6. A Note on Enumerated Values](#6-a-note-on-enumerated-values)

---

## 1. Output Format

### 1.1 Single Intent

Every command consists of **7 fields** separated by a pipe `|`, in a fixed order:

```text
action|device|attribute|value|unit|room|floor
```

### 1.2 Multiple Intents

When a user utterance contains multiple control intents, the commands are concatenated with a newline `\n`, in the order they appear in the original sentence:

```text
action₁|device₁|attribute₁|value₁|unit₁|room₁|floor₁
action₂|device₂|attribute₂|value₂|unit₂|room₂|floor₂
```

---

## 2. Field Overview

| # | Field | Meaning | Type | Default Placeholder |
|---|-------|---------|------|---------------------|
| 1 | `action` | Control action | Enum | — |
| 2 | `device` | Target device / scene mode | String | — |
| 3 | `attribute` | Controlled attribute | Enum | `*` |
| 4 | `value` | Attribute value | Number / String | `*` |
| 5 | `unit` | Unit of the value | Enum | `*` |
| 6 | `room` | Containing room | String | `*` |
| 7 | `floor` | Containing floor | String | `*` |

> See [§4 Placeholder Convention](#4-placeholder-convention) for the full rules on `*`.

---

## 3. Field Definitions

### 3.1 `action` — Action

Enum. Input language is unrestricted; model output is normalized to the set below.

| Action | Description | Typical Scenarios |
|--------|-------------|-------------------|
| `turnOn` | Turn a device on | Turn on light / AC, open curtain, enable a light mode |
| `turnOff` | Turn a device off | Turn off light / AC, close curtain, disable a light mode |
| `set` | Set to a specific value | Set brightness, color, color temperature, temperature, wind speed, AC mode, position |
| `adjustUp` | Increase an attribute | Raise brightness, color temperature, temperature, wind speed, position |
| `adjustDown` | Decrease an attribute | Lower brightness, color temperature, temperature, wind speed, position |
| `activate` | Activate a scene mode | Enter Party Mode, Romantic Mode, etc. |
| `deactivate` | Deactivate a scene mode | Exit Party Mode, Romantic Mode, etc. |
| `pause` | Pause a curtain in motion | Stop a curtain partway |

**Selection rules**

- When the utterance contains an **explicit numeric value**, use `set`.
- When the utterance contains hedging modifiers such as `a little` / `a bit`, use `adjustUp` / `adjustDown` and leave `value` as `*`.
- `pause` is reserved for curtain-class devices (`Curtain` / `Blind` / `Sheer Curtain`) — e.g. *"stop the curtain"*, *"pause the blind"*.

---

### 3.2 `device` — Device

#### 3.2.1 Naming Convention

- Base types are fixed: `Light`, `Curtain`, `Blind`, `AC`.
- Capitalized, singular (no trailing `s`).
- Multi-word names are joined by a **single space**, e.g. `Spot Light`, `Strip Light`.

#### 3.2.2 Physical Devices

The following naming patterns are supported:

| Pattern | Examples |
|---------|----------|
| Base type | `Light`, `Curtain`, `Blind`, `AC` |
| Numeric / letter suffix | `Light 1`, `Light 2`, `Light A`, `Light B` |
| Prefix + type | `Spot Light`, `Strip Light`, `Sheer Curtain` |
| Prefix + type + suffix | `Spot Light 1`, `Strip Light A` |

#### 3.2.3 Scene Modes

The `device` field also carries the name of a scene mode, paired with `activate` / `deactivate`:

- Capitalized, space-joined.
- Examples: `Romantic Mode`, `Party Mode`, `Sleeping Mode`, `Holiday Mode`.

---

### 3.3 `attribute` — Attribute

Grouped by device type. For cases not covered by the table, use the placeholder `*`.

| Attribute | Meaning | Applicable Device | Value Type |
|-----------|---------|-------------------|------------|
| `brightness` | Brightness | Light | Number |
| `color` | Color | Light | String |
| `colorTemperature` | Color temperature | Light | Number |
| `mode` | Light mode | Light | String |
| `mode` | AC mode | AC | String |
| `windSpeed` | Wind speed | AC | String |
| `temperature` | Temperature | AC | Number |
| `position` | Open / close position | Curtain | Number |
| `*` | No attribute | Used with `turnOn` / `turnOff` / `activate` / `deactivate` / `pause` | — |

---

### 3.4 `value` — Value

#### 3.4.1 Numeric and String Values

| Attribute | Value Type | Unit |
|-----------|------------|------|
| `brightness` | Number | `Percent` |
| `colorTemperature` | Number | `Kelvin` |
| Light `mode` | String | `*` |
| `position` | Number | `Percent` |
| `temperature` | Number | `Celsius` |
| `windSpeed` | String | `Level` |
| AC `mode` | String | `*` |

#### 3.4.2 Color Names

**Base colors**: `Blue`, `Red`, `Green`, `Yellow`, `Orange`, `Pink`, `Purple`, `Cyan`, `Magenta`, `Lavender`

**White variants**: `White`, `Warm White`, `Cool White`, `Sky Blue`

#### 3.4.3 Light Modes

Examples: `Romance`, `Soft`, `Reading`, `Eco`

#### 3.4.4 AC Modes

| Value | Description |
|-------|-------------|
| `Cool` | Cooling |
| `Heat` | Heating |
| `Dry` | Dehumidify |
| `Fan` | Fan only |
| `Auto` | Auto |

#### 3.4.5 Wind Speed Levels

| Value | Description |
|-------|-------------|
| `Low` | Low speed |
| `Medium` | Medium speed |
| `High` | High speed |

---

### 3.5 `unit` — Unit

| Unit | Applicable Attribute |
|------|----------------------|
| `Percent` | `brightness`, `position` |
| `Kelvin` | `colorTemperature` |
| `Celsius` | `temperature` |
| `Level` | `windSpeed` |
| `*` | Anything else (`color`, `mode`, or no attribute) |

---

### 3.6 `room` — Room

#### 3.6.1 Naming Convention

- Capitalized, words joined by a single space.
- Supports numeric / letter suffixes, e.g. `Bedroom 1`, `Bedroom A`, `Room B`.
- Supports prefix modifiers, e.g. `Master Bedroom`, `Second Bedroom`.

#### 3.6.2 Common Room Examples

| Category | Examples |
|----------|----------|
| Public areas | `Living Room`, `Dining Room`, `Kitchen`, `Entrance Hall`, `Corridor` |
| Bedrooms | `Master Bedroom`, `First Bedroom`, `Bedroom 1`, `Bedroom A` |
| Bathrooms | `Bathroom`, `Master Bathroom` |
| Work / Entertainment | `Home Office`, `Movie Theater`, `Gym` |
| Outdoor / Utility | `Balcony`, `Patio`, `Swimming Pool Area`, `Garage`, `Laundry Room` |
| Cultural | `Majlis` (Arabic-style reception room), `Prayer Room` |
| Others | `Closet`, `Nanny's Quarter`, `Room A`, `Room 1` |
| Unspecified | `*` |

---

### 3.7 `floor` — Floor

#### 3.7.1 Naming Convention

- Capitalized, words joined by a single space.
- Supports prefix modifiers.

#### 3.7.2 Examples

| Type | Examples |
|------|----------|
| Named floors | `Ground Floor`, `First Floor`, `Second Floor`, `Third Floor` |
| Relative floors | `Upstairs`, `Downstairs` |
| Unspecified (most common) | `*` |

---

## 4. Placeholder Convention

The character `*` means **the field is not applicable or not specified in this command**. 

---

## 5. End-to-End Examples

| Input | Output |
|-------|--------|
| Turn on the light in the living room | `turnOn\|Light\|*\|*\|*\|Living Room\|*` |
| Set the AC in the master bedroom to 24 degrees | `set\|AC\|temperature\|24\|Celsius\|Master Bedroom\|*` |
| Make the bedroom light a bit brighter on the second floor | `adjustUp\|Light\|brightness\|*\|*\|Bedroom\|Second Floor` |
| Pause the curtain in the home office | `pause\|Curtain\|*\|*\|*\|Home Office\|*` |
| Activate romantic mode | `activate\|Romantic Mode\|*\|*\|*\|*\|*` |
| Turn on the living room light and set the AC to cool | `turnOn\|Light\|*\|*\|*\|Living Room\|*\nset\|AC\|mode\|Cool\|*\|Living Room\|*` |

---

## 6. A Note on Enumerated Values

The colors, light modes, AC modes, wind speeds, rooms, and floors listed in this document are **verified samples**, not an exhaustive set. The model generalizes to reasonable expressions outside the listed values. When integrating with a downstream execution system, we recommend:

1. Match the core enums (`action`, `attribute`, `unit`) as a whitelist.
2. For open enums (`device`, `color`, `mode`, `room`, `floor`), normalize casing and apply a synonym map, then defer the final decision to the business layer.
3. Validate against the actual device inventory before dispatching commands.
