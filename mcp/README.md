# who-mcp

Standalone **MCP stdio server** that exposes the OSINT Tool ("who") FastAPI
HTTP API as a small set of tools usable from Claude Desktop, Cursor, or any
other MCP-capable client.

## Install

```bash
pip install -e .
```

## Configure

Two environment variables drive the server:

| Var            | Default                          | Meaning                                         |
| -------------- | -------------------------------- | ----------------------------------------------- |
| `WHO_BASE_URL` | `https://who.worldmapsound.com`  | Base URL of the FastAPI backend.                |
| `WHO_API_KEY`  | _(unset)_                        | Bearer token sent as `Authorization: Bearer …`. |

## Tools exposed

- `osint_create_case(inputs, legal_basis, legal_basis_note?)`
- `osint_run_case(case_id)`
- `osint_get_findings(case_id, kind?, collector?)`
- `osint_get_entities(case_id, type?)`
- `osint_investigate(case_id, provider="gemini", max_steps=8)`
- `osint_export(case_id, format)`

## Claude Desktop / Cursor config

Add this to `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) / `%APPDATA%\Claude\claude_desktop_config.json` (Windows), or to
Cursor's `mcp.json`:

```json
{
  "mcpServers": {
    "who": {
      "command": "who-mcp",
      "env": {
        "WHO_BASE_URL": "https://who.worldmapsound.com",
        "WHO_API_KEY": "YOUR_TOKEN_HERE"
      }
    }
  }
}
```

If `who-mcp` is not on PATH, use the absolute interpreter path instead:

```json
{
  "mcpServers": {
    "who": {
      "command": "/usr/bin/python3",
      "args": ["-m", "mcp.server"],
      "env": {
        "WHO_BASE_URL": "https://who.worldmapsound.com",
        "WHO_API_KEY": "YOUR_TOKEN_HERE"
      }
    }
  }
}
```

Restart Claude Desktop / Cursor; the six `osint_*` tools should appear.

## Development

```bash
pip install -e '.[dev]'
pytest
```
