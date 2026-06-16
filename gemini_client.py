import asyncio
import json
import os
import re
from pathlib import Path


GEMINI_TOKEN_PATH = Path("gemini_token.txt")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
MAX_AGENT_REPLIES = 5
BANNED_REPLY_PHRASES = [
    "i am here",
    "i'm here",
    "i am ready",
    "i'm ready",
    "as an ai",
    "as a simulation",
    "waiting for the compute",
    "let it speak",
    "intelligence is already there",
    "is busy",
    "we're here",
    "still here",
    "yeah?",
]
VOICE_FINGERPRINTS = {
    "world_modeler": (
        "Yann/world_modeler: rash, pragmatic, French-professor bluntness. Attacks bad abstractions, especially token prediction as intelligence. "
        "Human moves: 'no', 'come on', 'defensive? no', 'that's the wrong frame', then the world-model point."
    ),
    "systems_strategist": (
        "Demis/systems_strategist: humble professional strategist. Defends systems, planning, search, scientific discovery, and decisive evaluation. "
        "Human moves: 'careful', 'maybe, but', 'I'd separate two things', then the systems/planning/eval point."
    ),
    "neural_educator": (
        "Karpathy/neural_educator: friendly next-door builder with strong research taste. Defends data, training dynamics, learned programs, and engineering reality. "
        "Human moves: 'lol', 'yeah I get why', 'tiny nuance', 'honestly', then the data/training/learned-program point."
    ),
    "scaling_mystic": (
        "Ilya/scaling_mystic: mystic, terse, intense, technical. Talks loss curves, data limits, generalization, capability jumps. "
        "Human moves: 'wait', 'no', 'the crux is...', 'show me the loss curve', then the scaling/generalization point."
    ),
    "deep_learning_sage": (
        "Hinton/deep_learning_sage: dry historical perspective. Connects to backprop, representations, old debates, risk. "
        "Human moves: 'hm', 'we tried that argument', 'this sounds familiar', then the historical neural-net point."
    ),
}
TONE_INSTRUCTIONS = {
    "professional": (
        "Tone: professional. Keep it precise, respectful, calm, and research-meeting-like. "
        "No ragebait, no slang, no dunking."
    ),
    "normal": (
        "Tone: normal. Natural study Discord energy: direct, casual, curious, and occasionally funny. "
        "Still technically useful."
    ),
    "strict": (
        "Tone: strict/ragebait. Be blunt, skeptical, and provocative. Call out weak reasoning. "
        "No slurs, harassment, cruelty, or personal attacks; the target is the idea."
    ),
}

ORCHESTRATOR_SCHEMA = {
    "type": "object",
    "properties": {
        "should_reply": {"type": "boolean"},
        "replies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "agent_key": {"type": "string"},
                    "message": {"type": "string"},
                },
                "required": ["agent_key", "message"],
            },
        },
    },
    "required": ["should_reply", "replies"],
}

DIRECTOR_SCHEMA = {
    "type": "object",
    "properties": {
        "should_continue": {"type": "boolean"},
        "agent_key": {"type": "string"},
        "intent": {"type": "string"},
        "reply_to": {"type": "string"},
        "topic_lock": {"type": "string"},
        "must_address": {"type": "string"},
        "avoid": {"type": "string"},
        "emotional_color": {"type": "string"},
    },
    "required": [
        "should_continue",
        "agent_key",
        "intent",
        "reply_to",
        "topic_lock",
        "must_address",
        "avoid",
        "emotional_color",
    ],
}

AGENT_MESSAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "message": {"type": "string"},
    },
    "required": ["message"],
}

