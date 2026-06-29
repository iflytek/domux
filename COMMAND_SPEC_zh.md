# 智能家居语音指令完整规则

> 本文档包含**输入指令识别规则**和**输出命令生成规则**
>
> English version: [COMMAND_SPEC.md](COMMAND_SPEC.md).

---

# 第一部分：输入指令识别规则

## 一、输入指令的基本结构

### 1.1 指令组成模式

输入指令通常由以下部分组成：

```
[动作词] + [设备名称] + [属性/值描述] + [位置信息(可选)]
```

**完整结构**：

```
[位置信息] + [动作词] + [设备名称] + [属性/值描述]
```

**位置信息**包括:

- **房间**（room）：Living Room, Master Bedroom, Kitchen, Bathroom, Balcony 等
- **楼层**（floor）：Ground Floor, First Floor, Second Floor, upstairs, downstairs 等

**示例**：

- `turn on the strip light` → 动作词 + 设备
- `set the strip light to blue` → 动作词 + 设备 + 值描述
- `increase the spotlight 10 brightness` → 动作词 + 设备 + 属性
- `turn on all lights in the living room` → 动作词 + 设备 + 位置信息
- `set the bedroom AC to 24 degrees` → 动作词 + 位置 + 设备 + 值描述

**注意**：

- 位置信息**会显式体现**在输出格式的第6字段（room）和第7字段（floor）中
- 房间和楼层字段使用输入中的精确位置名称（如"Living Room", "Master Bedroom"）

---

## 二、动作词分类

### 2.1 开启类动作词

| 动作词 | 示例 |
|--------|------|
| turn on | "turn on the strip light" |
| switch on | "switch on the strip light" |
| get ... going | "get the strip light going" |
| open (仅窗帘) | "open the curtain" |

**表达意愿的变体**：
- `I'd like ... on` - "I'd like the strip light on"
- `... should be on` - "The strip light should be on"

---

### 2.2 关闭类动作词

| 动作词 | 示例 |
|--------|------|
| turn off | "turn off the floor lamp" |
| switch off | "switch off the AC" |
| close | "close the curtain" |

---

### 2.3 设置类动作词

| 动作词 | 语法模式 | 示例 |
|--------|---------|------|
| set ... to | set [设备] to [值] | "set the strip light to blue" |
| make ... [值] | make [设备] [值] | "make the strip light blue" |
| change ... to | change [设备] to [值] | "change the floor lamp color to pink" |

**表达意愿的变体**：
- `I want ... [值]` - "I want the strip light in blue"
- `... should be [值]` - "The strip light should be blue"
- `[值] for ..., please` - "Blue for the strip light, please"
- `Give ... [值]` - "Give the spotlights a warm tone"

---

### 2.4 增加类动作词

| 动作词 | 示例 |
|--------|------|
| increase | "increase the spotlight 10 brightness" |
| bring up | "bring up the floor lamp" |

---

### 2.5 减少类动作词

| 动作词 | 示例 |
|--------|------|
| decrease | "decrease the desk lamp brightness" |
| dim | "dim the desk lamp" |
| lower | "lower the AC fan speed" |
| turn down | "turn it down" |
| bring ... down | "bring the brightness down a notch" |
| take ... down | "take the wind speed down on the AC" |

**语境表达**：
- `... is too loud, turn it down` - "The AC fan is too loud, turn it down"（→ AC windSpeed）
- `... is too bright, turn it down` - "It's too bright, turn it down"（→ Light brightness）
- `... down a notch` - "bring the brightness down a notch"

---

### 2.6 模式切换类动作词

**激活场景**：

| 动作词 | 示例 |
|--------|------|
| switch to | "switch to relax mode" |
| switch ... to | "switch the room to relax mode" |
| set | "set the romantic mode" |
| change ... to | "change the mode to movie mode" |
| I want | "I want party mode" |
| I need ... on | "I need reading mode on" |

**表达意愿的变体**：
- `Let's go into ...` - "Let's go into movie mode"
- `... please` - "Romantic mode, please"
- `Pull up ...` - "Pull up party mode"
- `Get ... into ...` - "Get the room into relax mode"
- `... needs to be ...` - "The room needs to be in sleeping mode"

**保持场景**：

| 表达模式 | 示例 |
|---------|------|
| keep ... in | "keep the room in relax mode" |
| stay in | "The room should stay in sleeping mode" |
| hold ... in | "Let's hold the room in romantic mode" |
| leave ... as it is | "Leave the room as it is in movie mode" |

---

## 三、设备名称表达方式

