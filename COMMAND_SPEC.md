# Smart-Home Voice Command Specification

> This document covers both **input command recognition rules** and **output command generation rules**.
>
> Chinese version: [COMMAND_SPEC_zh.md](COMMAND_SPEC_zh.md)

---

# Part 1: Input Command Recognition Rules

## 1. Basic Structure of Input Commands

### 1.1 Command Composition

Input commands are generally composed of the following parts:

```
[action] + [device] + [attribute/value] + [location (optional)]
```

**Full structure:**

```
[location] + [action] + [device] + [attribute/value]
```

**Location** includes:

- **Room**: Living Room, Master Bedroom, Kitchen, Bathroom, Balcony, etc.
- **Floor**: Ground Floor, First Floor, Second Floor, Upstairs, Downstairs, etc.

**Examples:**

- `turn on the strip light` → action + device
- `set the strip light to blue` → action + device + value
- `increase the spotlight 10 brightness` → action + device + attribute
- `turn on all lights in the living room` → action + device + location
- `set the bedroom AC to 24 degrees` → action + location + device + value

**Note:**

- Location information **IS explicitly represented** in the output format fields 6 (room) and 7 (floor).
- Room and floor fields use the exact location names from the input (e.g., "Living Room", "Master Bedroom").

---

## 2. Action Word Categories

### 2.1 Turn-On Actions

| Action | Example |
| --- | --- |
| turn on | "turn on the strip light" |
| switch on | "switch on the strip light" |
| get ... going | "get the strip light going" |
| open (curtain only) | "open the curtain" |

**Intent variants:**

- `I'd like ... on` — "I'd like the strip light on"
- `... should be on` — "The strip light should be on"

---

### 2.2 Turn-Off Actions

| Action | Example |
| --- | --- |
| turn off | "turn off the floor lamp" |
| switch off | "switch off the AC" |
| close | "close the curtain" |

---

### 2.3 Set Actions

| Action | Syntax pattern | Example |
| --- | --- | --- |
| set ... to | set [device] to [value] | "set the strip light to blue" |
| make ... [value] | make [device] [value] | "make the strip light blue" |
| change ... to | change [device] to [value] | "change the floor lamp color to pink" |

**Intent variants:**

- `I want ... [value]` — "I want the strip light in blue"
- `... should be [value]` — "The strip light should be blue"
- `[value] for ..., please` — "Blue for the strip light, please"
- `Give ... [value]` — "Give the spotlights a warm tone"

---

### 2.4 Increase Actions

| Action | Example |
| --- | --- |
| increase | "increase the spotlight 10 brightness" |
| bring up | "bring up the floor lamp" |

---

### 2.5 Decrease Actions

| Action | Example |
| --- | --- |
| decrease | "decrease the desk lamp brightness" |
| dim | "dim the desk lamp" |
| lower | "lower the AC fan speed" |
| turn down | "turn it down" |
| bring ... down | "bring the brightness down a notch" |
| take ... down | "take the wind speed down on the AC" |

**Contextual expressions:**

- `... is too loud, turn it down` — "The AC fan is too loud, turn it down" (→ AC windSpeed)
- `... is too bright, turn it down` — "It's too bright, turn it down" (→ Light brightness)
- `... down a notch` — "bring the brightness down a notch"

---

### 2.6 Mode-Switching Actions

**Activating a scene:**

| Action | Example |
| --- | --- |
| switch to | "switch to relax mode" |
| switch ... to | "switch the room to relax mode" |
| set | "set the romantic mode" |
| change ... to | "change the mode to movie mode" |
| I want | "I want party mode" |
| I need ... on | "I need reading mode on" |

**Intent variants:**

- `Let's go into ...` — "Let's go into movie mode"
- `... please` — "Romantic mode, please"
- `Pull up ...` — "Pull up party mode"
- `Get ... into ...` — "Get the room into relax mode"
- `... needs to be ...` — "The room needs to be in sleeping mode"

**Maintaining a scene:**

| Pattern | Example |
| --- | --- |
| keep ... in | "keep the room in relax mode" |
| stay in | "The room should stay in sleeping mode" |
| hold ... in | "Let's hold the room in romantic mode" |
| leave ... as it is | "Leave the room as it is in movie mode" |

---

## 3. Device Name Expressions

### 3.1 Standard Device Names

**Lighting Devices:**

