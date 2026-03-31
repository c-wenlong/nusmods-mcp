"""NUSMods MCP Server."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime, timedelta
from typing import Any, Iterable

import httpx
from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

BASE_URL = "https://api.nusmods.com/v2"

# Lazy in-memory caches keyed by academic year or academic year + semester.
_module_list_cache: dict[str, list[dict[str, Any]]] = {}
_module_information_cache: dict[str, list[dict[str, Any]]] = {}
_module_information_index_cache: dict[str, dict[str, dict[str, Any]]] = {}
_module_detail_cache: dict[tuple[str, str], dict[str, Any]] = {}
_venue_list_cache: dict[tuple[str, int], list[str]] = {}
_venue_information_cache: dict[tuple[str, int], dict[str, list[dict[str, Any]]]] = {}


def _current_acad_year() -> str:
    """Return the current NUS academic year string, e.g. '2025-2026'."""
    now = datetime.now()
    start = now.year if now.month >= 8 else now.year - 1
    return f"{start}-{start + 1}"


DEFAULT_AY = _current_acad_year()


async def _fetch_json(path: str) -> dict[str, Any] | list[Any] | None:
    """Fetch JSON from the NUSMods API. Returns None on error."""
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(f"{BASE_URL}/{path}")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError:
            return None


async def _get_module_list(acad_year: str) -> list[dict[str, Any]]:
    """Fetch and cache the condensed module list for an academic year."""
    if acad_year not in _module_list_cache:
        data = await _fetch_json(f"{acad_year}/moduleList.json")
        _module_list_cache[acad_year] = data if isinstance(data, list) else []
    return _module_list_cache[acad_year]


async def _get_module_information(acad_year: str) -> list[dict[str, Any]]:
    """Fetch and cache detailed module information for an academic year."""
    if acad_year not in _module_information_cache:
        # Live API discovery showed moduleInformation.json tracks the student-facing
        # catalog more reliably than moduleInfo.json for this use case.
        data = await _fetch_json(f"{acad_year}/moduleInformation.json")
        _module_information_cache[acad_year] = data if isinstance(data, list) else []
    return _module_information_cache[acad_year]


async def _get_module_information_index(acad_year: str) -> dict[str, dict[str, Any]]:
    """Return a moduleCode -> moduleInformation mapping."""
    if acad_year not in _module_information_index_cache:
        modules = await _get_module_information(acad_year)
        _module_information_index_cache[acad_year] = {
            _normalize_module_code(module.get("moduleCode", "")): module
            for module in modules
            if module.get("moduleCode")
        }
    return _module_information_index_cache[acad_year]


async def _get_module_detail(acad_year: str, module_code: str) -> dict[str, Any] | None:
    """Fetch and cache a single module detail payload."""
    code = _normalize_module_code(module_code)
    cache_key = (acad_year, code)
    if cache_key not in _module_detail_cache:
        data = await _fetch_json(f"{acad_year}/modules/{code}.json")
        if isinstance(data, dict):
            _module_detail_cache[cache_key] = data
        else:
            return None
    return _module_detail_cache.get(cache_key)


async def _get_venue_list(acad_year: str, semester: int) -> list[str]:
    """Fetch and cache the venue list for an academic year and semester."""
    cache_key = (acad_year, semester)
    if cache_key not in _venue_list_cache:
        data = await _fetch_json(f"{acad_year}/semesters/{semester}/venues.json")
        _venue_list_cache[cache_key] = data if isinstance(data, list) else []
    return _venue_list_cache[cache_key]


async def _get_venue_information(
    acad_year: str, semester: int
) -> dict[str, list[dict[str, Any]]]:
    """Fetch and cache venue information for an academic year and semester."""
    cache_key = (acad_year, semester)
    if cache_key not in _venue_information_cache:
        data = await _fetch_json(f"{acad_year}/semesters/{semester}/venueInformation.json")
        _venue_information_cache[cache_key] = data if isinstance(data, dict) else {}
    return _venue_information_cache[cache_key]


def _normalize_module_code(module_code: str) -> str:
    """Normalize a module code for lookup."""
    return module_code.strip().upper()


def _normalize_module_codes(module_codes: Iterable[str] | str) -> list[str]:
    """Normalize either a comma-separated string or a list of module codes."""
    raw_codes = module_codes.split(",") if isinstance(module_codes, str) else module_codes
    return [
        normalized
        for code in raw_codes
        if isinstance(code, str)
        if (normalized := _normalize_module_code(code))
    ]


def _module_semesters(module: dict[str, Any]) -> list[int]:
    """Return the semesters in which a module appears."""
    semesters = module.get("semesters")
    if isinstance(semesters, list):
        return [int(value) for value in semesters if isinstance(value, int)]

    semester_data = module.get("semesterData", [])
    if isinstance(semester_data, list):
        found = {
            int(entry["semester"])
            for entry in semester_data
            if isinstance(entry, dict) and isinstance(entry.get("semester"), int)
        }
        return sorted(found)
    return []


def _extract_level(module_code: str) -> int | None:
    """Extract a NUS-style level bucket such as 2000 from a module code."""
    match = re.search(r"(\d{4})", module_code)
    if not match:
        return None
    value = int(match.group(1))
    return (value // 1000) * 1000


def _merge_module_records(
    summary: dict[str, Any], detail: dict[str, Any] | None
) -> dict[str, Any]:
    """Merge moduleList and moduleInformation records into one compact shape."""
    base = detail or {}
    return {
        "moduleCode": summary.get("moduleCode") or base.get("moduleCode"),
        "title": summary.get("title") or base.get("title"),
        "semesters": _module_semesters(summary) or _module_semesters(base),
        "department": base.get("department"),
        "faculty": base.get("faculty"),
        "description": base.get("description"),
        "moduleCredit": base.get("moduleCredit"),
        "workload": base.get("workload"),
        "gradingBasisDescription": base.get("gradingBasisDescription"),
    }


def _serialize_semesters(module: dict[str, Any]) -> list[dict[str, Any]]:
    """Serialize semesterData into a client-friendly shape."""
    semesters: list[dict[str, Any]] = []
    for sem in module.get("semesterData", []):
        if not isinstance(sem, dict):
            continue
        sem_info = {
            "semester": sem.get("semester"),
            "timetable": sem.get("timetable", []),
        }
        if sem.get("examDate"):
            sem_info["examDate"] = sem["examDate"]
        if sem.get("examDuration"):
            sem_info["examDuration"] = sem["examDuration"]
        semesters.append(sem_info)
    return semesters


def _serialize_module_detail(module: dict[str, Any]) -> dict[str, Any]:
    """Serialize a single detailed module payload."""
    return {
        "moduleCode": module.get("moduleCode"),
        "title": module.get("title"),
        "department": module.get("department"),
        "faculty": module.get("faculty"),
        "description": module.get("description"),
        "moduleCredit": module.get("moduleCredit"),
        "gradingBasisDescription": module.get("gradingBasisDescription"),
        "workload": module.get("workload"),
        "attributes": module.get("attributes"),
        "prerequisite": module.get("prerequisite"),
        "prereqTree": module.get("prereqTree"),
        "preclusion": module.get("preclusion"),
        "preclusionRule": module.get("preclusionRule"),
        "corequisite": module.get("corequisite"),
        "fulfillRequirements": module.get("fulfillRequirements", []),
        "semesters": _serialize_semesters(module),
    }


def _parse_module_credit(module_credit: Any) -> float | None:
    """Parse a module credit value into a number."""
    if module_credit is None:
        return None
    try:
        return float(module_credit)
    except (TypeError, ValueError):
        return None


def _parse_workload(workload: Any) -> tuple[list[float], float] | None:
    """Parse a workload 5-tuple into numeric hours."""
    if not isinstance(workload, list) or len(workload) != 5:
        return None
    values: list[float] = []
    for value in workload:
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            return None
    return values, sum(values)


def _parse_exam_datetime(value: Any) -> datetime | None:
    """Parse an ISO-like exam datetime string."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _get_semester_entry(module: dict[str, Any], semester: int) -> dict[str, Any] | None:
    """Return the semesterData record for a target semester."""
    for entry in module.get("semesterData", []):
        if isinstance(entry, dict) and entry.get("semester") == semester:
            return entry
    return None


