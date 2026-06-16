# Portfolio Case Study

## Project

Karpathy's Basement is a Discord multi-agent simulator where educational AI-research personas debate with a student in real time.

## Problem

Most chatbot projects feel like one assistant answering one prompt. That is useful, but it does not feel like a room.

I wanted to explore a more social learning interface: a Discord server where different research viewpoints can push against each other, disagree, and help the user learn through argument.

## What I Built

- A Discord bot runtime that can run multiple agent accounts.
- A profile system loaded from `agents.md`.
- Gemini-based routing that chooses who should answer first.
- A hidden director layer that plans follow-up turns before the visible agent writes.
- Persistent SQLite memory for room history, tone preferences, summaries, and user notes.
- Tone controls for professional, normal, and stricter debate modes.
- Typing delays, contextual pings, and student identity awareness to make the room feel less mechanical.
- Secret-scanning guardrails before commits.

## The Key Design Decision

The biggest improvement came from splitting "who should speak?" from "what should they say?"

Early versions let Gemini choose speakers and write messages in one pass. That made the conversation drift and made agents seem unaware of each other.

The better version adds a hidden director:

1. Decide if the room should continue.
2. Pick the next speaker.
3. Define the intent, topic lock, and derailments to avoid.
4. Ask the selected agent to write one short Discord message inside that plan.

That made the system feel more like a group chat and less like a panel of independent bots.

## What I Learned

- Multi-agent UX is mostly orchestration, not just more agents.
- Persistent memory helps, but only if prompts use it naturally.
- Small product details matter: typing delay, direct pings, tone, and message length change the feel dramatically.
- Safety boundaries need to be explicit when simulations are based on public figures.
- Local secret hygiene matters even for small portfolio projects.

## Tech Stack

- Python
- `discord.py`
- Google GenAI SDK / Gemini
- SQLite
- GitHub CLI

## Future Work

- Web dashboard for editing agent profiles.
- Better long-term memory controls.
- More explicit debate modes.
- Transcript export for study notes.
- Optional web UI for people who do not want to set up Discord bots.
