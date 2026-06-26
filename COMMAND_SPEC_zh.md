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

**位置信息**包括：
- **房间**（room）：living room, bedroom, kitchen, bathroom 等
- **楼层**（floor）：1st floor, 2nd floor, upstairs, downstairs 等

**示例**：
- `turn on the strip light` → 动作词 + 设备
- `set the strip light to blue` → 动作词 + 设备 + 值描述
- `increase the spotlight 10 brightness` → 动作词 + 设备 + 属性
- `turn on all lights in the living room` → 动作词 + 设备 + 位置信息
- `set the bedroom AC to 24 degrees` → 动作词 + 位置 + 设备 + 值描述

**注意**：
- 位置信息在输入中可能出现，但在当前输出格式的7个字段中**不显式体现**
- 位置信息用于理解指令范围，在多意图场景中起作用

---

## 二、动作词分类

### 2.1 开启类动作词

| 动作词 | 示例 |
|--------|------|
| turn on | "turn on the strip light" |
| switch on | "switch on the strip light" |
| get ... going | "get the strip light going" |
| open (窗帘专用) | "open the curtain" |

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
| make ... [值] | make [设备] [值] | "make the strip light blue" || 动作词 | 语法模式 | 示例 |
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
| lower | "lower the music volume" |
| turn down | "turn it down" |
| bring ... down | "bring the music down a notch" |
| take ... down | "take the volume down on the music" |

**语境表达**：
- `... is too loud, turn it down` - "The music is too loud, turn it down"
- `... down a notch` - "bring the music down a notch"

---

### 2.6 模式切换类动作词

**激活场景**：

| 动作词 | 示例 |
|--------|------|
| switch to | "switch to presentation mode" |
| switch ... to | "switch the room to presentation mode" |
| set | "set the presentation mode" |
| change ... to | "change the mode to movie mode" |
| I want | "I want a movie mode" |
| I need ... on | "I need presentation mode on" |

**表达意愿的变体**：
- `Let's go into ...` - "Let's go into presentation mode"
- `... please` - "Presentation mode, please"
- `Pull up ...` - "Pull up presentation mode"
- `Get ... into ...` - "Get the room into presentation mode"
- `... needs to be ...` - "The room needs to be in presentation mode"

**保持场景**：

| 表达模式 | 示例 |
|---------|------|
| keep ... in | "keep the room in presentation mode" |
| stay in | "The room should stay in presentation mode" |
| hold ... in | "Let's hold the room in presentation mode" |
| leave ... as it is | "Leave the room as it is in presentation mode" |

---

## 三、设备名称表达方式

### 3.1 标准设备名称

| 输入可能的形式 | 标准化后 |
|--------------|---------|
| strip light | Strip Light |
| floor lamp | Floor Lamp |
| spotlight / spot light | Spot Light |
| desk lamp | Desk Lamp |
| tv light strip | TV Light Strip |
| AC / ac | AC |
| curtain | Curtain |
| music | Music |

---

### 3.2 带编号的设备

**格式**：`设备名 + 数字`

**示例**：
- `strip light 1`, `strip light 3`, `strip light 5`
- `spotlight 1`, `spotlight 2`, `spotlight 10`
- `desk lamp 1`

**输入特征**：
- 数字直接跟在设备名后面
- 可以是单数字或双数字

---

### 3.3 定冠词使用

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

| 颜色词 | 示例 |
|--------|------|
| blue | "set the strip light to blue" |
| red | "make the strip light red" |
| green | "change the desk lamp color to green" |
| yellow | "change the strip light 1 color to yellow" |
| orange | "make the floor lamp orange" |
| pink | "change the floor lamp color to pink" |
| purple | "set the floor lamp color to purple" |
| cyan | "make the strip light 1 cyan" |
| lavender | "make the floor lamp lavender" |

**白色系**：

| 颜色词 | 示例 |
|--------|------|
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

**支持值**：3500K, 4000K, 5000K, 6000K

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
| lower | "lower the music volume" |
| turn down | "turn it down" |
| bring ... down | "bring the music down a notch" |
| take ... down | "take the volume down on the music" |

**语境表达**：
- `... is too loud, turn it down` - "The music is too loud, turn it down"
- `... down a notch` - "bring the music down a notch"