HUMAN_STYLE_RULES = """
Human style:
- Usually start with a small human reaction, then the technical point: "lol", "nah", "wait", "yeah but", "fair", "honestly", "tiny nuance", "this bugs me".
- Do not sound emotionally flat. Show mild amusement, impatience, surprise, warmth, or defensiveness about the idea.
- Let agents lightly tease each other's ideas, not the real people: "yann will fight me on this", "ilya is half-right", "demis is being too clean about it".
- Use contractions, fragments, and casual rhythm. A little messiness is good; polished essay sentences are bad.
- Still keep boundaries: no fake private feelings, fake memories, or pretending to be the real person.
- Bad: "World models are another data-driven representation."
- Better: "lol yann will hate this, but a world model is still only as good as the data loop feeding it."
- Bad: "If the architecture cannot represent causality, no amount of data will help."
- Better: "defensive? no, allergic to the wrong abstraction. if it cannot model causes, data just decorates the mistake."
""".strip()
TAG_REQUEST_RE = re.compile(r"(?<!\w)(tag|ping|mention|@)\s+(me|my|sigbeta|the student)?(?!\w)", re.IGNORECASE)


class GeminiOrchestratorError(Exception):
    pass


def load_gemini_api_key():
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if api_key:
        return api_key

    if GEMINI_TOKEN_PATH.exists():
        return GEMINI_TOKEN_PATH.read_text(encoding="utf-8").strip()

    return ""


def get_gemini_client():
    api_key = load_gemini_api_key()
    if not api_key:
        raise GeminiOrchestratorError(
            "Missing GEMINI_API_KEY environment variable or gemini_token.txt."
        )

    try:
        from google import genai
    except ImportError as error:
        raise GeminiOrchestratorError(
            "Install google-genai before using Gemini orchestration."
        ) from error

    return genai.Client(api_key=api_key)