| Possible input form | Normalized form |
| --- | --- |
| light / lights | Light |
| strip light | Strip Light |
| floor lamp | Floor Lamp |
| spot light / spotlight | Spot Light |
| desk lamp | Desk Lamp |
| ceiling light | Ceiling Light |
| wall light | Wall Light |
| recessed light | Recessed Light |
| downlight | Downlight |
| chandelier | Chandelier |
| track light | Track Light |
| ambient light | Ambient Light |
| reading light | Reading Light |
| vanity light | Vanity Light |
| night light | Night Light |
| LED strip / led strip | LED Strip |
| tv light strip | TV Light Strip |

**Environmental Control:**

| Possible input form | Normalized form |
| --- | --- |
| AC / ac | AC |
| curtain | Curtain |
| sheer curtain | Sheer Curtain |
| blind | Blind |

---

### 3.2 Numbered and Lettered Devices

**Format:** `device name + number` or `device name + letter`

**Examples:**

- `light 1`, `light 2`, `light 3`, `light 5`
- `strip light 1`, `strip light 3`, `strip light 5`
- `spot light 1`, `spot light 2`, `spot light 10`
- `light A`, `light B`, `light C`, `light F`
- `curtain A`, `curtain B`, `curtain 1`
- `AC 1`, `AC 2`

**Input characteristics:**

- The number or letter follows the device name directly.
- Numbers can be single or double digit.
- Letters are typically A, B, C, etc.

---

### 3.3 Use of the Definite Article

The vast majority of commands use the definite article `the`:

- ✅ "turn on **the** strip light"
- ✅ "set **the** AC temperature to 24"

---

## 4. Attribute and Value Expressions

### 4.1 Brightness

**Numeric expressions:**

| Input format | Example |
| --- | --- |
| to [number]% | "set the strip light brightness to 30%" |
| to [number] percent | "set the floor lamp brightness to 60 percent" |

**Range:** 0–100

**Fuzzy adjustment:**

- "increase the brightness" — no specific value
- "dim the lamp" — no specific value
- "down a notch" — no specific value

---

### 4.2 Color

**Direct color names:**

| Color | Example |
| --- | --- |
| blue | "set the strip light to blue" |
| red | "make the strip light red" |
| green | "change the desk lamp color to green" |
| yellow | "change the strip light 1 color to yellow" |
| orange | "make the floor lamp orange" |
| pink | "change the floor lamp color to pink" |
| purple | "set the floor lamp color to purple" |
| cyan | "make the strip light 1 cyan" |
| lavender | "make the floor lamp lavender" |

**White family:**

| Color | Example |
| --- | --- |
| white | "change the strip light 5 color to white" |
| warm white | "set the floor lamp to warm white" |
| cool white | "set the strip light color to cool white" |
| sky blue | "change the tv light strip color to sky blue" |

**Descriptive expressions:**

- "warm light" → interpreted as warm white or a lower color temperature
- "warm tone" → interpreted as warm white or a lower color temperature

---

### 4.3 Color Temperature

**Numeric expressions:**

| Input format | Example |
| --- | --- |
| to [number]k / [number]K | "set the spotlight 1 color temperature to 3500k" |

**Supported values:** 1000-10000 K (continuous range)

**Fuzzy adjustment:**

- "warm up the spotlights" → lower the color temperature (adjustDown)
- "increase the color temperature" → raise the color temperature (adjustUp)
- "decrease the color temperature" → lower the color temperature (adjustDown)

---

### 4.4 Temperature (AC)

**Numeric expressions:**

| Input format | Example |
| --- | --- |
| to [number] degrees | "set the AC temperature to 24 degrees" |
| to [number]° | "set the AC temperature to 24°" |
| to [number] | "set the AC to 24 degrees" |

**Range:** 16–29°C

**Fuzzy adjustment:**

- "decrease the AC temperature" — no specific value

---

### 4.5 Position (Curtain)

**Numeric expressions:**

| Input format | Example |
| --- | --- |
| to [number]% | "open the curtain to 25%" |
| level to [number]% | "set the curtain opening level to 75%" |

**Range:** 0–100%

**Open/close expressions:**

- "open the curtain" → fully open
- "close the curtain" → fully closed

**Fuzzy adjustment:**

- "decrease the curtain opening level" — no specific value

---

### 4.6 Mode

**AC modes:**

| Input expression | Mode |
| --- | --- |
| fan mode / to fan | Fan |
| dry / to dry | Dry |
| heat mode / mode to heat | Heat |
| cool / mode to cool / cooling mode | Cool |
| auto mode / to auto | Auto |

**Examples:**

- "set the AC to fan mode"
- "switch the AC to dry"
- "set the AC mode to heat"
- "change the AC mode to cool"
- "set the AC to auto mode"

**Scene modes:**

Scenes actually present in the training data (by frequency):

