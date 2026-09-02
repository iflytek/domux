# 失败簇分析（4057 条全量）

总样本 4057，失败 98（2.42%）

## 按类别
- multi_intent: 37/1641（失败率 2.25%）
- non_standard_naming: 29/559（失败率 5.19%）
- omitted_attribute: 21/735（失败率 2.86%）
- single_intent: 11/1122（失败率 0.98%）

## 三类典型错误
1. 多指令楼层/房间继承丢失（multi_intent 37 例）：如 `on the ground floor set the chandelier in the majlis ... turn on the floor lamp`，第二条指令的 floor 槽位偶发为 `*`
2. 属性混淆（non_standard_naming 29 例，失败率最高 5.19%）：`turn up the AC` 误判为 `windSpeed` 而非 `temperature`；`make it warmer` 的 brightness/colorTemperature 互换
3. 房间过度推断（omitted_attribute/single_intent 共 32 例）：非标准命名场景下给出具体房间而 gold 为 `*`

## 典型失败样例

- **#1068** [multi_intent]
  - Q: on the ground floor set the chandelier in the majlis to 40 percent brightness and turn on the floor lamp in the living room and set the ceiling light in the prayer room to reading mode
  - P: set|Chandelier|brightness|40|Percent|Majlis|Ground Floor | turnOn|Floor Lamp|*|*|*|Living Room|* | turnOn|Ceiling Light|mode|Reading|*|Prayer Room|*
  - G: set|Chandelier|brightness|40|Percent|Majlis|Ground Floor | turnOn|Floor Lamp|*|*|*|Living Room|Ground Floor | turnOn|Ceiling Light|mode|Reading|*|Prayer Room|Ground Floor

- **#1115** [multi_intent]
  - Q: set the ac in the prayer room to heat mode and turn on the ceiling light in the kitchen on the ground floor
  - P: set|AC|mode|Heat|*|Prayer Room|* | turnOn|Ceiling Light|*|*|*|Kitchen|Ground Floor
  - G: set|AC|mode|Heat|*|Prayer Room|Ground Floor | turnOn|Ceiling Light|*|*|*|Kitchen|Ground Floor

- **#1285** [multi_intent]
  - Q: turn on the ceiling light and close the curtain in the kids room and turn up the ac a bit
  - P: turnOn|Ceiling Light|*|*|*|Kids Room|* | turnOff|Curtain|*|*|*|Kids Room|* | adjustUp|AC|windSpeed|*|*|Kids Room|*
  - G: turnOn|Ceiling Light|*|*|*|Kids Room|* | turnOff|Curtain|*|*|*|Kids Room|* | adjustUp|AC|temperature|*|*|Kids Room|*

- **#2083** [multi_intent]
  - Q: set the ac in the kids room to cool mode at 20 degrees and close the curtain in the home office to 30 percent on the first floor
  - P: set|AC|mode|Cool|*|Kids Room|First Floor | set|Curtain|position|30|Percent|Home Office|First Floor
  - G: set|AC|mode|Cool|*|Kids Room|First Floor | set|AC|temperature|20|Celsius|Kids Room|First Floor | set|Curtain|position|30|Percent|Home Office|First Floor

- **#2105** [multi_intent]
  - Q: set the desk lamp to 75 percent and make it warmer
  - P: set|Desk Lamp|brightness|75|Percent|Desk|* | adjustDown|Desk Lamp|colorTemperature|*|*|Desk|*
  - G: set|Desk Lamp|brightness|75|Percent|*|* | adjustDown|Desk Lamp|colorTemperature|*|*|*|*