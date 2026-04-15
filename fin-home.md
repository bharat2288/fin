---
type: project-home
project: fin
date: 2026-03-07
cssclasses:
  - project-home
---
# fin
*[[dev-hub|Hub]] · [[README|GitHub]]*
<span class="hub-status">Dashboard expense-only contract is now tight, 2026 transfer cleanup is manual-locked, and transfer-rail hardening is in. Next: import credits/payments preview behavior + Resolve Modal flow_type UI.</span>

Personal finance tracker. Statement parsing, expense categorization, subscription tracking, monthly spending breakdown. Claude is the interface.

## Specs

```base
filters:
  and:
    - file.folder.contains("specs/fin")
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
