from pathlib import Path


AGENTS_MD_PATH = Path("agents.md")
REQUIRED_AGENT_FIELDS = {
    "key",
    "display name",
    "emoji",
    "personality",
    "preferred topics",
    "speaking style",
    "disagreement style",
    "things to avoid",
}


def clean_markdown_value(value):
    return value.strip().strip("`")


def normalize_field_name(name):
    return name.strip().lower()


def parse_agent_block(block):
    profile = {}

    for line in block.splitlines():
        if ":" not in line:
            continue

        field, value = line.split(":", 1)
        field = normalize_field_name(field)

        if field in REQUIRED_AGENT_FIELDS:
            profile[field] = clean_markdown_value(value)

    missing_fields = REQUIRED_AGENT_FIELDS - profile.keys()
    if missing_fields:
        missing = ", ".join(sorted(missing_fields))
        raise ValueError(f"Agent profile is missing required fields: {missing}")

    key = profile.pop("key")
    display_name = profile.pop("display name")
    preferred_topics = [
        topic.strip()
        for topic in profile.pop("preferred topics").split(",")
        if topic.strip()
    ]

    return key, {
        "key": key,
        "name": display_name,
        "display_name": display_name,
        "emoji": profile.pop("emoji"),
        "personality": profile.pop("personality"),
        "preferred_topics": preferred_topics,
        "speaking_style": profile.pop("speaking style"),
        "disagreement_style": profile.pop("disagreement style"),
        "things_to_avoid": profile.pop("things to avoid"),
    }


def load_global_instruction(markdown):
    marker = "Global instruction:"
    agent_marker = "## Agent"

    if marker not in markdown:
        return ""

    start = markdown.index(marker) + len(marker)
    end = markdown.find(agent_marker, start)

    if end == -1:
        end = len(markdown)

    return markdown[start:end].strip()


def load_agent_profiles(path=AGENTS_MD_PATH):
    markdown = Path(path).read_text(encoding="utf-8")
    blocks = markdown.split("## Agent")[1:]
    agents = {}

    for block in blocks:
        key, profile = parse_agent_block(block)
        agents[key] = profile

    if not agents:
        raise ValueError(f"No agent profiles found in {path}")

    return agents


def load_agents_markdown(path=AGENTS_MD_PATH):
    return Path(path).read_text(encoding="utf-8")


_AGENTS_MARKDOWN = load_agents_markdown(AGENTS_MD_PATH)

GLOBAL_AGENT_INSTRUCTION = load_global_instruction(_AGENTS_MARKDOWN)
AGENTS = load_agent_profiles(AGENTS_MD_PATH)
