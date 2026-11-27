# Configuration for the local procedure bench

The [QA Compose file](../compose.yaml) supplies all runtime settings. No `.env` copy or real credentials are needed.

| Input | Source or purpose |
|---|---|
| `HERMOD_CONTENT_ROOTS` | The product image includes the project and wizard submodules. Initialize them in a fresh clone before building. |
| Host, public URL and local bind flag | The container listens on its interface and Docker publishes **only** `127.0.0.1:8210`. No issuer is configured. |
| `HERMOD_DB_URL` | A fixed container-loopback QA DSN satisfies startup validation. No database runs there. Store operations are outside this bench. |

Do not substitute a deployed DSN. Hosted settings are documented separately in the [server inventory](../../.env.example).