### 3.1 设备名称标准化规则

**照明设备：**

| 输入可能的形式 | 标准化后 |
|--------------|---------|
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

**环境控制设备：**

| 输入可能的形式 | 标准化后 |
|--------------|---------|
| AC / ac | AC |
| curtain | Curtain |
| sheer curtain | Sheer Curtain |
| blind | Blind |

---

### 3.2 编号与字母设备

**格式**：`设备名 + 数字` 或 `设备名 + 字母`

**示例**：

- `light 1`, `light 2`, `light 3`, `light 5`
- `strip light 1`, `strip light 3`, `strip light 5`
- `spot light 1`, `spot light 2`, `spot light 10`
- `light A`, `light B`, `light C`, `light F`
- `curtain A`, `curtain B`, `curtain 1`
- `AC 1`, `AC 2`

**输入特征**：

- 数字或字母直接跟在设备名后面
- 数字可以是一位或两位
- 字母通常是 A、B、C 等

---

### 3.3 定冠词的使用

绝大多数指令都使用定冠词 `the`：

- ✅ "turn on **the** strip light"
- ✅ "set **the** AC temperature to 24"

---

## 四、属性与值的表达

### 4.1 亮度（Brightness）

**数值表达**：

| 输入格式 | 示例 |
|---------|------|
| to [数字]% | "set the strip light brightness to 30%" |
| to [数字] percent | "set the floor lamp brightness to 60 percent" |

**范围**：0-100

**模糊调整**：
- "increase the brightness" - 不指定具体值
- "dim the lamp" - 不指定具体值
- "down a notch" - 不指定具体值

---

### 4.2 颜色（Color）

**直接颜色名**：

| 颜色 | 示例 |
|------|------|
| blue | "set the strip light to blue" |
| red | "make the strip light red" |
| green | "change the desk lamp color to green" |
| yellow | "change the strip light 1 color to yellow" |
| orange | "make the floor lamp orange" |
| pink | "change the floor lamp color to pink" |
| purple | "set the floor lamp color to purple" |
| cyan | "make the strip light 1 cyan" |
| magenta | "set the light to magenta" |
| lavender | "make the floor lamp lavender" |

**白色系**：

| 颜色 | 示例 |
|------|------|
| white | "change the strip light 5 color to white" |
| warm white | "set the floor lamp to warm white" |
| cool white | "set the strip light color to cool white" |
| sky blue | "change the tv light strip color to sky blue" |

**描述性表达**：
- "warm light" → 理解为 warm white 或降低色温
- "warm tone" → 理解为 warm white 或降低色温

---

### 4.3 色温（Color Temperature）

**数值表达**：

| 输入格式 | 示例 |
|---------|------|
| to [数字]k / [数字]K | "set the spotlight 1 color temperature to 3500k" |

**支持值**：1000-10000 K（连续范围）

**模糊调整**：
- "warm up the spotlights" → 降低色温（adjustDown）
- "increase the color temperature" → 提高色温（adjustUp）
- "decrease the color temperature" → 降低色温（adjustDown）

---

### 4.4 温度（Temperature - 空调）

**数值表达**：

| 输入格式 | 示例 |
|---------|------|
| to [数字] degrees | "set the AC temperature to 24 degrees" |
| to [数字]° | "set the AC temperature to 24°" |
| to [数字] | "set the AC to 24 degrees" |

**范围**：16-29°C

**模糊调整**：
- "decrease the AC temperature" - 不指定具体值

---

### 4.5 位置（Position - 窗帘）

**数值表达**：

| 输入格式 | 示例 |
|---------|------|
| to [数字]% | "open the curtain to 25%" |
| level to [数字]% | "set the curtain opening level to 75%" |

**范围**：0-100%

**开合表达**：

- "open the curtain" → 完全打开
- "close the curtain" → 完全关闭

**模糊调整**：
- "decrease the curtain opening level" - 不指定具体值

---

### 4.6 模式（Mode）

**空调模式**：

| 输入表达 | 模式 |
|---------|------|
| fan mode / to fan | Fan |
| dry / to dry | Dry |
| heat mode / mode to heat | Heat |
| cool / mode to cool / cooling mode | Cool |
| auto mode / to auto | Auto |

**示例**：
- "set the AC to fan mode"
- "switch the AC to dry"
- "set the AC mode to heat"
- "change the AC mode to cool"
- "set the AC to auto mode"

**场景模式**（训练数据中实际出现，按频次）：

| 输入表达 | 场景名 |
|---------|--------|
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

**灯光模式（场景预设）**：