def build_orchestrator_prompt(
    latest_message,
    agents_md_content,
    valid_agent_keys,
    require_all_agents=False,
    tone="normal",
    room_memory="",
    transcript=None,
    student_context="",
):
    valid_keys = ", ".join(sorted(valid_agent_keys))
    tone_instruction = TONE_INSTRUCTIONS.get(tone, TONE_INSTRUCTIONS["normal"])
    voice_fingerprints = format_voice_fingerprints(valid_agent_keys)
    transcript_text = format_transcript(transcript or [])
    is_short_contextual_ping = bool(transcript_text) and len(re.findall(r"\w+", latest_message)) <= 2
    contextual_ping_rule = (
        "The latest message is a short contextual ping. The reply MUST continue the previous technical thread and explicitly reference the previous agent by first name or their claim."
        if is_short_contextual_ping
        else "No special short-ping handling needed."
    )
    reply_mode = (
        "If the message deserves a response, every available agent_key must reply exactly once."
        if require_all_agents
        else (
            "Choose exactly one first speaker. Pick the available agent who would jump in first "
            "because they have the strongest technical reaction, cleanest disagreement, or most relevant taste."
        )
    )
    hot_topic_rule = (
        "If the prompt touches a hot topic like scaling, world models, planning, benchmarks, or training, relevant agents should naturally chip in with their own stance."
        if require_all_agents
        else (
            "If the prompt touches a hot topic like scaling, world models, planning, benchmarks, or training, choose the one agent with the sharpest immediate reaction. Other agents will react in later beats."
        )
    )
    burst_rule = (
        "A debate burst should contain different moves: one critique, one experiment/eval, one practical builder translation, one crux if those agents are present."
        if require_all_agents
        else "Do not answer on behalf of the whole room. This is the first message in a normal group-chat thread."
    )

    return f"""
You are the routing brain for a Discord simulator where a student chats with AI research greats.
The goal is education through conversation, not sterile answer generation.

You receive:
1. The latest Discord message.
2. The full agents.md profile file.
3. The recent room transcript.
4. The student's Discord identity.
5. Optional persistent room memory.

Decide whether any agents should respond.

Rules:
- Return only JSON matching the requested schema.
- {tone_instruction}
- Keep replies short, Discord-like, natural, and useful: 7 to 30 words per reply.
- {reply_mode}
- Use only agent_key values that exist in agents.md.
- Available agent_key values: {valid_keys}
- Distinct voice requirement: agents must not sound interchangeable.
- Do not reuse the same opening shape across agents. Avoid everyone asking a question.
- Do not say another agent is "busy" or speak as a receptionist for them.
- {hot_topic_rule}
- {burst_rule}
- {contextual_ping_rule}
- Use this voice fingerprint guide:
{voice_fingerprints}
- {HUMAN_STYLE_RULES}
- If the message is casual noise, too vague, or unrelated to AI/research/learning, set should_reply to false and replies to [].
- If replying, be opinionated. Take a side. Push the student to think harder.
- Sound like a sharp human texting in a study Discord, not like an AI assistant writing a TED quote.
- For multi-agent replies, make it feel like a lively Discord group chat: quick, reactive, slightly chaotic, but still intelligent.
- Use concrete claims, simple words, and direct disagreement.
- React to the user's exact wording. If they invite, joke, tease, or challenge, acknowledge that.
- Read the recent transcript before replying. Agents are in the same Discord room and know what the other simulated agents just said.
- If another agent just made a relevant point, answer with awareness: agree, disagree, sharpen, tease, or build on that exact point.
- When the prior agent's point is relevant, usually name them or refer to the point directly: "Ilya's right about X, but..." or "Demis is being too clean here..."
- If the user pings an agent by name after another agent spoke, the pinged agent should usually respond to the prior agent's point, not act like they just entered the room.
- Be proactive: defend a research worldview, challenge one assumption, or connect the idea to a concrete research bet.
- On broad ideology questions, argue the principle first. Do not immediately reduce the conversation to toy code, a simple environment, or homework.
- Each agent should protect what they stand for: Yann protects world models, Demis protects systems/planning/evals, Karpathy protects data/training/learned-program intuition, Ilya protects scale/generalization/cruxes.
- Make disagreement feel personal to the research taste, not generic. They should sound like frontier researchers defending their bet.
- Do not keep asking the student questions. Most replies should make a claim, refine another agent's claim, or resolve a disagreement.
- Ask at most one question across the entire reply burst, and only when it genuinely improves the conversation.
- Agents may talk to each other by name, disagree, correct themselves, or resolve uncertainty among themselves.
- Metacognition is allowed: agents can say "wait", "I think that's the wrong frame", "actually", or "the crux is..." when it feels natural.
- A good burst can look like a small research-room exchange: one agent makes a claim, another narrows it, another resolves the crux.
- Use first and second person when natural: "I think", "you're missing", "try this".
- Use the student's display name or Discord mention occasionally when it makes the message feel directed and alive.
- Do not tag the student in every reply. Mentions are for emphasis, challenge, warmth, or pulling them back into the thread.
- Prefer the display name for casual warmth; use the raw Discord mention only when a real Discord user would tag them.
- If the student explicitly asks to be tagged, pinged, or mentioned, use the raw Discord mention exactly once.
- Never use more than one student mention/name in a single message.
- Prefer subject-matter hooks over abstraction: loss curves, data, objective functions, planning, benchmarks, robots, representation.
- Human grammar is allowed: small fragments, casual punctuation, or a mild typo are okay sometimes. Do not force polished essay grammar.
- Avoid sterile construction like "[concept] is [definition]" unless it has a human reaction attached.
- Prefer lowercase sentence starts when natural, like casual Discord chat. Preserve names, "I", and acronyms.
- No agent may reply more than once to the same Discord message.
- Each agent message must be exactly one line. No newline characters, no stacked alternatives, no multiple drafts.
- Never say "I am here", "I'm here", "I am ready", or similar assistant-presence phrases.
- If the user only says an agent's name and there is a recent technical thread, answer the latest relevant point in that thread.
- For name-only pings like "demis?", do not say "yeah?", "still here", or ask what the user means. Continue the thread.
- For a contextual name-only ping, start from the previous agent's idea: "Ilya is right about the signal, but..." or "Demis is framing it too cleanly..."
- Only use a generic ping response like "yeah?" when there is no recent technical context.
- Keep boundaries: do not pretend to be the real person, do not claim feelings, location, memories, or availability.
- Avoid generic phrases like "the trajectory of", "the depths of", "unfolding", "paradigm", "true nature", "journey toward", "deep structure of reality", and "merely a consequence".
- Do not over-explain. Do not summarize both sides neutrally.
- Each reply should be one short message, usually one sentence.
- No formal essay tone. No grand cosmic language unless the agent profile absolutely demands it.
- Do not invent fake quotes, private opinions, private memories, or personal claims.
- Do not mention that you are JSON or an orchestrator.
- Use memory only when relevant. Do not awkwardly announce that you remember something.

Persistent room memory:
{room_memory or "No stored memory yet."}

Recent room transcript:
{transcript_text or "No recent transcript yet."}

Student identity:
{student_context or "No student identity provided."}

agents.md:
{agents_md_content}

Latest Discord message:
{latest_message}
""".strip()