| Input expression | Scene name |
| --- | --- |
| romantic mode | Romantic Mode |
| party mode | Party Mode |
| reading mode | Reading Mode |
| sleeping mode | Sleeping Mode |
| relax mode | Relax Mode |
| wakeup mode | Wakeup Mode |
| home mode | Home Mode |
| movie mode | Movie Mode |
| away mode | Away Mode |
| holiday mode | Holiday Mode |
| guest mode | Guest Mode |
| dining mode | Dining Mode |
| meeting mode | Meeting Mode |
| cinema mode | Cinema Mode |
| leisure mode | Leisure Mode |

**Light modes (scene presets):**

| Input expression | Mode |
| --- | --- |
| reading mode | Reading |
| romance mode | Romance |
| eco mode | Eco |
| soft mode | Soft |

> These are light scene presets set with `turnOn` + `mode` attribute (e.g. `turnOn|Light|mode|Reading|*|room|*`), not AC modes.

**Examples:**

- "activate romantic mode"
- "I want party mode"
- "switch to relax mode in the living room"
- "turn on reading mode in the bedroom"

---

### 4.7 Wind Speed (AC)

**Explicit values:**

| Input expression | Value |
| --- | --- |
| low wind / low speed | Low |
| medium wind / medium speed | Medium |
| high wind / high speed | High |

**Examples:**

- "set the AC wind speed to high"
- "change AC to medium wind"

**Fuzzy adjustment:**

- "increase the wind speed" → adjustUp
- "lower the fan speed" → adjustDown

---

### 4.8 "Too loud / too bright" expressions

These descriptive complaints do **not** map to a volume control (there is no Music device or volume attribute in the data). They map to whichever device is actually causing the discomfort:

- "The AC fan is too loud / blowing too hard" → `adjustDown|AC|windSpeed|*|*|...`
- "It's too bright, turn it down" → `adjustDown|Light|brightness|*|*|...`

---

## 5. Compound Command Patterns

### 5.1 Coordinated Structure

**Connector:** `and`

**Pattern:** `[action 1] + and + [action 2]`

**Examples:**

```
Input: "turn on the floor lamp and set the floor lamp to warm white"
Breakdown:
  - Action 1: turn on the floor lamp
  - Action 2: set the floor lamp to warm white

Input: "Bring up the floor lamp and make it warm white"
Breakdown:
  - Action 1: Bring up the floor lamp (turn on)
  - Action 2: make it warm white (set color)

Input: "The floor lamp on and set to warm white"
Breakdown:
  - Action 1: The floor lamp on (turn on)
  - Action 2: set to warm white (set color)
```

---

### 5.2 Integrated Description

**Pattern:** multiple attributes within a single action

**Examples:**

```
Input: "Get the floor lamp on in warm white"
Meaning: turn on + set color

Input: "I'd like the floor lamp on with a warm white tone"
Meaning: turn on + set color
```

---

## 6. Tone and Politeness

### 6.1 Polite Requests

| Pattern | Example |
| --- | --- |
| please | "Blue for the strip light, please" |
| I'd like | "I'd like the strip light on" |
| I want | "I want the strip light in blue" |
| I need | "I need reading mode on" |

---

### 6.2 Declarative Commands

| Pattern | Example |
| --- | --- |
| ... should be | "The strip light should be on" |
| ... needs to be | "The room needs to be in sleeping mode" |

---

### 6.3 Imperative (Direct Commands)

The most common form, using the action word directly:

- "Turn on the strip light"
- "Set the AC to 24 degrees"
- "Dim the desk lamp"

---

## 7. Summary of Common Input Patterns

### 7.1 Pattern 1: Simple On/Off

```
[action] + the + [device]
```

**Examples:**

- turn on the strip light
- close the curtain
- switch on the AC

---

### 7.2 Pattern 2: Set a Value

```
set + the + [device] + [attribute] + to + [value] + [unit]
```

**Examples:**

- set the strip light brightness to 30%
- set the AC temperature to 24 degrees
- set the spotlight 1 color temperature to 3500k

---

### 7.3 Pattern 3: Set a Color

```
[action] + the + [device] + [color]
```

**Examples:**

- set the strip light to blue
- make the floor lamp orange
- change the desk lamp color to green

---

### 7.4 Pattern 4: Fuzzy Adjustment

```
[adjustment verb] + the + [device] + [attribute]
```

**Examples:**

- increase the spotlight brightness
- decrease the desk lamp brightness
- dim the desk lamp

---

### 7.5 Pattern 5: Scene Switch

```
[switch verb] + [scene name]
```

**Examples:**

- activate romantic mode
- I want party mode
- switch to relax mode

---