| 输入表达 | 模式 |
|---------|------|
| reading mode | Reading |
| romance mode | Romance |
| eco mode | Eco |
| soft mode | Soft |

> 灯光模式用 `turnOn` + `mode` 属性设置（如 `turnOn|Light|mode|Reading|*|room|*`），不是空调模式。

**示例**：
- "activate romantic mode"
- "I want party mode"
- "switch to relax mode in the living room"
- "turn on reading mode in the bedroom"

---

### 4.7 风速（Wind Speed - 空调）

**明确取值**：

| 输入表达 | 值 |
|---------|------|
| low wind / low speed | Low |
| medium wind / medium speed | Medium |
| high wind / high speed | High |

**示例**：
- "set the AC wind speed to high"
- "change AC to medium wind"

**模糊调整**：
- "increase the wind speed" → adjustUp
- "lower the fan speed" → adjustDown

---

### 4.8 "太吵 / 太亮" 类描述表达

这类抱怨式表达**不**对应音量控制（数据中没有 Music 设备，也没有 volume 属性）。它们映射到真正引起不适的设备：

- "The AC fan is too loud / blowing too hard" → `adjustDown|AC|windSpeed|*|*|...`
- "It's too bright, turn it down" → `adjustDown|Light|brightness|*|*|...`

---

## 五、复合指令模式

### 5.1 并列结构

**连接词**：`and`

**模式**：`[动作1] + and + [动作2]`

**示例**：

```
输入: "turn on the floor lamp and set the floor lamp to warm white"
拆解:
  - 动作1: turn on the floor lamp
  - 动作2: set the floor lamp to warm white

输入: "Bring up the floor lamp and make it warm white"
拆解:
  - 动作1: Bring up the floor lamp (开启)
  - 动作2: make it warm white (设置颜色)

输入: "The floor lamp on and set to warm white"
拆解:
  - 动作1: The floor lamp on (开启)
  - 动作2: set to warm white (设置颜色)
```

---

### 5.2 一体化描述

**模式**：单个动作中包含多个属性

**示例**：

```
输入: "Get the floor lamp on in warm white"
含义: 开启 + 设置颜色

输入: "I'd like the floor lamp on with a warm white tone"
含义: 开启 + 设置颜色
```

---

## 六、语气与礼貌用语

### 6.1 礼貌请求

| 表达模式 | 示例 |
|---------|------|
| please | "Blue for the strip light, please" |
| I'd like | "I'd like the strip light on" |
| I want | "I want the strip light in blue" |
| I need | "I need reading mode on" |

---

### 6.2 陈述式指令

| 表达模式 | 示例 |
|---------|------|
| ... should be | "The strip light should be on" |
| ... needs to be | "The room needs to be in sleeping mode" |

---

### 6.3 祈使句（直接命令）

最常见的形式，直接使用动作词：

- "Turn on the strip light"
- "Set the AC to 24 degrees"
- "Dim the desk lamp"

---

## 七、输入指令的常见模式总结

### 7.1 模式一：简单开关

```
[动作词] + the + [设备]
```

**示例**：

- turn on the strip light
- close the curtain
- switch on the AC

---

### 7.2 模式二：设置数值

```
set + the + [设备] + [属性] + to + [值] + [单位]
```

**示例**：

- set the strip light brightness to 30%
- set the AC temperature to 24 degrees
- set the spotlight 1 color temperature to 3500k

---

### 7.3 模式三：设置颜色

```
[动作词] + the + [设备] + [颜色]
```

**示例**：

- set the strip light to blue
- make the floor lamp orange
- change the desk lamp color to green

---

### 7.4 模式四：模糊调整

```
[调整动词] + the + [设备] + [属性]
```

**示例**：

- increase the spotlight brightness
- decrease the desk lamp brightness
- dim the desk lamp

---

### 7.5 模式五：场景切换

```
[切换动词] + [场景名]
```

**示例**：

- activate romantic mode
- I want party mode
- switch to relax mode

---

### 7.6 模式六：复合指令

```
[命令1] + and + [命令2]
```

**示例**：

- turn on the floor lamp and set it to warm white
- open the curtain and turn on the floor lamp

---

## 八、特殊语言现象

### 8.1 代词替换

在复合指令的第二部分，代词 `it` 通常指代前面提到的设备：

```
输入: "turn on the strip light and set it to blue"
     （第二部分的 "it" 指代 "strip light"）

输入: "I'd like the floor lamp on with a warm white tone"
     （隐含代词；"tone" 的主语是 "floor lamp"）
```