---

### 2.6 模式切换类动作词

**激活场景**：

| 动作词 | 示例 |
|--------|------|
| switch to | "switch to presentation mode" |
| switch ... to | "switch the room to presentation mode" |
| set | "set the presentation mode" |
| change ... to | "change the mode to movie mode" |
| I want | "I want a movie mode" |
| I need ... on | "I need presentation mode on" |

**表达意愿的变体**：
- `Let's go into ...` - "Let's go into presentation mode"
- `... please` - "Presentation mode, please"
- `Pull up ...` - "Pull up presentation mode"
- `Get ... into ...` - "Get the room into presentation mode"
- `... needs to be ...` - "The room needs to be in presentation mode"

**保持场景**：

| 表达模式 | 示例 |
|---------|------|
| keep ... in | "keep the room in presentation mode" |
| stay in | "The room should stay in presentation mode" |
| hold ... in | "Let's hold the room in presentation mode" |
| leave ... as it is | "Leave the room as it is in presentation mode" |

---

## 三、设备名称表达方式

### 3.1 标准设备名称

| 输入可能的形式 | 标准化后 |
|--------------|---------|
| strip light | Strip Light |
| floor lamp | Floor Lamp |
| spotlight / spot light | Spot Light |
| desk lamp | Desk Lamp |
| tv light strip | TV Light Strip |
| AC / ac | AC |
| curtain | Curtain |
| music | Music |

---

### 3.2 带编号的设备

**格式**：`设备名 + 数字`

**示例**：
- `strip light 1`, `strip light 3`, `strip light 5`
- `spotlight 1`, `spotlight 2`, `spotlight 10`
- `desk lamp 1`

**输入特征**：
- 数字直接跟在设备名后面
- 可以是单数字或双数字

---

### 3.3 定冠词使用

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

| 颜色词 | 示例 |
|--------|------|
| blue | "set the strip light to blue" |
| red | "make the strip light red" |
| green | "change the desk lamp color to green" |
| yellow | "change the strip light 1 color to yellow" |
| orange | "make the floor lamp orange" |
| pink | "change the floor lamp color to pink" |
| purple | "set the floor lamp color to purple" |
| cyan | "make the strip light 1 cyan" |
| lavender | "make the floor lamp lavender" |

**白色系**：

| 颜色词 | 示例 |
|--------|------|
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

**支持值**：3500K, 4000K, 5000K, 6000K

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

**开关表达**：
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
| cool / mode to cool | Cool |

**示例**：
- "set the AC to fan mode"
- "switch the AC to dry"
- "set the AC mode to heat"
- "change the AC mode to cool"

**场景模式**：

| 输入表达 | 场景名 |
|---------|--------|
| presentation mode | Presentation Mode |
| movie mode | Movie Mode |
| music video mode | Music Video Mode |
| favorite movie mode | Favorite Movie Mode |
| volume down mode | Volume Down Mode |

**示例**：
- "switch to presentation mode"
- "I want a movie mode"
- "set the music video mode"

---

### 4.7 音量（Volume）

**模糊调整**：
- "lower the music volume"
- "bring the music down a notch"
- "take the volume down on the music"
- "The music is too loud, turn it down"

> **注**：测试数据中未见明确数值的音量设置

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

**模式**：在单个动作中包含多个属性

**示例**：
```
输入: "Get the floor lamp on in warm white"
含义: 开灯 + 设置颜色

输入: "I'd like the floor lamp on with a warm white tone"
含义: 开灯 + 设置颜色
```

---

## 六、语气与礼貌用语

### 6.1 礼貌请求

| 表达模式 | 示例 |
|---------|------|
| please | "Blue for the strip light, please" |
| I'd like | "I'd like the strip light on" |
| I want | "I want the strip light in blue" |
| I need | "I need presentation mode on" |

---

### 6.2 陈述式指令