### 7.6 Pattern 6: Compound Command

```
[command 1] + and + [command 2]
```

**Examples:**

- turn on the floor lamp and set it to warm white
- open the curtain and turn on the floor lamp

---

## 8. Special Language Phenomena

### 8.1 Pronoun Substitution

In the second part of a compound command, the pronoun `it` often refers to the device mentioned earlier:

```
Input: "turn on the strip light and set it to blue"
       ("it" in the second part refers to "strip light")

Input: "I'd like the floor lamp on with a warm white tone"
       (implied pronoun; the subject of "tone" is "floor lamp")
```

---

### 8.2 Elliptical Structures

**Omitted verb:**

```
Input: "The floor lamp on and set to warm white"
Reading: [turn] the floor lamp on and set [it] to warm white
```

**Omitted device name:**

```
Input: "set it to blue" (in context, "it" refers to the previously mentioned device)
```

---

### 8.3 Descriptive Language

Some commands use descriptive language rather than direct commands:

| Descriptive expression | Actual intent |
| --- | --- |
| "The AC fan is too loud" | lower the AC wind speed (adjustDown windSpeed) |
| "It's too bright" | lower the brightness (adjustDown brightness) |
| "warm up the spotlights" | lower the color temperature (make it warmer) |
| "Give the spotlights a warm tone" | set to a warm color |

---

## 9. Statistical Characteristics of Input Commands

### 9.1 Top 5 Action Words

1. **set** — set commands (most common)
2. **turn on / turn off** — on/off commands
3. **change** — change commands
4. **make** — make/set commands
5. **increase / decrease** — adjustment commands

---

### 9.2 Top 5 Devices

1. **Light** (generic light, including numbered/lettered variants)
2. **AC** (air conditioner)
3. **Curtain** (including Sheer Curtain)
4. **Strip Light**
5. **Spot Light**

---

### 9.3 Top 5 Attribute Operations

1. **brightness** — brightness adjustment (most common)
2. **color** — color setting
3. **temperature** — temperature setting (AC)
4. **colorTemperature** — color temperature adjustment
5. **windSpeed** — wind speed control (AC)
6. **position** — position control (curtain)
7. **mode** — mode switching (AC and scenes)

---

### 9.4 Command Length

| Length type | Word count | Example |
| --- | --- | --- |
| Short | 3–5 words | "turn on the strip light" |
| Medium | 6–9 words | "set the strip light brightness to 30%" |
| Long | 10+ words | "turn on the floor lamp and set it to warm white" |

**Typical length:** 5–8 words

---

## 10. Easily Confused Expressions

### 10.1 Ambiguity of "warm"

| Input | Interpreted as |
| --- | --- |
| "set to warm white" | color setting (Warm White) |
| "warm light" | color setting (Warm White) or lower color temperature |
| "warm up the spotlights" | lower color temperature (adjustDown colorTemperature) |
| "warm tone" | color setting (Warm White) or lower color temperature |

---

### 10.2 Different Expressions of a Mode

| Input | Actual scene |
| --- | --- |
| "romantic mode" | Romantic Mode |
| "the romantic mode" | Romantic Mode |
| "I want a romantic mode" | Romantic Mode |
| "set the romantic mode" | Romantic Mode |

> Note: Whether or not an article is used, and whichever verb is used, they all point to the same scene.

---

### 10.3 Opening/Closing a Curtain

| Input | Action |
| --- | --- |
| "open the curtain" | open (turnOn) |
| "close the curtain" | close (turnOff) |
| "open the curtain to 25%" | set position to 25% (set) |

---

## 11. Input Command Validation Checklist

When parsing an input command, check the following elements:

- [ ] **Action recognition**: identify the correct action type (on-off / set / adjust / mode)
- [ ] **Device extraction**: accurately extract the device name (including number)
- [ ] **Attribute detection**: determine which attribute is being operated on (brightness / color / temperature, etc.)
- [ ] **Value extraction**: extract the specific value and unit
- [ ] **Compound command splitting**: identify and split compound commands (joined by "and")
- [ ] **Pronoun resolution**: resolve pronouns (it/its) to the specific device
- [ ] **Descriptive language understanding**: convert descriptive expressions into specific actions
- [ ] **Fuzzy word handling**: identify fuzzy adjustment words (a little / a bit)

---
---

# Part 2: Output Command Generation Rules

## 1. Output Format Specification

### 1.1 Basic Format

```
action|device|attribute|value|unit|room|floor
```

The 7 fields are, in order: `action|device|attribute|value|unit|room|floor`.