---

### 8.2 省略结构

**省略动词**：

```
输入: "The floor lamp on and set to warm white"
理解: [turn] the floor lamp on and set [it] to warm white
```

**省略设备名**：

```
输入: "set it to blue"（在上下文中，"it" 指代前面提到的设备）
```

---

### 8.3 描述性语言

有些指令使用描述性语言而非直接命令：

| 描述性表达 | 实际意图 |
|-----------|---------|
| "The AC fan is too loud" | 降低空调风速（adjustDown windSpeed） |
| "It's too bright" | 降低亮度（adjustDown brightness） |
| "warm up the spotlights" | 降低色温（使其更暖） |
| "Give the spotlights a warm tone" | 设置为暖色 |

---

## 九、输入指令的统计特征

### 9.1 高频动作词（Top 5）

1. **set** - 设置类命令（最常见）
2. **turn on / turn off** - 开关类命令
3. **change** - 改变类命令
4. **make** - 制作/设置类命令
5. **increase / decrease** - 调整类命令

---

### 9.2 高频设备（Top 5）

1. **Light**（泛指灯具，含编号/字母变体）
2. **AC**（空调）
3. **Curtain**（含 Sheer Curtain）
4. **Strip Light**
5. **Spot Light**

---

### 9.3 高频属性操作（Top 7）

1. **brightness** - 亮度调整（最常见）
2. **color** - 颜色设置
3. **temperature** - 温度设置（空调）
4. **colorTemperature** - 色温调整
5. **windSpeed** - 风速控制（空调）
6. **position** - 位置控制（窗帘）
7. **mode** - 模式切换（空调与场景）

---

### 9.4 指令长度

| 长度类型 | 词数 | 示例 |
|---------|------|------|
| 短 | 3-5 词 | "turn on the strip light" |
| 中 | 6-9 词 | "set the strip light brightness to 30%" |
| 长 | 10+ 词 | "turn on the floor lamp and set it to warm white" |

**典型长度**：5-8 词

---

## 十、易混淆的表达

### 10.1 "warm" 的多义性

| 输入 | 理解为 |
|------|--------|
| "set to warm white" | 颜色设置（Warm White） |
| "warm light" | 颜色设置（Warm White）或降低色温 |
| "warm up the spotlights" | 降低色温（adjustDown colorTemperature） |
| "warm tone" | 颜色设置（Warm White）或降低色温 |

---

### 10.2 模式的不同表达

| 输入 | 实际场景 |
|------|---------|
| "romantic mode" | Romantic Mode |
| "the romantic mode" | Romantic Mode |
| "I want a romantic mode" | Romantic Mode |
| "set the romantic mode" | Romantic Mode |

> 注意：加不加冠词、用什么动词，最终都指向同一个场景。

---

### 10.3 窗帘的开关

| 输入 | 动作 |
|------|------|
| "open the curtain" | 打开（turnOn） |
| "close the curtain" | 关闭（turnOff） |
| "open the curtain to 25%" | 设置位置到 25%（set） |

---

## 十一、输入指令的验证清单

解析输入指令时，检查以下要素：

- [ ] **动作识别**：识别正确的动作类型（开关 / 设置 / 调整 / 模式）
- [ ] **设备提取**：准确提取设备名称（含编号）
- [ ] **属性检测**：判断操作的是哪个属性（亮度 / 颜色 / 温度等）
- [ ] **值提取**：提取具体值和单位
- [ ] **复合指令拆分**：识别并拆分复合指令（用 "and" 连接）
- [ ] **代词解析**：将代词（it/its）解析为具体设备
- [ ] **描述性语言理解**：将描述性表达转换为具体动作
- [ ] **模糊词处理**：识别模糊调整词（a little / a bit）

---
---

# 第二部分：输出命令生成规则

## 一、输出格式规范

### 1.1 基本格式

```
action|device|attribute|value|unit|room|floor
```

7 个字段依次为：`action|device|attribute|value|unit|room|floor`。

**第6字段（room）**：房间名称（如 "Living Room", "Master Bedroom", "Kitchen"），未指定时用 `*`
**第7字段（floor）**：楼层标识（如 "Ground Floor", "First Floor"），未指定时用 `*`

### 1.2 多命令连接

- 多条命令用换行符 `\n` 连接。

### 1.3 示例

```
turnOn|Strip Light|*|*|*|Living Room|*
set|Strip Light|color|Blue|*|Living Room|*
set|Light|brightness|70|Percent|Master Bedroom|*
activate|Party Mode|*|*|*|Living Room|*
```

