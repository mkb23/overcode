# Simple Overcode Demo

This is a proof-of-concept demo showing the core Overcode idea: one Claude (you, the user's current Claude) supervising other Claude instances.

## The Demo

This demo launches two Claude instances:
1. **times-tables** - Generates multiplication tables
2. **recipes** - Creates creative recipe ideas

The current Claude session (me!) acts as the "Overcode" supervisor and can report on what the other Claudes are doing.

## How to Run

### Step 1: Launch the supervised Claudes

```bash
cd overcode/demo
python3 simple_overcode.py launch
```

This will start two Claude instances in the background, each working on their assigned tasks.

### Step 2: Check status

Ask me (your current Claude) for an update! I'll use the monitoring tools to tell you what the other Claudes are up to.

Or you can check manually:

```bash
python3 simple_overcode.py status
```

### Step 3: View detailed output

```bash
# View times tables output
python3 simple_overcode.py output times-tables

# View recipes output
python3 simple_overcode.py output recipes
```

## What's Happening

The `simple_overcode.py` script:
- Launches Claude instances with specific prompts
- Captures their stdout to log files
- Provides commands to monitor their progress
- Acts as a simple supervisor

The current Claude session can read these logs and give you updates on what the supervised Claudes are doing.

## Output Location

All output is saved to `overcode_output/`:
- `times-tables.log` - Full output from times tables Claude
- `recipes.log` - Full output from recipes Claude
- `*.meta.json` - Session metadata

## Future Vision

This simple demo illustrates the core concept. The full Overcode (from DESIGN.md) will add:
- Interactive I/O injection
- Autopilot rules
- Full-featured supervisor dashboard
- asciinema recording
- Multi-agent coordination
