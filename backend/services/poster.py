"""
Event poster generation — Hugging Face Inference API.

Gemini (already used for search and event copy) can't do this: its free
tier has no image quota, and every image model there just returns 429.
Hugging Face's serverless Inference API has a genuinely free tier for
text-to-image, so posters use that instead — same graceful-degradation
pattern as the rest of the AI features: no key, no poster button, nothing
else breaks.
"""

import logging

import httpx

from core.config import settings

logger = logging.getLogger(__name__)

# Hugging Face retired the old api-inference.huggingface.co host and moved
# to a router that fronts several inference providers. Most free
# text-to-image models (SDXL, FLUX) have since been dropped from the
# free "hf-inference" provider entirely — this is the one still on it as
# of writing. Check https://huggingface.co/api/models?pipeline_tag=text-to-image&inference_provider=hf-inference
# if this starts returning 410/400.
MODEL = "stabilityai/stable-diffusion-3-medium-diffusers"
ENDPOINT = f"https://router.huggingface.co/hf-inference/models/{MODEL}"

# Image generation is slower than text — cold-starting a model on the
# free tier can take a while, so this gets a longer budget than the
# search/copy calls.
TIMEOUT_SECONDS = 90.0

MAX_BRIEF_CHARS = 200

# Image models reliably render fake, garbled text when asked for "no text"
# only in the negative prompt — putting it in the main prompt too, plus
# steering toward a purely visual scene, cuts that down a lot.
NEGATIVE_PROMPT = (
    "text, words, letters, writing, typography, caption, title, watermark, "
    "logo, signature, blurry, low quality, distorted, extra limbs, extra fingers"
)


def is_enabled() -> bool:
    """Frontend uses this to hide the poster button when no key is set."""
    return bool(settings.HUGGINGFACE_API_KEY)


def _prompt_for(brief: str) -> str:
    # Deliberately not asking for a "poster" — this model's training data
    # ties that word so strongly to typography-heavy designs that it draws
    # fake, garbled text over the image even with an explicit negative
    # prompt against it. Asking for a photograph of the event instead gets
    # a clean, text-free background image that still works as a poster.
    return (
        f"Cinematic photograph for an event: {brief}. "
        "Dramatic concert stage lighting, crowd silhouettes, vibrant colors, "
        "high detail, photorealistic, absolutely no text or writing anywhere, "
        "pure photography"
    )


def generate_poster(brief: str) -> bytes | None:
    """
    Generate a poster image from an organizer's brief.

    Returns raw JPEG/PNG bytes, or None if the feature is off, the brief
    is empty, or the call fails for any reason. Like the other AI calls in
    this project, this never raises — a poster is a nice-to-have on the
    create-event form, not something that should block event creation.
    """
    if not is_enabled():
        return None

    brief = brief.strip()[:MAX_BRIEF_CHARS]
    if not brief:
        return None

    try:
        res = httpx.post(
            ENDPOINT,
            headers={"Authorization": f"Bearer {settings.HUGGINGFACE_API_KEY}"},
            timeout=TIMEOUT_SECONDS,
            json={
                "inputs": _prompt_for(brief),
                "parameters": {"negative_prompt": NEGATIVE_PROMPT},
                # Wait for a cold model instead of getting an immediate
                # "still loading" error back.
                "options": {"wait_for_model": True},
            },
        )
        res.raise_for_status()
        if res.headers.get("content-type", "").startswith("image/"):
            return res.content
        # HF returns 200 with a JSON error body in a few edge cases
        # (e.g. queue full) instead of an actual HTTP error status.
        logger.warning("Poster request returned non-image content: %s", res.text[:200])
        return None
    except httpx.HTTPStatusError as exc:
        logger.warning("Poster generation: Hugging Face returned %s", exc.response.status_code)
        return None
    except Exception as exc:
        logger.warning("Poster generation failed: %s", type(exc).__name__)
        return None