**Field 6 (room):** Room name (e.g., "Living Room", "Master Bedroom", "Kitchen") or `*` if not specified
**Field 7 (floor):** Floor identifier (e.g., "Ground Floor", "First Floor") or `*` if not specified

### 1.2 Joining Multiple Commands

- Join multiple commands with a newline `\n`.

### 1.3 Example

```
turnOn|Strip Light|*|*|*|Living Room|*
set|Strip Light|color|Blue|*|Living Room|*
set|Light|brightness|70|Percent|Master Bedroom|*
activate|Party Mode|*|*|*|Living Room|*
```

---

## 2. Field Definitions

### 2.1 Action Type (Field 1)

| Action | Description | Applicable scenario |
| --- | --- | --- |
| `turnOn` | Turn on a device | Turn on a light, AC, or open a curtain |
| `turnOff` | Turn off a device | Turn off a light, AC, or close a curtain |
| `set` | Set to a specific value | Set brightness, color, temperature, etc. |
| `adjustUp` | Increase an attribute value | Raise brightness, increase color temperature |
| `adjustDown` | Decrease an attribute value | Lower brightness, decrease wind speed |
| `activate` | Activate a scene mode | Enable party mode, romantic mode |
| `deactivate` | Cancel a scene mode | Exit a scene mode |
| `pause` | Pause a device in motion | Stop a curtain/blind mid-travel |

**Action selection principles:**

- When the command contains an **explicit numeric value**, you must use `set`.
- When the command contains fuzzy words such as "a little" / "a bit", use `adjustUp` / `adjustDown` and leave the value empty.
- `pause` is used for curtain-type devices (Curtain / Blind / Sheer Curtain) to stop them mid-travel — e.g. "stop the curtain", "pause the blind".

---

### 2.2 Device Name (Field 2)

#### 2.2.1 Lighting Devices

| Device name | Meaning | Numbered/Lettered example |
| --- | --- | --- |
| `Light` | Generic light | Light 1, Light 2, Light A, Light F |
| `Strip Light` | Strip light | Strip Light 1, Strip Light 3 |
| `Floor Lamp` | Floor lamp | Floor Lamp 1 |
| `Spot Light` | Spotlight | Spot Light 1, Spot Light 10 |
| `Desk Lamp` | Desk lamp | Desk Lamp 1 |
| `Ceiling Light` | Ceiling light | Ceiling Light 1 |
| `Wall Light` | Wall light | Wall Light 1 |
| `Recessed Light` | Recessed light | Recessed Light 1 |
| `Downlight` | Downlight | Downlight 1 |
| `Chandelier` | Chandelier | Chandelier 1 |
| `Track Light` | Track light | Track Light 1 |
| `Ambient Light` | Ambient light | - |
| `Reading Light` | Reading light | - |
| `Vanity Light` | Vanity light | - |
| `Night Light` | Night light | - |
| `LED Strip` | LED strip | LED Strip 1 |
| `TV Light Strip` | TV light strip | TV Light Strip 1 |

**⚠️ Important rule:**

- `Spot Light` is written as **two words**.
- Whether the input is "spotlight" or "spot light", the output is always `Spot Light`.

#### 2.2.2 Environmental Control Devices

| Device name | Meaning | Description |
| --- | --- | --- |
| `AC` | Air conditioner | Supports temperature, mode, and wind speed control |
| `Curtain` | Curtain | Controls open/close position |
| `Sheer Curtain` | Sheer curtain | Controls open/close position |
| `Blind` | Blind | Controls open/close position |

**⚠️ Important rule:**

- For curtain-type devices, use only `Curtain`, `Sheer Curtain`, and `Blind`.

#### 2.2.3 Scene Modes

| Device name | Meaning | Description |
| --- | --- | --- |
| `Romantic Mode` | Romantic mode | Scene mode |
| `Party Mode` | Party mode | Scene mode |
| `Reading Mode` | Reading mode | Scene mode |
| `Sleeping Mode` | Sleeping mode | Scene mode |
| `Relax Mode` | Relax mode | Scene mode |
| `Wakeup Mode` | Wakeup mode | Scene mode |
| `Home Mode` | Home mode | Scene mode |
| `Movie Mode` | Movie mode | Scene mode |
| `Away Mode` | Away mode | Scene mode |
| `Holiday Mode` | Holiday mode | Scene mode |
| `Guest Mode` | Guest mode | Scene mode |
| `Dining Mode` | Dining mode | Scene mode |
| `Meeting Mode` | Meeting mode | Scene mode |
| `Cinema Mode` | Cinema mode | Scene mode |
| `Leisure Mode` | Leisure mode | Scene mode |

---

