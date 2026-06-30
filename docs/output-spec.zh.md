# Domux 指令输出解析规范

> 🌐 **English version**: [output-spec.md](output-spec.md)

本文档定义 Domux 模型输出的结构化控制指令格式，供解析器、下游执行模块及评测系统统一参考。

---

## 📑 目录

- [1. 输出格式](#1-输出格式)
  - [1.1 单意图](#11-单意图)
  - [1.2 多意图](#12-多意图)
- [2. 字段总览](#2-字段总览)
- [3. 字段定义](#3-字段定义)
  - [3.1 `action` — 动作](#31-action--动作)
  - [3.2 `device` — 设备](#32-device--设备)
  - [3.3 `attribute` — 属性](#33-attribute--属性)
  - [3.4 `value` — 取值](#34-value--取值)
  - [3.5 `unit` — 单位](#35-unit--单位)
  - [3.6 `room` — 房间](#36-room--房间)
  - [3.7 `floor` — 楼层](#37-floor--楼层)
- [4. 占位符约定](#4-占位符约定)
- [5. 端到端示例](#5-端到端示例)
- [6. 关于枚举值的说明](#6-关于枚举值的说明)

---

## 1. 输出格式

### 1.1 单意图

每条指令由 **7 个字段** 组成，以竖线 `|` 分隔，顺序固定：

```text
action|device|attribute|value|unit|room|floor
```

### 1.2 多意图

当用户语句包含多条控制意图时，多条指令使用换行符 `\n` 连接，顺序按原句中出现顺序：

```text
action₁|device₁|attribute₁|value₁|unit₁|room₁|floor₁
action₂|device₂|attribute₂|value₂|unit₂|room₂|floor₂
```

---

## 2. 字段总览

| # | 字段 | 含义 | 类型 | 缺省占位 |
|---|------|------|------|----------|
| 1 | `action` | 控制动作 | 枚举 | — |
| 2 | `device` | 目标设备 / 场景模式 | 字符串 | — |
| 3 | `attribute` | 控制属性 | 枚举 | `*` |
| 4 | `value` | 属性取值 | 数值 / 字符串 | `*` |
| 5 | `unit` | 取值单位 | 枚举 | `*` |
| 6 | `room` | 所在房间 | 字符串 | `*` |
| 7 | `floor` | 所在楼层 | 字符串 | `*` |

> 占位符 `*` 的完整规则见 [§4 占位符约定](#4-占位符约定)。

---

## 3. 字段定义

### 3.1 `action` — 动作

枚举类型。输入语言不受限，模型输出会归一化到以下集合：

| 动作 | 说明 | 典型场景 |
|------|------|----------|
| `turnOn` | 打开设备 | 开灯、开空调、拉开窗帘、开启灯光模式 |
| `turnOff` | 关闭设备 | 关灯、关空调、关闭窗帘、关闭灯光模式 |
| `set` | 设置到具体值 | 设置亮度、颜色、色温、温度、风速、空调模式、开合度 |
| `adjustUp` | 增加属性值 | 提高亮度、色温、温度、风速、开合度 |
| `adjustDown` | 减少属性值 | 降低亮度、色温、温度、风速、开合度 |
| `activate` | 激活场景模式 | 开启派对模式、浪漫模式等 |
| `deactivate` | 取消场景模式 | 退出派对模式、浪漫模式等 |
| `pause` | 暂停运动中的窗帘 | 让窗帘在中途停止 |

**选择原则**

- 指令中包含**明确数值**时，必须使用 `set`。
- 指令中包含 `a little` / `a bit` 等模糊修饰词时，使用 `adjustUp` / `adjustDown`，`value` 字段留空（填 `*`）。
- `pause` 仅用于窗帘类设备（`Curtain` / `Blind` / `Sheer Curtain`），表示中途停下，如 *"stop the curtain"*、*"pause the blind"*。

---

### 3.2 `device` — 设备

#### 3.2.1 命名规范

- 基础类型固定为：`Light`、`Curtain`、`Blind`、`AC`。
- 首字母大写、单数形式（不带 `s`）。
- 由多个词组成时以**单个空格**连接，例如 `Spot Light`、`Strip Light`。

#### 3.2.2 实体设备

支持以下命名模式：

| 模式 | 示例 |
|------|------|
| 基础类型 | `Light`、`Curtain`、`Blind`、`AC` |
| 编号 / 字母后缀 | `Light 1`、`Light 2`、`Light A`、`Light B` |
| 前缀 + 类型 | `Spot Light`、`Strip Light`、`Sheer Curtain` |
| 前缀 + 类型 + 后缀 | `Spot Light 1`、`Strip Light A` |

#### 3.2.3 场景模式

`device` 字段也用于承载场景模式名，配合 `activate` / `deactivate` 使用：

- 首字母大写、空格连接。
- 示例：`Romantic Mode`、`Party Mode`、`Sleeping Mode`、`Holiday Mode`。

---

### 3.3 `attribute` — 属性

按设备类型分组列出，未在表中的属性场景请使用占位符 `*`。

| 属性 | 含义 | 适用设备 | 值类型 |
|------|------|----------|--------|
| `brightness` | 亮度 | 灯具 | 数值 |
| `color` | 颜色 | 灯具 | 字符串 |
| `colorTemperature` | 色温 | 灯具 | 数值 |
| `mode` | 灯光模式 | 灯具 | 字符串 |
| `mode` | 空调模式 | 空调 | 字符串 |
| `windSpeed` | 风速 | 空调 | 字符串 |
| `temperature` | 温度 | 空调 | 数值 |
| `position` | 开合位置 | 窗帘 | 数值 |
| `*` | 无属性 | 配合 `turnOn` / `turnOff` / `activate` / `deactivate` / `pause` | — |

---

### 3.4 `value` — 取值

#### 3.4.1 数值与字符串

| 属性 | 值类型 | 单位 |
|------|--------|------|
| 亮度 `brightness` | 数值 | `Percent` |
| 色温 `colorTemperature` | 数值 | `Kelvin` |
| 灯光模式 `mode` | 字符串 | `*` |
| 开合位置 `position` | 数值 | `Percent` |
| 温度 `temperature` | 数值 | `Celsius` |
| 风速 `windSpeed` | 字符串 | `Level` |
| 空调模式 `mode` | 字符串 | `*` |

#### 3.4.2 颜色名称

**基础颜色**：`Blue`、`Red`、`Green`、`Yellow`、`Orange`、`Pink`、`Purple`、`Cyan`、`Magenta`、`Lavender`

**白色系**：`White`、`Warm White`、`Cool White`、`Sky Blue`

#### 3.4.3 灯光模式

示例：`Romance`、`Soft`、`Reading`、`Eco`

#### 3.4.4 空调模式

| 值 | 说明 |
|----|------|
| `Cool` | 制冷 |
| `Heat` | 制热 |
| `Dry` | 除湿 |
| `Fan` | 送风 |
| `Auto` | 自动 |

#### 3.4.5 风速等级

| 值 | 说明 |
|----|------|
| `Low` | 低速 |
| `Medium` | 中速 |
| `High` | 高速 |

---

### 3.5 `unit` — 单位

| 单位 | 适用属性 |
|------|----------|
| `Percent` | `brightness`、`position` |
| `Kelvin` | `colorTemperature` |
| `Celsius` | `temperature` |
| `Level` | `windSpeed` |
| `*` | 其他情况（`color`、`mode`、无属性等） |

---

### 3.6 `room` — 房间

#### 3.6.1 命名规范

- 首字母大写，词间以单个空格连接。
- 支持后缀编号 / 字母，如 `Bedroom 1`、`Bedroom A`、`Room B`。
- 支持前缀修饰，如 `Master Bedroom`、`Second Bedroom`。

#### 3.6.2 常见房间示例

| 类别 | 示例 |
|------|------|
| 公共区 | `Living Room`、`Dining Room`、`Kitchen`、`Entrance Hall`、`Corridor` |
| 卧室 | `Master Bedroom`、`First Bedroom`、`Bedroom 1`、`Bedroom A` |
| 卫浴 | `Bathroom`、`Master Bathroom` |
| 工作 / 娱乐 | `Home Office`、`Movie Theater`、`Gym` |
| 户外 / 辅助 | `Balcony`、`Patio`、`Swimming Pool Area`、`Garage`、`Laundry Room` |
| 文化特色 | `Majlis`（阿拉伯式会客厅）、`Prayer Room` |
| 其他 | `Closet`、`Nanny's Quarter`、`Room A`、`Room 1` |
| 未指定 | `*` |

---

### 3.7 `floor` — 楼层

#### 3.7.1 命名规范

- 首字母大写，词间以单个空格连接。
- 支持前缀修饰。

#### 3.7.2 示例

| 类型 | 示例 |
|------|------|
| 命名楼层 | `Ground Floor`、`First Floor`、`Second Floor`、`Third Floor` |
| 相对楼层 | `Upstairs`、`Downstairs` |
| 未指定（最常见） | `*` |

---

## 4. 占位符约定

字符 `*` 表示**该字段在当前指令中不适用或未指定**

---

## 5. 端到端示例

| 输入 | 输出 |
|------|------|
| Turn on the light in the living room | `turnOn\|Light\|*\|*\|*\|Living Room\|*` |
| Set the AC in the master bedroom to 24 degrees | `set\|AC\|temperature\|24\|Celsius\|Master Bedroom\|*` |
| Make the bedroom light a bit brighter on the second floor | `adjustUp\|Light\|brightness\|*\|*\|Bedroom\|Second Floor` |
| Pause the curtain in the home office | `pause\|Curtain\|*\|*\|*\|Home Office\|*` |
| Activate romantic mode | `activate\|Romantic Mode\|*\|*\|*\|*\|*` |
| Turn on the living room light and set the AC to cool | `turnOn\|Light\|*\|*\|*\|Living Room\|*\nset\|AC\|mode\|Cool\|*\|Living Room\|*` |

---

## 6. 关于枚举值的说明

文档中列出的颜色、灯光模式、空调模式、风速、房间、楼层等枚举为**已验证样本**，并非全集。模型对未列出的合理表达同样具备泛化能力，正式接入下游执行系统时建议：

1. 按白名单方式匹配核心枚举（`action`、`attribute`、`unit`）。
2. 对开放枚举（`device`、`color`、`mode`、`room`、`floor`）做大小写归一与同义词映射，再交业务侧判断是否支持。
3. 结合实际设备清单做最终落地校验。