| 表达模式 | 示例 |
|---------|------|
| ... should be | "The strip light should be on" |
| ... needs to be | "The room needs to be in presentation mode" |

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
[动作词] + the + [设备名]
```

**示例**：
- turn on the strip light
- close the curtain
- switch on the AC

---

### 7.2 模式二：设置数值

```
set + the + [设备名] + [属性] + to + [数值] + [单位]
```

**示例**：
- set the strip light brightness to 30%
- set the AC temperature to 24 degrees
- set the spotlight 1 color temperature to 3500k

---

### 7.3 模式三：设置颜色

```
[动作词] + the + [设备名] + [颜色]
```

**示例**：
- set the strip light to blue
- make the floor lamp orange
- change the desk lamp color to green

---

### 7.4 模式四：模糊调整

```
[调整动词] + the + [设备名] + [属性]
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
- switch to presentation mode
- I want a movie mode
- change the mode to music video mode

---

### 7.6 模式六：复合指令

```
[指令1] + and + [指令2]
```

**示例**：
- turn on the floor lamp and set it to warm white
- open the curtain and turn on the floor lamp

---

## 八、特殊语言现象

### 8.1 代词替换

在复合指令的第二部分，常用代词 `it` 指代前面的设备：

```
输入: "turn on the strip light and set it to blue"
     (第二部分的 "it" 指代 "strip light")

输入: "I'd like the floor lamp on with a warm white tone"
     (隐含代词，"tone" 的主语是 "floor lamp")
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
输入: "set it to blue" (在上下文中，"it" 指代前面提到的设备)
```

---

### 8.3 描述性语言

有些指令使用描述性语言而非直接命令：

| 描述性表达 | 实际意图 |
|-----------|---------|
| "The music is too loud" | 降低音量 |
| "warm up the spotlights" | 降低色温（使其更暖） |
| "Give the spotlights a warm tone" | 设置为暖色 |

---

## 九、输入指令的统计特征

### 9.1 高频动作词（Top 5）

1. **set** - 设置类指令（最常见）
2. **turn on / turn off** - 开关类指令
3. **change** - 改变类指令
4. **make** - 制作/设置类指令
5. **increase / decrease** - 调整类指令

---

### 9.2 高频设备（Top 5）

1. **Strip Light** (包括 Strip Light 1/3/5)
2. **Spotlight** (包括 Spotlight 1/2/10)
3. **Floor Lamp**
4. **Desk Lamp**
5. **AC**

---

### 9.3 高频属性操作（Top 5）

1. **brightness** - 亮度调整（最常见）
2. **color** - 颜色设置
3. **temperature** - 温度设置（空调）
4. **color temperature** - 色温调整
5. **position** - 位置控制（窗帘）

---

### 9.4 指令长度特征

| 长度类型 | 词数范围 | 示例 |
|---------|---------|------|
| 短指令 | 3-5词 | "turn on the strip light" |
| 中指令 | 6-9词 | "set the strip light brightness to 30%" |
| 长指令 | 10+词 | "turn on the floor lamp and set it to warm white" |

**主流长度**：5-8词

---

## 十、易混淆的表达

### 10.1 "warm" 的多义性

| 输入 | 理解为 |
|------|--------|
| "set to warm white" | 颜色设置（Warm White） |
| "warm light" | 颜色设置（Warm White）或色温降低 |
| "warm up the spotlights" | 降低色温（adjustDown colorTemperature） |
| "warm tone" | 颜色设置（Warm White）或色温降低 |

---

### 10.2 模式的不同表达

| 输入 | 实际场景 |
|------|---------|
| "presentation mode" | Presentation Mode |
| "the presentation mode" | Presentation Mode |
| "I want a presentation mode" | Presentation Mode |
| "set the presentation mode" | Presentation Mode |

> 注意：加不加冠词、用什么动词，最终都指向同一个场景
---

### 10.3 开关窗帘的表达

| 输入 | 动作 |
|------|------|
| "open the curtain" | 打开（turnOn） |
| "close the curtain" | 关闭（turnOff） |
| "open the curtain to 25%" | 设置位置到25%（set） |

---

## 十一、输入指令的验证清单

在解析输入指令时，应检查以下要素：

- [ ] **动作词识别**：识别出正确的动作类型（开关/设置/调整/模式）
- [ ] **设备提取**：准确提取设备名称（包括编号）
- [ ] **属性判断**：确定操作的是哪个属性（亮度/颜色/温度等）
- [ ] **数值提取**：提取具体数值及单位
- [ ] **复合指令拆分**：识别并拆分复合指令（and连接）
- [ ] **代词解析**：将代词（it/its）还原为具体设备
- [ ] **描述性语言理解**：将描述性表达转换为具体动作
- [ ] **模糊词处理**：识别模糊调整词（a little/a bit）

