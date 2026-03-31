"""NUSMods MCP Server"""

from __future__ import annotations

import httpx
from datetime import datetime
from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

BASE_URL = "https://api.nusmods.com/v2"

# Cache module list
_module_list_cache: dict[str, list[dict]] = {}


def _current_acad_year() -> str:
    """Return the current NUS academic year string, e.g. '2025-2026'."""
    now = datetime.now()
    start = now.year if now.month >= 8 else now.year - 1
    return f"{start}-{start + 1}"


DEFAULT_AY = _current_acad_year()


async def _fetch_json(path: str) -> dict | list | None:
    """Fetch JSON from the NUSMods API. Returns None on error."""
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(f"{BASE_URL}/{path}")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError:
            return None


async def _get_module_list(acad_year: str) -> list[dict]:
    """Fetch and cache the condensed module list for an academic year."""
    if acad_year not in _module_list_cache:
        data = await _fetch_json(f"{acad_year}/moduleList.json")
        _module_list_cache[acad_year] = data or []
    return _module_list_cache[acad_year]


# Server

mcp = FastMCP(
    "NUSMods",
    instructions=(
        "You are an NUS course-planning assistant powered by the NUSMods API. "
        "Use the available tools to look up modules, timetables, and prerequisites, "
        "and to help students plan their semesters. "
        f"The current academic year is {DEFAULT_AY}. "
        "Module codes follow NUS conventions (e.g. CS1101S, MA2001, GEA1000)."
    ),
)


# Tools


@mcp.tool(
    description=(
        "Get detailed information about a specific NUS module including "
        "description, credits, prerequisites, preclusions, workload, "
        "semester availability, timetable, and exam dates."
    ),
    annotations=ToolAnnotations(
        title="Get Module Info",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def get_module(
    module_code: str = Field(description="NUS module code, e.g. 'CS2040S'"),
    acad_year: str = Field(
        default=DEFAULT_AY,
        description="Academic year in 'YYYY-YYYY' format, e.g. '2025-2026'",
    ),
) -> dict:
    """Look up a single NUS module by its code."""
    code = module_code.strip().upper()
    data = await _fetch_json(f"{acad_year}/modules/{code}.json")
    if data is None:
        return {"error": f"Module {code} not found for AY {acad_year}."}

    semesters = []
    for sem in data.get("semesterData", []):
        sem_info = {
            "semester": sem.get("semester"),
            "timetable": sem.get("timetable", []),
        }
        if sem.get("examDate"):
            sem_info["examDate"] = sem["examDate"]
        if sem.get("examDuration"):
            sem_info["examDuration"] = sem["examDuration"]
        semesters.append(sem_info)

    return {
        "moduleCode": data.get("moduleCode"),
        "title": data.get("title"),
        "department": data.get("department"),
        "faculty": data.get("faculty"),
        "description": data.get("description"),
        "moduleCredit": data.get("moduleCredit"),
        "workload": data.get("workload"),
        "prerequisite": data.get("prerequisite"),
        "preclusion": data.get("preclusion"),
        "corequisite": data.get("corequisite"),
        "semesters": semesters,
    }


@mcp.tool(
    description=(
        "Search for NUS modules by keyword. Matches against module codes "
        "and titles. Returns up to `limit` results."
    ),
    annotations=ToolAnnotations(
        title="Search Modules",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def search_modules(
    query: str = Field(description="Search keyword (matches code or title)"),
    acad_year: str = Field(default=DEFAULT_AY, description="Academic year"),
    limit: int = Field(default=20, description="Max results to return"),
) -> list[dict]:
    """Search the module list by keyword."""
    modules = await _get_module_list(acad_year)
    if not modules:
        return [{"error": f"Could not fetch module list for AY {acad_year}."}]

    q = query.strip().upper()
    results = [
        m
        for m in modules
        if q in m.get("moduleCode", "").upper() or q in m.get("title", "").upper()
    ]
    return results[:limit]


# Resources


@mcp.resource(
    "nusmods://server/info",
    description="Server capabilities, current academic year, and NUSMods API reference.",
)
async def server_info() -> dict:
    """Provide server metadata as context for the LLM."""
    return {
        "server": "NUSMods MCP Server",
        "currentAcademicYear": DEFAULT_AY,
        "apiBase": BASE_URL,
        "moduleCodeFormat": (
            "NUS module codes have a 2-4 letter prefix (department), "
            "4-digit number (level), and an optional letter suffix. "
            "Examples: CS1101S, MA2001, GEA1000, ACC1701X."
        ),
        "semesters": {
            "1": "August – December",
            "2": "January – May",
            "3": "Special Term I (May – June)",
            "4": "Special Term II (June – July)",
        },
    }


# Prompts


@mcp.prompt(
    description=(
        "Generate a structured semester planning prompt. Helps students "
        "plan their workload by looking up multiple modules, checking "
        "prerequisites, and flagging potential issues."
    ),
)
def plan_semester(
    modules: str = Field(
        description="Comma-separated module codes, e.g. 'CS2040S,CS2030S,MA2001'"
    ),
    semester: int = Field(default=1, description="Semester number (1 or 2)"),
    acad_year: str = Field(default=DEFAULT_AY, description="Academic year"),
) -> str:
    """Create a semester planning prompt."""
    codes = [c.strip().upper() for c in modules.split(",") if c.strip()]
    code_list = ", ".join(codes)
    return (
        f"I'm planning my NUS semester {semester} for AY {acad_year}. "
        f"I'm considering these modules: {code_list}.\n\n"
        f"For each module, please use the get_module tool to look it up, then:\n"
        f"1. Verify I meet all prerequisites\n"
        f"2. Flag any potential timetable conflicts (e.g. lectures with only "
        f"one slot at the same time) — note that students choose their own "
        f"tutorial/lab groups so those are flexible\n"
        f"3. Check for exam date/time conflicts\n"
        f"4. Assess total workload (sum of MCs and workload hours)\n"
        f"5. Suggest if the combination is manageable"
    )


def main() -> None:
    """Entrypoint to run the NUSMods MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
