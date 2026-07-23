"""
Dynamic songwriting guidelines that apply context-aware rules during lyric generation.

Guidelines are applied based on genre, mood, energy level, and song structure.
This ensures generated lyrics are memorable, singable, and emotionally resonant.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger(__name__)

# Song energy levels: affects rhythm, word choice, imagery
EnergyLevel = Literal["low", "medium", "high"]

# Song emotional arc: affects narrative structure
EmotionalArc = Literal[
    "hope_to_heartbreak",
    "anger_to_acceptance",
    "fear_to_courage",
    "loneliness_to_freedom",
    "confusion_to_clarity",
    "discovery",
    "yearning",
]


@dataclass
class SingabilityGuidelines:
    """Rules for how lyrics should sound when sung."""

    vowel_sounds: list[str] = None
    strong_consonants: list[str] = None
    avoid_clusters: bool = True
    target_phrase_length: str = "short"

    def __post_init__(self):
        if self.vowel_sounds is None:
            self.vowel_sounds = [
                "fire",
                "light",
                "alone",
                "home",
                "away",
                "forever",
                "higher",
                "believe",
                "tonight",
                "alive",
            ]
        if self.strong_consonants is None:
            self.strong_consonants = ["b", "d", "k", "p", "t", "m", "n", "l"]


def singable_word_examples(energy: EnergyLevel) -> list[str]:
    """Generate examples of singable words based on energy level."""
    base_words = [
        "broken",
        "burning",
        "heartbeat",
        "midnight",
        "runaway",
        "beautiful",
        "paradise",
    ]
    high_energy_words = [
        "lightning",
        "thunder",
        "explode",
        "wild",
        "free",
        "alive",
        "electric",
    ]
    low_energy_words = [
        "whisper",
        "fade",
        "drift",
        "sleep",
        "dream",
        "silence",
        "alone",
    ]

    result = base_words
    if energy == "high":
        result += high_energy_words
    elif energy == "low":
        result += low_energy_words
    return result


def vivid_imagery_examples(genre_id: str, mood: str | None = None) -> list[str]:
    """Generate vivid imagery examples suited to genre/mood."""
    imagery_map: dict[str, list[str]] = {
        "rock": [
            "City lights blur past the windshield",
            "Smoke curling into neon signs",
            "Asphalt still warm from the sun",
            "Leather jacket catching rain",
        ],
        "indie": [
            "Your coffee cup still on my desk",
            "Film photographs scattered on hardwood",
            "Empty swing swaying in the yard",
            "Your side of the bed stays cold",
        ],
        "electronic": [
            "Neon grids reflecting in wet streets",
            "Synth waves cutting through silence",
            "Pixels dissolving into light",
            "Digital dreams pixelating",
        ],
        "folk": [
            "Dirt under fingernails from the garden",
            "Smoke from the campfire rising slowly",
            "Worn wooden door creaking open",
            "Roots running deep beneath the earth",
        ],
        "jazz": [
            "Smoke hanging heavy in the club",
            "Your silhouette in the spotlight",
            "Velvet ropes and whispered names",
            "Keys catching the lamplight",
        ],
        "pop": [
            "Glitter catching in your hair",
            "Champagne bubbles rising high",
            "Mirrors reflecting a thousand versions",
            "Confetti falling like rain",
        ],
        "hiphop": [
            "Streetlights casting shadows",
            "Chains reflecting the moon",
            "Concrete wearing your footprints",
            "Beat dropping like thunder",
        ],
        "blues": [
            "Whiskey burning slow",
            "Train whistles in the distance",
            "Rain on tin roofs",
            "Your perfume still on my pillow",
        ],
        "classical": [
            "Marble columns casting long shadows",
            "Piano keys like ivory teeth",
            "Candlelight dancing on ancient walls",
            "Silence so pure it rings",
        ],
        "reggae": [
            "Sunlight through palm fronds",
            "Salt spray on sun-warmed skin",
            "Waves rolling like a heartbeat",
            "Earth colors and sky meeting",
        ],
        "country": [
            "Dust on the porch rail",
            "Stars spread across the prairie",
            "Old truck idling at dawn",
            "Worn leather and memory",
        ],
    }
    examples = imagery_map.get(genre_id, imagery_map["indie"])
    return examples[:4]


def cliche_alternatives(overused: str) -> str:
    """Suggest fresh alternatives to common clichés."""
    alternatives_map: dict[str, str] = {
        "broken heart": "You left fingerprints on every tomorrow",
        "forever and always": "You became the before and after",
        "stars align": "Our orbits finally found each other",
        "tears in the rain": "My voice dissolves into the downpour",
        "love conquers all": "Love learned to bend without breaking",
        "soul mate": "You're the name my silence whispers",
        "butterflies in stomach": "My ribs became a birdcage",
        "fire in my eyes": "I became the match that wouldn't go out",
        "carry me away": "I drifted somewhere I could finally breathe",
    }
    return alternatives_map.get(overused.lower(), overused)


def lyric_generation_prompt(
    genre_id: str,
    mood: str,
    energy: EnergyLevel,
    arc: str,
    section: str,
    title: str,
    artist: str,
) -> str:
    """
    Generate a context-aware prompt that guides lyric generation with songwriting rules.

    Args:
        genre_id: Music genre (rock, indie, electronic, etc.)
        mood: Mood descriptor (melancholic, uplifting, energetic, etc.)
        energy: Energy level (low, medium, high)
        arc: Emotional progression (hope_to_heartbreak, etc.)
        section: Song section (verse, chorus, bridge, etc.)
        title: Song title
        artist: Artist name

    Returns:
        Detailed prompt incorporating dynamic songwriting guidelines
    """
    # Energy-specific guidance
    max_words = 10 if energy == "high" else 15 if energy == "medium" else 12
    syllable_target = 7 if energy == "high" else 9 if energy == "medium" else 8
    phrase_guidance = (
        "Use punchy, short phrases (1-3 words) that feel like a shout"
        if energy == "high"
        else "Use phrases that breathe, 3-6 words typical"
        if energy == "medium"
        else "Use sparse, deliberate phrasing, 2-5 words"
    )

    # Anti-cliché guidance (dynamic based on mood/arc)
    cliche_guidance = """AVOID OVERUSED PHRASES (reinvent or replace them):