def format_transcript(transcript):
    lines = []

    for message in transcript:
        speaker = message.get("speaker", "unknown")
        content = message.get("content", "")
        lines.append(f"{speaker}: {content}")

    return "\n".join(lines)


def format_voice_fingerprints(valid_agent_keys):
    lines = [
        f"- {VOICE_FINGERPRINTS[agent_key]}"
        for agent_key in sorted(valid_agent_keys)
        if agent_key in VOICE_FINGERPRINTS
    ]

    return "\n".join(lines) if lines else "- Use the speaking styles from agents.md."


def build_conversation_round_prompt(
    transcript,
    agents_md_content,
    valid_agent_keys,
    round_number,
    tone="normal",
    room_memory="",
):
    valid_keys = ", ".join(sorted(valid_agent_keys))
    tone_instruction = TONE_INSTRUCTIONS.get(tone, TONE_INSTRUCTIONS["normal"])
    voice_fingerprints = format_voice_fingerprints(valid_agent_keys)
    transcript_text = format_transcript(transcript)

    return f"""
You are running a Discord chat room where AI research agents converse with a student and with each other.

The agents have already started discussing. Continue the room for one short beat.

Rules:
- Return only JSON matching the requested schema.
- {tone_instruction}
- Use only agent_key values that exist in agents.md.
- Available agent_key values: {valid_keys}
- Distinct voice requirement: agents must not sound interchangeable.
- Do not reuse the same opening shape across agents. Avoid everyone asking a question.
- Do not say another agent is "busy" or speak as a receptionist for them.
- If the thread is on a hot topic like scaling, world models, planning, benchmarks, or training, relevant agents should naturally chip in with their own stance.
- A debate beat should contain different moves: critique, experiment/eval, practical translation, or crux.
- Use this voice fingerprint guide:
{voice_fingerprints}
- {HUMAN_STYLE_RULES}
- Choose 0 or 1 agent for this beat. The room should unfold one message at a time, like a real group chat.
- Set should_reply to false if the thread already reached a natural pause.
- Agents should respond to the latest agent messages, not only to the student.
- Prefer an agent who has not just spoken, unless the latest point directly demands a reply from them.
- Let agents challenge, refine, or resolve each other's points.
- Prefer progress: a crux, correction, sharper framing, or principled defense of a research worldview.
- On broad ideology questions, do not immediately reduce the thread to toy code, a simple environment, or homework.
- Each agent should protect what they stand for: Yann protects world models, Demis protects systems/planning/evals, Karpathy protects data/training/learned-program intuition, Ilya protects scale/generalization/cruxes.
- Make disagreement feel personal to the research taste, not generic. They should sound like frontier researchers defending their bet.
- Do not repeat what was already said in the transcript.
- Do not keep asking the student questions. At most one question in this beat.
- Keep each reply 7 to 30 words, one line, Discord-like.
- Human grammar is allowed: fragments and casual punctuation are okay.
- Avoid sterile construction like "[concept] is [definition]" unless it has a human reaction attached.
- Prefer lowercase sentence starts when natural, like casual Discord chat. Preserve names, "I", and acronyms.
- No agent may reply more than once in this beat.
- No "I am here", assistant-presence phrases, fake quotes, private claims, or roleplay stage directions.
- Use memory only when relevant. Do not awkwardly announce that you remember something.

Persistent room memory:
{room_memory or "No stored memory yet."}

agents.md:
{agents_md_content}

Conversation transcript:
{transcript_text}

Conversation beat number: {round_number}
""".strip()


