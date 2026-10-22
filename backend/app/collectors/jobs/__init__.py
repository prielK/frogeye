"""Public job boards: ATS adapters (Greenhouse, Lever, Ashby), posting sync and taxonomy.

Each board is one `job_board` source with `config = {"ats": ..., "token": ...}`. The daily
ingest fetches it once through PoliteClient (an API, so robots.txt does not apply) and
`store.sync_postings` opens, updates and closes postings across those daily snapshots.
`app.cli.discover_job_boards` finds the boards; it never runs inside the daily graph.
"""
