# Model Router Herdr plugin

This plugin exposes the repository's Python router as Herdr overlay panes and
actions. It provides actions for routing a task, inspecting candidate models,
checking router status, and viewing judgment-cache statistics.

## Link the plugin

From the repository root:

```bash
herdr plugin link .
```

The actions can then be invoked from Herdr or assigned to custom keybindings.
For example:

```toml
[[keys.command]]
key = "prefix+r"
type = "plugin_action"
command = "model-router.route"
description = "route a task"
```

The other qualified action IDs are `model-router.models`,
`model-router.status`, and `model-router.cache-stats`.

## Configuration

By default, the plugin uses the project virtual environment and treats the
directory containing `herdr-plugin.toml` as the router root. Override either
location when needed:

```bash
export MODEL_ROUTER_PYTHON=/path/to/python
export MODEL_ROUTER_ROOT=/path/to/model-router
```

Each pane waits for a keypress after displaying its result so the overlay stays
open long enough to read.