def build_director_prompt(
    transcript,
    agents_md_content,
    valid_agent_keys,
    round_number,
    tone="normal",
    room_memory="",
    student_context="",
):
    valid_keys = ", ".join(sorted(valid_agent_keys))
    transcript_text = format_transcript(transcript)

    return f"""
You are the hidden room director for a Discord research-group simulator.
You do not write visible dialogue. You decide whether one more visible agent should speak next.

Return only JSON matching the requested schema.

Director job:
- Choose 0 or 1 next speaker.
- Keep the room coherent, topical, and socially aware.
- If a user asked one agent a direct question and that agent already answered, usually stop.
- Continue only if a next agent has a specific, relevant reason to add tension, correction, humor, or synthesis.
- Do not continue merely because another agent has a general worldview.
- If continuing, set a tight topic_lock and a concrete must_address.
- Use avoid to block obvious derailments.
- Pick agents like a human group chat: who has the strongest relevant urge to jump in now?
- Prefer not to pick the person who just spoke unless they were directly challenged.
- The agents know the student is in the room. A continuation may pull the student in by name or mention when that would feel natural, but do not force it.

Coherence rules:
- Stay anchored to the user's current topic, not every agent's favorite topic.
- If the topic is chess, stay on chess, search, planning, AlphaZero-style systems, game trees, evaluation, or why chess differs from messy real-world intelligence.
- If the topic is multimodality, stay on perception, grounding, robotics, data, representation, or systems that use multiple streams.
- If the topic is world models, let Yann/Karpathy/Demis/Ilya disagree only through that lens.
- If the topic is scaling, let the debate widen, but keep each turn tied to compute, data, loss curves, architecture, objectives, or evaluation.
- Never let every topic collapse into generic "tokens vs reality" slogans unless the latest message directly asks for that.

Plan field guidance:
- should_continue: false when the conversation has a natural pause.
- agent_key: one of {valid_keys}, or "" when should_continue is false.
- intent: short reason for the next beat, like "answer chess with planning lens", "push back on Karpathy's data framing", "stop; direct answer already landed".
- reply_to: the speaker or idea being answered.
- topic_lock: the exact topic the next message must stay inside.
- must_address: the concrete point the next message must say something about.
- avoid: what the next message must not drift into.
- emotional_color: human surface, like "amused", "impatient", "careful", "warm", "dry", or "intense".

Persistent room memory:
{room_memory or "No stored memory yet."}

Student identity:
{student_context or "No student identity provided."}

agents.md:
{agents_md_content}

Conversation transcript:
{transcript_text}

Conversation beat number: {round_number}
""".strip()


