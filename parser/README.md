# Domux Output Parser

Offline parser that turns raw Domux model output into validated, structured JSON.

模型输出是 7 字段竖线分隔的槽位(每行一条):

```
action|device|attribute|value|unit|room|floor
```

本解析器把它解析成结构化对象并做格式校验。**纯 Python 标准库,无任何依赖,不连接 Home Assistant。**

## 用法

```bash
# 单条 / 多行复合命令 -> 美化 JSON
echo "turnOn|light|*|*|*|living room|1" | python parser/domux_parser.py

# 批处理:每行一条预测,逐行输出一个 JSON 对象(适合数据集/eval 流水线)
python parser/domux_parser.py --jsonl predictions.txt > parsed.jsonl
```

作为库调用:

```python
from parser.domux_parser import parse

res = parse("set|air conditioner|temperature|22|celsius|bedroom|*")
res.valid          # True
res.slots[0].value # 22 (int)
res.slots[0].unit  # "Celsius" (Title Case归一化)
res.to_json()      # 结构化 JSON 字符串
```

## 输出约定

- `*`(don't-care)字段 → `None`
- `value` / `floor` 能转数字就转 `int`/`float`,否则保留字符串(如 AC 的 `mode=cool`)
- 每个 segment 都带 `valid` 和 `errors`;格式错误的 segment **不会被丢弃**,而是标记出来,方便你看清模型到底吐了什么
- 校验口径(action 枚举、`*` 语义、`<think>` 剥离、换行分段)与训练时的 [reward_plugin_slot.py](../training/rewards/reward_plugin_slot.py) **保持一致**,避免解析器和 reward 打架

## 新增:Title Case 归一化(按 [COMMAND_SPEC_zh.md](../COMMAND_SPEC_zh.md))

解析器自动把 device / color / unit 字段归一化到规范的 Title Case:

| 模型吐的(可能大小写混乱) | 归一化后 |
|---|---|
| `light` / `Light` | `Light` |
| `spotlight` / `spot light` | `Spot Light` |
| `air conditioner` / `ac` / `AC` | `AC` |
| `percent` / `Percent` | `Percent` |
| `blue` / `Blue` | `Blue` |
| `warm white` / `Warm White` | `Warm White` |

这样解析器输出和文档约定对齐,下游不用再做一遍大小写清洗。

## 新增:非控制输出识别(`kind`)

模型输出分 3 类:

- `kind: "control"` — 有 `|` 管道符,解析成 slots
- `kind: "non_control"` — 无管道符(提问/闲聊),原文透传到 `text` 字段,**不当成格式错误**
- `kind: "empty"` — 空输出

示例:

```python
parse("Sorry, I can't help with that.")  # kind: non_control, text: "...", valid: True
```

## floor 字段告警

训练数据用数字 `1/2/3` 表示楼层,但你提供的示例用字符串 `Second Floor`。**[COMMAND_SPEC_zh.md](../COMMAND_SPEC_zh.md) 的所有示例都用 `*` 占位没给真实楼层值,规范本身没定清楚。**

解析器现在的策略:**不强制拒绝字符串 floor,但在 `errors` 里标记警告**,方便你排查数据约定不一致的样本:

```python
parse("turnOn|Light|*|*|*|bedroom|Second Floor")
# -> slots[0].valid = False
#    slots[0].errors = ["floor is string 'Second Floor' (training data uses int 1/2/3); spec ambiguous — verify intended format"]
```

**要彻底解决,你得定规范**:要么全用数字(训练数据、模型输出统一改),要么全用字符串(重新标注训练数据、更新文档),不能混用——否则 reward 在计算 floor 字段匹配时数字和字符串永远 0 分。

## 不在范围内(重要)

这一层**只做字符串 → 结构化语义**,刻意不做以下事情:

| 没做的事 | 为什么 / 谁该做 |
| --- | --- |
| `device=light` → HA domain(`curtain`→`cover`、`air conditioner`→`climate`) | 需要 HA 命名映射,属于落地层 |
| `room=living room` → `entity_id`(如 `light.living_room_ceiling`) | 需要 HA 运行时的 area/entity 注册表 |
| `adjustUp`/`adjustDown` → 具体 service call | **有状态**,要读当前设备状态 + step |
| `value=20 percent` → brightness 0-255 还是 brightness_pct | 单位/量纲转换,设备相关 |
| `room=*` 到底控制哪些设备 | **产品决策**,不是解析问题 |

落地到 HA 是独立的一层(Resolver/Mapper),它需要 HA 运行时上下文。推荐做法是把映射交给 HA 自己的 conversation agent / intent 去解析实体,本解析器只负责产出干净的抽象语义。

## 测试

```bash
python parser/test_domux_parser.py   # 或 python -m pytest parser/test_domux_parser.py
```