---

## 二、字段定义

### 2.1 动作类型（第1字段）

| 动作 | 说明 | 适用场景 |
|------|------|---------|
| `turnOn` | 打开设备 | 开灯、开空调、拉开窗帘 |
| `turnOff` | 关闭设备 | 关灯、关空调、关闭窗帘 |
| `set` | 设置到具体值 | 设置亮度、颜色、温度等 |
| `adjustUp` | 增加属性值 | 提高亮度、提高色温 |
| `adjustDown` | 减少属性值 | 降低亮度、降低风速 |
| `activate` | 激活场景模式 | 开启派对模式、浪漫模式 |
| `deactivate` | 取消场景模式 | 退出场景模式 |
| `pause` | 暂停运动中的设备 | 让窗帘/百叶窗在中途停下 |

**动作选择原则**：

- 指令中包含**明确数值**时，必须用 `set`。
- 指令中包含 "a little" / "a bit" 等模糊词时，用 `adjustUp` / `adjustDown`，不填具体值。
- `pause` 用于窗帘类设备（Curtain / Blind / Sheer Curtain），使其在中途停下 —— 如 "stop the curtain"、"pause the blind"。

---

### 2.2 设备名称（第2字段）

#### 2.2.1 灯具类设备

| 设备名 | 中文 | 编号/字母示例 |
|--------|------|---------|
| `Light` | 泛指灯具 | Light 1, Light 2, Light A, Light F |
| `Strip Light` | 灯带 | Strip Light 1, Strip Light 3 |
| `Floor Lamp` | 落地灯 | Floor Lamp 1 |
| `Spot Light` | 射灯 | Spot Light 1, Spot Light 10 |
| `Desk Lamp` | 台灯 | Desk Lamp 1 |
| `Ceiling Light` | 吸顶灯 | Ceiling Light 1 |
| `Wall Light` | 壁灯 | Wall Light 1 |
| `Recessed Light` | 嵌入式灯 | Recessed Light 1 |
| `Downlight` | 筒灯 | Downlight 1 |
| `Chandelier` | 吊灯 | Chandelier 1 |
| `Track Light` | 轨道灯 | Track Light 1 |
| `Ambient Light` | 氛围灯 | - |
| `Reading Light` | 阅读灯 | - |
| `Vanity Light` | 化妆灯 | - |
| `Night Light` | 夜灯 | - |
| `LED Strip` | LED灯带 | LED Strip 1 |
| `TV Light Strip` | 电视灯带 | TV Light Strip 1 |

**⚠️ 重要规范**：

- `Spot Light` 写成**两个单词**。
- 无论输入是 "spotlight" 还是 "spot light"，输出统一为 `Spot Light`。

#### 2.2.2 环境控制类设备

| 设备名 | 中文 | 说明 |
|--------|------|------|
| `AC` | 空调 | 支持温度、模式、风速控制 |
| `Curtain` | 窗帘 | 控制开合位置 |
| `Sheer Curtain` | 纱帘 | 控制开合位置 |
| `Blind` | 百叶窗 | 控制开合位置 |

**⚠️ 重要规范**：

- 窗帘类设备仅使用 `Curtain`、`Sheer Curtain`、`Blind`。

#### 2.2.3 场景模式类

| 设备名 | 中文 | 说明 |
|--------|------|------|
| `Romantic Mode` | 浪漫模式 | 场景模式 |
| `Party Mode` | 派对模式 | 场景模式 |
| `Reading Mode` | 阅读模式 | 场景模式 |
| `Sleeping Mode` | 睡眠模式 | 场景模式 |
| `Relax Mode` | 放松模式 | 场景模式 |
| `Wakeup Mode` | 起床模式 | 场景模式 |
| `Home Mode` | 在家模式 | 场景模式 |
| `Movie Mode` | 电影模式 | 场景模式 |
| `Away Mode` | 离家模式 | 场景模式 |
| `Holiday Mode` | 假日模式 | 场景模式 |
| `Guest Mode` | 访客模式 | 场景模式 |
| `Dining Mode` | 用餐模式 | 场景模式 |
| `Meeting Mode` | 会议模式 | 场景模式 |
| `Cinema Mode` | 影院模式 | 场景模式 |
| `Leisure Mode` | 休闲模式 | 场景模式 |

---

### 2.3 属性（第3字段）

