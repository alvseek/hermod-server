# Hermod loop mechanisms · 2026-11-27

The [Compose file](../compose.yaml) owns reset and start. The [smoke](smoke.py) exercises the published service; the [transport tests](../../tests/test_end_to_end.py) instead manage their own server and database fixture.
