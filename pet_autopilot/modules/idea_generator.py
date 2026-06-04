import json
import logging
import time
import requests
from config import GEMINI_API_KEY, GEMINI_API_URL, GROQ_API_KEYS, GROQ_API_URL, GROQ_MODEL, NICHE

log = logging.getLogger("idea_generator")

FALLBACK_TITLES = [
    "Why Cats Actually Purr (It's Not What You Think)",
    "5 Dog Behaviors That Prove They Understand You",
    "The Smartest Animal in the World Explained",
    "Why Dogs Tilt Their Heads When You Speak",
    "What Your Cat Is Actually Trying to Tell You",
]

FALLBACK_TAGS = ["pets", "cats", "dogs", "animals", "facts", "cute", "wildlife", "animal facts", "pet care", "pet secrets"]


def call_gemini(prompt: str) -> str:
    """Call Gemini API via REST."""
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is not set")
    
    resp = requests.post(
        GEMINI_API_URL,
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}]
        },
        timeout=60
    )
    resp.raise_for_status()
    data = resp.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise ValueError(f"Invalid Gemini response structure: {data}") from e


def call_llm(prompt: str) -> str:
    """Call Gemini first, fallback to Groq on failure or rate limits."""
    try:
        log.info("Attempting LLM call via Gemini...")
        return call_gemini(prompt)
    except Exception as e:
        log.warning(f"Gemini API call failed: {e}. Falling back to Groq...")
        return call_groq(prompt)


def call_groq(prompt: str) -> str:
    """Call Groq API with automatic 3-key fallback on rate limit."""
    last_error = None
    for key in GROQ_API_KEYS:
        try:
            resp = requests.post(
                GROQ_API_URL,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": GROQ_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.7,
                },
                timeout=60,
            )
            if resp.status_code == 429:
                log.warning(f"Groq key ...{key[-6:]} rate limited, trying next key")
                last_error = f"429 rate limit on key ...{key[-6:]}"
                continue
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except requests.exceptions.RequestException as e:
            log.warning(f"Groq key ...{key[-6:]} failed: {e}")
            last_error = str(e)
            continue

    raise RuntimeError(f"All Groq keys failed: {last_error}")


def _parse_json_list(raw: str) -> list:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1]) if len(lines) > 2 else cleaned
    cleaned = cleaned.strip()
    return json.loads(cleaned)