| 属性名 | 说明 | 适用设备 | 取值范围 |
|--------|------|---------|---------|
| `brightness` | 亮度 | 所有灯具 | 0-100 |
| `color` | 颜色 | 所有灯具 | 见颜色表 |
| `colorTemperature` | 色温 | 灯具（尤其是Spot Light） | 1000-10000 |
| `temperature` | 温度 | AC | 16-29 |
| `position` | 开合位置 | Curtain/Blind/Sheer Curtain | 0-100 |
| `mode` | 运行模式 | AC, Light | Fan/Dry/Heat/Cool/Auto（AC）；Reading/Romance/Eco/Soft（Light） |
| `windSpeed` | 风速 | AC | Low/Medium/High |
| `*` | 无属性 | turnOn/turnOff/activate/deactivate/pause | - |

---

### 2.4 值（第4字段）

#### 2.4.1 数值型

| 类型 | 范围 | 单位 |
|------|------|------|
| 亮度 | 0-100 | Percent |
| 位置 | 0-100 | Percent |
| 色温 | 1000-10000 | Kelvin |
| 温度 | 16-29 | Celsius |
| 风速 | Low, Medium, High | Level |

#### 2.4.2 颜色名称

**基础颜色**：

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

**白色系**：

- `White`
- `Warm White`
- `Cool White`
- `Sky Blue`

#### 2.4.3 空调模式

| 模式 | 说明 |
|------|------|
| `Fan` | 送风 |
| `Dry` | 除湿 |
| `Heat` | 制热 |
| `Cool` | 制冷 |
| `Auto` | 自动 |

#### 2.4.4 风速等级

| 等级 | 说明 |
|------|------|
| `Low` | 低速 |
| `Medium` | 中速 |
| `High` | 高速 |

#### 2.4.5 占位符

- 当动作为 `turnOn/turnOff/adjustUp/adjustDown/activate/deactivate` 时，
- 或属性不需要具体值时，
- 用 `*` 占位。

---

### 2.5 单位（第5字段）

| 单位 | 适用属性 |
|------|---------|
| `Percent` | brightness, position |
| `Kelvin` | colorTemperature |
| `Celsius` | temperature |
| `Level` | windSpeed |
| `*` | 其他情况（color, mode, 或无属性） |

---

### 2.6 房间（第6字段）

房间名称首字母大写、词间留空格。训练数据中实际出现的房间（按频次）：

**高频房间**：
- `Living Room`
- `Master Bedroom`
- `Dining Room`
- `Majlis`（阿拉伯式会客厅）
- `Kitchen`
- `Home Office`
- `Balcony`
- `Bathroom` / `Master Bathroom`
- `Prayer Room`
- `Patio`

**编号/字母卧室与房间**：
- `First Bedroom` / `Second Bedroom` / `Third Bedroom` ...
- `Bedroom 1` / `Bedroom 2` / `Bedroom 3` ...
- `Bedroom A` / `Bedroom B` / `Bedroom C` ...
- `Room A` / `Room B` / `Room 1` / `Room 2` ...

**其他房间**：
- `Nanny's Quarter`、`Closet`、`Entrance Hall`
- `Movie Theater`、`Laundry Room`、`Gym`
- `Corridor`、`Garage`、`Swimming Pool Area`
- `*`（未指定房间时）

---

### 2.7 楼层（第7字段）

楼层标识。训练数据中实际出现的值（按频次）：

- `*`（未指定楼层时 - 最常见）
- `Ground Floor`
- `First Floor`
- `Upstairs`
- `Downstairs`
- `Second Floor`
- `Third Floor`

> **注意**：使用英式楼层叫法 —— `Ground Floor` 是地面层，`First Floor` 是其上一层。**不要**使用 `1st Floor` / `2nd Floor` 这种写法。

---

## 三、输入解析规则

### 3.1 开关操作

| 输入关键词 | 动作 | 示例 |
|-----------|------|------|
| turn on, switch on, get going | `turnOn` | "turn on the strip light" |
| turn off, switch off | `turnOff` | "turn off the floor lamp" |
| close | `turnOff` | "close the curtain" |
| open (窗帘) | `turnOn` | "open the blind" |
| stop, pause, halt | `pause` | "stop the curtain" |

**暂停规则**：

- `pause` 仅用于窗帘类设备（Curtain / Blind / Sheer Curtain），使其在中途停下。
- 属性及之后所有字段均为 `*`。

```
输入: "stop the curtain in the living room"
输出: pause|Curtain|*|*|*|Living Room|*

输入: "pause the blind"
输出: pause|Blind|*|*|*|*|*
```

---

### 3.2 设置操作

