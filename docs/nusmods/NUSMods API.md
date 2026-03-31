# NUSMods API Summary

Base URL: `https://api.nusmods.com/v2`

This API is read-only, has no auth, and the docs note that data usually only needs to be refreshed about once per day. CORS is enabled on all endpoints.

## Path parameters

- `acadYear`: academic year in `YYYY-YYYY` format, for example `2025-2026`
- `semester`: semester number, typically `1`, `2`, `3`, or `4`
- `moduleCode`: module code like `CS2040S`

## Endpoints

### 1. `GET /{acadYear}/moduleList.json`

Use this for lightweight search and autocomplete.

- Returns a condensed list of all modules for the academic year
- Typical item shape:
  - `moduleCode`
  - `title`
  - `semesters`
- Best for:
  - keyword search
  - code/title lookup
  - listing what semesters a module is offered in

Example:

```json
{
  "moduleCode": "ABM5001",
  "title": "Leadership in Biomedicine",
  "semesters": [2]
}
```

### 2. `GET /{acadYear}/moduleInfo.json`

Use this when you need full data for every module in one shot.

- Returns detailed information for all modules
- Heavy endpoint compared to `moduleList.json`
- Good for:
  - offline indexing
  - building a local cache
  - batch analytics across all modules

### 3. `GET /{acadYear}/moduleInformation.json`

Deprecated alias for `moduleInfo.json`.

- Same purpose as `moduleInfo.json`
- Prefer `moduleInfo.json` for new work

### 4. `GET /{acadYear}/modules/{moduleCode}.json`

Use this for the detailed view of one module. This is the most important endpoint for an MCP server.

- Returns full module details for one module
- Common fields include:
  - `moduleCode`
  - `title`
  - `department`
  - `faculty`
  - `description`
  - `moduleCredit`
  - `attributes`
  - `prerequisite`
  - `preclusion`
  - `corequisite`
  - `prereqTree`
  - `fulfillRequirements`
  - `semesterData`

`semesterData` typically contains:

- `semester`
- `timetable`
- `examDate`
- `examDuration`
- sometimes `covidZones`

Each timetable lesson typically contains:

- `classNo`
- `lessonType`
- `day`
- `startTime`
- `endTime`
- `venue`
- `size`
- `weeks`
- sometimes `covidZone`

Best for:

- module detail pages
- prerequisite and preclusion checks
- exam clash checks
- timetable analysis
- semester planning prompts

### 5. `GET /{acadYear}/semesters/{semester}/venues.json`

Use this for a flat list of venues used in a semester.

- Returns venue names only
- Example values:
  - `ERC-ALR`
  - `UT-AUD2`
  - `E1-06-05`
- Best for:
  - venue search
  - validating whether a venue exists in a semester

### 6. `GET /{acadYear}/semesters/{semester}/venueInformation.json`

Use this for venue occupancy and venue-centric timetable views.

- Returns an object keyed by venue name
- Each venue maps to a list of day-based entries
- Each day entry contains:
  - `day`
  - `availability`
  - `classes`

Each class in `classes` typically contains:

- `moduleCode`
- `classNo`
- `lessonType`
- `day`
- `startTime`
- `endTime`
- `size`
- `weeks`

Best for:

- “what is happening in this room?” queries
- venue occupancy views
- finding which classes are scheduled in a venue

## Important schema notes from the doc

### `workload`

Usually a 5-tuple describing weekly hours for:

1. lectures
2. tutorials
3. laboratory
4. projects or fieldwork
5. preparatory work

Example: `[2, 1, 1, 3, 3]`

The doc also notes this field can sometimes be an unparsed string, so clients should not assume it is always a numeric array.

### `prereqTree`

This appears on the individual module endpoint when the prerequisites can be represented structurally.

- It is recursive
- It uses `and` / `or` groupings
- Leaves are usually module codes, sometimes with grade suffixes like `CS1010:D`

Example:

```json
{
  "and": [
    "CS1231",
    {
      "or": ["CS1010S", "CS1010X"]
    }
  ]
}
```

This is better than parsing the plain `prerequisite` string when you want machine-readable prerequisite logic.

### `fulfillRequirements`

- Array of module codes unlocked by taking the current module
- Useful for dependency navigation and planner suggestions

### `weeks`

Lesson weeks are not always a simple full-semester list.

The doc notes they may be represented as:

- a start/end date range
- an explicit list of week numbers
- a `weekInterval`

So timetable consumers should not assume “every week”.

## Recommended MCP mapping

For the current MCP server, the most useful endpoints are:

- `moduleList.json` for `search_modules`
- `modules/{moduleCode}.json` for `get_module`

Useful future additions:

- `moduleInfo.json` for local caching and faster search
- `venueInformation.json` for room and timetable conflict tools
- `venues.json` for venue lookup or validation tools

## Practical integration notes

- Prefer `moduleList.json` for search because it is much smaller than full module payloads
- Prefer `modules/{moduleCode}.json` when the user asks about one module in detail
- Prefer `prereqTree` over `prerequisite` when doing logic checks
- Keep `prerequisite`, `preclusion`, and `corequisite` raw strings too, because some school-provided values do not parse cleanly
- Treat `moduleInformation.json` as legacy