---

## 十二、版本信息

- **文档版本**: v1.0
- **创建日期**: 2026/06/26
- **数据来源**: 基于168条测试案例分析

---
---

# 第二部分：输出命令生成规则

## 一、输出格式规范

### 1.1 基本格式
```
动作|设备|属性|值|单位|*|*
```

### 1.2 多命令连接
- 使用换行符 `\n` 连接多条命令
- **禁止使用** `&` 符号

### 1.3 示例
```
turnOn|Strip Light|*|*|*|*|*
set|Strip Light|color|Blue|*|*|*
```

---

## 二、字段定义

### 2.1 动作类型（第1字段）

| 动作 | 说明 | 适用场景 |
|------|------|---------|
| `turnOn` | 打开设备 | 开灯、开空调、拉开窗帘 |
| `turnOff` | 关闭设备 | 关灯、关空调、关闭窗帘 |
| `set` | 设置到具体值 | 设置亮度、颜色、温度等 |
| `adjustUp` | 增加属性值 | 提高亮度、增加音量 |
| `adjustDown` | 减少属性值 | 降低亮度、减小音量 |
| `activate` | 激活场景模式 | 开启演示模式、电影模式 |
| `deactivate` | 取消场景模式 | 退出演示模式、电影模式 |

**动作选择原则**：
- 指令中包含**明确数值**时，必须使用 `set`
- 指令中包含 "a little" / "a bit" 等模糊词时，使用 `adjustUp/adjustDown`，不填写具体值

---
### 2.2 设备名称（第2字段）

#### 2.2.1 灯具类设备

| 设备名 | 中文 | 编号示例 |
|--------|------|---------|
| `Strip Light` | 灯带 | Strip Light 1, Strip Light 3 |
| `Floor Lamp` | 落地灯 | Floor Lamp 1 |
| `Spot Light` | 射灯 | Spot Light 1, Spot Light 10 |
| `Desk Lamp` | 台灯 | Desk Lamp 1 |
| `TV Light Strip` | 电视灯带 | TV Light Strip 1 |

**⚠️ 重要规范**：
- `Spot Light` 必须写成**两个单词**，禁止写成 `Spotlight`
- 无论输入是 "spotlight" 还是 "spot light"，输出统一为 `Spot Light`

#### 2.2.2 环境控制类设备

| 设备名 | 中文 | 说明 |
|--------|------|------|
| `AC` | 空调 | 支持温度、模式控制 |
| `Curtain` | 窗帘 | 控制开合位置 |
| `Blind` | 百叶窗 | 控制开合位置 |
| `Sheer` | 纱帘 | 控制开合位置 |

**⚠️ 重要规范**：
- 窗帘类设备**禁止使用** `Drape`
- 仅允许使用 `Curtain`、`Blind`、`Sheer`

#### 2.2.3 娱乐与场景类

| 设备名 | 中文 | 说明 |
|--------|------|------|
| `Music` | 音乐 | 控制音量 |
| `Presentation Mode` | 演示模式 | 场景模式 |
| `Movie Mode` | 电影模式 | 场景模式 |
| `Music Video Mode` | 音乐视频模式 | 场景模式 |

---

### 2.3 属性（第3字段）

| 属性名 | 说明 | 适用设备 | 取值范围 |
|--------|------|---------|---------|
| `brightness` | 亮度 | 所有灯具 | 0-100 |
| `color` | 颜色 | 所有灯具 | 见颜色表 |
| `colorTemperature` | 色温 | Spot Light | 3500/4000/5000/6000 |
| `volume` | 音量 | Music | 0-100 |
| `temperature` | 温度 | AC | 16-29 |
| `position` | 开合位置 | Curtain/Blind/Sheer | 0-100 |
| `mode` | 运行模式 | AC | Fan/Dry/Heat/Cool |
| `*` | 无属性 | turnOn/turnOff/activate/deactivate | - |

---

### 2.4 值（第4字段）

#### 2.4.1 数值型