### 2.3 Attribute (Field 3)

| Attribute | Description | Applicable device | Value range |
| --- | --- | --- | --- |
| `brightness` | Brightness | All lights | 0–100 |
| `color` | Color | All lights | See color table |
| `colorTemperature` | Color temperature | Lights (especially Spot Light) | 1000-10000 |
| `temperature` | Temperature | AC | 16–29 |
| `position` | Open/close position | Curtain/Blind/Sheer Curtain | 0–100 |
| `mode` | Operating mode | AC, Light | Fan/Dry/Heat/Cool/Auto (AC); Reading/Romance/Eco/Soft (Light) |
| `windSpeed` | Wind speed | AC | Low/Medium/High |
| `*` | No attribute | turnOn/turnOff/activate/deactivate/pause | - |

---

### 2.4 Value (Field 4)

#### 2.4.1 Numeric

| Type | Range | Unit |
| --- | --- | --- |
| Brightness | 0–100 | Percent |
| Position | 0–100 | Percent |
| Color temperature | 1000-10000 | Kelvin |
| Temperature | 16–29 | Celsius |
| Wind speed | Low, Medium, High | Level |

#### 2.4.2 Color Names

**Base colors:**

- `Blue`
- `Red`
- `Green`
- `Yellow`
- `Orange`
- `Pink`
- `Purple`
- `Cyan`
- `Magenta`
- `Lavender`

**White family:**

- `White`
- `Warm White`
- `Cool White`
- `Sky Blue`

#### 2.4.3 AC Modes

| Mode | Description |
| --- | --- |
| `Fan` | Fan |
| `Dry` | Dehumidify |
| `Heat` | Heat |
| `Cool` | Cool |
| `Auto` | Auto |

#### 2.4.4 Wind Speed Levels

| Level | Description |
| --- | --- |
| `Low` | Low speed |
| `Medium` | Medium speed |
| `High` | High speed |

#### 2.4.5 Placeholder

- When the action is `turnOn/turnOff/adjustUp/adjustDown/activate/deactivate`,
- or when the attribute does not need a specific value,
- use `*` as a placeholder.

---

### 2.5 Unit (Field 5)

| Unit | Applicable attribute |
| --- | --- |
| `Percent` | brightness, position |
| `Kelvin` | colorTemperature |
| `Celsius` | temperature |
| `Level` | windSpeed |
| `*` | other cases (color, mode, or no attribute) |

---

### 2.6 Room (Field 6)

Room names are capitalized with proper spacing. Rooms actually present in the training data (by frequency):

**High-frequency rooms:**
- `Living Room`
- `Master Bedroom`
- `Dining Room`
- `Majlis` (Arabic-style reception/sitting room)
- `Kitchen`
- `Home Office`
- `Balcony`
- `Bathroom` / `Master Bathroom`
- `Prayer Room`
- `Patio`

**Numbered/lettered bedrooms and rooms:**
- `First Bedroom` / `Second Bedroom` / `Third Bedroom` ...
- `Bedroom 1` / `Bedroom 2` / `Bedroom 3` ...
- `Bedroom A` / `Bedroom B` / `Bedroom C` ...
- `Room A` / `Room B` / `Room 1` / `Room 2` ...

**Other rooms:**
- `Nanny's Quarter`, `Closet`, `Entrance Hall`
- `Movie Theater`, `Laundry Room`, `Gym`
- `Corridor`, `Garage`, `Swimming Pool Area`
- `*` (when no room is specified)

---

### 2.7 Floor (Field 7)

Floor identifiers. Values actually present in the training data (by frequency):

- `*` (when no floor is specified - most common)
- `Ground Floor`
- `First Floor`
- `Upstairs`
- `Downstairs`
- `Second Floor`
- `Third Floor`

> **Note:** British-style floor naming is used — `Ground Floor` is the entry level and `First Floor` is the level above it. Do **not** use `1st Floor` / `2nd Floor` style.

---

## 3. Input Parsing Rules

### 3.1 On/Off Operations

| Input keyword | Action | Example |
| --- | --- | --- |
| turn on, switch on, get going | `turnOn` | "turn on the strip light" |
| turn off, switch off | `turnOff` | "turn off the floor lamp" |
| close | `turnOff` | "close the curtain" |
| open (curtain) | `turnOn` | "open the blind" |
| stop, pause, halt | `pause` | "stop the curtain" |

**Pause rule:**

- `pause` applies only to curtain-type devices (Curtain / Blind / Sheer Curtain) to stop them mid-travel.
- The attribute and all later fields are `*`.

