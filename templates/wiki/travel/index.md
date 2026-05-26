---
title: Travel
partition: travel
type: index
sources: [synthesized]
created: 2026-05-25
updated: 2026-05-25
tags: [travel, index]
---

# Travel

Trips, itineraries, places-visited timeline. Connects to [[../places/|places/]] (individual venues) and [[../calendar/|calendar/]] (date-tied events).

## Layout

`trips/<trip-slug>.md` — one per trip.

## Schema

```yaml
type: trip
partition: travel
destination: ""
start_date: YYYY-MM-DD
end_date: YYYY-MM-DD
places: []  # links into wiki/places/
itinerary: ""
notes: ""
```

Body: free-form trip notes — highlights, photos folder reference, anything memorable.

See [[../../dashboards/travel|Travel dashboard]] for upcoming + past-trips timeline.
