# OpenAI-compatible model connections

Lambda WebUI can use a private or hosted model service that implements the
OpenAI v1 Chat Completions API. Configure it under **Admin Panel → Settings →
Connections → OpenAI API**.

## Add a connection

1. Enable **OpenAI API** and select **Add Connection**.
2. Leave **Connection Type** set to **External** for a service outside the
   Lambda WebUI host.
3. Enter the API base URL, normally ending in `/v1`. Do not include
   `/chat/completions`; Lambda WebUI appends that path.
4. Select **Bearer** authentication and enter the authorization token.
5. Keep **API Type** set to **Chat Completions**.
6. Use **Verify Connection** when the provider implements `GET /v1/models`.
7. Save the connection.

The Lambda WebUI backend must be able to reach the URL. For a private client
deployment, also verify its VPC, VPN, firewall, DNS, TLS trust, and routing
configuration from the backend host—not only from an administrator's browser.

## Automatic model discovery

When **Model IDs** is empty, Lambda WebUI discovers the connection's models
from:

```text
GET <base-url>/models
Authorization: Bearer <token>
```

The response must use the OpenAI model-list shape, including a `data` array
whose entries contain `id` values.

## Add models manually

Manual model IDs are useful when the provider does not expose `/v1/models`,
returns models that should not all be available, or requires explicit model
selection.

1. Open the connection using its gear icon.
2. Expand **Advanced**.
3. Find **Model IDs**.
4. Enter the exact model identifier expected by the upstream API.
5. Select the small **+** button beside the field. The identifier must appear
   in the list above the field before saving.
6. Repeat for each permitted model, then save the connection.

When one or more IDs are present, Lambda WebUI uses that explicit list instead
of `/v1/models`. The configured value is sent unchanged as the `model` field
in requests to `<base-url>/chat/completions`.

The connection verification button still checks `/models`; therefore, it can
report a verification failure for an otherwise usable endpoint that implements
Chat Completions but not model discovery. In that case, confirm the base URL,
token, and model ID independently, save the manual IDs, and test a chat.

## Optional settings

- **Prefix ID** prevents collisions when different providers expose identical
  model IDs. The prefix is used in Lambda WebUI's model selector and removed
  before the upstream request is routed.
- **Tags** organize models in the selector.
- **Headers** adds provider-specific HTTP headers as a JSON object.
- **Passthrough params** allows named top-level request parameters required by
  a compatible provider.

For installations that intentionally expose provider-discovered models to all
approved users, see the `BYPASS_MODEL_ACCESS_CONTROL` guidance in the main
README. Keep normal access controls when users must be isolated from one
another's providers.
