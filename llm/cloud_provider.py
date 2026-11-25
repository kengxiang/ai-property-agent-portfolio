import os

from pathlib import Path

from dotenv import load_dotenv
from google import genai


class GeminiProvider:

    def __init__(self, client=None, model=None):

        env_path = Path(__file__).resolve().parents[1] / ".env"
        load_dotenv(env_path)

        self.model = model or os.getenv("GEMINI_MODEL")

        if not self.model:
            raise ValueError("GEMINI_MODEL is required")

        if client is not None:
            self.client = client
        else:
            api_key = os.getenv("GEMINI_API_KEY")

            if not api_key:
                raise ValueError("GEMINI_API_KEY is required")

            self.client = genai.Client(api_key=api_key)

    def chat(self, message: str) -> str:

        if not isinstance(message, str) or not message.strip():
            raise ValueError("Message cannot be empty")

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=message
            )

            answer = response.text

        except Exception as error:
            raise RuntimeError(
                f"Gemini request failed: {type(error).__name__}"
            ) from error

        if not isinstance(answer, str) or not answer.strip():
            raise RuntimeError("Gemini returned an empty answer")

        return answer