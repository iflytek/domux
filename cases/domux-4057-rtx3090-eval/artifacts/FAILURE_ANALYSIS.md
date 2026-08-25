# Domux 4,057 条开放评测失败簇分析

> 本报告由逐样本原始输出重新计算。一个失败样本可能同时属于多个错误簇，
> 因此各错误簇数量之和可能大于失败样本总数。

## 总览

- 完成样本：4057
- 完全正确：3988
- 失败样本：69
- API 错误：0

## 官方指标

| 类别 | 样本 | 格式合规 | Result Accuracy | Slot F1 | Intent F1 | 并发评测平均延迟 |
|---|---:|---:|---:|---:|---:|---:|
| multi_intent | 1641 | 100.00% | 97.87% | 99.69% | 98.74% | 0.369s |
| non_standard_naming | 559 | 100.00% | 95.89% | 99.27% | 95.98% | 0.207s |
| omitted_attribute | 735 | 100.00% | 99.05% | 99.67% | 99.05% | 0.209s |
| single_intent | 1122 | 100.00% | 99.64% | 99.94% | 99.64% | 0.207s |
| **OVERALL** | **4057** | **100.00%** | **98.30%** | **99.70%** | **98.69%** | **0.273s** |

该延迟是并发评测中的端到端 HTTP 请求延迟，不是 TTFT；单并发延迟另见 `latency_summary.json`。

## 错误簇

| 错误簇 | 样本数 | 占失败样本 |
|---|---:|---:|
| 非标准设备名样本失败 | 23 | 33.33% |
| device 错误 | 19 | 27.54% |
| floor 错误 | 17 | 24.64% |
| action 错误 | 13 | 18.84% |
| attribute 错误 | 12 | 17.39% |
| 缺少动作/意图 | 9 | 13.04% |
| value 错误 | 9 | 13.04% |
| room 错误 | 8 | 11.59% |
| 省略属性样本失败 | 7 | 10.14% |
| unit 错误 | 2 | 2.90% |
| 多输出动作/意图 | 1 | 1.45% |
| API 请求失败 | 0 | 0.00% |
| 非七字段格式 | 0 | 0.00% |
| 其他集合匹配错误 | 0 | 0.00% |

## 代表性失败

### 1. 样本 #2544（multi_intent）

错误簇：action 错误、attribute 错误、device 错误、value 错误

输入：

```text
dim the reading light and switch it to eco mode
```

Domux 原始输出：

```text
adjustDown|Reading Light|brightness|*|*|*|*
activate|Eco Mode|*|*|*|*|*
```

标准答案：

```text
adjustDown|Reading Light|brightness|*|*|*|*
turnOn|Reading Light|mode|Eco|*|*|*
```

### 2. 样本 #3544（omitted_attribute）

错误簇：action 错误、attribute 错误、省略属性样本失败、value 错误

输入：

```text
set the Wall Light to heat
```

Domux 原始输出：

```text
set|Wall Light|color|Warm White|*|*|*
```

标准答案：

```text
turnOn|Wall Light|mode|Heat|*|*|*
```

### 3. 样本 #3547（omitted_attribute）

错误簇：action 错误、attribute 错误、省略属性样本失败、value 错误

输入：

```text
set the Floor Lamp to cool
```

Domux 原始输出：

```text
set|Floor Lamp|colorTemperature|*|*|*|*
```

标准答案：

```text
turnOn|Floor Lamp|mode|Cool|*|*|*
```

### 4. 样本 #3548（omitted_attribute）

错误簇：action 错误、attribute 错误、省略属性样本失败、value 错误

输入：

```text
set the Floor Lamp to heat
```

Domux 原始输出：

```text
set|Floor Lamp|color|Warm White|*|*|*
```

标准答案：

```text
turnOn|Floor Lamp|mode|Heat|*|*|*
```

### 5. 样本 #3553（omitted_attribute）

错误簇：attribute 错误、省略属性样本失败、unit 错误、value 错误

输入：

```text
set the ac in the prayer room on the ground floor to high
```

Domux 原始输出：

```text
set|AC|temperature|24|Celsius|Prayer Room|Ground Floor
```

标准答案：

```text
set|AC|windSpeed|High|Level|Prayer Room|Ground Floor
```

### 6. 样本 #3554（omitted_attribute）

错误簇：attribute 错误、省略属性样本失败、unit 错误、value 错误

输入：

```text
set the ac in the gym on the second floor to high
```

Domux 原始输出：

```text
set|AC|temperature|24|Celsius|Gym|Second Floor
```

标准答案：