❌ "broken heart" → ✅ Show the feeling: "my chest cracks when"
❌ "fire in my eyes" → ✅ "I'm burning from the inside"
❌ "lost in your love" → ✅ "I forgot the way back to myself"
❌ "tears falling down" → ✅ "I'm drowning on dry land"
❌ "scars on my soul" → ✅ Show the wound: "I flinch when you touch me"

Rule: Show the emotion through ACTION or IMAGE, never name it directly."""

    # Rhyme scheme guidance (dynamic based on section)
    if section == "chorus":
        rhyme_guidance = f"""RHYME SCHEME FOR MEMORABILITY:
- Lines 1 & 2 should rhyme (end with similar sounds)
- This makes the hook STICK in listeners' heads
- Examples: "tonight/light", "believe/see", "way/stay"
- The title "{title}" should appear at a strong rhyme point
- Rhyming chorus = stuck in people's heads for days"""
    elif section == "verse":
        rhyme_guidance = """RHYME SCHEME OPTIONS:
- Loose rhyming (ABCB pattern) works for storytelling
- Internal rhymes (rhyming within a line) add flow: "I'm tied to the tide"
- Don't force rhymes—conversational truth > perfect rhyme
- Rhythm matters more than rhyme in verses"""
    elif section == "bridge":
        rhyme_guidance = """RHYME SCHEME CONTRAST:
- Change your rhyme pattern from verses (breaks expectations)
- Can use tighter rhyming to build energy
- Or looser to create vulnerability
- Use this section to surprise the listener"""
    else:
        rhyme_guidance = "Maintain consistent rhythm and rhyme where natural."

    # Title integration (dynamic)
    title_guidance = f"""TITLE PLACEMENT:
- "{title}" must appear in this {section} naturally (not forced)
- In a chorus: place at peak emotional moment (often the hook line)
- In a verse: can appear early to set context
- In a bridge: can appear for final emotional payoff
- Test: Does the title feel like it's MEANT to be there, or shoehorned?"""

    # Refrain patterns (for chorus)
    if section == "chorus":
        refrain_guidance = """REFRAIN PATTERNS (repetition makes hooks catchier):
- Opening hook (1 line repeated): "Say my name, say my name"
- Bookended chorus: Start & end with same phrase for closure
- Internal repetition: "I'm falling, falling, falling down"
- Rhythm repetition: Same word pattern on successive lines
- Pick ONE pattern and execute perfectly—don't mix"""
    else:
        refrain_guidance = ""

    # Section-specific guidance
    if section == "verse":
        section_instruction = """VERSE PURPOSE:
- Tell the story with SPECIFIC details (not generic feelings)
- Build tension toward the chorus
- Each line should be 1-2 ideas maximum
- Use "I" or "you" to create intimacy
- Save the big emotion for the chorus"""
    elif section == "chorus":
        section_instruction = f"""CHORUS PURPOSE:
- This is the PAYOFF—the reason people listen
- Express the core emotion in simplest language
- MUST be memorable after one listen (will it stick?)
- Every word should matter and be singable
- Build to emotional peak, not down from it
- Lines should almost be shout-able"""
    elif section == "bridge":
        section_instruction = """BRIDGE PURPOSE:
- Provide new emotional perspective (show a different angle)
- Build toward climactic final chorus
- Can introduce vulnerability or contrast
- Often quieter/rawer than verses
- Sets up the final chorus to hit harder"""
    else:
        section_instruction = f"Focus on: {section}"

    # Emotional language (dynamic based on arc + mood)
    emotion_intensity = "raw and visceral" if energy == "high" else "introspective and tender" if energy == "low" else "balanced and building"
    emotion_examples = {
        "hope_to_heartbreak": "Start with possibility ('I thought...'), end with loss ('Now I know...')",
        "anger_to_acceptance": "Start sharp ('You lied, you stole'), move to understanding ('But I see why now')",
        "fear_to_courage": "Start small ('What if...'), build to action ('I'm doing it')",
        "loneliness_to_freedom": "Start isolated ('Alone with my thoughts'), end liberated ('Finally just me')",
        "confusion_to_clarity": "Start disoriented ('Nothing makes sense'), end with truth ('Now I see')",
        "discovery": "Show the moment of realization—the before and after",
        "yearning": "Show the wanting without resolution—leave it aching",
    }
    arc_instruction = emotion_examples.get(
        arc, f"Follow emotional arc: {arc}"
    )

    # Imagery guidance
    imagery = vivid_imagery_examples(genre_id, mood)
    imagery_instruction = f"""SHOW, DON'T TELL (use specific imagery, not named emotions):
Instead of: "I'm sad" → Show: "I can't lift my head from the pillow"
Instead of: "You hurt me" → Show: "Your silence tastes like iron"
Examples for {genre_id}:
- {imagery[0]}
- {imagery[1]}
- {imagery[2]}
Make the listener FEEL it through specific sensory details."""

    # Singability guidance
    singable_words = singable_word_examples(energy)
    singability_instruction = f"""SINGABILITY (how these words feel when sung):
- Use vowel sounds that hold notes: fire, light, alone, home, forever, tonight
- Strong consonants (b, d, k, p, t, m, n, l): broken, burning, heartbeat, midnight, paradise
- Target ~{syllable_target} syllables per line (leaves room for melody/breathing)
- Examples for this energy: {", ".join(singable_words[:4])}
- {phrase_guidance}
- Max {max_words} words per line—melody needs space"""

    return f"""You are a masterful songwriter crafting lyrics for "{title}" by {artist} ({genre_id}).

CONTEXT:
- Genre: {genre_id}, Mood: {mood}, Energy: {energy}
- Emotional arc: {arc} → {arc_instruction}
- Section: {section.upper()}

{section_instruction}

EMOTIONAL DELIVERY ({emotion_intensity}):
- Create a clear emotional journey
- Use sensory details, not labels
- Make listeners FEEL before they think

{singability_instruction}

{imagery_instruction}

STRUCTURE & MEMORABILITY:

{rhyme_guidance}

{title_guidance}

{refrain_guidance}

{cliche_guidance}

KEY PRINCIPLES:
1. WRITE FOR THE EAR FIRST—read aloud, check flow & singability
2. CONVERSATIONAL LANGUAGE: natural speech > poetic prose
3. ONE CLEAR IDEA PER LINE—don't overload
4. RHYTHM MATTERS: read it AS IF IT'S SUNG
5. EDIT RUTHLESSLY: every word must earn its place
6. EMOTION > RHYME: truth before technique
7. Leave breathing room so melody can shine

OUTPUT:
Write only the {section} lyrics—4-8 lines typically
No explanations, no meta-commentary
Just the powerful, singable, memorable words."""


def apply_songwriting_standards(
    lyrics: str, section: str, energy: EnergyLevel
) -> dict:
    """Evaluate generated lyrics against songwriting standards."""
    lines = lyrics.strip().split("\n")
    non_empty_lines = [l for l in lines if l.strip()]

    if not non_empty_lines:
        return {
            "lines_not_empty": False,
            "summary": "No non-empty lines found",
        }

    avg_words_per_line = sum(len(line.split()) for line in non_empty_lines) / len(
        non_empty_lines
    )
    max_words_in_line = max(len(line.split()) for line in non_empty_lines)

    checks = {
        "lines_not_empty": len(non_empty_lines) > 0,
        "avg_words_reasonable": 5 <= avg_words_per_line <= 15,
        "has_short_memorable_phrases": any(
            len(line.split()) <= 4 for line in non_empty_lines
        ),
        "avoids_excessive_length": max_words_in_line <= 20,
        "singability_score": (
            "excellent"
            if avg_words_per_line < 10 and max_words_in_line <= 12
            else "good"
            if avg_words_per_line <= 12
            else "fair"
        ),
        "summary": f"Avg {avg_words_per_line:.1f} words/line | {len(non_empty_lines)} lines | {max_words_in_line} max words",
    }
    return checks
