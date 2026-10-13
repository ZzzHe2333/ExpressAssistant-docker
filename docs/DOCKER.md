# Docker / Hermes headless service

This is a **new optional Linux service** alongside the original Android APK. It
does not run the Android UI or Shizuku in Docker. It ports the Taobao H5
order-list/SSR details and Cainiao H5 \`queryalltrace\` code. Xiaomi, JD and PDD
channels are **not yet ported**; the existing Android application is preserved.

## Quick start (Ubuntu / Docker Compose v2)

\`\`\`bash
git clone https://github.com/ZzzHe2333/ExpressAssistant-docker.git
cd ExpressAssistant-docker
cp .env.example .env
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
# Put that random value into API_KEY= in .env (do not commit .env).
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:8766/health
\`\`\`

Credentials are intentionally **not** baked into the image. Log into your own
Taobao H5 account via the browser and obtain your own session cookie.
Only do this for accounts you own and are authorized to access.
Store it once via authenticated local API, e.g. use a temporary JSON file
created locally (protect it with \`chmod 600\`):

\`\`\`bash
# /tmp/taobao-auth.json: {"cookie":"<your Taobao H5 Cookie header>"}
# DO NOT paste credentials into public chat, GitHub Issues, or commit history.
set -a; . ./.env; set +a
curl -fsS -X POST http://127.0.0.1:8766/api/credentials/taobao \
  -H "Authorization: Bearer $API_KEY" -H 'Content-Type: application/json' \
  --data-binary @/tmp/taobao-auth.json
rm -f /tmp/taobao-auth.json
\`\`\`

The credential is saved as a mode 0600 file in the Docker persistent volume.
The same volume holds \`packages.db\`. **Keep volume backups secure.**

### API

All routes except \`GET /health\` need \`Authorization: Bearer <API_KEY>\`.

| Method | Route | Function |
|---|---|---|
| GET | \`/health\` | Liveness + whether a cookie is configured |
| POST | \`/api/credentials/taobao\` | Save authorized H5 Cookie: \`{"cookie":"..."}\` |
| POST | \`/api/sync\` | Sync Taobao orders → SSR logistics |
| GET | \`/api/packages\` | Return saved packages |
| GET | \`/api/packages/<mailNo>\` | Return cached package + traces |
| POST | \`/api/query\` | Cainiao \`queryalltrace\` for \`{"mailNo":"..."}\` |
| POST | \`/mcp\` | MCP-style JSON-RPC \`tools/list\`, \`tools/call\` |

Example:

\`\`\`bash
curl -fsS -X POST http://127.0.0.1:8766/api/query \
  -H "Authorization: Bearer $API_KEY" -H 'Content-Type: application/json' \
  -d '{"mailNo":"YOUR_WAYBILL_NUMBER"}'
curl -fsS -H "Authorization: Bearer $API_KEY" \
  http://127.0.0.1:8766/api/packages
\`\`\`

MCP-style tool list: \`list_packages\`, \`package_detail\`, \`query_waybill\`,
\`sync_packages\`. This is a minimal JSON-RPC HTTP endpoint, **not** a full
streamable HTTP MCP transport. Hermes can use the REST API directly or a small
bridge adapter.

\`\`\`bash
curl -fsS -X POST http://127.0.0.1:8766/mcp \
  -H "Authorization: Bearer $API_KEY" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"sync_packages","arguments":{}}}'
\`\`\`

## Security and limitations

- Compose binds to **127.0.0.1:8766 on the host**, not public internet.
  For Hermes in a separate container, attach both containers to the same
  private Docker network and use the service name \`expressassistant:8766\`;
  do not expose credentials over cleartext public HTTP.
- The H5 endpoints are private/undocumented and may fail due to expired
  cookies, CAPTCHA, account risk controls or server-side API changes.
  The service does not bypass any login, CAPTCHA, or security checks.
- Taobao order sync only covers supported Taobao transactions, not all parcels
  associated with a phone number or the complete Cainiao app package list.
- \`/api/query\` can attempt a known waybill but might need buyer permissions.
- The session may expire. Refresh credentials with the same authorized API.
- \`SYNC_INTERVAL_SECONDS=0\` disables polling; default 900 seconds.
  Automatic polling starts on the next interval after container startup.
- This fork keeps the original Android Gradle build untouched.

## Validate

\`\`\`bash
python3 -m unittest discover -s server/tests -v
docker compose config
docker compose build
\`\`\`