def get_trending_pet_topics() -> list:
    """Fetch top posts from pet subreddits to use as inspiration."""
    topics = []
    subreddits = ["aww", "pets", "todayilearned"]
    headers = {"User-Agent": "PetdramaBot/1.0"}
    
    for sub in subreddits:
        try:
            resp = requests.get(f"https://www.reddit.com/r/{sub}/top.json?limit=5&t=day", headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for child in data.get("data", {}).get("children", []):
                    title = child.get("data", {}).get("title")
                    if title:
                        topics.append(title)
        except Exception as e:
            log.warning(f"Failed to fetch Reddit trends for r/{sub}: {e}")
            
    filtered_topics = []
    for t in topics:
        if "dog" in t.lower() or "cat" in t.lower() or "animal" in t.lower() or "pet" in t.lower() or "wildlife" in t.lower():
            filtered_topics.append(t)
        elif "TIL" not in t:
            filtered_topics.append(t)
            
    return filtered_topics[:15]


def generate_ideas(count: int = 10) -> list:
    trending_topics = get_trending_pet_topics()
    trending_text = ""
    if trending_topics:
        trending_text = "\nHere are some currently trending topics online for inspiration:\n- " + "\n- ".join(trending_topics) + "\n"

    prompt = (
        f"You are a YouTube content strategist for a faceless channel about {NICHE}. "
        f"Generate {count} unique, engaging YouTube video titles.\n"
        f"{trending_text}"
        "Rules:\n"
        "- Each title must be curiosity-driven and clickbaity but NOT misleading\n"
        "- Titles should be 40-70 characters long\n"
        "- Mix formats: lists ('5 Things...'), stories ('The Truth About...'), questions ('What Happens If...'), shocking facts ('Scientists Just Discovered...')\n"
        "- Topics: cats, dogs, animal behavior, wildlife facts, pet secrets, exotic pets, animal intelligence, strange animal habits\n"
        "- Return ONLY a valid JSON array of strings. No explanation. No markdown. Just the JSON array.\n"
        'Example format: ["Title 1", "Title 2", "Title 3"]'
    )

    for attempt in range(1, 4):
        try:
            raw = call_llm(prompt)
            ideas = _parse_json_list(raw)
            log.info(f"Generated {len(ideas)} ideas via LLM")
            return ideas
        except Exception as e:
            log.warning(f"Idea generation attempt {attempt} failed: {e}")
            if attempt < 3:
                time.sleep(5)

    log.warning("Using fallback titles")
    return FALLBACK_TITLES.copy()


def generate_description(title: str) -> str:
    prompt = (
        f"Write a YouTube video description for a video titled: '{title}'\n"
        f"The channel is about {NICHE}.\n"
        "Requirements:\n"
        "- 150-200 words\n"
        "- First line is a hook sentence\n"
        "- Include what viewers will learn\n"
        "- End with: 'Like and Subscribe for more Space Facts!'\n"
        "- Add 10 relevant hashtags at the very end on a new line\n"
        "- Hashtags must include: #pets #cats #dogs #animals #facts"
    )

    for attempt in range(1, 4):
        try:
            desc = call_llm(prompt).strip()
            log.info(f"Generated description for: {title}")
            return desc
        except Exception as e:
            log.warning(f"Description generation attempt {attempt} failed: {e}")
            if attempt < 3:
                time.sleep(5)

    return (
        f"Discover the mind-blowing truth about {title}! "
        "In this video, we explore fascinating facts about our furry friends and the animal kingdom that will leave you speechless. "
        "Like and Subscribe for more Pet Facts!\n\n"
        "#pets #cats #dogs #animals #facts #cute #wildlife #animalfacts #petlovers #petsecrets"
    )


def generate_script(title: str) -> str:
    prompt = (
        f"Write a fact-packed narration script for a YouTube Shorts video titled: '{title}'\n"
        "Rules:\n"
        "- 120-150 words total\n"
        "- Every sentence must contain a REAL, specific, mind-blowing fact with numbers or measurements if possible\n"
        "- Include at least 5 distinct facts (lifespans, physical abilities, sensory limits, unique behaviors, scientific discoveries)\n"
        "- Start with one shocking fact as a hook — no questions, no 'have you ever wondered'\n"
        "- No filler sentences, no vague statements\n"
        "- Plain narration text only — no headers, no bullet points, no markdown\n"
        "- End with the most surprising fact saved for last\n"
        "Example of good fact: 'A dog's sense of smell is 40 times better than a human's, with over 300 million olfactory receptors.'\n"
        "Example of bad: 'Dogs are incredibly fascinating and loyal animals.'\n"
        "Return ONLY the narration script. Nothing else."
    )

    for attempt in range(1, 4):
        try:
            script = call_llm(prompt).strip()
            log.info(f"Generated script ({len(script.split())} words) for: {title}")
            return script
        except Exception as e:
            log.warning(f"Script generation attempt {attempt} failed: {e}")
            if attempt < 3:
                time.sleep(5)

    return (
        "A cat's purr has been shown to heal bones and tissues, vibrating at frequencies between 25 and 150 Hertz. "
        "Dogs can smell your feelings, detecting changes in your sweat and breath when you are stressed or afraid. "
        "The fingerprints of a koala are so indistinguishable from humans that they have on occasion been confused at a crime scene. "
        "An octopus has three hearts, nine brains, and blue blood. "
        "Sloths can hold their breath for up to 40 minutes underwater, far longer than dolphins. "
        "Cows have best friends and experience stress when they are separated. "
        "And the most shocking fact: a house cat shares 95.6 percent of its genetic makeup with tigers."
    )


def generate_tags(title: str) -> list:
    prompt = (
        f"Generate 15 YouTube SEO tags for a video titled: '{title}' about {NICHE}.\n"
        "Return ONLY a valid JSON array of strings. No explanation. No markdown."
    )

    for attempt in range(1, 4):
        try:
            raw = call_llm(prompt)
            tags = _parse_json_list(raw)
            log.info(f"Generated {len(tags)} tags")
            return tags
        except Exception as e:
            log.warning(f"Tag generation attempt {attempt} failed: {e}")
            if attempt < 3:
                time.sleep(5)

    return FALLBACK_TAGS.copy()


def generate_video_terms(script: str) -> str:
    prompt = (
        f"Read this video script about {NICHE}:\n\n"
        f"\"{script}\"\n\n"
        "Extract exactly 3 to 5 highly visual, specific search terms to find stock footage for this video on Pexels/Pixabay.\n"
        "Examples of good terms: 'cute cat sleeping', 'dog running in park', 'golden retriever', 'lion roaring'.\n"
        "Examples of bad terms: 'science', 'interesting', 'dark'.\n"
        "Return ONLY a comma-separated list of strings. No explanation. No JSON. Just the words."
    )

    for attempt in range(1, 4):
        try:
            terms = call_llm(prompt).strip()
            # Clean up if the LLM adds quotes or brackets
            terms = terms.replace('"', '').replace('[', '').replace(']', '').strip()
            log.info(f"Generated video terms: {terms}")
            return terms
        except Exception as e:
            log.warning(f"Video terms generation attempt {attempt} failed: {e}")
            if attempt < 3:
                time.sleep(5)

    return "pets, cats, dogs, animals, cute"


AVAILABLE_VOICES = {
    "en-US-ChristopherNeural": "Energetic, enthusiastic, good for fun or surprising facts",
    "en-US-GuyNeural": "Deep, serious, slightly dramatic, good for mysterious or dark facts",
    "en-US-AriaNeural": "Friendly, upbeat female voice, good for cute or wholesome pet facts",
    "en-US-EricNeural": "Upbeat, casual male voice, good for relatable pet habits",
}

def select_voice(title: str) -> str:
    voices_str = "\n".join([f"- {name}: {desc}" for name, desc in AVAILABLE_VOICES.items()])
    prompt = (
        f"Select the best text-to-speech voice for a YouTube video titled: '{title}'\n"
        f"Available voices:\n{voices_str}\n\n"
        "Return ONLY the exact voice name as a string (e.g., en-US-ChristopherNeural). No explanation."
    )
    
    for attempt in range(1, 3):
        try:
            voice = call_llm(prompt).strip()
            voice = voice.replace('"', '').replace("'", '').strip()
            if voice in AVAILABLE_VOICES:
                log.info(f"Selected dynamic voice: {voice} for title: {title}")
                return voice
        except Exception as e:
            log.warning(f"Voice selection attempt {attempt} failed: {e}")
            
    return "en-US-ChristopherNeural"