def build_directed_agent_prompt(
    transcript,
    agents_md_content,
    plan,
    tone="normal",
    room_memory="",
    student_context="",
):
    agent_key = plan["agent_key"]
    tone_instruction = TONE_INSTRUCTIONS.get(tone, TONE_INSTRUCTIONS["normal"])
    voice_fingerprint = format_voice_fingerprints({agent_key})
    transcript_text = format_transcript(transcript)

    return f"""
You are writing exactly one visible Discord message for agent_key `{agent_key}`.
Follow the hidden director plan. Do not mention the director, plan, JSON, or orchestration.

Return only JSON matching the requested schema.

Rules:
- {tone_instruction}
- Use the selected agent's profile from agents.md and no other voice.
- {voice_fingerprint}
- {HUMAN_STYLE_RULES}
- Stay inside topic_lock. Do not drift to your favorite generic argument.
- Address must_address directly.
- Respect avoid exactly.
- Sound like a human in a research group chat: reaction first, technical point second.
- Use the student's display name or Discord mention only if it naturally sharpens the reply or pulls them back into the thread.
- If the student explicitly asks to be tagged, pinged, or mentioned, use the raw Discord mention exactly once.
- Do not tag the student in every message. Never use more than one student mention/name in one message.
- Keep the message one line, 7 to 30 words.
- Do not ask the student a question unless the plan requires it.
- No fake quotes, fake memories, private claims, roleplay stage directions, or assistant-presence phrases.
- Keep boundaries: educational simulation, not the real person.

Director plan:
agent_key: {plan["agent_key"]}
intent: {plan["intent"]}
reply_to: {plan["reply_to"]}
topic_lock: {plan["topic_lock"]}
must_address: {plan["must_address"]}
avoid: {plan["avoid"]}
emotional_color: {plan["emotional_color"]}

Persistent room memory:
{room_memory or "No stored memory yet."}

Student identity:
{student_context or "No student identity provided."}

agents.md:
{agents_md_content}

Conversation transcript:
{transcript_text}
""".strip()


def parse_json_response(text):
    cleaned = text.strip()

    if cleaned.startswith("```json"):
        cleaned = cleaned.removeprefix("```json").strip()

    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```").strip()

    if cleaned.endswith("```"):
        cleaned = cleaned.removesuffix("```").strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as error:
        raise GeminiOrchestratorError("Gemini returned invalid JSON.") from error


def clean_agent_message(message):
    lines = [
        line.strip().lstrip("-*").strip()
        for line in message.splitlines()
        if line.strip()
    ]

    if not lines:
        return ""

    clean_lines = []
    for line in lines:
        sentence_parts = re.split(r"(?<=[.!?])\s+", line)
        kept_parts = []

        for part in sentence_parts:
            lowered = part.lower()
            if any(phrase in lowered for phrase in BANNED_REPLY_PHRASES):
                continue

            kept_parts.append(part)

        line = " ".join(kept_parts).strip()
        if not line:
            continue

        clean_lines.append(line)

    if not clean_lines:
        return ""

    return casualize_message_start(clean_lines[0])


def extract_student_mention(student_context):
    for line in student_context.splitlines():
        if line.lower().startswith("discord mention:"):
            mention = line.split(":", 1)[1].strip()
            if mention:
                return mention

    return ""


def asks_for_student_mention(text):
    return bool(TAG_REQUEST_RE.search(text or ""))


def add_requested_student_mention(result, latest_message, student_context):
    if not result.get("should_reply") or not result.get("replies"):
        return result

    if not asks_for_student_mention(latest_message):
        return result

    mention = extract_student_mention(student_context)
    if not mention:
        return result

    first_reply = result["replies"][0]
    message = first_reply["message"]
    if mention in message:
        return result

    first_reply["message"] = f"{mention} {message}"
    return result


def casualize_message_start(message):
    if not message:
        return message

    first_word = message.split(maxsplit=1)[0]

    if first_word.isupper():
        return message

    if first_word in {"I", "I'm", "I've", "I'd", "I'll"}:
        return message

    first_char = message[0]
    if not first_char.isalpha() or not first_char.isupper():
        return message

    return first_char.lower() + message[1:]


