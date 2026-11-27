# Hermod QA instrument map

Rescanned 2026-11-27 after exercising the local content bench. PostgreSQL-backed CI is separate.

| Phase | Verified mechanism | Grade |
|---|---|---|
| Reset | [QA Compose](compose.yaml) removed only the owned instance. | Documented |
| Inject | [Image build](../Dockerfile) loaded both submodules. | Documented |
| Act | [QA Compose](compose.yaml) recreated a healthy server. | Documented |
| Observe | [MCP smoke](scripts/smoke.py) failed with the server down and passed with it up. | Documented |

The [daily index](README.md#the-riao-loop) links the mechanisms above.

| Category | Map | Finding |
|---|---|---|
| Runbooks | [Run instructions](runbooks/qa-runbooks-map.md) | Bench runbook |
| Playbook | [Cross-service operation](qa-playbook-map.md) | Missing |
| Checklists | [Feature checks](checklists/qa-checklists-map.md) | Missing |
| Fixtures | [Existing fixtures](fixtures/qa-fixtures-map.md) | Embedded in tests |
| Config | [Runtime configuration](config/qa-config-map.md) | Documented |
| Scripts | [Loop mechanisms](scripts/qa-scripts-map.md) | Content loop documented |
| Seeds | [Content and test data](seeds/qa-seeds-map.md) | Available |
