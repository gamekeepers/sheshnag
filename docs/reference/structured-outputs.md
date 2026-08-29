# Structured Outputs (JSON Mode) on Ollama Runtime

*Last updated 2026-08-04. Moved into its current location on 2026-08-26 and **not** re-verified against code since.*

The Sheshnag platform supports schema-constrained JSON outputs (structured outputs) on the Ollama runtime (Ollama version >= 0.5.0). This capability enforces that the model responses conform strictly to JSON structures, eliminating prose wrapping or formatting errors.

---

## 1. Supported Modes

There are two primary modes supported by the platform, following the OpenAI-compatible standard:

### A. Loose Mode (`json_object`)
In loose mode, the model is instructed to return a valid JSON object. The daemon automatically validates that the response is parseable JSON before returning it.

* **OpenAI Payload Parameter**: `response_format: {"type": "json_object"}`
* **Behavior**: Fails if the model returns prose or non-parseable JSON.

### B. Strict Mode (`json_schema`)
In strict mode, you supply a target JSON Schema. The daemon translates this and ensures the Ollama engine strictly adheres to it, and also validates the returned content against your schema using server-side post-inference checks.

* **OpenAI Payload Parameter**: `response_format: {"type": "json_schema", "json_schema": {"schema": <target_schema>}}`
* **Behavior**: Fails if the response is not valid JSON or if it violates the JSON Schema.

---

## 2. Configuration & Examples

### Example: Loose Mode Request
```json
{
  "custom_id": "loose-mode-job",
  "method": "POST",
  "url": "/v1/chat/completions",
  "body": {
    "model": "llama3:8b",
    "messages": [
      {
        "role": "system",
        "content": "You are a helpful assistant. You must output in JSON."
      },
      {
        "role": "user",
        "content": "List the capital, population, and country of France as a JSON object."
      }
    ],
    "response_format": {
      "type": "json_object"
    }
  }
}
```

### Example: Strict Mode Request
```json
{
  "custom_id": "strict-mode-job",
  "method": "POST",
  "url": "/v1/chat/completions",
  "body": {
    "model": "llama3:8b",
    "messages": [
      {
        "role": "user",
        "content": "Extract country data for France."
      }
    ],
    "response_format": {
      "type": "json_schema",
      "json_schema": {
        "schema": {
          "type": "object",
          "properties": {
            "country": { "type": "string" },
            "capital": { "type": "string" },
            "population": { "type": "integer" }
          },
          "required": ["country", "capital", "population"]
        }
      }
    }
  }
}
```

---

## 3. Error Handling

If a worker is unable to honor the JSON request constraint, the job row is failed and the specific error code/message is reported in `CompletionResult.error`:

* **`EMPTY_RESPONSE`**: The Ollama engine returned no choices or an empty response body.
* **`JSON_PARSE_ERROR`**: The response was not valid parseable JSON.
* **`SCHEMA_VIOLATION`**: The response was valid JSON but violated the defined JSON Schema.
* **`VERSION_MISMATCH`**: The assigned worker's Ollama engine is version < 0.5.0, which does not support schema-constrained formatting.