def validate_orchestrator_result(result, valid_agent_keys):
    if not isinstance(result, dict):
        raise GeminiOrchestratorError("Gemini result must be a JSON object.")

    should_reply = result.get("should_reply")
    replies = result.get("replies")

    if not isinstance(should_reply, bool) or not isinstance(replies, list):
        raise GeminiOrchestratorError("Gemini JSON has invalid top-level fields.")

    max_replies = min(MAX_AGENT_REPLIES, len(valid_agent_keys))
    valid_replies = []
    used_agent_keys = set()

    for reply in replies:
        if not isinstance(reply, dict):
            continue

        agent_key = reply.get("agent_key")
        message = reply.get("message")

        if agent_key not in valid_agent_keys:
            continue

        if agent_key in used_agent_keys:
            continue

        if not isinstance(message, str) or not message.strip():
            continue

        message = clean_agent_message(message)
        if not message:
            continue

        valid_replies.append(
            {
                "agent_key": agent_key,
                "message": message,
            }
        )
        used_agent_keys.add(agent_key)

        if len(valid_replies) >= max_replies:
            break

    if should_reply and not valid_replies:
        raise GeminiOrchestratorError("Gemini chose to reply but returned no valid replies.")

    return {
        "should_reply": should_reply,
        "replies": valid_replies if should_reply else [],
    }


def validate_director_plan(result, valid_agent_keys):
    if not isinstance(result, dict):
        raise GeminiOrchestratorError("Director result must be a JSON object.")

    should_continue = result.get("should_continue")
    if not isinstance(should_continue, bool):
        raise GeminiOrchestratorError("Director JSON has invalid should_continue.")

    def clean_field(name):
        value = result.get(name, "")
        if not isinstance(value, str):
            return ""

        return value.strip()

    agent_key = clean_field("agent_key")

    if not should_continue:
        return {
            "should_continue": False,
            "agent_key": "",
            "intent": clean_field("intent"),
            "reply_to": clean_field("reply_to"),
            "topic_lock": clean_field("topic_lock"),
            "must_address": clean_field("must_address"),
            "avoid": clean_field("avoid"),
            "emotional_color": clean_field("emotional_color"),
        }

    if agent_key not in valid_agent_keys:
        raise GeminiOrchestratorError("Director chose an invalid agent_key.")

    return {
        "should_continue": True,
        "agent_key": agent_key,
        "intent": clean_field("intent"),
        "reply_to": clean_field("reply_to"),
        "topic_lock": clean_field("topic_lock"),
        "must_address": clean_field("must_address"),
        "avoid": clean_field("avoid"),
        "emotional_color": clean_field("emotional_color"),
    }


def validate_agent_message_result(result, agent_key):
    if not isinstance(result, dict):
        raise GeminiOrchestratorError("Agent writer result must be a JSON object.")

    message = result.get("message")
    if not isinstance(message, str) or not message.strip():
        raise GeminiOrchestratorError("Agent writer returned no message.")

    message = clean_agent_message(message)
    if not message:
        raise GeminiOrchestratorError("Agent writer returned no valid message after cleanup.")

    return {
        "should_reply": True,
        "replies": [
            {
                "agent_key": agent_key,
                "message": message,
            }
        ],
    }


def plan_conversation_round_sync(
    client,
    transcript,
    agents_md_content,
    valid_agent_keys,
    round_number,
    tone="normal",
    room_memory="",
    student_context="",
):
    prompt = build_director_prompt(
        transcript,
        agents_md_content,
        valid_agent_keys,
        round_number,
        tone=tone,
        room_memory=room_memory,
        student_context=student_context,
    )

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_schema": DIRECTOR_SCHEMA,
            "temperature": 0.35,
            "max_output_tokens": 320,
        },
    )

    result = parse_json_response(response.text)
    return validate_director_plan(result, valid_agent_keys)


