# h - Highlight Text Patterns

Highlight text patterns in color by piping text into it. This uses the standard DevOps approach with `grep --color=always` for colored output.

## Usage

```bash
command | h pattern [color]
```

## Parameters

- `pattern` - The text pattern or regex to highlight (required)
- `color` - Color to use for highlighting (optional, defaults to red)

## Available Colors

- black, red, green, yellow, blue, magenta, cyan, white
- bright-black, bright-red, bright-green, bright-yellow, bright-blue, bright-magenta, bright-cyan, bright-white

## Examples

### Highlight ERROR in red (default)
```bash
cat file.log | h ERROR
```

### Highlight WARNING in yellow
```bash
tail -f app.log | h WARNING yellow
```

### Highlight patterns in other colors
```bash
docker ps | h running green
```

### Monitor logs with multiple highlights
```bash
# First highlight errors, then warnings (using multiple pipes)
tail -f app.log | h ERROR red | h WARNING yellow
```

### Use with Azure CLI
```bash
az webapp log tail --name myapp --resource-group myrg | h Exception bright-red
```