| 输入模式 | 动作 | 输出格式 |
|---------|------|---------|
| set ... to [值] | `set` | `set\|设备\|属性\|值\|单位\|*\|*` |
| make ... [颜色] | `set` | `set\|设备\|color\|颜色\|*\|*\|*` |
| change ... to [值] | `set` | `set\|设备\|属性\|值\|单位\|*\|*` |

**核心原则**：

- 指令中包含**明确数值**时，动作必须为 `set`。
- 例如："set brightness to 50"、"make it 24 degrees"。

**示例**：

```
输入: "set the strip light to blue"
输出: set|Strip Light|color|Blue|*|*|*

输入: "change AC temperature to 24"
输出: set|AC|temperature|24|Celsius|*|*
```

---

### 3.3 调整操作

| 输入关键词 | 动作 | 示例 |
|-----------|------|------|
| increase, bring up | `adjustUp` | "increase the brightness" |
| decrease, dim, lower, turn down | `adjustDown` | "dim the desk lamp" |

**模糊调整处理**：

- 当指令中包含 "a little" / "a bit" 时，
- 输出的值字段用 `*`，
- 由后端系统自动填充默认调整量。

**示例**：

```
输入: "lower the AC fan speed a little"
输出: adjustDown|AC|windSpeed|*|*|*|*

输入: "dim the light a bit"
输出: adjustDown|Desk Lamp|brightness|*|*|*|*
```

---

### 3.4 模式操作

#### 3.4.1 场景模式

**激活场景**：

| 输入模式 | 动作 | 输出格式 |
|---------|------|---------|
| switch to [模式] | `activate` | `activate\|模式名\|*\|*\|*\|*\|*` |
| set the [模式] | `activate` | `activate\|模式名\|*\|*\|*\|*\|*` |
| I want [模式] | `activate` | `activate\|模式名\|*\|*\|*\|*\|*` |

**示例**：

```
输入: "activate romantic mode"
输出: activate|Romantic Mode|*|*|*|*|*
```

**退出场景**：

| 输入模式 | 动作 | 输出格式 |
|---------|------|---------|
| exit [模式] | `deactivate` | `deactivate\|模式名\|*\|*\|*\|*\|*` |
| turn off [模式] | `deactivate` | `deactivate\|模式名\|*\|*\|*\|*\|*` |
| deactivate [模式] | `deactivate` | `deactivate\|模式名\|*\|*\|*\|*\|*` |

**示例**：

```
输入: "exit movie mode"
输出: deactivate|Movie Mode|*|*|*|*|*
```

#### 3.4.2 设备模式

**⚠️ 特殊规则**：

- **灯光模式**：灯光场景预设（Reading、Romance、Eco、Soft）用 `turnOn` 动作 + mode 属性：
  - `turnOn|Light|mode|Reading|*|room|*`
- **空调模式**：用 `set` 动作：
  - `set|AC|mode|Cool|*|room|*`

**示例**：

```
输入: "set AC to cool mode"
输出: set|AC|mode|Cool|*|Living Room|*

输入: "turn on reading mode"（灯光）
输出: turnOn|Light|mode|Reading|*|Master Bedroom|*

输入: "switch on reading mode in the master bedroom"
输出: turnOn|Light|mode|Reading|*|Master Bedroom|*
```

---

### 3.5 数值提取规则

| 输入格式 | 提取结果 | 输出格式 |
|---------|---------|---------|
| 30%, 30 percent | 30 | `30\|Percent` |
| 3500k, 3500K | 3500 | `3500\|Kelvin` |
| 24 degrees, 24° | 24 | `24\|Celsius` |

---

### 3.6 复合命令处理

**连接规则**：

- 多条命令用 `\n` 连接。

**示例**：

```
输入: "turn on the strip light and set it to blue"
输出: turnOn|Strip Light|*|*|*|*|*\nset|Strip Light|color|Blue|*|*|*

输入: "open the curtain and turn on the floor lamp"
输出: turnOn|Curtain|*|*|*|*|*\nturnOn|Floor Lamp|*|*|*|*|*
```

---

## 四、特殊规则

### 4.1 多意图处理

当一条指令包含多个意图时：

- 若**楼层**未单独说明，默认与前一个相同。
- 若**房间**未单独说明，默认与前一个相同。
- 两者都省略时，结果仍视为正确。

**示例**：

```
输入: "turn on all lights in the living room"
说明: 所有灯具默认在同一房间（living room）
```

---

### 4.2 设备名称标准化

#### 规则1：Spot Light 分写为两个单词

- 标准写法：`Spot Light`
- 无论输入是 "spotlight" 还是 "spot light"，输出统一为 `Spot Light`。

