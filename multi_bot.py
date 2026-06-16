import asyncio
import json
import msvcrt
import os
import random
import re
from pathlib import Path

import discord
from discord.ext import commands

import memory_store
from agents import AGENTS, load_agents_markdown
from gemini_client import clean_agent_message
from gemini_client import GeminiOrchestratorError
from gemini_client import orchestrate_agent_replies, orchestrate_conversation_round
from gemini_client import summarize_room_memory


ACTIVE_AGENT_KEYS = [
    "world_modeler",
    "systems_strategist",
    "neural_educator",
    "scaling_mystic",
]
AGENT_ALIASES = {
    "world_modeler": ["yann", "lecun"],
    "systems_strategist": ["demis", "hassabis"],
    "neural_educator": ["andrej", "karpathy"],
    "scaling_mystic": ["ilya", "sutskever"],
}
SCALING_DEBATE_TERMS = [
    "scaling",
    "scale",
    "compute",
    "pretraining",
    "pre-training",
    "data wall",
    "loss curve",
    "loss curves",
    "next-token",
    "llm",
    "agi",
]
WORLD_MODEL_DEBATE_TERMS = [
    "world model",
    "world models",
    "grounding",
    "representation",
    "representations",
    "latent",
]
SYSTEMS_DEBATE_TERMS = [
    "benchmark",
    "benchmarks",
    "experiment",
    "experiments",
    "planning",
    "search",
    "rl",
    "reinforcement",
]
BUILDER_DEBATE_TERMS = [
    "code",
    "training",
    "dataset",
    "datasets",
    "tokenizer",
    "debug",
    "loss",
]
ROOM_CALL_TERMS = [
    "you guys",
    "everyone",
    "all of you",
    "room",
    "debate",
    "agree",
    "thoughts",
]
SECOND_PERSON_TERMS = [
    "you",
    "your",
    "yours",
    "u",
    "ur",
]

BOT_TOKENS_PATH = Path("bot_tokens.json")
LOCK_PATH = Path(".bot.lock")
MESSAGE_COOLDOWN_SECONDS = 0.25
MIN_REACTION_DELAY_SECONDS = 0.35
MAX_REACTION_DELAY_SECONDS = 1.4
MIN_TYPING_DELAY_SECONDS = 0.8
MAX_TYPING_DELAY_SECONDS = 4.2
SECONDS_PER_WORD = 0.11
PUNCTUATION_PAUSE_SECONDS = 0.18
FOLLOWUP_ROUND_PAUSE_MIN_SECONDS = 1.0
FOLLOWUP_ROUND_PAUSE_MAX_SECONDS = 2.7
MIN_MESSAGE_WORDS = 4
MAX_AGENT_CHAT_ROUNDS = 3
MAX_ROOM_HISTORY_MESSAGES = 20
SUMMARY_EVERY_MESSAGES = 12
SUMMARY_RECENT_MESSAGE_LIMIT = 40
USER_MEMORY_LIMIT = 8
AGENTS_MARKDOWN = load_agents_markdown()
REQUIRE_ALL_AGENTS = False
MAX_CONTROLLER_REPLIES = 1
INSTANCE_LOCK_FILE = None
CHANNEL_TRANSCRIPTS = {}
VALID_TONES = {"professional", "normal", "strict"}
DEFAULT_TONE = os.getenv("AGENT_TONE", "normal").strip().lower()
if DEFAULT_TONE not in VALID_TONES:
    DEFAULT_TONE = "normal"
CHANNEL_TONES = {}