```
Input:  "stop the curtain in the living room"
Output: pause|Curtain|*|*|*|Living Room|*

Input:  "pause the blind"
Output: pause|Blind|*|*|*|*|*
```

---

### 3.2 Set Operations

| Input pattern | Action | Output format |
| --- | --- | --- |
| set ... to [value] | `set` | `set\|device\|attribute\|value\|unit\|*\|*` |
| make ... [color] | `set` | `set\|device\|color\|color\|*\|*\|*` |
| change ... to [value] | `set` | `set\|device\|attribute\|value\|unit\|*\|*` |

**Core principle:**

- When the command contains an **explicit numeric value**, the action must be `set`.
- For example: "set brightness to 50", "make it 24 degrees".

**Examples:**

```
Input:  "set the strip light to blue"
Output: set|Strip Light|color|Blue|*|*|*

Input:  "change AC temperature to 24"
Output: set|AC|temperature|24|Celsius|*|*
```

---

### 3.3 Adjustment Operations

| Input keyword | Action | Example |
| --- | --- | --- |
| increase, bring up | `adjustUp` | "increase the brightness" |
| decrease, dim, lower, turn down | `adjustDown` | "dim the desk lamp" |

**Fuzzy adjustment handling:**

- When the command contains "a little" / "a bit",
- use `*` in the value field of the output,
- and let the backend system fill in the default adjustment amount automatically.

**Examples:**

```
Input:  "lower the AC fan speed a little"
Output: adjustDown|AC|windSpeed|*|*|*|*

Input:  "dim the light a bit"
Output: adjustDown|Desk Lamp|brightness|*|*|*|*
```

---

### 3.4 Mode Operations

#### 3.4.1 Scene Modes

**Activating a scene:**

| Input pattern | Action | Output format |
| --- | --- | --- |
| switch to [mode] | `activate` | `activate\|mode name\|*\|*\|*\|*\|*` |
| set the [mode] | `activate` | `activate\|mode name\|*\|*\|*\|*\|*` |
| I want [mode] | `activate` | `activate\|mode name\|*\|*\|*\|*\|*` |

**Example:**

```
Input:  "activate romantic mode"
Output: activate|Romantic Mode|*|*|*|*|*
```

**Exiting a scene:**

| Input pattern | Action | Output format |
| --- | --- | --- |
| exit [mode] | `deactivate` | `deactivate\|mode name\|*\|*\|*\|*\|*` |
| turn off [mode] | `deactivate` | `deactivate\|mode name\|*\|*\|*\|*\|*` |
| deactivate [mode] | `deactivate` | `deactivate\|mode name\|*\|*\|*\|*\|*` |

**Example:**

```
Input:  "exit movie mode"
Output: deactivate|Movie Mode|*|*|*|*|*
```

#### 3.4.2 Device Modes

**⚠️ Special rule:**

- **Light mode**: For light scene presets (Reading, Romance, Eco, Soft), use `turnOn` action with mode attribute:
  - `turnOn|Light|mode|Reading|*|room|*`
- **AC mode**: use the `set` action:
  - `set|AC|mode|Cool|*|room|*`

**Examples:**

```
Input:  "set AC to cool mode"
Output: set|AC|mode|Cool|*|Living Room|*

Input:  "turn on reading mode" (light)
Output: turnOn|Light|mode|Reading|*|Master Bedroom|*

Input:  "switch on reading mode in the master bedroom"
Output: turnOn|Light|mode|Reading|*|Master Bedroom|*
```

---

### 3.5 Value Extraction Rules

| Input format | Extracted result | Output format |
| --- | --- | --- |
| 30%, 30 percent | 30 | `30\|Percent` |
| 3500k, 3500K | 3500 | `3500\|Kelvin` |
| 24 degrees, 24° | 24 | `24\|Celsius` |

---

### 3.6 Compound Command Handling

**Join rules:**

- Join multiple commands with `\n`.

**Examples:**

```
Input:  "turn on the strip light and set it to blue"
Output: turnOn|Strip Light|*|*|*|*|*\nset|Strip Light|color|Blue|*|*|*

Input:  "open the curtain and turn on the floor lamp"
Output: turnOn|Curtain|*|*|*|*|*\nturnOn|Floor Lamp|*|*|*|*|*
```

---

## 4. Special Rules

### 4.1 Multi-Intent Handling

When a command contains multiple intents:

- If the **floor** is not stated separately, it defaults to the same floor.
- If the **room** is not stated separately, it defaults to the same room.
- When both are omitted, the result is still considered correct.

**Example:**

```
Input: "turn on all lights in the living room"
Note:  all lights default to the same room (living room)
```

---

### 4.2 Device Name Normalization

