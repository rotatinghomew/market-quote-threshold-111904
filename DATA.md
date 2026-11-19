# 资料字典

CSV 为 UTF-8，第一行为列名。日期为北京时间，时间带偏移；资料可见时刻与业务发生时刻分别以字段含义为准。空值不能默认做零。

### `price_rules.csv`

询价日适用版本、整车运费和优惠起订量。未满的旧报价余额不因新版生效消失。渠道范围值 1 代表早市场内报价；另渠道成单也计采购窗贡献。

字段：`version`、`first_day`、`threshold_boxes`、`regular_fee_cents`、`discount_fee_cents`、`sales_channels`。

### `quotes.csv`

同一买方跨渠道窗口由 `purchase_window` 认定。`original_boxes` 是未凑量时记录；优惠豁免经主管批准。

字段：`quote`、`buyer`、`purchase_window`、`issued_at`、`expires_at`、`rule_version`、`original_boxes`、`other_eligibility`、`waiver`、`offered_fee_cents`。

### `orders.csv`

毛利未减由卖方承担的运费及退款，窗口内有非摊位渠道。

字段：`order`、`quote`、`purchase_window`、`channel`、`boxes`、`margin_before_shipping_cents`、`booked_at`、`returns_close`。

### `loading.csv`

实际收取的运费及优惠使用标记以实装记录为准，同车固定成本见政策。

字段：`order`、`truck`、`loaded_boxes`、`paid_freight_cents`、`used_promo`、`known_at`。

### `returns.csv`

`margin_delta_cents` 是应减毛利，不代表退回用车承诺。

字段：`order`、`returned_boxes`、`margin_delta_cents`、`known_at`。

### `open_quotes.csv`

仅列已经发出仍受保护的特殊报价，数量及分款是截至该次记录的未兑保留量。

字段：`quote`、`valid_boxes`、`reserved_cents`、`reserved_truck_slots`、`state`、`known_at`。

### `policy.json`

候选门槛和下一采购窗车位及预算；按实际车次扣固定费，每辆车最大装载为整箱数。限额试用也必须占有效的车和预算。
