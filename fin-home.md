---
type: project-home
project: fin
date: 2026-03-07
cssclasses:
  - project-home
---
# fin
*[[dev-hub|Hub]] · [[README|GitHub]]*
<span class="hub-status">Full-stack SPA — billing model, anomaly system, dashboard filters complete. Verify FX ratios next.</span>

Personal finance tracker. Statement parsing, expense categorization, subscription tracking, monthly spending breakdown. Claude is the interface.

## Specs

```base
filters:
  and:
    - file.folder.contains("fin/specs")
    - type != "spec-prompts"
properties:
  "0":
    name: file.link
    label: Spec
  "1":
    name: type
    label: Type
  "2":
    name: date
    label: Date
  "3":
    name: created_by
    label: Created By
  "4":
    name: file.mtime
    label: Modified
views:
  - type: table
    name: All Specs
    order:
      - type
      - file.name
      - file.mtime
      - file.backlinks
    sort:
      - property: file.mtime
        direction: DESC
      - property: type
        direction: ASC
```
> [!warning]- Open Errors (`$= dv.pages('"knowledge/exports/errors"').where(p => p.project == "fin" && !p.resolved).length`)
> ```dataview
> TABLE module, date
> FROM "knowledge/exports/errors"
> WHERE project = "fin" AND resolved = false
> SORT date DESC
> LIMIT 5
> ```

> [!info]- Decisions (`$= dv.pages('"knowledge/exports/decisions"').where(p => p.project == "fin").length`)
> ```dataview
> TABLE date
> FROM "knowledge/exports/decisions"
> WHERE project = "fin"
> SORT date DESC
> LIMIT 5
> ```
>
> > [!info]- All Decisions
> > ```dataview
> > TABLE date
> > FROM "knowledge/exports/decisions"
> > WHERE project = "fin"
> > SORT date DESC
> > ```

> [!tip]- Learnings (`$= dv.pages('"knowledge/exports/learnings"').where(p => p.project == "fin").length`)
> ```dataview
> TABLE tags
> FROM "knowledge/exports/learnings"
> WHERE project = "fin"
> SORT date DESC
> LIMIT 5
> ```
>
> > [!tip]- All Learnings
> > ```dataview
> > TABLE tags
> > FROM "knowledge/exports/learnings"
> > WHERE project = "fin"
> > SORT date DESC
> > ```

> [!abstract]- Project Plans (`$= dv.pages('"knowledge/plans"').where(p => p.project == "fin").length`)
> ```dataview
> TABLE title, default(date, file.ctime) as Date
> FROM "knowledge/plans"
> WHERE project = "fin"
> SORT default(date, file.ctime) DESC
> ```

> [!note]- Sessions (`$= dv.pages('"knowledge/sessions/fin"').length`)
> ```dataview
> TABLE topic
> FROM "knowledge/sessions/fin"
> SORT file.mtime DESC
> LIMIT 5
> ```
>
> > [!note]- All Sessions
> > ```dataview
> > TABLE topic
> > FROM "knowledge/sessions/fin"
> > SORT file.mtime DESC
> > ```