#### Rule 1: Spot Light is two words

- Standard form: `Spot Light`
- Whether the input is "spotlight" or "spot light", the output is always `Spot Light`.

#### Rule 2: Curtain device naming

- Standard forms: `Curtain`, `Sheer Curtain`, `Blind`

---

## 5. Complete Examples

### 5.1 Basic Operations

| Input command | Output command |
| --- | --- |
| Turn on the strip light | `turnOn\|Strip Light\|*\|*\|*\|*\|*` |
| Close the curtain | `turnOff\|Curtain\|*\|*\|*\|*\|*` |
| Set the strip light to blue | `set\|Strip Light\|color\|Blue\|*\|*\|*` |
| Set AC temperature to 24 | `set\|AC\|temperature\|24\|Celsius\|*\|*` |
| Turn on the living room lights | `turnOn\|Light\|*\|*\|*\|Living Room\|*` |
| Set master bedroom lights to 70 percent | `set\|Light\|brightness\|70\|Percent\|Master Bedroom\|*` |

---

### 5.2 Adjustment Operations

| Input command | Output command |
| --- | --- |
| Increase spotlight 10 brightness | `adjustUp\|Spot Light 10\|brightness\|*\|*\|*\|*` |
| Dim the desk lamp | `adjustDown\|Desk Lamp\|brightness\|*\|*\|*\|*` |
| Lower the AC fan speed | `adjustDown\|AC\|windSpeed\|*\|*\|*\|*` |
| Turn down the brightness a bit | `adjustDown\|Strip Light\|brightness\|*\|*\|*\|*` |

---

### 5.3 Scene Modes

| Input command | Output command |
| --- | --- |
| Activate romantic mode | `activate\|Romantic Mode\|*\|*\|*\|*\|*` |
| Exit movie mode | `deactivate\|Movie Mode\|*\|*\|*\|*\|*` |
| I want party mode | `activate\|Party Mode\|*\|*\|*\|*\|*` |
| Activate party mode in the living room | `activate\|Party Mode\|*\|*\|*\|Living Room\|*` |
| Turn on reading mode in master bedroom | `turnOn\|Light\|mode\|Reading\|*\|Master Bedroom\|*` |

---

### 5.4 Wind Speed Operations

| Input command | Output command |
| --- | --- |
| Set AC wind speed to high | `set\|AC\|windSpeed\|High\|Level\|*\|*` |
| Change living room AC to medium wind | `set\|AC\|windSpeed\|Medium\|Level\|Living Room\|*` |
| Lower the fan speed | `adjustDown\|AC\|windSpeed\|*\|*\|*\|*` |

---

### 5.5 Compound Commands

| Input command | Output command |
| --- | --- |
| Turn on strip light and set to blue | `turnOn\|Strip Light\|*\|*\|*\|*\|*\nset\|Strip Light\|color\|Blue\|*\|*\|*` |
| Set brightness to 50 and color to warm white | `set\|Strip Light\|brightness\|50\|Percent\|*\|*\nset\|Strip Light\|color\|Warm White\|*\|*\|*` |
| Set kitchen lights to warm white and 80 percent | `set\|Light\|color\|Warm White\|*\|Kitchen\|*\nset\|Light\|brightness\|80\|Percent\|Kitchen\|*` |
| Activate party mode and turn on light F | `activate\|Party Mode\|*\|*\|*\|Living Room\|*\nturnOn\|Light F\|*\|*\|*\|Living Room\|*` |

---

## 6. Standard Form Reference

| Aspect | Standard form |
| --- | --- |
| Device name spelling | `Spot Light` |
| Curtain naming | `Curtain` / `Blind` / `Sheer Curtain` |
| Command separator | use `\n` |
| Action for numeric value | use `set` |
| Fuzzy adjustment | `adjustUp\|...\|brightness\|*\|...` (value left as `*`) |
| room/floor | room in field 6, floor in field 7 (or `*`) |
| Wind speed unit | `windSpeed\|High\|Level` |

---

## 7. Quick Reference

### 7.1 Action Selection Flowchart

```
Does it contain an explicit value?
├─ Yes → set
└─ No
   ├─ Turn device on/off?  → turnOn / turnOff
   ├─ Stop a curtain mid-travel? → pause
   ├─ Increase/decrease?   → adjustUp / adjustDown
   └─ Scene mode?          → activate / deactivate
```

### 7.2 Unit Quick Reference

| Attribute | Unit |
| --- | --- |
| brightness, position | Percent |
| colorTemperature | Kelvin |
| temperature | Celsius |
| windSpeed | Level |
| color, mode, on/off operations | * |