def _strip_grade_suffix(module_code: str) -> str:
    """Strip any grade suffix from a prereqTree leaf such as CS1010:D."""
    return module_code.split(":", 1)[0]


def _evaluate_prereq_tree(tree: Any, completed_modules: set[str]) -> dict[str, Any]:
    """Evaluate a prereqTree against completed modules."""
    if isinstance(tree, str):
        module_code = _strip_grade_suffix(tree)
        satisfied = module_code in completed_modules
        return {
            "resolved": True,
            "satisfied": satisfied,
            "missing": [] if satisfied else [module_code],
        }

    if not isinstance(tree, dict):
        return {"resolved": False, "satisfied": False, "missing": []}

    if "and" in tree and isinstance(tree["and"], list):
        children = [_evaluate_prereq_tree(child, completed_modules) for child in tree["and"]]
        resolved = all(child["resolved"] for child in children)
        satisfied = resolved and all(child["satisfied"] for child in children)
        missing = sorted(
            {
                code
                for child in children
                for code in child["missing"]
            }
        )
        return {"resolved": resolved, "satisfied": satisfied, "missing": missing}

    if "or" in tree and isinstance(tree["or"], list):
        children = [_evaluate_prereq_tree(child, completed_modules) for child in tree["or"]]
        if any(child["resolved"] and child["satisfied"] for child in children):
            return {"resolved": True, "satisfied": True, "missing": []}
        resolved = all(child["resolved"] for child in children)
        missing = sorted(
            {
                code
                for child in children
                for code in child["missing"]
            }
        )
        return {"resolved": resolved, "satisfied": False, "missing": missing}

    return {"resolved": False, "satisfied": False, "missing": []}


