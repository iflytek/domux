

### Fields
1. **action**: One of the valid actions above
2. **device**: Device type (e.g., light, air conditioner, curtain)
3. **attribute**: Property to control (e.g., temperature, brightness)
4. **value**: Numeric or string value
5. **unit**: Unit of measurement (e.g., celsius, percent)
6. **room**: Room location (e.g., living room, bedroom)
7. **floor**: Floor number

Use `*` for unspecified or don't-care fields.

### Multiple Actions

Separate multiple actions with newlines:

```
turnOn|light|*|*|*|living room|*
set|air conditioner|temperature|22|celsius|bedroom|*
```

## Preparing Your Data

1. Convert your data to the appropriate JSONL format
2. Ensure all outputs follow the slot format specification
3. Validate a few samples manually
4. Split into train/validation sets (recommended 90/10 split)
5. Update paths in training scripts
