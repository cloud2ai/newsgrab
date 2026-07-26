"""Collector backends package.

Importing this package (which happens transitively whenever anything does
`from app.collectors.base import ...` or `from app.collectors import ...`)
must trigger every backend module's self-registration side effect
(`COLLECTORS["name"] = collect`). Each backend module is imported here for
that reason -- without this, `app/main.py` only ever sees the registry as
it looked right after `collectors/base.py` finished loading (i.e. only the
placeholder "echo" backend), since nothing else in the app's own import
graph reaches `collectors/google_news.py` otherwise.
"""
from app.collectors import google_news  # noqa: F401