```text
set|AC|windSpeed|High|Level|Gym|Second Floor
```

### 7. 样本 #3826（non_standard_naming）

错误簇：device 错误、非标准设备名样本失败、room 错误

输入：

```text
dim the nightstand light to 100 percent
```

Domux 原始输出：

```text
set|Light|brightness|100|Percent|Nightstand|*
```

标准答案：

```text
set|Nightstand Light|brightness|100|Percent|*|*
```

### 8. 样本 #3833（non_standard_naming）

错误簇：device 错误、非标准设备名样本失败、room 错误

输入：

```text
change the nightstand lamp color to green
```

Domux 原始输出：

```text
set|Lamp|color|Green|*|Nightstand|*
```

标准答案：

```text
set|Nightstand Lamp|color|Green|*|*|*
```

### 9. 样本 #4050（non_standard_naming）

错误簇：device 错误、非标准设备名样本失败、room 错误

输入：

```text
change the nightstand lamp color to blue
```

Domux 原始输出：

```text
set|Lamp|color|Blue|*|Nightstand|*
```

标准答案：

```text
set|Nightstand Lamp|color|Blue|*|*|*
```

### 10. 样本 #912（single_intent）

错误簇：action 错误、value 错误

输入：

```text
set the AC to reading mode
```

Domux 原始输出：

```text
turnOn|AC|mode|Read|*|*|*
```

标准答案：

```text
set|AC|mode|Reading|*|*|*
```

### 11. 样本 #2234（multi_intent）

错误簇：floor 错误、room 错误

输入：

```text
set the ac in the home office to 22 degrees and turn on the reading light on the first floor
```

Domux 原始输出：

```text
set|AC|temperature|22|Celsius|Home Office|*
turnOn|Reading Light|*|*|*|*|First Floor
```

标准答案：

```text
set|AC|temperature|22|Celsius|Home Office|First Floor
turnOn|Reading Light|*|*|*|Home Office|First Floor
```

### 12. 样本 #2471（multi_intent）

错误簇：device 错误、floor 错误

输入：

```text
lavender and a bit brighter for the floor lamp
```

Domux 原始输出：

```text
set|Light|color|Lavender|*|*|*
adjustUp|Floor Lamp|brightness|*|*|*|Floor
```

标准答案：

```text
set|Floor Lamp|color|Lavender|*|*|*
adjustUp|Floor Lamp|brightness|*|*|*|*
```

### 13. 样本 #2546（multi_intent）

错误簇：floor 错误、缺少动作/意图

输入：

```text
on the ground floor set the ac in the guest bedroom to cool mode at 19 degrees and turn on the chandelier in the majlis at 60 percent brightness
```

Domux 原始输出：

```text
set|AC|mode|Cool|*|Guest Bedroom|Ground Floor
set|Chandelier|brightness|60|Percent|Majlis|*
```

标准答案：

```text
set|AC|mode|Cool|*|Guest Bedroom|Ground Floor
set|AC|temperature|19|Celsius|Guest Bedroom|Ground Floor
set|Chandelier|brightness|60|Percent|Majlis|Ground Floor
```

### 14. 样本 #2629（multi_intent）

错误簇：floor 错误、缺少动作/意图

输入：

```text
set the ac in the kitchen to heat mode at 22 degrees and turn on the ambient light in the majlis on the ground floor
```

Domux 原始输出：

```text
set|AC|mode|Heat|*|Kitchen|*
turnOn|Ambient Light|*|*|*|Majlis|Ground Floor
```

标准答案：

```text
set|AC|mode|Heat|*|Kitchen|Ground Floor
set|AC|temperature|22|Celsius|Kitchen|Ground Floor
turnOn|Ambient Light|*|*|*|Majlis|Ground Floor
```

### 15. 样本 #3431（non_standard_naming）

错误簇：action 错误、非标准设备名样本失败

输入：

```text
set the floor light to auto mode
```

Domux 原始输出：

```text
set|Floor Light|mode|Auto|*|*|*
```

标准答案：

```text
turnOn|Floor Light|mode|Auto|*|*|*
```

## 边界与安全说明

- 完全匹配率对大小写、字段值和动作数量都很严格；Slot F1 更适合观察局部正确性。
- Domux 的输出是控制语义，不是最终设备授权。门锁、燃气、加热设备等高风险动作仍应在执行层增加身份校验、状态检查和二次确认。
- 本报告只描述本次固定 revision、硬件和参数下的结果，不将未测试的优化写成已实现能力。
- 后续可针对高频 slot 错误补充难例，但新增数据必须明确来源和许可，并重新运行相同评测验证。