#### 规则2：窗帘类设备命名

- 标准写法：`Curtain`、`Sheer Curtain`、`Blind`

---

## 五、完整示例

### 5.1 基础操作

| 输入指令 | 输出命令 |
|---------|---------|
| Turn on the strip light | `turnOn\|Strip Light\|*\|*\|*\|*\|*` |
| Close the curtain | `turnOff\|Curtain\|*\|*\|*\|*\|*` |
| Set the strip light to blue | `set\|Strip Light\|color\|Blue\|*\|*\|*` |
| Set AC temperature to 24 | `set\|AC\|temperature\|24\|Celsius\|*\|*` |
| Turn on the living room lights | `turnOn\|Light\|*\|*\|*\|Living Room\|*` |
| Set master bedroom lights to 70 percent | `set\|Light\|brightness\|70\|Percent\|Master Bedroom\|*` |

---

### 5.2 调整操作

| 输入指令 | 输出命令 |
|---------|---------|
| Increase spotlight 10 brightness | `adjustUp\|Spot Light 10\|brightness\|*\|*\|*\|*` |
| Dim the desk lamp | `adjustDown\|Desk Lamp\|brightness\|*\|*\|*\|*` |
| Lower the AC fan speed | `adjustDown\|AC\|windSpeed\|*\|*\|*\|*` |
| Turn down the brightness a bit | `adjustDown\|Strip Light\|brightness\|*\|*\|*\|*` |

---

### 5.3 场景模式

| 输入指令 | 输出命令 |
|---------|---------|
| Activate romantic mode | `activate\|Romantic Mode\|*\|*\|*\|*\|*` |
| Exit movie mode | `deactivate\|Movie Mode\|*\|*\|*\|*\|*` |
| I want party mode | `activate\|Party Mode\|*\|*\|*\|*\|*` |
| Activate party mode in the living room | `activate\|Party Mode\|*\|*\|*\|Living Room\|*` |
| Turn on reading mode in master bedroom | `turnOn\|Light\|mode\|Reading\|*\|Master Bedroom\|*` |

---

### 5.4 风速操作

| 输入指令 | 输出命令 |
|---------|---------|
| Set AC wind speed to high | `set\|AC\|windSpeed\|High\|Level\|*\|*` |
| Change living room AC to medium wind | `set\|AC\|windSpeed\|Medium\|Level\|Living Room\|*` |
| Lower the fan speed | `adjustDown\|AC\|windSpeed\|*\|*\|*\|*` |

---

### 5.5 复合命令

| 输入指令 | 输出命令 |
|---------|---------|
| Turn on strip light and set to blue | `turnOn\|Strip Light\|*\|*\|*\|*\|*\nset\|Strip Light\|color\|Blue\|*\|*\|*` |
| Set brightness to 50 and color to warm white | `set\|Strip Light\|brightness\|50\|Percent\|*\|*\nset\|Strip Light\|color\|Warm White\|*\|*\|*` |
| Set kitchen lights to warm white and 80 percent | `set\|Light\|color\|Warm White\|*\|Kitchen\|*\nset\|Light\|brightness\|80\|Percent\|Kitchen\|*` |
| Activate party mode and turn on light F | `activate\|Party Mode\|*\|*\|*\|Living Room\|*\nturnOn\|Light F\|*\|*\|*\|Living Room\|*` |

---

## 六、标准写法速查

| 要点 | 标准写法 |
|---------|--------|
| 设备名拼写 | `Spot Light` |
| 窗帘命名 | `Curtain` / `Blind` / `Sheer Curtain` |
| 命令分隔符 | 用 `\n` |
| 有数值时的动作 | 用 `set` |
| 模糊调整 | `adjustUp\|...\|brightness\|*\|...`（值留 `*`） |
| room/floor | room 放第6字段、floor 放第7字段（或 `*`） |
| 风速单位 | `windSpeed\|High\|Level` |

---

## 七、快速查询表

### 7.1 动作选择流程图

```
是否包含明确数值？
├─ 是 → set
└─ 否
   ├─ 开/关设备？        → turnOn / turnOff
   ├─ 让窗帘中途停下？    → pause
   ├─ 增加/减少？        → adjustUp / adjustDown
   └─ 场景模式？         → activate / deactivate
```

### 7.2 单位速查

| 属性 | 单位 |
|------|------|
| brightness, position | Percent |
| colorTemperature | Kelvin |
| temperature | Celsius |
| windSpeed | Level |
| color, mode, 开关操作 | * |