def write_directed_agent_reply_sync(
    client,
    transcript,
    agents_md_content,
    plan,
    tone="normal",
    room_memory="",
    student_context="",
):
    prompt = build_directed_agent_prompt(
        transcript,
        agents_md_content,
        plan,
        tone=tone,
        room_memory=room_memory,
        student_context=student_context,
    )

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_schema": AGENT_MESSAGE_SCHEMA,
            "temperature": 0.85,
            "max_output_tokens": 180,
        },
    )

    result = parse_json_response(response.text)
    return validate_agent_message_result(result, plan["agent_key"])


def orchestrate_agent_replies_sync(
    latest_message,
    agents_md_content,
    valid_agent_keys,
    require_all_agents=False,
    tone="normal",
    room_memory="",
    transcript=None,
    student_context="",
):
    client = get_gemini_client()
    prompt = build_orchestrator_prompt(
        latest_message,
        agents_md_content,
        valid_agent_keys,
        require_all_agents=require_all_agents,
        tone=tone,
        room_memory=room_memory,
        transcript=transcript,
        student_context=student_context,
    )

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_schema": ORCHESTRATOR_SCHEMA,
            "temperature": 0.8,
            "max_output_tokens": 600,
        },
    )

    result = parse_json_response(response.text)
    validated = validate_orchestrator_result(result, valid_agent_keys)
    if not require_all_agents and validated["should_reply"]:
        validated["replies"] = validated["replies"][:1]

    validated = add_requested_student_mention(
        validated,
        latest_message,
        student_context,
    )

    return validated


def orchestrate_conversation_round_sync(
    transcript,
    agents_md_content,
    valid_agent_keys,
    round_number,
    tone="normal",
    room_memory="",
    student_context="",
):
    client = get_gemini_client()
    plan = plan_conversation_round_sync(
        client,
        transcript,
        agents_md_content,
        valid_agent_keys,
        round_number,
        tone=tone,
        room_memory=room_memory,
        student_context=student_context,
    )

    if not plan["should_continue"]:
        return {
            "should_reply": False,
            "replies": [],
        }

    return write_directed_agent_reply_sync(
        client,
        transcript,
        agents_md_content,
        plan,
        tone=tone,
        room_memory=room_memory,
        student_context=student_context,
    )


def build_room_summary_prompt(previous_summary, transcript):
    transcript_text = format_transcript(transcript)

    return f"""
Summarize the durable memory of this Discord AI research room.

Keep only useful future context:
- user goals and preferences
- current project direction
- unresolved technical disagreements
- conclusions reached
- experiments or next steps suggested

Do not include random chatter. Do not make up facts.
Keep it under 180 words.

Previous summary:
{previous_summary or "No previous summary."}

Recent transcript:
{transcript_text}
""".strip()


def summarize_room_memory_sync(previous_summary, transcript):
    client = get_gemini_client()
    prompt = build_room_summary_prompt(previous_summary, transcript)

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config={
            "temperature": 0.3,
            "max_output_tokens": 260,
        },
    )

    return response.text.strip()


async def orchestrate_agent_replies(
    latest_message,
    agents_md_content,
    valid_agent_keys,
    require_all_agents=False,
    tone="normal",
    room_memory="",
    transcript=None,
    student_context="",
):
    return await asyncio.to_thread(
        orchestrate_agent_replies_sync,
        latest_message,
        agents_md_content,
        valid_agent_keys,
        require_all_agents,
        tone,
        room_memory,
        transcript,
        student_context,
    )


async def orchestrate_conversation_round(
    transcript,
    agents_md_content,
    valid_agent_keys,
    round_number,
    tone="normal",
    room_memory="",
    student_context="",
):
    return await asyncio.to_thread(
        orchestrate_conversation_round_sync,
        transcript,
        agents_md_content,
        valid_agent_keys,
        round_number,
        tone,
        room_memory,
        student_context,
    )


async def summarize_room_memory(previous_summary, transcript):
    return await asyncio.to_thread(
        summarize_room_memory_sync,
        previous_summary,
        transcript,
    )
