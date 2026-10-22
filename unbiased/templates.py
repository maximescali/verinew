from pathlib import Path

DIR = Path(__file__).parent / "prompts"


def render(name, **values):
    text = (DIR / f"{name}.md").read_text()
    for k, v in values.items():
        text = text.replace("{" + k + "}", str(v))
    return text


def bullets(items):
    return "\n".join(f"- {x}" for x in items) or "(none)"
