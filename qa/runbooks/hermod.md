# Hermod procedure bench runbook

Use the [daily loop](../README.md#daily-loop) from the server checkout. Reset and inject are the first two rows of the [loop table](../README.md#the-riao-loop); neither touches another service.

- **Health fails:** inspect `docker compose -p hermod-qa -f qa/compose.yaml ps` and `logs hermod`. Check whether another process owns port 8210.
- **One procedure fails:** check the corresponding submodule is initialized and the image was rebuilt after source changes. Inspect the returned procedure before changing an expected expansion.
- **Database tests:** these need a separate disposable PostgreSQL setup. Never give the test schema fixture a database used by another bench.