def acquire_single_instance_lock():
    global INSTANCE_LOCK_FILE

    INSTANCE_LOCK_FILE = LOCK_PATH.open("w", encoding="utf-8")

    try:
        msvcrt.locking(INSTANCE_LOCK_FILE.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as error:
        raise RuntimeError(
            "Another simulator process is already running. Stop the old terminal first."
        ) from error

    INSTANCE_LOCK_FILE.write(str(os.getpid()))
    INSTANCE_LOCK_FILE.flush()


def token_env_name(agent_key):
    return f"DISCORD_TOKEN_{agent_key.upper()}"


def load_bot_tokens():
    tokens = {}

    if BOT_TOKENS_PATH.exists():
        tokens.update(json.loads(BOT_TOKENS_PATH.read_text(encoding="utf-8")))

    for agent_key in ACTIVE_AGENT_KEYS:
        env_token = os.getenv(token_env_name(agent_key), "").strip()
        if env_token:
            tokens[agent_key] = env_token

    available_tokens = {
        agent_key: tokens[agent_key].strip()
        for agent_key in ACTIVE_AGENT_KEYS
        if tokens.get(agent_key, "").strip()
    }

    missing = [
        agent_key
        for agent_key in ACTIVE_AGENT_KEYS
        if agent_key not in available_tokens
    ]

    if not available_tokens:
        raise RuntimeError(
            "No Discord bot tokens found. "
            f"Add them to {BOT_TOKENS_PATH} or environment variables."
        )

    if missing:
        names = ", ".join(missing)
        print(f"Skipping agents without real bot tokens: {names}")

    return available_tokens


def choose_fake_replies(text, available_agent_keys=None):
    available_agent_keys = set(available_agent_keys or ACTIVE_AGENT_KEYS)
    text = text.lower()

    if "world model" in text or "world models" in text:
        replies = [
            ("world_modeler", "Finally. World models are the missing abstraction here."),
            ("systems_strategist", "Okay, but test it with planning and RL instead of vibes."),
            ("neural_educator", "Tiny translation: ask what the model can predict before acting."),
            ("scaling_mystic", "Maybe, but ask what improves if the predictor gets 100x better."),
        ]
        return filter_available_replies(replies, available_agent_keys)

    if "scaling" in text:
        replies = [
            ("world_modeler", "Scaling helps, but without grounded models you're still guessing."),
            ("systems_strategist", "The question is what experiment separates scale from architecture."),
            ("neural_educator", "Scaling is powerful, but the dataset and objective are doing a lot too."),
            ("scaling_mystic", "Dead? No. Show me the loss curve flattening first."),
        ]
        return filter_available_replies(replies, available_agent_keys)

    return []


def choose_fake_first_reply(text, available_agent_keys=None):
    replies = choose_fake_replies(text, available_agent_keys)
    if not replies:
        return []

    return [random.choice(replies)]


def filter_available_replies(replies, available_agent_keys):
    return [
        (agent_key, reply_text)
        for agent_key, reply_text in replies
        if agent_key in available_agent_keys
    ]


def dedupe_agent_replies(replies):
    seen_agent_keys = set()
    deduped_replies = []

    for agent_key, reply_text in replies:
        if agent_key in seen_agent_keys:
            continue

        seen_agent_keys.add(agent_key)
        deduped_replies.append((agent_key, reply_text))

    return deduped_replies


def has_minimum_words(text):
    return len(text.split()) >= MIN_MESSAGE_WORDS


def has_fake_reply_route(text, available_agent_keys=None):
    return bool(choose_fake_replies(text, available_agent_keys))


def has_agent_mention(text, available_agent_keys):
    lowered = text.lower()

    for agent_key in available_agent_keys or []:
        aliases = AGENT_ALIASES.get(agent_key, [])
        if any(alias in lowered for alias in aliases):
            return True

    return False


def should_trigger_agents(text, available_agent_keys=None):
    return (
        has_minimum_words(text)
        or has_fake_reply_route(text, available_agent_keys)
        or has_agent_mention(text, available_agent_keys)
    )


def get_addressed_agent_keys(message, bots):
    mentioned_ids = {user.id for user in message.mentions}
    addressed_keys = {
        agent_key
        for agent_key, bot in bots.items()
        if bot.user and bot.user.id in mentioned_ids
    }

    lowered = message.content.lower()
    for agent_key in bots:
        aliases = AGENT_ALIASES.get(agent_key, [])
        if any(re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", lowered) for alias in aliases):
            addressed_keys.add(agent_key)

    return addressed_keys


def text_has_any(text, terms):
    return any(term in text for term in terms)


def get_topic_debate_agent_keys(text, available_agent_keys):
    lowered = text.lower()
    debate_keys = set()

    if text_has_any(lowered, SCALING_DEBATE_TERMS):
        debate_keys.update(
            {
                "scaling_mystic",
                "world_modeler",
                "systems_strategist",
                "neural_educator",
            }
        )

    if text_has_any(lowered, WORLD_MODEL_DEBATE_TERMS):
        debate_keys.update(
            {
                "world_modeler",
                "systems_strategist",
                "scaling_mystic",
                "neural_educator",
            }
        )

    if text_has_any(lowered, SYSTEMS_DEBATE_TERMS):
        debate_keys.update(
            {
                "systems_strategist",
                "world_modeler",
                "neural_educator",
            }
        )

    if text_has_any(lowered, BUILDER_DEBATE_TERMS):
        debate_keys.update(
            {
                "neural_educator",
                "systems_strategist",
                "scaling_mystic",
            }
        )

    return debate_keys & set(available_agent_keys)


def get_room_call_agent_keys(text, available_agent_keys):
    lowered = text.lower()

    if text_has_any(lowered, ROOM_CALL_TERMS):
        return set(available_agent_keys)

    return set()


def get_last_agent_key_from_transcript(channel_id, available_agent_keys):
    transcript = get_channel_transcript(channel_id)
    available_agent_keys = set(available_agent_keys)

    for message in reversed(transcript):
        speaker = message.get("speaker", "")
        for agent_key in available_agent_keys:
            if speaker == AGENTS[agent_key]["name"]:
                return agent_key

    return None


def is_second_person_followup(text):
    lowered = text.lower()
    return any(
        re.search(rf"(?<!\w){re.escape(term)}(?!\w)", lowered)
        for term in SECOND_PERSON_TERMS
    )


def get_channel_tone(channel_id):
    if channel_id not in CHANNEL_TONES:
        CHANNEL_TONES[channel_id] = memory_store.get_channel_tone(channel_id) or DEFAULT_TONE

    return CHANNEL_TONES[channel_id]


def set_channel_tone(channel_id, tone):
    tone = tone.strip().lower()

    if tone == "ragebait":
        tone = "strict"

    if tone not in VALID_TONES:
        return None

    CHANNEL_TONES[channel_id] = tone
    memory_store.set_channel_tone(channel_id, tone)
    return tone


def format_tone_help():
    tones = ", ".join(sorted(VALID_TONES))
    return f"Tone options: {tones}. Use `!tone normal`, `!tone professional`, or `!tone strict`."


async def handle_tone_command(message):
    content = message.content.strip()

    if not content.lower().startswith("!tone"):
        return False

    parts = content.split(maxsplit=1)

    if len(parts) == 1:
        tone = get_channel_tone(message.channel.id)
        student_context = build_student_context(message)
        await message.channel.send(f"Current tone: `{tone}`. {format_tone_help()}")
        return True

    tone = set_channel_tone(message.channel.id, parts[1])

    if tone is None:
        await message.channel.send(format_tone_help())
        return True

    await message.channel.send(f"Tone set to `{tone}`.")
    return True


async def handle_memory_command(message):
    raw_content = message.content.strip()
    content = raw_content.lower()

    if content not in {"!memory clear", "!memory reset"}:
        if raw_content.lower().startswith("!remember "):
            memory = raw_content[len("!remember "):].strip()
            if not memory:
                await message.channel.send("give me a memory after `!remember`.")
                return True

            memory_store.add_user_memory(
                message.author.id,
                message.author.display_name,
                memory,
            )
            await message.channel.send("remembered.")
            return True

        if content == "!memory":
            await message.channel.send(format_memory_status(message.channel.id, message.author.id))
            return True

        if content in {"!memory forget me", "!memory clear me"}:
            memory_store.clear_user_memories(message.author.id)
            await message.channel.send("your saved memories are cleared.")
            return True

        if content == "!summary":
            summary = memory_store.get_channel_summary(message.channel.id)
            if summary is None:
                await message.channel.send("no room summary yet.")
            else:
                await message.channel.send(summary["summary"][:1900])
            return True

        return False

    CHANNEL_TRANSCRIPTS.pop(message.channel.id, None)
    memory_store.clear_channel_messages(message.channel.id)
    await message.channel.send("room memory cleared.")
    return True


def format_memory_status(channel_id, user_id):
    summary = memory_store.get_channel_summary(channel_id)
    memories = memory_store.get_user_memories(user_id, USER_MEMORY_LIMIT)
    parts = []

    if summary:
        parts.append(f"room summary: {summary['summary']}")
    else:
        parts.append("room summary: none yet")

    if memories:
        memory_lines = "\n".join(f"- {memory}" for memory in memories)
        parts.append(f"your memories:\n{memory_lines}")
    else:
        parts.append("your memories: none yet")

    return "\n\n".join(parts)[:1900]


async def choose_agent_replies(
    text,
    available_agent_keys,
    require_all_agents=REQUIRE_ALL_AGENTS,
    tone=DEFAULT_TONE,
    room_memory="",
    transcript=None,
    student_context="",
):
    try:
        result = await orchestrate_agent_replies(
            latest_message=text,
            agents_md_content=AGENTS_MARKDOWN,
            valid_agent_keys=set(available_agent_keys),
            require_all_agents=require_all_agents,
            tone=tone,
            room_memory=room_memory,
            transcript=transcript,
            student_context=student_context,
        )
    except GeminiOrchestratorError as error:
        print(f"Gemini fallback: {error}")
        if require_all_agents:
            return choose_fake_replies(text, available_agent_keys)
        return choose_fake_first_reply(text, available_agent_keys)
    except Exception as error:
        print(f"Gemini fallback: {type(error).__name__}: {error}")
        if require_all_agents:
            return choose_fake_replies(text, available_agent_keys)
        return choose_fake_first_reply(text, available_agent_keys)

    if not result["should_reply"]:
        return []

    return [
        (reply["agent_key"], reply["message"])
        for reply in result["replies"]
        if reply["agent_key"] in available_agent_keys
    ]


def limit_controller_replies(replies, controller_key):
    controller_replies = 0
    limited_replies = []

    for agent_key, reply_text in replies:
        if agent_key == controller_key:
            controller_replies += 1
            if controller_replies > MAX_CONTROLLER_REPLIES:
                continue

        limited_replies.append((agent_key, reply_text))

    return limited_replies


def get_typing_delay(text):
    word_count = len(text.split())
    punctuation_count = sum(text.count(mark) for mark in ".?!,;:")
    delay = (word_count * SECONDS_PER_WORD) + (punctuation_count * PUNCTUATION_PAUSE_SECONDS)
    jitter = random.uniform(0.85, 1.25)
    return max(MIN_TYPING_DELAY_SECONDS, min(delay * jitter, MAX_TYPING_DELAY_SECONDS))


def get_reaction_delay(agent_index):
    base_delay = random.uniform(MIN_REACTION_DELAY_SECONDS, MAX_REACTION_DELAY_SECONDS)
    stagger_delay = agent_index * random.uniform(0.1, 0.35)
    return base_delay + stagger_delay


def get_round_pause(round_number):
    pause = random.uniform(FOLLOWUP_ROUND_PAUSE_MIN_SECONDS, FOLLOWUP_ROUND_PAUSE_MAX_SECONDS)
    return pause + ((round_number - 1) * random.uniform(0.4, 0.9))


def get_channel_transcript(channel_id):
    if channel_id not in CHANNEL_TRANSCRIPTS:
        CHANNEL_TRANSCRIPTS[channel_id] = memory_store.get_recent_messages(
            channel_id,
            MAX_ROOM_HISTORY_MESSAGES,
        )

    return CHANNEL_TRANSCRIPTS[channel_id]


def append_transcript_message(channel_id, speaker, content):
    transcript = get_channel_transcript(channel_id)
    transcript.append(
        {
            "speaker": speaker,
            "content": content,
        }
    )
    del transcript[:-MAX_ROOM_HISTORY_MESSAGES]
    memory_store.add_message(channel_id, speaker, content)


def append_agent_transcript_messages(channel_id, sent_replies):
    for agent_key, text in sent_replies:
        agent = AGENTS[agent_key]
        append_transcript_message(channel_id, agent["name"], text)


def build_room_memory_context(channel_id, user_id):
    summary = memory_store.get_channel_summary(channel_id)
    user_memories = memory_store.get_user_memories(user_id, USER_MEMORY_LIMIT)
    parts = []

    if summary:
        parts.append(f"Room summary:\n{summary['summary']}")

    if user_memories:
        memories = "\n".join(f"- {memory}" for memory in user_memories)
        parts.append(f"Student memory:\n{memories}")

    return "\n\n".join(parts)


def build_student_context(message):
    return "\n".join(
        [
            f"Display name: {message.author.display_name}",
            f"Username: {message.author.name}",
            f"Discord mention: {message.author.mention}",
        ]
    )


async def maybe_update_room_summary(channel_id):
    message_count = memory_store.get_message_count(channel_id)
    existing_summary = memory_store.get_channel_summary(channel_id)
    last_count = existing_summary["message_count_at_update"] if existing_summary else 0

    if message_count - last_count < SUMMARY_EVERY_MESSAGES:
        return

    transcript = memory_store.get_recent_messages(channel_id, SUMMARY_RECENT_MESSAGE_LIMIT)
    previous_summary = existing_summary["summary"] if existing_summary else ""

    try:
        summary = await summarize_room_memory(previous_summary, transcript)
    except Exception as error:
        print(f"Room summary skipped: {type(error).__name__}: {error}")
        return

    if summary:
        memory_store.set_channel_summary(channel_id, summary, message_count)
        print(f"Updated room summary for channel {channel_id}")


async def choose_conversation_round_replies(
    transcript,
    available_agent_keys,
    round_number,
    tone,
    room_memory,
    student_context,
):
    try:
        result = await orchestrate_conversation_round(
            transcript=transcript,
            agents_md_content=AGENTS_MARKDOWN,
            valid_agent_keys=set(available_agent_keys),
            round_number=round_number,
            tone=tone,
            room_memory=room_memory,
            student_context=student_context,
        )
    except GeminiOrchestratorError as error:
        print(f"Gemini conversation stop: {error}")
        return []
    except Exception as error:
        print(f"Gemini conversation stop: {type(error).__name__}: {error}")
        return []

    if not result["should_reply"]:
        return []

    return [
        (reply["agent_key"], reply["message"])
        for reply in result["replies"]
        if reply["agent_key"] in available_agent_keys
    ]


async def run_agent_conversation(channel_id, available_agent_keys, bots, tone, user_id, student_context):
    for round_number in range(1, MAX_AGENT_CHAT_ROUNDS + 1):
        await asyncio.sleep(get_round_pause(round_number))
        transcript = list(get_channel_transcript(channel_id))
        room_memory = build_room_memory_context(channel_id, user_id)
        replies = await choose_conversation_round_replies(
            transcript,
            available_agent_keys,
            round_number,
            tone,
            room_memory,
            student_context,
        )
        replies = dedupe_agent_replies(replies)

        if not replies:
            break

        sent_replies = await send_agent_replies(channel_id, replies, bots)
        append_agent_transcript_messages(channel_id, sent_replies)


def create_agent_bot(agent_key, controller_key, bots):
    intents = discord.Intents.default()
    intents.message_content = True

    bot = commands.Bot(
        command_prefix="!",
        intents=intents,
    )

    @bot.event
    async def on_ready():
        agent = AGENTS[agent_key]
        role = "controller" if agent_key == controller_key else "speaker"
        print(f"{agent['name']} logged in as {bot.user} ({role})")

    @bot.event
    async def on_message(message):
        if agent_key != controller_key:
            return

        if message.author.bot:
            return

        print(f"{message.author}: {message.content}")

        if await handle_tone_command(message):
            return

        if await handle_memory_command(message):
            return

        available_agent_keys = set(bots)
        addressed_agent_keys = get_addressed_agent_keys(message, bots)
        inferred_agent_key = None
        if not addressed_agent_keys and is_second_person_followup(message.content):
            inferred_agent_key = get_last_agent_key_from_transcript(
                message.channel.id,
                available_agent_keys,
            )
            if inferred_agent_key:
                addressed_agent_keys.add(inferred_agent_key)

        debate_agent_keys = get_topic_debate_agent_keys(message.content, available_agent_keys)
        room_call_agent_keys = get_room_call_agent_keys(message.content, available_agent_keys)
        reply_agent_keys = (
            addressed_agent_keys
            | debate_agent_keys
            | room_call_agent_keys
            or available_agent_keys
        )
        tone = get_channel_tone(message.channel.id)
        student_context = build_student_context(message)

        if addressed_agent_keys:
            print(f"Direct mention targets: {', '.join(sorted(addressed_agent_keys))}")

        if inferred_agent_key:
            print(f"Inferred follow-up target: {inferred_agent_key}")

        if debate_agent_keys:
            print(f"Topic debate targets: {', '.join(sorted(debate_agent_keys))}")

        if not (
            addressed_agent_keys
            or debate_agent_keys
            or room_call_agent_keys
            or should_trigger_agents(message.content, available_agent_keys)
        ):
            return

        append_transcript_message(message.channel.id, message.author.display_name, message.content)
        room_memory = build_room_memory_context(message.channel.id, message.author.id)
        transcript = list(get_channel_transcript(message.channel.id))
        replies = await choose_agent_replies(
            message.content,
            reply_agent_keys,
            require_all_agents=False,
            tone=tone,
            room_memory=room_memory,
            transcript=transcript,
            student_context=student_context,
        )
        replies = dedupe_agent_replies(replies)
        replies = limit_controller_replies(replies, controller_key)
        sent_replies = await send_agent_replies(message.channel.id, replies, bots)
        append_agent_transcript_messages(message.channel.id, sent_replies)

        is_one_on_one = addressed_agent_keys and not debate_agent_keys and not room_call_agent_keys

        if not is_one_on_one:
            await run_agent_conversation(
                message.channel.id,
                available_agent_keys,
                bots,
                tone,
                message.author.id,
                student_context,
            )

        await maybe_update_room_summary(message.channel.id)

    return bot


async def get_bot_channel(bot, channel_id):
    channel = bot.get_channel(channel_id)
    if channel is not None:
        return channel

    return await bot.fetch_channel(channel_id)


async def send_agent_message(channel_id, agent_key, text, bots):
    text = clean_agent_message(text)
    if not text:
        return None

    bot = bots[agent_key]
    channel = await get_bot_channel(bot, channel_id)

    async with channel.typing():
        await asyncio.sleep(get_typing_delay(text))

    await channel.send(text)
    return text


async def send_agent_message_with_timing(channel_id, agent_key, text, bots, agent_index):
    await asyncio.sleep(get_reaction_delay(agent_index))
    return await send_agent_message(channel_id, agent_key, text, bots)


async def send_agent_replies(channel_id, replies, bots):
    tasks = [
        (
            agent_key,
            asyncio.create_task(
                send_agent_message_with_timing(
                    channel_id,
                    agent_key,
                    reply_text,
                    bots,
                    index,
                )
            ),
        )
        for index, (agent_key, reply_text) in enumerate(replies)
        if agent_key in bots
    ]

    sent_replies = []
    for agent_key, task in tasks:
        sent_text = await task
        if sent_text:
            sent_replies.append((agent_key, sent_text))

    return sent_replies


async def run_bots():
    acquire_single_instance_lock()
    memory_store.init_db()
    tokens = load_bot_tokens()
    controller_key = next(
        agent_key
        for agent_key in ACTIVE_AGENT_KEYS
        if agent_key in tokens
    )
    bots = {}

    for agent_key in tokens:
        bots[agent_key] = create_agent_bot(
            agent_key,
            controller_key,
            bots,
        )

    await asyncio.gather(
        *[
            bots[agent_key].start(tokens[agent_key])
            for agent_key in tokens
        ]
    )


def main():
    asyncio.run(run_bots())


if __name__ == "__main__":
    main()
