from gemini_client import GEMINI_MODEL, GeminiOrchestratorError, get_gemini_client


def main():
    try:
        client = get_gemini_client()
    except GeminiOrchestratorError as error:
        raise SystemExit(f"Gemini setup error: {error}") from error

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents="Say hello in one sentence.",
        )
    except Exception as error:
        raise SystemExit(f"Gemini request failed: {type(error).__name__}: {error}") from error

    text = response.text.strip()
    if not text:
        raise SystemExit("Gemini returned an empty response.")

    print(text)


if __name__ == "__main__":
    main()
