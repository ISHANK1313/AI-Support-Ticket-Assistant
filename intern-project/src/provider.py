"""Killable provider boundary: SDK socket timeouts do not bound total wall time."""
import json
import subprocess
import sys
from pathlib import Path

PROVIDER_TIMEOUT_SECONDS = 35


def run_provider(operation, settings, contents, timeout=PROVIDER_TIMEOUT_SECONDS):
    payload = {"operation": operation, "api_key": settings.gemini_api_key,
               "model": settings.gemini_model if operation == "generate" else settings.gemini_embedding_model,
               "contents": contents}
    process = subprocess.Popen(
        [sys.executable, "-m", "src.provider"],
        cwd=str(Path(__file__).resolve().parent.parent),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8",
    )
    try:
        output, _ = process.communicate(json.dumps(payload), timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise TimeoutError("Provider deadline exceeded") from None
    if process.returncode:
        raise RuntimeError("Provider unavailable")
    try:
        return json.loads(output)["result"]
    except (ValueError, KeyError, TypeError):
        raise RuntimeError("Provider returned invalid data") from None


def main():
    from google import genai
    from google.genai import types
    from .schemas import DecisionOutput

    try:
        payload = json.load(sys.stdin)
        import httpx
        # This development network has working IPv4 but unreachable IPv6.
        options = types.HttpOptions(
            timeout=30_000, retry_options=types.HttpRetryOptions(attempts=1),
            client_args={"transport": httpx.HTTPTransport(local_address="0.0.0.0")},
        )
        with genai.Client(api_key=payload["api_key"], http_options=options) as client:
            operation, contents = payload["operation"], payload["contents"]
            if operation == "generate":
                response = client.models.generate_content(
                    model=payload["model"], contents=contents,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_json_schema=DecisionOutput.model_json_schema(),
                        max_output_tokens=2048,
                        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                    ),
                )
                result = response.text or ""
            else:
                query = operation == "embed_query"
                config = {}
                if payload["model"] == "gemini-embedding-2":
                    prefix = "task: search result | query: " if query else "title: none | text: "
                    contents = [prefix + text for text in contents]
                else:
                    config["task_type"] = "RETRIEVAL_QUERY" if query else "RETRIEVAL_DOCUMENT"
                response = client.models.embed_content(
                    model=payload["model"], contents=contents,
                    config=types.EmbedContentConfig(**config),
                )
                result = [entry.values for entry in response.embeddings or []]
        print(json.dumps({"result": result}))
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