def _normalize_weeks(weeks: Any) -> set[int] | None:
    """Normalize week specifications into a comparable set of week numbers."""
    if weeks is None:
        return None

    if isinstance(weeks, int):
        return {weeks}

    if isinstance(weeks, list):
        values = {int(value) for value in weeks if isinstance(value, int)}
        return values or None

    if isinstance(weeks, dict):
        explicit = weeks.get("weeks")
        if isinstance(explicit, list):
            values = {int(value) for value in explicit if isinstance(value, int)}
            if values:
                return values

        start_raw = weeks.get("start")
        end_raw = weeks.get("end")
        if isinstance(start_raw, str) and isinstance(end_raw, str):
            try:
                start = date.fromisoformat(start_raw)
                end = date.fromisoformat(end_raw)
            except ValueError:
                return None
            interval = weeks.get("weekInterval", 1)
            if not isinstance(interval, int) or interval <= 0:
                interval = 1
            span = ((end - start).days // 7) + 1
            if span <= 0:
                return None
            return set(range(1, span + 1, interval))

    return None


def _lesson_signature(lesson: dict[str, Any]) -> tuple[Any, ...]:
    """Create a stable signature for a timetable lesson entry."""
    weeks = _normalize_weeks(lesson.get("weeks"))
    weeks_key: tuple[Any, ...]
    if weeks is None:
        weeks_key = ("ALL",)
    else:
        weeks_key = tuple(sorted(weeks))
    return (
        lesson.get("day"),
        lesson.get("startTime"),
        lesson.get("endTime"),
        weeks_key,
    )


def _extract_fixed_lessons(module: dict[str, Any], semester: int) -> list[dict[str, Any]]:
    """Extract lesson types with only one unique schedule across all class options."""
    semester_entry = _get_semester_entry(module, semester)
    if not semester_entry:
        return []

    timetable = semester_entry.get("timetable", [])
    if not isinstance(timetable, list):
        return []

    by_type: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for lesson in timetable:
        if not isinstance(lesson, dict):
            continue
        lesson_type = lesson.get("lessonType")
        class_no = str(lesson.get("classNo", "__default__"))
        if not isinstance(lesson_type, str):
            continue
        by_type.setdefault(lesson_type, {}).setdefault(class_no, []).append(lesson)

    fixed_lessons: list[dict[str, Any]] = []
    for lesson_type, by_class_no in by_type.items():
        class_signatures = {
            tuple(sorted(_lesson_signature(lesson) for lesson in lessons))
            for lessons in by_class_no.values()
        }
        if len(class_signatures) != 1:
            continue

        canonical_class_no = sorted(by_class_no.keys())[0]
        for lesson in by_class_no[canonical_class_no]:
            fixed_lessons.append(
                {
                    "moduleCode": module.get("moduleCode"),
                    "title": module.get("title"),
                    "lessonType": lesson_type,
                    "classNo": lesson.get("classNo"),
                    "day": lesson.get("day"),
                    "startTime": lesson.get("startTime"),
                    "endTime": lesson.get("endTime"),
                    "weeks": lesson.get("weeks"),
                    "venue": lesson.get("venue"),
                }
            )

    return fixed_lessons


def _times_overlap(
    left_start: Any, left_end: Any, right_start: Any, right_end: Any
) -> bool:
    """Check whether two HHMM ranges overlap."""
    try:
        left_start_int = int(left_start)
        left_end_int = int(left_end)
        right_start_int = int(right_start)
        right_end_int = int(right_end)
    except (TypeError, ValueError):
        return False
    return left_start_int < right_end_int and right_start_int < left_end_int


def _weeks_overlap(left_weeks: Any, right_weeks: Any) -> bool:
    """Check whether two week definitions overlap."""
    left = _normalize_weeks(left_weeks)
    right = _normalize_weeks(right_weeks)
    if left is None or right is None:
        return True
    return bool(left & right)


def _lessons_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Check whether two fixed lessons overlap in time."""
    if left.get("day") != right.get("day"):
        return False
    if not _times_overlap(
        left.get("startTime"),
        left.get("endTime"),
        right.get("startTime"),
        right.get("endTime"),
    ):
        return False
    return _weeks_overlap(left.get("weeks"), right.get("weeks"))


def _find_preclusion_warnings(
    module: dict[str, Any],
    selected_codes: set[str],
    completed_codes: set[str],
) -> list[dict[str, Any]]:
    """Find exact module-code mentions inside a module's preclusion text."""
    current_code = _normalize_module_code(module.get("moduleCode", ""))
    texts = [
        value
        for value in (module.get("preclusion"), module.get("preclusionRule"))
        if isinstance(value, str) and value
    ]
    if not texts:
        return []

    haystack = " ".join(texts).upper()
    warnings: list[dict[str, Any]] = []
    for code in sorted((selected_codes | completed_codes) - {current_code}):
        pattern = rf"(?<![A-Z0-9]){re.escape(code)}(?![A-Z0-9])"
        if re.search(pattern, haystack):
            warnings.append(
                {
                    "moduleCode": current_code,
                    "matchedModuleCode": code,
                    "matchedSource": "selected" if code in selected_codes else "completed",
                    "preclusion": module.get("preclusion"),
                }
            )
    return warnings


def _build_module_plan_entry(
    module: dict[str, Any],
    semester: int,
    completed_codes: set[str],
    selected_codes: set[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the per-module evaluate_module_plan response and status summary."""
    current_code = _normalize_module_code(module.get("moduleCode", ""))
    semester_entry = _get_semester_entry(module, semester)
    workload_parsed = _parse_workload(module.get("workload"))
    prereq_tree = module.get("prereqTree")
    prerequisite_text = module.get("prerequisite")

    missing_prerequisites: list[str] = []
    raw_prerequisite: str | None = None
    prerequisite_logic_resolved = True
    if prereq_tree is not None:
        result = _evaluate_prereq_tree(prereq_tree, completed_codes)
        prerequisite_logic_resolved = bool(result["resolved"])
        if prerequisite_logic_resolved:
            missing_prerequisites = result["missing"]
        else:
            raw_prerequisite = prerequisite_text
    elif prerequisite_text:
        prerequisite_logic_resolved = False
        raw_prerequisite = prerequisite_text

    preclusion_warnings = _find_preclusion_warnings(module, selected_codes, completed_codes)
    fixed_lessons = _extract_fixed_lessons(module, semester)

    entry = {
        "moduleCode": current_code,
        "title": module.get("title"),
        "department": module.get("department"),
        "faculty": module.get("faculty"),
        "description": module.get("description"),
        "moduleCredit": module.get("moduleCredit"),
        "workload": module.get("workload"),
        "offeredInSemester": semester_entry is not None,
        "semester": semester,
        "examDate": semester_entry.get("examDate") if semester_entry else None,
        "examDuration": semester_entry.get("examDuration") if semester_entry else None,
        "prerequisite": prerequisite_text,
        "prereqTree": prereq_tree,
        "missingPrerequisites": missing_prerequisites,
        "prerequisiteLogicResolved": prerequisite_logic_resolved,
        "rawPrerequisite": raw_prerequisite,
        "preclusion": module.get("preclusion"),
        "preclusionWarnings": preclusion_warnings,
        "fulfillRequirements": module.get("fulfillRequirements", []),
        "fixedLessons": fixed_lessons,
    }

    summary = {
        "moduleCredit": _parse_module_credit(module.get("moduleCredit")),
        "workloadTotal": workload_parsed[1] if workload_parsed else None,
        "unparsedWorkload": module.get("workload") is not None and workload_parsed is None,
        "unresolvedPrerequisite": not prerequisite_logic_resolved,
        "offeredInSemester": semester_entry is not None,
        "preclusionWarnings": preclusion_warnings,
    }
    return entry, summary


def _build_exam_window(module_entry: dict[str, Any]) -> tuple[datetime, datetime] | None:
    """Build an exam time window for a selected module entry."""
    start = _parse_exam_datetime(module_entry.get("examDate"))
    duration = module_entry.get("examDuration")
    if start is None or not isinstance(duration, int):
        return None
    return start, start + timedelta(minutes=duration)


def _unique_in_order(values: Iterable[str]) -> list[str]:
    """Return values with duplicates removed while preserving order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


# Server

mcp = FastMCP(
    "NUSMods",
    instructions=(
        "You are an NUS course-planning assistant powered by the NUSMods API. "
        "Use the available tools to look up modules, timetables, prerequisites, "
        "and venue schedules to help students plan a single semester. "
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
) -> dict[str, Any]:
    """Look up a single NUS module by its code."""
    code = _normalize_module_code(module_code)
    data = await _get_module_detail(acad_year, code)
    if data is None:
        return {"moduleCode": code, "error": f"Module {code} not found for AY {acad_year}."}
    return _serialize_module_detail(data)


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
) -> list[dict[str, Any]]:
    """Search the module list by keyword."""
    modules = await _get_module_list(acad_year)
    if not modules:
        return [{"error": f"Could not fetch module list for AY {acad_year}."}]

    q = query.strip().upper()
    results = [
        module
        for module in modules
        if q in module.get("moduleCode", "").upper() or q in module.get("title", "").upper()
    ]
    return results[: max(limit, 0)]


@mcp.tool(
    description=(
        "Filter the NUS module catalog by prefix, faculty, semester, or level. "
        "Use include_details=true to enrich results with moduleInformation.json data."
    ),
    annotations=ToolAnnotations(
        title="Filter Modules",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def filter_modules(
    prefix: str | None = Field(
        default=None, description="Module code prefix, e.g. 'CS' or 'MA'"
    ),
    faculty: str | None = Field(
        default=None, description="Exact faculty name, case-insensitive, e.g. 'Computing'"
    ),
    semester: int | None = Field(
        default=None, description="Only include modules offered in this semester"
    ),
    level: int | None = Field(
        default=None, description="NUS level bucket such as 1000, 2000, 3000"
    ),
    include_details: bool = Field(
        default=False,
        description="Join in moduleInformation.json fields like faculty, credits, and workload",
    ),
    acad_year: str = Field(default=DEFAULT_AY, description="Academic year"),
    limit: int = Field(default=100, description="Max results to return"),
) -> list[dict[str, Any]]:
    """Filter modules using catalog metadata and optional detailed information."""
    modules = await _get_module_list(acad_year)
    if not modules:
        return [{"error": f"Could not fetch module list for AY {acad_year}."}]

    normalized_prefix = prefix.strip().upper() if isinstance(prefix, str) and prefix.strip() else None
    normalized_faculty = faculty.strip().casefold() if isinstance(faculty, str) and faculty.strip() else None

    details_by_code: dict[str, dict[str, Any]] = {}
    needs_details = include_details or normalized_faculty is not None
    if needs_details:
        details_by_code = await _get_module_information_index(acad_year)

    results: list[dict[str, Any]] = []
    for summary in modules:
        code = _normalize_module_code(summary.get("moduleCode", ""))
        if normalized_prefix and not code.startswith(normalized_prefix):
            continue
        if level is not None and _extract_level(code) != level:
            continue
        if semester is not None and semester not in _module_semesters(summary):
            continue

        detail = details_by_code.get(code) if needs_details else None
        if normalized_faculty:
            faculty_value = detail.get("faculty") if detail else None
            if not isinstance(faculty_value, str) or faculty_value.casefold() != normalized_faculty:
                continue

        record = _merge_module_records(summary, detail) if include_details else dict(summary)
        results.append(record)
        if len(results) >= max(limit, 0):
            break

    return results


@mcp.tool(
    description=(
        "Fetch multiple NUS modules in one call. Preserves input order and returns "
        "per-item errors for invalid module codes."
    ),
    annotations=ToolAnnotations(
        title="Get Multiple Modules",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def get_modules(
    module_codes: list[str] | str = Field(
        description="Module codes as a JSON array or comma-separated string"
    ),
    acad_year: str = Field(default=DEFAULT_AY, description="Academic year"),
) -> list[dict[str, Any]]:
    """Fetch multiple module detail records while preserving input order."""
    codes = _normalize_module_codes(module_codes)
    details = await asyncio.gather(*(_get_module_detail(acad_year, code) for code in codes))

    results: list[dict[str, Any]] = []
    for code, detail in zip(codes, details):
        if detail is None:
            results.append({"moduleCode": code, "error": f"Module {code} not found for AY {acad_year}."})
        else:
            results.append(_serialize_module_detail(detail))
    return results


@mcp.tool(
    description=(
        "Evaluate a shortlist of modules for one semester. Returns structured data "
        "about semester availability, prerequisites, preclusions, fixed timetable "
        "conflicts, exam conflicts, and workload totals."
    ),
    annotations=ToolAnnotations(
        title="Evaluate Module Plan",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def evaluate_module_plan(
    module_codes: list[str] | str = Field(
        description="Shortlisted module codes as a JSON array or comma-separated string"
    ),
    semester: int = Field(description="Semester number, e.g. 1 or 2"),
    completed_modules: list[str] | str = Field(
        default_factory=list,
        description="Completed module codes as a JSON array or comma-separated string",
    ),
    acad_year: str = Field(default=DEFAULT_AY, description="Academic year"),
) -> dict[str, Any]:
    """Evaluate a one-semester shortlist using authoritative module detail data."""
    requested_codes = _normalize_module_codes(module_codes)
    completed_codes = set(_normalize_module_codes(completed_modules))
    selected_codes = set(requested_codes)

    details = await asyncio.gather(
        *(_get_module_detail(acad_year, code) for code in requested_codes)
    )

    selected_modules: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    summary = {
        "selectedModuleCount": 0,
        "offeredModuleCodes": [],
        "notOfferedModuleCodes": [],
        "totalModuleCredits": 0.0,
        "totalWorkloadHours": 0.0,
        "hasUnparsedWorkload": False,
        "unparsedWorkloadModules": [],
        "unresolvedPrerequisiteModules": [],
    }
    aggregated_preclusion_warnings: list[dict[str, Any]] = []

    for code, detail in zip(requested_codes, details):
        if detail is None:
            errors.append({"moduleCode": code, "error": f"Module {code} not found for AY {acad_year}."})
            continue

        module_entry, module_summary = _build_module_plan_entry(
            detail,
            semester,
            completed_codes,
            selected_codes,
        )
        selected_modules.append(module_entry)
        summary["selectedModuleCount"] += 1

        if module_summary["offeredInSemester"]:
            summary["offeredModuleCodes"].append(code)
        else:
            summary["notOfferedModuleCodes"].append(code)

        if module_summary["moduleCredit"] is not None:
            summary["totalModuleCredits"] += module_summary["moduleCredit"]

        if module_summary["workloadTotal"] is not None:
            summary["totalWorkloadHours"] += module_summary["workloadTotal"]
        if module_summary["unparsedWorkload"]:
            summary["hasUnparsedWorkload"] = True
            summary["unparsedWorkloadModules"].append(code)
        if module_summary["unresolvedPrerequisite"]:
            summary["unresolvedPrerequisiteModules"].append(code)

        aggregated_preclusion_warnings.extend(module_summary["preclusionWarnings"])

    exam_conflicts: list[dict[str, Any]] = []
    fixed_timetable_conflicts: list[dict[str, Any]] = []

    for left_index, left_module in enumerate(selected_modules):
        left_exam = _build_exam_window(left_module)
        left_fixed = left_module.get("fixedLessons", [])

        for right_module in selected_modules[left_index + 1 :]:
            right_exam = _build_exam_window(right_module)
            if left_exam and right_exam:
                start = max(left_exam[0], right_exam[0])
                end = min(left_exam[1], right_exam[1])
                if start < end:
                    exam_conflicts.append(
                        {
                            "modules": [
                                left_module["moduleCode"],
                                right_module["moduleCode"],
                            ],
                            "overlapStart": start.isoformat(),
                            "overlapEnd": end.isoformat(),
                            "left": {
                                "moduleCode": left_module["moduleCode"],
                                "examDate": left_module.get("examDate"),
                                "examDuration": left_module.get("examDuration"),
                            },
                            "right": {
                                "moduleCode": right_module["moduleCode"],
                                "examDate": right_module.get("examDate"),
                                "examDuration": right_module.get("examDuration"),
                            },
                        }
                    )

            for left_lesson in left_fixed:
                for right_lesson in right_module.get("fixedLessons", []):
                    if not _lessons_overlap(left_lesson, right_lesson):
                        continue
                    fixed_timetable_conflicts.append(
                        {
                            "modules": [
                                left_module["moduleCode"],
                                right_module["moduleCode"],
                            ],
                            "left": left_lesson,
                            "right": right_lesson,
                        }
                    )

    summary["offeredModuleCodes"] = _unique_in_order(summary["offeredModuleCodes"])
    summary["notOfferedModuleCodes"] = _unique_in_order(summary["notOfferedModuleCodes"])
    summary["unparsedWorkloadModules"] = _unique_in_order(summary["unparsedWorkloadModules"])
    summary["unresolvedPrerequisiteModules"] = _unique_in_order(
        summary["unresolvedPrerequisiteModules"]
    )

    return {
        "acadYear": acad_year,
        "semester": semester,
        "requestedModuleCodes": requested_codes,
        "completedModules": sorted(completed_codes),
        "selectedModules": selected_modules,
        "errors": errors,
        "summary": summary,
        "preclusionWarnings": aggregated_preclusion_warnings,
        "examConflicts": exam_conflicts,
        "fixedTimetableConflicts": fixed_timetable_conflicts,
    }


@mcp.tool(
    description="List venue names for a semester. Optionally filter by a query substring.",
    annotations=ToolAnnotations(
        title="List Venues",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def list_venues(
    semester: int = Field(description="Semester number, e.g. 1 or 2"),
    acad_year: str = Field(default=DEFAULT_AY, description="Academic year"),
    query: str | None = Field(
        default=None, description="Case-insensitive venue search string"
    ),
    limit: int = Field(default=50, description="Max venues to return"),
) -> dict[str, Any]:
    """Return venue names for a semester."""
    venues = await _get_venue_list(acad_year, semester)
    if not venues:
        return {"error": f"Could not fetch venues for AY {acad_year} semester {semester}."}

    if query:
        needle = query.strip().casefold()
        venues = [venue for venue in venues if needle in venue.casefold()]

    return {
        "acadYear": acad_year,
        "semester": semester,
        "query": query,
        "venues": venues[: max(limit, 0)],
    }


@mcp.tool(
    description="Get the day-by-day schedule for a venue in a semester.",
    annotations=ToolAnnotations(
        title="Get Venue Schedule",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def get_venue_schedule(
    venue: str = Field(description="Venue name, e.g. 'UT-AUD2'"),
    semester: int = Field(description="Semester number, e.g. 1 or 2"),
    acad_year: str = Field(default=DEFAULT_AY, description="Academic year"),
) -> dict[str, Any]:
    """Return the schedule for a specific venue."""
    venue_map = await _get_venue_information(acad_year, semester)
    if not venue_map:
        return {
            "error": f"Could not fetch venue information for AY {acad_year} semester {semester}."
        }

    requested = venue.strip()
    canonical = next(
        (candidate for candidate in venue_map if candidate.casefold() == requested.casefold()),
        None,
    )
    if canonical is None:
        return {
            "venue": requested,
            "error": f"Venue {requested} not found for AY {acad_year} semester {semester}.",
        }

    return {
        "acadYear": acad_year,
        "semester": semester,
        "venue": canonical,
        "schedule": venue_map.get(canonical, []),
    }


# Resources


@mcp.resource(
    "nusmods://server/info",
    description="Server capabilities, current academic year, and NUSMods API reference.",
)
async def server_info() -> str:
    """Provide server metadata as context for the LLM."""
    return json.dumps(
        {
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
        "tools": [
            "get_module",
            "search_modules",
            "filter_modules",
            "get_modules",
            "evaluate_module_plan",
            "list_venues",
            "get_venue_schedule",
        ],
        "catalogSourceNotes": {
            "moduleList": "Used for fast search and basic filtering.",
            "moduleInformation": "Used for detailed catalog filtering and enrichment.",
            "moduleInfo": "Intentionally not used because live API discovery showed it diverges from the student-facing catalog.",
        },
        }
    )


# Prompts


@mcp.prompt(
    description=(
        "Generate a structured semester planning prompt. Guides Claude to "
        "shortlist modules with filter_modules, then evaluate the final "
        "shortlist with evaluate_module_plan."
    ),
)
def plan_semester(
    modules: str = Field(
        description="Shortlisted module codes, e.g. 'CS2040S,CS2030S,MA2001'"
    ),
    completed_modules: str = Field(
        default="",
        description="Completed module codes, e.g. 'CS1010S,CS1231S'",
    ),
    semester: int = Field(default=1, description="Semester number (1 or 2)"),
    acad_year: str = Field(default=DEFAULT_AY, description="Academic year"),
) -> str:
    """Create a semester planning prompt."""
    shortlisted_codes = _normalize_module_codes(modules)
    completed_codes = _normalize_module_codes(completed_modules)
    shortlist = ", ".join(shortlisted_codes) if shortlisted_codes else "(none provided)"
    completed = ", ".join(completed_codes) if completed_codes else "(none provided)"

    return (
        f"I'm planning NUS semester {semester} for AY {acad_year}.\n\n"
        f"Shortlisted modules: {shortlist}\n"
        f"Completed modules: {completed}\n\n"
        "Please do this workflow:\n"
        "1. If the shortlist needs refinement, use filter_modules to find better candidates.\n"
        "2. Use evaluate_module_plan on the final shortlist and completed modules.\n"
        "3. Explain which modules are offered this semester, any missing prerequisites, "
        "preclusion warnings, fixed timetable clashes, exam clashes, and the total MCs/workload.\n"
        "4. Suggest whether the semester looks manageable and call out any unresolved "
        "prerequisite text or unparsed workload values."
    )


def main() -> None:
    """Entrypoint to run the NUSMods MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
