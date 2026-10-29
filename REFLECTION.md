# Reflection

### What assumptions did you make?

The biggest one is that the portal's timestamps are Indian Standard Time. They carry no
zone, but the utility is in Jaipur and days run from 00:00 to 23:30. I kept the `+05:30`
offset in responses instead of converting to UTC, so "a day" stays the utility's day. I
assumed `kwh`/`kvah` are cumulative registers (they only ever increase), so usage is a
difference between readings. I treated the transformer list as the master record wherever
a meter's copy of the hierarchy disagrees with it. I assumed meter and network data change
slowly enough for a 15-minute cache, and that one shared service account is acceptable for
every consumer. Where the data contradicts itself (codes reused under different parents,
decommissioned meters still consuming), I didn't assume an answer. I reported it.

### Which part was the most difficult, and how did you get unstuck?

Modelling the network hierarchy. The first profile showed blanks and one stale name, which
are easy to repair. Then it showed `SD-01` under three divisions and `F-001` under two
substations, so there was no tree if codes are ids. What got me unstuck was asking which
level is reliable rather than which tree is right. Grouping by transformer showed that
every DT has exactly one path once blanks are ignored, and the transformer list agrees on
every DT's feeder. That gave me an anchor for the repairs, and it made path-qualified node
ids the honest choice: it is a strict tree, nothing is silently merged, and code filters
still give the "everything on F-001" view. Reading the SvelteKit bundles instead of the
rendered HTML was the other unlock. The meter list page renders "0 total" on the server,
and the bundles named every JSON endpoint, including the hidden signed export.

### If you had another day, what would you improve?

A background sync that pulls every meter's consumption into a small database on a
schedule, staying inside the rate limit. Today consumption is per meter and on demand, so
fleet questions can't be answered: which transformers are overloaded against their kVA
rating, which decommissioned meters are still drawing power, which meters spike. That's the
analysis the data teams actually want, and it's what the web client's overview should
show. After that: a scheduled contract test against the live portal to catch protocol
drift, and metrics on portal latency, 429s and re-logins.

### What mistake did you make while solving this?

Two related ones, both from moving faster than my understanding. My first energy probe
over 25 meters came back with 30 rows for most meters and 1,440 for a few, and I first
read that as the portal degrading under load. It wasn't. Most meters simply report daily
and 40 report half-hourly. Then, to settle it, I scanned all 403 meters with 4 concurrent
requests *before* I had measured the rate limit. 283 of them came back 429. Neither did
any damage, but I should have measured the limit with a handful of requests first, and
checked the timestamps before forming a theory. The client's limiter and its 429 handling
exist because of that mistake. A smaller one: the web client's 7-day chart first used the
portal's default window, which starts at 23:30, so it drew a one-reading stub bar. I found
it by looking at the rendered chart and switched to whole-day windows.

### If you were reviewing your own submission, what would you criticise?

The in-memory catalog is right for 403 meters but would not survive several API workers:
each would hold its own copy and spend the shared portal rate budget on its own export.
A consumption request can also be slow under load, because it queues behind our own
limiter instead of failing fast. The repair logic leans on "the majority of meters on a
transformer are right". That holds here, but it would quietly pick the wrong value on a
transformer with two meters that disagree. The fake portal in the tests encodes my reading
of the protocol, so if I misunderstood something, the tests misunderstand it too. Only
the live checks I ran by hand guard against that. Finally, `build` (`legacy`/`v2`) and the
timezone are inferred, not confirmed, and I'd want someone at the utility to check them.
