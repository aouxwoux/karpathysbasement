# Security Notes

This project uses local Discord bot tokens and a Gemini API key.

Before committing or sharing the project, run:

```powershell
py scripts\secret_scan.py
```

The scanner checks the working tree and reachable Git history. It prints only file names, line numbers, and pattern names; it should not print secret values.

Local-only files that must not be committed:

- `bot_tokens.json`
- `gemini_token.txt`
- `token.txt`
- `.env`
- `.bot.lock`
- `agent_memory.sqlite3*`
- `pfps/`

Use `bot_tokens.example.json` as the safe template for bot-token config.

If a Discord token or Gemini key is pasted into chat, committed, pushed, or exposed in a screenshot, rotate it before treating the project as safe.
