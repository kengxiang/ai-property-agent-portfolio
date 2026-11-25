import json
import os
import urllib.error
import urllib.request


class OllamaProvider:

    def __init__(self):
        self.base_url = (
            os.getenv("OLLAMA_BASE_URL")
            or "http://127.0.0.1:11434"
        ).rstrip("/")

        self.model = os.getenv(
            "OLLAMA_MODEL", "qwen3.5:4b"
        )

        self.timeout = float(
            os.getenv("MODEL_TIMEOUT_S", "120")
        )

    def chat(self, message: str) -> str:

        if not isinstance(message, str) or not message.strip():
            raise ValueError("Message cannot be empty")

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": message
                }
            ],
            "stream": False,
            "think": False
        }

        request = urllib.request.Request(
            url=f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout
            ) as response:
                result = json.load(response)

        except (urllib.error.URLError, TimeoutError) as error:
            raise RuntimeError(
                f"Cannot connect to Ollama: {error}"
            ) from error

        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise RuntimeError(
                "Ollama returned an invalid JSON response"
            ) from error

        try:
            answer = result["message"]["content"]
        except (KeyError, TypeError) as error:
            raise RuntimeError(
                "Ollama response is missing message content"
            ) from error

        if not isinstance(answer, str) or not answer.strip():
            raise RuntimeError("Ollama returned an empty answer")

        return answer