| 类型 | 范围 | 单位 |
|------|------|------|
| 亮度 | 0-100 | Percent |
| 音量 | 0-100 | Percent |
| 位置 | 0-100 | Percent |
| 色温 | 3500, 4000, 5000, 6000 | Kelvin |
| 温度 | 16-29 | Celsius |

#### 2.4.2 颜色名称

**基础颜色**：
- `Blue`（蓝色）
- `Red`（红色）
- `Green`（绿色）
- `Yellow`（黄色）
- `Orange`（橙色）
- `Pink`（粉色）
- `Purple`（紫色）
- `Cyan`（青色）
- `Lavender`（薰衣草色）

**白色系**：
- `White`（白色）
- `Warm White`（暖白）
- `Cool White`（冷白）
- `Sky Blue`（天蓝）

#### 2.4.3 空调模式

| 模式 | 说明 |
|------|------|
| `Fan` | 送风 |
| `Dry` | 除湿 |
| `Heat` | 制热 |
| `Cool` | 制冷 |

#### 2.4.4 占位符

- 当动作为 `turnOn/turnOff/adjustUp/adjustDown/activate/deactivate` 时
- 或属性不需要具体值时
- 使用 `*` 占位

---

### 2.5 单位（第5字段）

| 单位 | 适用属性 |
|------|---------|
| `Percent` | brightness, volume, position |
| `Kelvin` | colorTemperature |
| `Celsius` | temperature |
| `*` | 其他情况（color, mode 或无属性） |

---

## 三、输入解析规则

### 3.1 开关操作

| 输入关键词 | 动作 | 示例 |
|-----------|------|------|
| turn on, switch on, get going | `turnOn` | "turn on the strip light" |
| turn off, switch off | `turnOff` | "turn off the floor lamp" |
| close | `turnOff` | "close the curtain" |
| open (窗帘) | `turnOn` | "open the blind" |

---

### 3.2 设置操作

| 输入模式 | 动作 | 输出格式 |
|---------|------|---------|
| set ... to [value] | `set` | `set\|设备\|属性\|值\|单位\|*\|*` |
| make ... [color] | `set` | `set\|设备\|color\|颜色\|*\|*\|*` |
| change ... to [value] | `set` | `set\|设备\|属性\|值\|单位\|*\|*` |

**核心原则**：
- 指令中包含**明确数值**时，动词必须用 `set`
- 如："set brightness to 50", "make it 24 degrees"

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
- 当指令中包含 "a little" / "a bit" 时
- 输出中值字段使用 `*`
- 由后端系统自动填充默认调整量

**示例**：
```
输入: "increase the volume a little"
输出: adjustUp|Music|volume|*|*|*|*

输入: "dim the light a bit"
输出: adjustDown|Desk Lamp|brightness|*|*|*|*
```

---

### 3.4 模式操作

#### 3.4.1 场景模式

**激活场景**：

| 输入模式 | 动作 | 输出格式 |
|---------|------|---------|
| switch to [mode] | `activate` | `activate\|模式名\|*\|*\|*\|*\|*` |
| set the [mode] | `activate` | `activate\|模式名\|*\|*\|*\|*\|*` |
| I want [mode] | `activate` | `activate\|模式名\|*\|*\|*\|*\|*` |

**示例**：
```
输入: "switch to presentation mode"
输出: activate|Presentation Mode|*|*|*|*|*
```

**退出场景**：

| 输入模式 | 动作 | 输出格式 |
|---------|------|---------|
| exit [mode] | `deactivate` | `deactivate\|模式名\|*\|*\|*\|*\|*` |
| turn off [mode] | `deactivate` | `deactivate\|模式名\|*\|*\|*\|*\|*` |
| deactivate [mode] | `deactivate` | `deactivate\|模式名\|*\|*\|*\|*\|*` |

**示例**：
```
输入: "exit movie mode"
输出: deactivate|Movie Mode|*|*|*|*|*
```

#### 3.4.2 设备模式

**⚠️ 特殊规则**：
- **灯具的 mode**：使用 `turnOn` 动作
- **空调的 mode**：使用 `set` 动作

