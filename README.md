# NUSMods MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) server for searching and querying module information from the [NUSMods API](https://api.nusmods.com/v2/).

## Tools

| Tool | Description |
|------|-------------|
| `get_module` | Get detailed module info - description, credits, prerequisites, preclusions, workload, semesters, exam dates |
| `search_modules` | Search modules by keyword across codes and titles |
| `filter_modules` | Filter the module catalog by prefix, faculty, semester, or level, with optional detail enrichment |
| `get_modules` | Fetch multiple module detail payloads in one call while preserving input order |
| `evaluate_module_plan` | Evaluate a shortlist for one semester - prerequisites, preclusions, workload, fixed timetable clashes, and exam conflicts |
| `list_venues` | List venues for a semester with optional substring filtering |
| `get_venue_schedule` | Get the day-by-day schedule for a venue in a semester |

## Prompts

| Prompt | Description |
|--------|-------------|
| `plan_semester` | Structured semester planning prompt that tells Claude to use `filter_modules` and `evaluate_module_plan` for a shortlist |

## Resources

| URI | Description |
|-----|-------------|
| `nusmods://server/info` | Server capabilities, current academic year, module code format reference |

## Development

```bash
git clone https://github.com/shawnnygoh/nusmods-mcp.git
cd nusmods-mcp
pip install -r requirements.txt

# Inspect server components
fastmcp inspect server.py

# Run locally with HTTP transport
fastmcp run server.py --transport http

# Test with MCP Inspector
npx @modelcontextprotocol/inspector

# Run tests
python3 -m unittest discover -s tests -v
```

## License

MIT
