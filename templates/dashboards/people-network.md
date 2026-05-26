---
title: People Network
tags: [dashboard, people, personal]
---

# People Network

## All people (grouped by relationship)

```dataview
TABLE name, relationship
FROM "wiki/personal/people"
WHERE file.name != "index" AND !contains(file.outlinks, [[bot]]) AND !contains(string(sources), "synthesized")
GROUP BY relationship
SORT relationship ASC
```

## Unspecified relationships (need clarification)

```dataview
LIST
FROM "wiki/personal/people"
WHERE file.name != "index" AND (!relationship OR relationship = "")
```

## Synthesized pages (hallucination residue — review and either ground or delete)

```dataview
LIST
FROM "wiki/personal/people"
WHERE contains(string(sources), "synthesized")
```

## Recent additions

```dataview
TABLE relationship, file.ctime AS added
FROM "wiki/personal/people"
WHERE file.name != "index"
SORT file.ctime DESC
LIMIT 10
```

---

For the visual graph, use Obsidian's core Graph view with the "people" filter preset.