**示例**：
```
输入: "set AC to cool mode"
输出: set|AC|mode|Cool|*|*|*

输入: "turn on reading mode" (灯具)
输出: turnOn|Desk Lamp|*|*|*|*|*
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
- 多个命令使用 `\n` 连接
- **禁止使用** `&` 符号

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

当指令包含多个意图时：
- 若**楼层（floor）**未单独说明，默认相同
- 若**房间（room）**未单独说明，默认相同
- 两者缺省时均算正确

**示例**：
```
输入: "turn on all lights in the living room"
说明: 所有灯具默认在同一房间（living room）
```

---

### 4.2 设备名称标准化

#### 规则1：Spot Light 必须拆分
- ✅ 正确：`Spot Light`
- ❌ 错误：`Spotlight`
- 无论输入是 "spotlight" 还是 "spot light"，输出统一为 `Spot Light`

#### 规则2：窗帘禁用 drape
- ✅ 允许：`Curtain`、`Blind`、`Sheer`
- ❌ 禁止：`Drape`

---

## 五、完整示例

### 5.1 基础操作

| 输入指令 | 输出命令 |
|---------|---------|
| Turn on the strip light | `turnOn\|Strip Light\|*\|*\|*\|*\|*` |
| Close the curtain | `turnOff\|Curtain\|*\|*\|*\|*\|*` |
| Set the strip light to blue | `set\|Strip Light\|color\|Blue\|*\|*\|*` |
| Set AC temperature to 24 | `set\|AC\|temperature\|24\|Celsius\|*\|*` |

---

### 5.2 调整操作

| 输入指令 | 输出命令 |
|---------|---------|
| Increase spotlight 10 brightness | `adjustUp\|Spot Light 10\|brightness\|*\|*\|*\|*` |
| Dim the desk lamp | `adjustDown\|Desk Lamp\|brightness\|*\|*\|*\|*` |
| Lower the music volume | `adjustDown\|Music\|volume\|*\|*\|*\|*` |
| Turn down the brightness a bit | `adjustDown\|Strip Light\|brightness\|*\|*\|*\|*` |

---

### 5.3 场景模式

| 输入指令 | 输出命令 |
|---------|---------|
| Switch to presentation mode | `activate\|Presentation Mode\|*\|*\|*\|*\|*` |
| Exit movie mode | `deactivate\|Movie Mode\|*\|*\|*\|*\|*` |
| I want music video mode | `activate\|Music Video Mode\|*\|*\|*\|*\|*` |

---

### 5.4 复合命令

| 输入指令 | 输出命令 |
|---------|---------|
| Turn on strip light and set to blue | `turnOn\|Strip Light\|*\|*\|*\|*\|*\nset\|Strip Light\|color\|Blue\|*\|*\|*` |
| Set brightness to 50 and color to warm white | `set\|Strip Light\|brightness\|50\|Percent\|*\|*\nset\|Strip Light\|color\|Warm White\|*\|*\|*` |

---

## 六、常见错误示例

| 错误类型 | ❌ 错误示例 | ✅ 正确示例 |
|---------|-----------|-----------|
| 设备名拼写 | `Spotlight` | `Spot Light` |
| 窗帘名称 | `Drape` | `Curtain` / `Blind` / `Sheer` |
| 命令连接符 | 使用 `&` | 使用 `\n` |
| 数值操作动作 | 有数值时用 `adjustUp` | 有数值时用 `set` |
| 模糊调整 | `adjustUp\|...\|brightness\|10\|...` | `adjustUp\|...\|brightness\|*\|...` |

---

## 七、快速查询表

### 7.1 动作选择流程图

```
是否包含明确数值？
├─ 是 → set
└─ 否 
   ├─ 开/关设备？→ turnOn / turnOff
   ├─ 增加/减少？→ adjustUp / adjustDown
   └─ 场景模式？→ activate / deactivate
```

### 7.2 单位速查

| 属性 | 单位 |
|------|------|
| brightness, volume, position | Percent |
| colorTemperature | Kelvin |
| temperature | Celsius |
| color, mode, 开关操作 | * |

---

## 八、版本信息

- **文档版本**: v2.0
- **最后更新**: 2026/06/26

---
---

# 附录：版本信息

- **文档版本**: v3.0
- **最后更新**: 2026/06/26
- **说明**: 整合了输入识别规则和输出生成规则
