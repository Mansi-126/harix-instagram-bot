import hashlib
import io
import json
import math
import os
import re
import sys
import time
from datetime import datetime
from urllib.parse import quote

# google-genai must be on path: `pip install google-genai` or ./vendor_genai (pip install -t vendor_genai)
_vend = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor_genai")
if os.path.isfile(os.path.join(_vend, "google", "genai", "__init__.py")) and _vend not in sys.path:
    sys.path.insert(0, _vend)

import cloudinary
import cloudinary.uploader
import requests
import schedule
from dotenv import load_dotenv
import google.genai as genai
from google.genai import errors as genai_errors
from google.genai import types
from PIL import Image, ImageDraw, ImageFont

load_dotenv()

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
)

GEMINI_KEY = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
# Text: Flash fits free tier; set GEMINI_MODEL=gemini-2.5-pro if your plan includes it
GEMINI_TEXT_MODEL = (os.getenv("GEMINI_MODEL") or "gemini-2.5-flash").strip()
# After 429 / quota, try these in order (comma-separated)
GEMINI_TEXT_FALLBACK = (
    os.getenv("GEMINI_TEXT_FALLBACK") or "gemini-2.5-flash,gemini-2.0-flash"
).strip()
# Images: Nano Banana Pro first, then Nano Banana 2 (Flash Image preview)
GEMINI_IMAGE_PRIMARY = os.getenv("GEMINI_IMAGE_MODEL", "gemini-3-pro-image-preview")
GEMINI_IMAGE_FALLBACK = os.getenv("GEMINI_IMAGE_FALLBACK", "gemini-3.1-flash-image-preview")
GEMINI_IMAGE_SIZE = os.getenv("GEMINI_IMAGE_SIZE", "2K")

# hybrid (default): Flux/Pollinations illustration + branded overlay, then Gemini full image, then PIL
# gemini_only | pil_only | hybrid
IMAGE_GEN_STRATEGY = (os.getenv("IMAGE_GEN_STRATEGY") or "hybrid").strip().lower()
POLLINATIONS_MODEL = (os.getenv("POLLINATIONS_MODEL") or "flux").strip()
# canva_editorial = cream/dusty-rose business templates (default). legacy = older blue layouts.
POST_VISUAL_STYLE = (os.getenv("POST_VISUAL_STYLE") or "canva_editorial").strip().lower()

IG_TOKEN = (os.getenv("IG_ACCESS_TOKEN") or "").strip()
IG_BIZ_ID = (os.getenv("IG_BUSINESS_ID") or "").strip()

GRAPH_API_ROOT = "https://graph.facebook.com/v21.0"

HARIX = """
Company: Harix Global Solutions Pvt Ltd
Website: https://harixsolutions.com
Location: Bhavnagar, Gujarat, India
Services: Custom Software, AI Integration, Web Development,
eCommerce, Mobile Apps (iOS/Android), SEO, SMM
Stack: React, Next.js, Node.js, Python, AWS, Flutter
Tone: Professional, confident, growth-focused
Audience: Business owners, startups, SMEs
"""

SERVICES = [
    "custom software development",
    "AI integration and automation",
    "website development",
    "eCommerce solutions",
    "mobile app development",
    "SEO and digital marketing",
]

# Rotating “Instagram-native” formats — different layout + hook style each run (not the same blue slide).
POST_ARCHETYPES = [
    {
        "key": "split_isometric",
        "name": "Split + 3D / isometric scene",
        "instructions": "Asymmetrical split (about 45/55 or 50/50). One side: bold typography zone. Other side: isometric miniature scene (tiny desk, app windows, servers, growth chart as 3D objects) OR floating glass UI cards. Strong depth, shadows, rim light. Not flat.",
    },
    {
        "key": "magazine_cover",
        "name": "Magazine / poster cover",
        "instructions": "Mimic a premium tech magazine cover: dominant hero type, kicker line, optional small date strip, textured background (grain, soft photo, or paper). Logo as editorial mark. One hero visual (abstract or object), not empty gradient.",
    },
    {
        "key": "infographic_steps",
        "name": "3-step micro infographic",
        "instructions": "Three numbered steps or icons in a row or triangle with short labels tied to the headline. Clean Swiss-style spacing, connectors or arrows, one accent pop color. Feels save-worthy and skimmable.",
    },
    {
        "key": "bold_center_stack",
        "name": "Centered poster stack",
        "instructions": "Huge centered headline (2–3 lines max), subline beneath, CTA as pill button or underline. Background: radial glow, mesh gradient, or soft abstract shapes behind type — typography is the hero but background must have energy.",
    },
    {
        "key": "bento_cards",
        "name": "Bento grid cards",
        "instructions": "2x2 or asymmetric bento layout: mixed tiles (one with abstract pattern, one with icon cluster, one with stat-style number, one with headline). Rounded corners, subtle inner shadows. Modern product-marketing look.",
    },
    {
        "key": "myth_vs_reality",
        "name": "Myth vs truth split",
        "instructions": "Two labeled zones (MYTH / TRUTH or ❌ / ✓) contrasting statements visually. Distinct color accents per side, clear divider. Engaging, debate-style layout.",
    },
    {
        "key": "polaroid_memo",
        "name": "Polaroid / sticky memo",
        "instructions": "Main content inside a tilted white frame or sticky-note card with soft drop shadow on a textured desk or gradient backdrop. Handwritten-style accent optional for one word. Casual, human, feed-native.",
    },
    {
        "key": "dark_neon_tech",
        "name": "Dark mode neon accent",
        "instructions": "Near-black or deep charcoal base with neon cyan or electric blue accent lines, subtle grid or circuit motif, headline in high-contrast white. Cyber-product vibe without looking like a hacker movie cliché — keep premium.",
    },
]

IMAGE_HARD_NEGATIVES = """
STRICTLY AVOID (these make posts fail on Instagram):
• Plain full-frame navy/blue gradient with all text stacked on the left and empty dead space — looks like PowerPoint.
• Generic “corporate slide” or LinkedIn banner layout.
• Tiny body copy; headline must dominate.
REQUIRE: asymmetry OR clear focal illustration/3D/abstract viz OR editorial texture so the thumb stops scrolling.
"""

BRAND_TOP = (22, 88, 160)
BRAND_BOTTOM = (6, 28, 62)
ACCENT = (255, 214, 120)
TEXT_LIGHT = (240, 248, 255)
TEXT_MUTED = (176, 210, 245)

# Canva-style editorial business templates (cream + dusty rose, minimal)
CREAM = (252, 250, 245)
CREAM_DEEP = (245, 240, 232)
CREAM_CARD = (255, 253, 248)
DUST_ROSE = (218, 165, 170)
DUST_ROSE_LIGHT = (236, 205, 210)
DUST_ROSE_DEEP = (195, 130, 145)
CHARCOAL = (38, 36, 40)
INK = (26, 24, 28)
ED_MUTED = (95, 90, 94)
ED_ACCENT_LINE = (180, 130, 145)

_gemini_client: genai.Client | None = None


def get_gemini_client() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        if not GEMINI_KEY:
            raise RuntimeError("Set GEMINI_API_KEY (or GOOGLE_API_KEY) in .env")
        _gemini_client = genai.Client(api_key=GEMINI_KEY)
    return _gemini_client


def _text_models_sequence() -> list[str]:
    extra = [m.strip() for m in GEMINI_TEXT_FALLBACK.split(",") if m.strip()]
    out: list[str] = []
    seen: set[str] = set()
    for m in [GEMINI_TEXT_MODEL] + extra:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def _is_text_quota_exhausted(err: BaseException) -> bool:
    if isinstance(err, genai_errors.ClientError) and err.code == 429:
        return True
    s = str(err)
    return "429" in s and "RESOURCE_EXHAUSTED" in s


def parse_model_json(raw: str) -> dict:
    clean = raw.strip()
    clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s*```\s*$", "", clean)
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", clean)
        if m:
            return json.loads(m.group())
        raise


def _font_path_candidates(bold: bool) -> list:
    custom = (os.getenv("HARIX_FONT_PATH") or "").strip()
    out = []
    if custom and os.path.isfile(custom):
        out.append(custom)
    if sys.platform == "darwin":
        out += [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
            if bold
            else "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
        ]
    elif sys.platform == "win32":
        out += [r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf"]
    else:
        out += [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
    return out


def load_ttf(size: int, bold: bool = True) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _font_path_candidates(bold):
        try:
            if os.path.isfile(path):
                return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def wrap_lines(text: str, font: ImageFont.ImageFont, draw: ImageDraw.ImageDraw, max_width: int) -> list:
    words = text.split()
    if not words:
        return []
    lines, cur = [], words[0]
    for w in words[1:]:
        trial = cur + " " + w
        if draw.textlength(trial, font=font) <= max_width:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def fill_vertical_gradient(img: Image.Image, top_rgb: tuple, bottom_rgb: tuple) -> None:
    w, h = img.size
    draw = ImageDraw.Draw(img)
    r0, g0, b0 = top_rgb
    r1, g1, b1 = bottom_rgb
    for y in range(h):
        t = y / max(h - 1, 1)
        r = int(r0 + (r1 - r0) * t)
        g = int(g0 + (g1 - g0) * t)
        b = int(b0 + (b1 - b0) * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b))


def _resolve_logo_path() -> str | None:
    env = (os.getenv("HARIX_LOGO_PATH") or "").strip()
    if env and os.path.isfile(env):
        return env
    base = os.path.dirname(os.path.abspath(__file__))
    for name in ("Harix Global Solution Icon.jpg", "harix-logo.png", "logo.png"):
        candidate = os.path.join(base, name)
        if os.path.isfile(candidate):
            return candidate
    return None


def load_logo(max_px: int = 220) -> Image.Image | None:
    path = _resolve_logo_path()
    if not path:
        return None
    logo = Image.open(path).convert("RGBA")
    w, h = logo.size
    scale = min(max_px / max(w, 1), max_px / max(h, 1), 1.0)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    return logo.resize((nw, nh), Image.Resampling.LANCZOS)


def draw_text_block(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    max_width: int,
    lines: list,
    font: ImageFont.ImageFont,
    fill,
    line_gap: int = 12,
) -> int:
    cy = y
    for line in lines:
        draw.text((x, cy), line, font=font, fill=fill)
        cy += int(draw.textbbox((0, 0), line, font=font)[3]) + line_gap
    return cy


def pick_post_archetype() -> dict:
    """Stable-but-varying pick: changes by date, 4h block, and service focus."""
    service = SERVICES[datetime.now().weekday() % len(SERVICES)]
    now = datetime.now()
    salt = f"{now.date().isoformat()}|{now.hour // 4}|{service}|{now.isoweekday()}"
    idx = int(hashlib.sha256(salt.encode()).hexdigest(), 16) % len(POST_ARCHETYPES)
    return POST_ARCHETYPES[idx]


def get_viral_post_idea(archetype: dict) -> dict:
    service = SERVICES[datetime.now().weekday() % len(SERVICES)]
    today = datetime.now().strftime("%B %d, %Y")
    ak = archetype["key"]
    vis_palette = (
        "Palette: warm cream and ivory paper, dusty rose blush shapes, charcoal ink — organic blobs, subtle crosses/circles/hatching; abstract tech motifs only, NO human faces. NOT a flat blue gradient slide."
        if POST_VISUAL_STYLE == "canva_editorial"
        else "Palette: Harix blues #1859a5 / #0d3d6b, charcoal, white, optional gold accent — but layout must NOT be a flat blue gradient slide."
    )

    prompt = f"""Today is {today}. You are an elite Instagram strategist who studies what actually works on the feed (hooks, saves, comments) — not generic corporate marketing.
{HARIX}

Focus service area: {service}

ASSIGNED VISUAL FORMAT (must shape BOTH your copy energy AND the visual_scene — do not ignore):
Format name: {archetype["name"]}
Layout / art direction: {archetype["instructions"]}

Think like top business / tech creators: pattern interrupt, relatable pain, one clear takeaway, “save this” energy, optional mild humor, zero fluff.

Rules:
- First line of caption must STOP the scroll (bold claim, question, or “Most founders get this wrong…”).
- Teach ONE concrete thing or reframe ONE belief (helpful, not vague).
- Sound human; avoid brochure-speak like “leverage synergies”.
- Include a comment prompt (question) in the caption body — not only at the end.
- Headline/subline on-image must feel native to THIS format (not generic slogans repeated every week).

Return ONLY valid JSON (no markdown). Set post_format_key exactly to "{ak}".
{{
  "post_format_key": "{ak}",
  "topic": "short internal label",
  "trend_note": "specific 2025–2026 tech/business trend this ties to (one sentence)",
  "viral_angle": "thumb-stop reason (one sentence)",
  "value_nugget": "one specific actionable tip or reframe (max 25 words) — must appear or echo in caption",
  "caption": "Line1 = punchy hook. Then 3-6 short lines, line breaks. 2-4 emojis max, tasteful. Weave value_nugget. Mention Harix Global Solutions once naturally. Include one question inviting comments. Penultimate line: Visit harixsolutions.com | Link in bio",
  "hashtags": "18-28 hashtags separated by spaces, each starting with #",
  "headline": "MAX 7 words, Title Case — must fit assigned visual format",
  "subline": "MAX 16 words",
  "image_cta": "MAX 8 words — button-style or imperative",
  "visual_scene": "4-6 sentences: precise scene for an AI image generator implementing the ASSIGNED FORMAT above. Describe focal objects, composition, lighting, textures, where text blocks sit. {vis_palette}"
}}"""

    client = get_gemini_client()
    last_err: BaseException | None = None
    for model_id in _text_models_sequence():
        try:
            response = client.models.generate_content(model=model_id, contents=prompt)
            text = response.text
            if not text:
                raise RuntimeError("Empty response from text model")
            if model_id != GEMINI_TEXT_MODEL:
                print(f"Note: used text model {model_id} (primary {GEMINI_TEXT_MODEL} was unavailable).")
            return parse_model_json(text)
        except Exception as e:
            last_err = e
            if _is_text_quota_exhausted(e):
                print(f"Quota/rate limit on text model {model_id}, trying fallback…")
                continue
            raise
    if last_err:
        raise last_err
    raise RuntimeError("No text models configured")


def build_nano_banana_prompt(data: dict, has_logo: bool, archetype: dict) -> str:
    headline = data.get("headline") or "Build With Harix"
    subline = data.get("subline") or ""
    cta = data.get("image_cta") or "DM for a free consult"
    angle = data.get("viral_angle") or ""
    scene = data.get("visual_scene") or ""
    trend = data.get("trend_note") or ""
    nugget = data.get("value_nugget") or ""

    logo_clause = (
        "The FIRST attached image is the official Harix company logo. "
        "Integrate it as a design element (corner lockup, embossed card, or editorial masthead) — sharp, undistorted, correct colors."
        if has_logo
        else "No logo file supplied: use a clean typographic 'HARIX' wordmark only (no fake crests)."
    )
    editorial_style = ""
    if POST_VISUAL_STYLE == "canva_editorial":
        editorial_style = """
Overall look: Canva/Pinterest premium business template — cream and ivory base, dusty rose organic blobs, charcoal type, pill-shaped CTA,
fine geometric accents (small x-marks, circles, light diagonal hatching). Elegant B2B studio aesthetic; NO stock portraits or faces."""

    return f"""Design ONE square 1:1 Instagram FEED image for Harix Global Solutions (IT / software company, India).
This must look crafted for Instagram — thumb-stopping, save-worthy — NOT a LinkedIn slide or PowerPoint.
{editorial_style}

MANDATORY FORMAT (match this structure visually):
{archetype["name"]}
{archetype["instructions"]}

{IMAGE_HARD_NEGATIVES}

{logo_clause}

Context: {trend}
Hook idea: {angle}
Helpful angle to echo visually or in tiny type: {nugget}

Scene & style (follow closely):
{scene}

On-image copy (high contrast, large headline, perfect readability):
— Headline: "{headline}"
— Subline: "{subline}"
— CTA: "{cta}"
— Footer (small): harixsolutions.com · Bhavnagar, Gujarat, India

Output: one polished, publication-quality graphic. No watermarks, no "AI" labels, no competitor logos."""


def _image_models_sequence() -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in (GEMINI_IMAGE_PRIMARY, GEMINI_IMAGE_FALLBACK):
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def _generate_image_with_model(model_id: str, contents: list) -> bytes:
    config = types.GenerateContentConfig(
        response_modalities=["TEXT", "IMAGE"],
        image_config=types.ImageConfig(
            aspect_ratio="1:1",
            image_size=GEMINI_IMAGE_SIZE,
        ),
    )
    response = get_gemini_client().models.generate_content(
        model=model_id,
        contents=contents,
        config=config,
    )
    parts = response.parts
    if not parts:
        raise RuntimeError(f"No parts in image response ({model_id})")
    for part in parts:
        g_img = part.as_image()
        if g_img is not None and g_img.image_bytes:
            return g_img.image_bytes
    raise RuntimeError(f"No image bytes in response ({model_id})")


def generate_nano_banana_post_image(data: dict, archetype: dict) -> bytes:
    logo_path = _resolve_logo_path()
    prompt = build_nano_banana_prompt(data, has_logo=bool(logo_path), archetype=archetype)
    contents: list = [prompt]
    if logo_path:
        contents.append(Image.open(logo_path))

    last_err: Exception | None = None
    for model_id in _image_models_sequence():
        try:
            raw = _generate_image_with_model(model_id, contents)
            print(f"Image generated with {model_id} ({GEMINI_IMAGE_SIZE}, 1:1).")
            return ensure_square_instagram(raw)
        except Exception as e:
            last_err = e
            print(f"{model_id} failed ({e}); trying next image model if any…")

    if last_err:
        raise last_err
    raise RuntimeError("No image models configured")


def build_illustration_prompt(archetype: dict, data: dict) -> str:
    """Text-free scene for external / Imagen generators (they render type poorly)."""
    scene = (data.get("visual_scene") or "")[:520]
    trend = (data.get("trend_note") or "")[:220]
    angle = (data.get("viral_angle") or "")[:220]
    base = (
        "Cinematic 1:1 digital illustration, ultra detailed, high contrast, busy and rich composition, "
        "NOT empty, NOT a flat solid color field, NOT a blank slide. "
        "Absolutely NO text, NO letters, NO numbers as typography, NO watermark, NO UI screenshots, NO logos. "
        f"Emotional hook as pure visuals: {angle} "
        f"Trend atmosphere: {trend}. "
        f"Scene: {scene} "
        f"Format energy — {archetype['name']}: {archetype['instructions'][:420]} "
    )
    if POST_VISUAL_STYLE == "canva_editorial":
        return (
            base
            + "Style: minimalist Canva/Pinterest business template — warm cream and ivory paper tones, "
            "dusty rose blush organic blob shapes, soft desaturated shadows, subtle geometric accents "
            "(small crosses, fine circles, light hatching). Elegant B2B creative agency mood. "
            "Abstract soft shapes only — NO human faces, NO stock photos of people. "
            "Muted charcoal-friendly palette; NOT loud neon; NOT saturated corporate blue slide."
        )
    return (
        base
        + "Deep blue and teal atmosphere, gold rim light, glass, chrome, abstract 3D data flows, "
        "isometric micro-details, particles, volumetric light, editorial tech aesthetic 2026."
    )


def fetch_pollinations_background(archetype: dict, data: dict) -> bytes:
    prompt = build_illustration_prompt(archetype, data)
    seed = int(
        hashlib.sha256(
            f"{archetype['key']}|{data.get('headline', '')}|{datetime.now().isoformat()}".encode()
        ).hexdigest()[:12],
        16,
    ) % 999_999_991
    q = quote(prompt[:1900])
    url = (
        f"https://image.pollinations.ai/prompt/{q}"
        f"?width=1080&height=1080&seed={seed}&nologo=true&model={quote(POLLINATIONS_MODEL, safe='')}"
    )
    r = requests.get(url, timeout=180, headers={"User-Agent": "HarixInstagramBot/1.0"})
    r.raise_for_status()
    if len(r.content) < 2500:
        raise RuntimeError("Pollinations returned a trivial response")
    return r.content


def try_imagen_background(archetype: dict, data: dict) -> bytes | None:
    prompt = build_illustration_prompt(archetype, data)
    for model in ("imagen-4.0-generate-001", "imagen-3.0-generate-002"):
        try:
            resp = get_gemini_client().models.generate_images(
                model=model,
                prompt=prompt,
                config=types.GenerateImagesConfig(
                    number_of_images=1,
                    aspect_ratio="1:1",
                    output_mime_type="image/png",
                    negative_prompt="text, letters, words, typography, watermark, logo, blurry, empty, flat gradient only",
                ),
            )
            if not resp.generated_images:
                continue
            gi = resp.generated_images[0]
            if gi.image and gi.image.image_bytes:
                print(f"Imagen background OK ({model}).")
                return gi.image.image_bytes
        except Exception as e:
            print(f"Imagen {model} skipped: {e}")
    return None


def _draw_text_stroked(
    draw: ImageDraw.ImageDraw,
    xy: tuple,
    text: str,
    font: ImageFont.ImageFont,
    fill,
    stroke_fill=(0, 0, 0),
    stroke: int = 2,
) -> None:
    x, y = xy
    for dx in range(-stroke, stroke + 1):
        for dy in range(-stroke, stroke + 1):
            if dx == 0 and dy == 0:
                continue
            draw.text((x + dx, y + dy), text, font=font, fill=stroke_fill)
    draw.text((x, y), text, font=font, fill=fill)


def _canva_composite_template_id(archetype: dict, headline: str) -> int:
    raw = f"canva_overlay|{archetype['key']}|{headline}|{datetime.now().date().isoformat()}"
    return int(hashlib.sha256(raw.encode()).hexdigest(), 16) % 8


def _multiline_stroked(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    font: ImageFont.ImageFont,
    cw: int,
    fill,
    stroke_w: int,
    line_gap: int = 6,
) -> int:
    cy = y
    for line in wrap_lines(text.strip(), font, draw, cw):
        _draw_text_stroked(draw, (x, cy), line, font, fill, (0, 0, 0), stroke_w)
        cy += int(draw.textbbox((0, 0), line, font=font)[3]) + line_gap
    return cy


def _multiline_stroked_right(
    draw: ImageDraw.ImageDraw,
    right_x: int,
    y: int,
    text: str,
    font: ImageFont.ImageFont,
    cw: int,
    fill,
    stroke_w: int,
    line_gap: int = 6,
) -> int:
    cy = y
    for line in wrap_lines(text.strip(), font, draw, cw):
        bbox = draw.textbbox((0, 0), line, font=font)
        tw = bbox[2] - bbox[0]
        lx = right_x - tw
        _draw_text_stroked(draw, (lx, cy), line, font, fill, (0, 0, 0), stroke_w)
        cy += bbox[3] - bbox[1] + line_gap
    return cy


def _multiline_stroked_centered(
    draw: ImageDraw.ImageDraw,
    y: int,
    text: str,
    font: ImageFont.ImageFont,
    cw: int,
    fill,
    stroke_w: int,
    line_gap: int = 6,
    canvas_w: int = 1080,
) -> int:
    cy = y
    for line in wrap_lines(text.strip(), font, draw, cw):
        bbox = draw.textbbox((0, 0), line, font=font)
        tw = bbox[2] - bbox[0]
        x = (canvas_w - tw) // 2
        _draw_text_stroked(draw, (x, cy), line, font, fill, (0, 0, 0), stroke_w)
        cy += bbox[3] - bbox[1] + line_gap
    return cy


def _paste_logo_corner(img: Image.Image, corner: str, max_px: int = 108) -> None:
    lg = load_logo(max_px)
    if not lg:
        return
    w, h = img.size
    m = 36
    if corner == "tl":
        img.paste(lg, (m, m), lg)
    elif corner == "tr":
        img.paste(lg, (w - lg.size[0] - m, m), lg)
    elif corner == "bl":
        img.paste(lg, (m, h - lg.size[1] - m - 200), lg)
    else:
        img.paste(lg, (w - lg.size[0] - m, h - lg.size[1] - m - 200), lg)


def _editorial_soften_background(img: Image.Image) -> Image.Image:
    gray = img.convert("L").convert("RGB")
    return Image.blend(img, gray, 0.32).convert("RGB")


def _editorial_composite_template_id(archetype: dict, headline: str) -> int:
    raw = f"editorial_overlay|{archetype['key']}|{headline}|{datetime.now().date().isoformat()}"
    return int(hashlib.sha256(raw.encode()).hexdigest(), 16) % 6


def _multiline_editorial(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    font: ImageFont.ImageFont,
    cw: int,
    fill,
    line_gap: int = 7,
) -> int:
    cy = y
    for line in wrap_lines(text.strip(), font, draw, cw):
        _draw_text_stroked(draw, (x, cy), line, font, fill, (CREAM_CARD[0], CREAM_CARD[1], CREAM_CARD[2]), 1)
        cy += int(draw.textbbox((0, 0), line, font=font)[3]) + line_gap
    return cy


def _editorial_pill(
    draw: ImageDraw.ImageDraw, x: int, y: int, label: str, font: ImageFont.ImageFont, dark: bool = True
) -> None:
    bbox = draw.textbbox((0, 0), label, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    px, py = 32, 16
    x2, y2 = x + tw + px * 2, y + th + py * 2
    rad = (y2 - y) // 2
    if dark:
        draw.rounded_rectangle([x, y, x2, y2], radius=rad, fill=CHARCOAL)
        draw.text((x + px, y + py), label, font=font, fill=CREAM_CARD)
    else:
        draw.rounded_rectangle([x, y, x2, y2], radius=rad, outline=DUST_ROSE_DEEP, width=3, fill=CREAM_CARD)
        draw.text((x + px, y + py), label, font=font, fill=CHARCOAL)


def composite_editorial_branded_square(
    bg_bytes: bytes, headline: str, subline: str, cta: str, archetype: dict
) -> bytes:
    """Cream/rose Canva-style overlays on AI background — Harix business, no portrait required."""
    print("Canva editorial overlay (cream/rose) on generated background")
    base = Image.open(io.BytesIO(bg_bytes)).convert("RGB").resize((1080, 1080), Image.Resampling.LANCZOS)
    base = _editorial_soften_background(base)
    w, h = 1080, 1080
    tid = _editorial_composite_template_id(archetype, headline)
    glass = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glass)

    if tid == 0:
        gd.rectangle([0, 0, 540, h], fill=(*CREAM, 238))
        gd.line([(540, 0), (540, h)], fill=(*DUST_ROSE_DEEP, 220), width=4)
        gd.ellipse([520, 140, 1040, 620], fill=(*DUST_ROSE_LIGHT, 210))
        gd.ellipse([600, 400, 1000, 760], fill=(*DUST_ROSE, 180))
    elif tid == 1:
        gd.rectangle([540, 0, w, h], fill=(*CREAM, 240))
        gd.line([(540, 0), (540, h)], fill=(*DUST_ROSE_DEEP, 200), width=4)
        gd.ellipse([40, 100, 500, 560], fill=(*DUST_ROSE_LIGHT, 200))
        gd.ellipse([80, 420, 460, 780], fill=(*DUST_ROSE, 165))
    elif tid == 2:
        gd.rectangle([0, 0, w, 480], fill=(*DUST_ROSE_LIGHT, 215))
        gd.ellipse([200, 60, 880, 460], fill=(*DUST_ROSE, 190))
        gd.rectangle([0, 470, w, h], fill=(*CREAM_DEEP, 250))
        gd.line([(0, 470), (w, 470)], fill=(*CHARCOAL, 40), width=2)
    elif tid == 3:
        gd.polygon([(0, 0), (w, 0), (w, 380), (0, 520)], fill=(*CREAM, 245))
        gd.ellipse([620, 80, 1020, 480], fill=(*DUST_ROSE, 175))
        for y in range(520, h):
            a = int(30 + (y - 520) / (h - 520) * 200)
            gd.line([(0, y), (w, y)], fill=(*CREAM_DEEP, min(a, 248)))
    elif tid == 4:
        gd.rounded_rectangle([56, 120, w - 56, 780], radius=48, fill=(*CREAM_CARD, 248))
        gd.rounded_rectangle([56, 120, w - 56, 780], radius=48, outline=(*DUST_ROSE_DEEP, 200), width=3)
        gd.ellipse([720, 160, 1000, 440], fill=(*DUST_ROSE_LIGHT, 160))
    else:
        gd.rectangle([0, 0, 140, h], fill=(*DUST_ROSE_DEEP, 230))
        gd.rectangle([140, 0, w, h], fill=(*CREAM, 242))
        gd.ellipse([700, 200, 1040, 680], fill=(*DUST_ROSE_LIGHT, 185))

    out = Image.alpha_composite(base.convert("RGBA"), glass).convert("RGB")
    draw = ImageDraw.Draw(out)
    fh, fs, fc, ffoot = load_ttf(40, True), load_ttf(23, False), load_ttf(21, True), load_ttf(17, False)

    if tid == 0:
        _paste_logo_corner(out, "tl", 88)
        y = 200
        y = _multiline_editorial(draw, 56, y, headline, fh, 460, CHARCOAL, 8)
        y += 14
        y = _multiline_editorial(draw, 56, y, subline, fs, 460, ED_MUTED, 6)
        y += 20
        _editorial_pill(draw, 56, y, cta, fc, dark=True)
    elif tid == 1:
        _paste_logo_corner(out, "tr", 88)
        y = 180
        y = _multiline_editorial(draw, 580, y, headline, fh, 460, CHARCOAL, 8)
        y += 14
        y = _multiline_editorial(draw, 580, y, subline, fs, 460, ED_MUTED, 6)
        y += 20
        _editorial_pill(draw, 580, y, cta, fc, dark=True)
    elif tid == 2:
        _paste_logo_corner(out, "tl", 80)
        y = 520
        y = _multiline_stroked_centered(draw, y, headline, fh, 900, CHARCOAL, 1)
        y += 16
        y = _multiline_stroked_centered(draw, y, subline, fs, 820, ED_MUTED, 1)
        y += 22
        tb = draw.textbbox((0, 0), cta, font=fc)
        tw = tb[2] - tb[0]
        _editorial_pill(draw, (w - tw - 64) // 2, y, cta, fc, dark=False)
    elif tid == 3:
        _paste_logo_corner(out, "tl", 84)
        y = 560
        y = _multiline_editorial(draw, 64, y, headline, fh, 920, CHARCOAL, 8)
        y += 12
        y = _multiline_editorial(draw, 64, y, subline, fs, 880, ED_MUTED, 6)
        y += 18
        _editorial_pill(draw, 64, y, cta, fc, dark=True)
    elif tid == 4:
        _paste_logo_corner(out, "tl", 76)
        draw.text((88, 152), "HARIX", font=load_ttf(20, True), fill=DUST_ROSE_DEEP)
        y = 220
        y = _multiline_editorial(draw, 100, y, headline, load_ttf(38, True), 860, CHARCOAL, 8)
        y += 14
        y = _multiline_editorial(draw, 100, y, subline, fs, 800, ED_MUTED, 6)
        y += 22
        _editorial_pill(draw, 100, y, cta, fc, dark=True)
    else:
        _paste_logo_corner(out, "tl", 82)
        y = 160
        y = _multiline_editorial(draw, 180, y, headline, fh, 820, CHARCOAL, 8)
        y += 14
        y = _multiline_editorial(draw, 180, y, subline, fs, 780, ED_MUTED, 6)
        y += 22
        _editorial_pill(draw, 180, y, cta, fc, dark=False)

    _draw_footer_editorial(draw, w, h, 56, ffoot)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def composite_branded_square(bg_bytes: bytes, headline: str, subline: str, cta: str, archetype: dict) -> bytes:
    """Canva-style rotating overlays on AI background — different UI every pick."""
    base = Image.open(io.BytesIO(bg_bytes)).convert("RGB").resize((1080, 1080), Image.Resampling.LANCZOS)
    w, h = 1080, 1080
    tid = _canva_composite_template_id(archetype, headline)
    glass = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glass)

    if tid == 0:
        for y in range(320):
            a = int(95 * (1 - y / 320))
            gd.line([(0, y), (w, y)], fill=(0, 8, 28, a))
        gd.rounded_rectangle([32, 448, w - 32, 838], radius=40, fill=(8, 22, 52, 248))
        gd.rounded_rectangle([32, 448, w - 32, 838], radius=40, outline=(*ACCENT, 200), width=3)
    elif tid == 1:
        gd.rectangle([0, 0, 400, h], fill=(248, 250, 252, 235))
        gd.line([(400, 0), (400, h)], fill=(18, 65, 130, 255), width=8)
        for y in range(h):
            gd.line([(400, y), (w, y)], fill=(0, 0, 0, min(55, int(25 + y / 40))))
    elif tid == 2:
        cx, cy = w // 2, h // 2 - 40
        gw, gh = 880, 560
        gd.rounded_rectangle([cx - gw // 2, cy - gh // 2, cx + gw // 2, cy + gh // 2], radius=48, fill=(12, 28, 58, 240))
        gd.rounded_rectangle([cx - gw // 2, cy - gh // 2, cx + gw // 2, cy + gh // 2], radius=48, outline=(*ACCENT, 220), width=4)
    elif tid == 3:
        for y in range(int(h * 0.58), h):
            t = (y - h * 0.58) / (h * 0.42)
            a = int(80 + 140 * t)
            gd.line([(0, y), (w, y)], fill=(4, 18, 45, min(a, 245)))
        gd.line([(0, int(h * 0.58)), (w, int(h * 0.58))], fill=(*ACCENT, 255), width=5)
    elif tid == 4:
        gd.rectangle([w - 420, 0, w, h], fill=(6, 18, 42, 238))
        gd.line([(w - 420, 0), (w - 420, h)], fill=(*ACCENT, 255), width=6)
    elif tid == 5:
        gd.polygon([(0, 380), (w, 180), (w, h), (0, h)], fill=(10, 30, 60, 236))
        gd.line([(0, 380), (w, 180)], fill=(*ACCENT, 255), width=4)
    elif tid == 6:
        gd.rounded_rectangle([48, 420, w - 48, 560], radius=28, fill=(255, 255, 255, 228))
        gd.rounded_rectangle([88, 500, w - 88, 720], radius=28, fill=(18, 55, 110, 245))
    else:
        gd.rectangle([0, 0, w, 118], fill=(18, 65, 130, 252))
        gd.rounded_rectangle([40, 492, w - 40, 812], radius=38, fill=(10, 28, 58, 244))
        gd.rounded_rectangle([40, 492, w - 40, 812], radius=38, outline=(*ACCENT, 180), width=3)

    out = Image.alpha_composite(base.convert("RGBA"), glass).convert("RGB")
    draw = ImageDraw.Draw(out)
    fh, fs, fc, ffoot = load_ttf(42, True), load_ttf(24, False), load_ttf(22, True), load_ttf(18, False)
    print(f"Canva-style overlay template #{tid}")

    if tid == 0:
        _paste_logo_corner(out, "tl", 110)
        y = 478
        y = _multiline_stroked(draw, 72, y, headline, fh, 920, TEXT_LIGHT, 2, 8)
        y += 10
        y = _multiline_stroked(draw, 72, y, subline, fs, 920, TEXT_MUTED, 1, 6)
        y += 12
        _multiline_stroked(draw, 72, y, cta, fc, 920, ACCENT, 1, 6)
    elif tid == 1:
        _paste_logo_corner(out, "tl", 96)
        y = 120
        y = _multiline_stroked(draw, 48, y, headline, fh, 320, (12, 40, 90), 2, 8)
        y += 8
        y = _multiline_stroked(draw, 48, y, subline, fs, 320, (55, 65, 80), 1, 5)
        y += 10
        _multiline_stroked(draw, 48, y, cta, fc, 320, (18, 90, 160), 1, 5)
    elif tid == 2:
        _paste_logo_corner(out, "tr", 100)
        y = 340
        y = _multiline_stroked(draw, 120, y, headline, fh, 840, TEXT_LIGHT, 2, 8)
        y += 8
        y = _multiline_stroked(draw, 120, y, subline, fs, 840, TEXT_MUTED, 1, 6)
        y += 12
        _multiline_stroked(draw, 120, y, cta, fc, 840, ACCENT, 1, 6)
    elif tid == 3:
        _paste_logo_corner(out, "tl", 100)
        y = int(h * 0.58) + 36
        y = _multiline_stroked_centered(draw, y, headline, fh, 900, TEXT_LIGHT, 2)
        y += 14
        y = _multiline_stroked_centered(draw, y, subline, fs, 820, TEXT_MUTED, 1)
        y += 12
        _multiline_stroked_centered(draw, y, cta, fc, 700, ACCENT, 1)
    elif tid == 4:
        _paste_logo_corner(out, "tr", 96)
        cw = 360
        rx = w - 52
        y = 160
        y = _multiline_stroked_right(draw, rx, y, headline, fh, cw, TEXT_LIGHT, 2)
        y += 10
        y = _multiline_stroked_right(draw, rx, y, subline, fs, cw, TEXT_MUTED, 1)
        y += 12
        _multiline_stroked_right(draw, rx, y, cta, fc, cw, ACCENT, 1)
    elif tid == 5:
        _paste_logo_corner(out, "tl", 100)
        y = 620
        y = _multiline_stroked(draw, 56, y, headline, fh, 640, TEXT_LIGHT, 2, 8)
        y += 8
        y = _multiline_stroked(draw, 56, y, subline, fs, 640, TEXT_MUTED, 1, 6)
        y += 10
        _multiline_stroked(draw, 56, y, cta, fc, 640, ACCENT, 1, 6)
    elif tid == 6:
        _paste_logo_corner(out, "tl", 92)
        y = 440
        y = _multiline_stroked(draw, 80, y, headline, load_ttf(40, True), 920, (12, 45, 95), 1, 6)
        y = 528
        y = _multiline_stroked(draw, 120, y, subline, fs, 880, TEXT_LIGHT, 1, 6)
        y += 8
        _multiline_stroked(draw, 120, y, cta, fc, 880, ACCENT, 1, 6)
    else:
        _paste_logo_corner(out, "tl", 88)
        tb = draw.textbbox((0, 0), "HARIX GLOBAL", font=load_ttf(26, True))
        tw = tb[2] - tb[0]
        draw.text(((w - tw) // 2, 48), "HARIX GLOBAL", font=load_ttf(26, True), fill=(255, 255, 255))
        y = 528
        y = _multiline_stroked(draw, 64, y, headline, load_ttf(46, True), 920, TEXT_LIGHT, 2, 8)
        draw.line([(64, y + 8), (420, y + 8)], fill=ACCENT, width=5)
        y += 28
        y = _multiline_stroked(draw, 64, y, subline, fs, 900, TEXT_MUTED, 1, 6)
        y += 10
        _multiline_stroked(draw, 64, y, cta, fc, 900, ACCENT, 1, 6)

    _draw_footer_bar(draw, w, h, 52, ffoot)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def generate_social_image(data: dict, archetype: dict) -> bytes:
    """Pick generator by IMAGE_GEN_STRATEGY; default hybrid = interesting bg + sharp overlay."""
    headline = data.get("headline") or "Build Smarter With Harix"
    subline = data.get("subline") or "Software, AI & web that grows your business"
    image_cta = data.get("image_cta") or "DM us — free consult"
    layout_idx = _fallback_layout_index(archetype, headline)

    if IMAGE_GEN_STRATEGY == "pil_only":
        return create_branded_post_image(headline, subline, image_cta, layout_idx)

    if IMAGE_GEN_STRATEGY == "gemini_only":
        return generate_nano_banana_post_image(data, archetype)

    # --- hybrid ---
    for name, getter in (
        ("Pollinations+overlay", lambda: fetch_pollinations_background(archetype, data)),
        ("Imagen+overlay", lambda: try_imagen_background(archetype, data)),
    ):
        try:
            raw = getter()
            if raw:
                print(f"Image pipeline: {name}")
                if POST_VISUAL_STYLE == "canva_editorial":
                    return composite_editorial_branded_square(raw, headline, subline, image_cta, archetype)
                return composite_branded_square(raw, headline, subline, image_cta, archetype)
        except Exception as e:
            print(f"{name} failed ({e})")

    try:
        print("Image pipeline: Gemini native (Nano Banana)")
        return generate_nano_banana_post_image(data, archetype)
    except Exception as e:
        print(f"Gemini native image failed ({e})")

    print(f"Image pipeline: PIL template layout {layout_idx}")
    return create_branded_post_image(headline, subline, image_cta, layout_idx)


def ensure_square_instagram(raw_bytes: bytes, size: int = 1080) -> bytes:
    img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    if img.size != (size, size):
        img = img.resize((size, size), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _draw_footer_bar(draw: ImageDraw.ImageDraw, w: int, h: int, margin: int, font_small: ImageFont.ImageFont) -> None:
    footer_top = 820
    draw.rectangle([0, footer_top, w, h], fill=(4, 18, 40))
    draw.line([0, footer_top, w, footer_top], fill=ACCENT, width=3)
    fy = footer_top + 28
    draw.text((margin, fy), "Harix Global Solutions Pvt Ltd", font=font_small, fill=TEXT_LIGHT)
    fy += 30
    draw.text((margin, fy), "harixsolutions.com  ·  Bhavnagar, Gujarat, India", font=font_small, fill=TEXT_MUTED)
    fy += 30
    draw.text(
        (margin, fy),
        "Custom Software  ·  AI  ·  Web  ·  Mobile",
        font=font_small,
        fill=TEXT_MUTED,
    )


def _draw_footer_editorial(draw: ImageDraw.ImageDraw, w: int, h: int, margin: int, font_small: ImageFont.ImageFont) -> None:
    footer_top = 888
    draw.rectangle([0, footer_top, w, h], fill=CREAM_DEEP)
    draw.line([0, footer_top, w, footer_top], fill=DUST_ROSE_DEEP, width=3)
    fy = footer_top + 20
    draw.text((margin, fy), "Harix Global Solutions Pvt Ltd", font=font_small, fill=CHARCOAL)
    fy += 26
    draw.text((margin, fy), "harixsolutions.com  ·  Bhavnagar, Gujarat, India", font=font_small, fill=ED_MUTED)
    fy += 24
    draw.text((margin, fy), "Custom Software  ·  AI  ·  Web  ·  Mobile", font=font_small, fill=ED_MUTED)


def _pil_layout_classic(headline: str, subline: str, image_cta: str) -> Image.Image:
    w, h = 1080, 1080
    img = Image.new("RGB", (w, h))
    fill_vertical_gradient(img, BRAND_TOP, BRAND_BOTTOM)
    draw = ImageDraw.Draw(img)
    for cx, cy, r in ((820, 320, 180), (940, 520, 120), (720, 640, 90)):
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=ACCENT, width=5)
        draw.ellipse(
            [cx - r + 14, cy - r + 14, cx + r - 14, cy + r - 14],
            outline=(100, 160, 220),
            width=2,
        )
    margin, content_w = 64, 620
    logo = load_logo(160)
    y = margin
    if logo:
        img.paste(logo, (margin, margin), logo)
        y = margin + logo.size[1] + 28
    else:
        draw.rounded_rectangle([margin, margin, margin + 180, margin + 64], radius=10, fill=(35, 75, 130), outline=ACCENT, width=2)
        draw.text((margin + 16, margin + 14), "HARIX", font=load_ttf(36, bold=True), fill=ACCENT)
        y = margin + 88
    fh, fs, fc, fsm = load_ttf(52, True), load_ttf(28, False), load_ttf(26, True), load_ttf(20, False)
    y = draw_text_block(draw, margin, y, content_w, wrap_lines(headline, fh, draw, content_w), fh, TEXT_LIGHT, 6)
    y += 14
    y = draw_text_block(draw, margin, y, content_w, wrap_lines(subline, fs, draw, content_w), fs, TEXT_MUTED, 5)
    y += 18
    draw_text_block(draw, margin, y, content_w, wrap_lines(image_cta, fc, draw, content_w), fc, ACCENT, 4)
    draw.rectangle([640, 120, 1040, 720], fill=(20, 55, 110), outline=ACCENT, width=2)
    draw.rounded_rectangle([680, 180, 1000, 380], radius=16, fill=(40, 90, 150))
    draw.rounded_rectangle([680, 420, 1000, 560], radius=16, fill=(30, 70, 125))
    _draw_footer_bar(draw, w, h, margin, fsm)
    return img


def _pil_layout_split(headline: str, subline: str, image_cta: str) -> Image.Image:
    w, h = 1080, 1080
    img = Image.new("RGB", (w, h), (14, 22, 38))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 420, h], fill=(8, 32, 72))
    draw.line([420, 0, 420, h], fill=ACCENT, width=6)
    for i, y0 in enumerate(range(80, 720, 140)):
        x0, x1 = 460, 1020
        y1 = y0 + 100
        base = (25 + i * 8, 60 + i * 5, 120 + i * 4)
        draw.rounded_rectangle([x0, y0, x1, y1], radius=20, fill=base)
        for t in range(-200, 900, 14):
            draw.line([(x0 + t, y0), (x0 + t + 80, y1)], fill=(min(255, base[0] + 40), min(255, base[1] + 35), min(255, base[2] + 30)), width=2)
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        draw.ellipse([cx - 22, cy - 22, cx + 22, cy + 22], outline=ACCENT, width=3)
        draw.ellipse([cx - 10, cy - 10, cx + 10, cy + 10], fill=(240, 248, 255))
    margin, content_w = 48, 340
    y = 72
    logo = load_logo(100)
    if logo:
        img.paste(logo, (margin, y), logo)
        y += logo.size[1] + 24
    fh, fs, fc, fsm = load_ttf(40, True), load_ttf(22, False), load_ttf(22, True), load_ttf(18, False)
    y = draw_text_block(draw, margin, y, content_w, wrap_lines(headline, fh, draw, content_w), fh, TEXT_LIGHT, 5)
    y += 10
    y = draw_text_block(draw, margin, y, content_w, wrap_lines(subline, fs, draw, content_w), fs, TEXT_MUTED, 4)
    y += 12
    draw_text_block(draw, margin, y, content_w, wrap_lines(image_cta, fc, draw, content_w), fc, ACCENT, 3)
    _draw_footer_bar(draw, w, h, 48, fsm)
    return img


def _pil_layout_center_poster(headline: str, subline: str, image_cta: str) -> Image.Image:
    w, h = 1080, 1080
    img = Image.new("RGB", (w, h))
    fill_vertical_gradient(img, (10, 25, 55), (30, 80, 150))
    draw = ImageDraw.Draw(img)
    draw.ellipse([-120, 200, 520, 920], fill=(50, 110, 190))
    draw.ellipse([600, -80, 1180, 500], fill=(25, 70, 130))
    fh, fs, fc, fsm = load_ttf(54, True), load_ttf(30, False), load_ttf(28, True), load_ttf(20, False)

    def cx_text(text: str, font: ImageFont.ImageFont, y: int, fill) -> int:
        lines = wrap_lines(text, font, draw, 920)
        cy = y
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            tw = bbox[2] - bbox[0]
            draw.text(((w - tw) // 2, cy), line, font=font, fill=fill)
            cy += bbox[3] - bbox[1] + 10
        return cy

    y = 100
    lg = load_logo(90)
    if lg:
        img.paste(lg, ((w - lg.size[0]) // 2, y), lg)
        y += lg.size[1] + 36
    y = cx_text(headline, fh, y, TEXT_LIGHT)
    y += 8
    y = cx_text(subline, fs, y, TEXT_MUTED)
    y += 16
    pill_y = y
    bbox = draw.textbbox((0, 0), image_cta, font=fc)
    pw = bbox[2] - bbox[0] + 80
    px = (w - pw) // 2
    draw.rounded_rectangle([px, pill_y, px + pw, pill_y + 56], radius=28, fill=ACCENT)
    draw.text((px + 40, pill_y + 14), image_cta, font=fc, fill=(20, 30, 50))
    _draw_footer_bar(draw, w, h, 64, fsm)
    return img


def _pil_layout_topbar(headline: str, subline: str, image_cta: str) -> Image.Image:
    w, h = 1080, 1080
    img = Image.new("RGB", (w, h), (245, 248, 252))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, w, 300], fill=(18, 65, 130))
    draw.polygon([(0, 300), (w, 260), (w, 300)], fill=(245, 248, 252))
    fh, fs, fc, fsm = load_ttf(48, True), load_ttf(26, False), load_ttf(26, True), load_ttf(19, False)
    y = 72
    for line in wrap_lines(headline, fh, draw, 920):
        bbox = draw.textbbox((0, 0), line, font=fh)
        tw = bbox[2] - bbox[0]
        draw.text(((w - tw) // 2, y), line, font=fh, fill=(255, 255, 255))
        y += bbox[3] - bbox[1] + 6
    y = 340
    margin, cw = 72, 936
    y = draw_text_block(draw, margin, y, cw, wrap_lines(subline, fs, draw, cw), fs, (35, 45, 65), 6)
    y += 20
    draw_text_block(draw, margin, y, cw, wrap_lines(image_cta, fc, draw, cw), fc, (18, 90, 160), 5)
    for i in range(6):
        draw.rounded_rectangle([72 + i * 160, 620, 200 + i * 160, 760], radius=12, outline=(180, 195, 215), width=3)
    _draw_footer_bar(draw, w, h, 64, fsm)
    return img


def _pil_layout_bento(headline: str, subline: str, image_cta: str) -> Image.Image:
    w, h = 1080, 1080
    img = Image.new("RGB", (w, h), (232, 236, 242))
    draw = ImageDraw.Draw(img)
    cells = [(48, 48, 520, 380), (584, 48, 1032, 280), (584, 308, 1032, 520), (48, 412, 520, 780)]
    for i, box in enumerate(cells):
        draw.rounded_rectangle(box, radius=24, fill=(255, 255, 255), outline=(200, 210, 225), width=2)
    fh, fs, fc, fsm = load_ttf(38, True), load_ttf(24, False), load_ttf(22, True), load_ttf(18, False)
    draw_text_block(draw, 80, 100, 420, wrap_lines(headline, fh, draw, 420), fh, (12, 50, 100), 6)
    draw_text_block(draw, 80, 300, 420, wrap_lines(subline, fs, draw, 420), fs, (70, 80, 95), 5)
    draw.text((620, 100), "01", font=load_ttf(72, True), fill=(18, 80, 160))
    draw.text((620, 360), "TIP", font=fc, fill=ACCENT)
    draw_text_block(draw, 620, 400, 380, wrap_lines(image_cta, fc, draw, 380), fc, (30, 40, 55), 4)
    _draw_footer_bar(draw, w, h, 48, fsm)
    return img


def _pil_canva_diagonal(headline: str, subline: str, image_cta: str) -> Image.Image:
    w, h = 1080, 1080
    img = Image.new("RGB", (w, h), (18, 42, 88))
    draw = ImageDraw.Draw(img)
    draw.polygon([(0, 0), (w, 0), (w, 520), (0, 720)], fill=(32, 95, 168))
    draw.polygon([(0, 720), (w, 520), (w, h), (0, h)], fill=(8, 24, 52))
    draw.line([(0, 720), (w, 520)], fill=ACCENT, width=8)
    fh, fs, fc, fsm = load_ttf(46, True), load_ttf(24, False), load_ttf(22, True), load_ttf(18, False)
    lg = load_logo(100)
    if lg:
        img.paste(lg, (48, 48), lg)
    y = 200
    y = draw_text_block(draw, 56, y, 920, wrap_lines(headline, fh, draw, 920), fh, (255, 255, 255), 7)
    y += 16
    y = draw_text_block(draw, 56, y, 880, wrap_lines(subline, fs, draw, 880), fs, (200, 225, 250), 5)
    y += 18
    draw.rounded_rectangle([56, y, 56 + 420, y + 52], radius=26, fill=ACCENT)
    draw_text_block(draw, 72, y + 10, 400, wrap_lines(image_cta, fc, draw, 400), fc, (15, 25, 45), 4)
    _draw_footer_bar(draw, w, h, 48, fsm)
    return img


def _pil_canva_ribbon_right(headline: str, subline: str, image_cta: str) -> Image.Image:
    w, h = 1080, 1080
    img = Image.new("RGB", (w, h), (236, 240, 247))
    draw = ImageDraw.Draw(img)
    draw.rectangle([600, 0, w, h], fill=(14, 52, 118))
    draw.polygon([(600, 0), (660, 0), (600, 200)], fill=ACCENT)
    fh, fs, fc, fsm = load_ttf(40, True), load_ttf(23, False), load_ttf(21, True), load_ttf(18, False)
    y = 80
    y = draw_text_block(draw, 48, y, 520, wrap_lines(headline, fh, draw, 520), fh, (10, 45, 95), 6)
    y += 12
    y = draw_text_block(draw, 48, y, 520, wrap_lines(subline, fs, draw, 520), fs, (60, 70, 88), 5)
    y += 14
    draw_text_block(draw, 48, y, 520, wrap_lines(image_cta, fc, draw, 520), fc, (18, 85, 160), 5)
    cx = 780
    for r, col in ((180, (40, 90, 160)), (120, (60, 120, 190)), (55, (200, 220, 255))):
        draw.ellipse([cx - r, 320 - r, cx + r, 320 + r], fill=col)
    _draw_footer_bar(draw, w, h, 48, fsm)
    return img


def _pil_canva_circle_hero(headline: str, subline: str, image_cta: str) -> Image.Image:
    w, h = 1080, 1080
    img = Image.new("RGB", (w, h), (245, 248, 252))
    draw = ImageDraw.Draw(img)
    draw.ellipse([60, 80, 620, 640], fill=(22, 78, 150))
    draw.ellipse([100, 120, 580, 600], outline=ACCENT, width=6)
    fh, fs, fc, fsm = load_ttf(36, True), load_ttf(22, False), load_ttf(21, True), load_ttf(18, False)
    draw_text_block(draw, 660, 140, 380, wrap_lines(headline, fh, draw, 380), fh, (12, 50, 100), 6)
    draw_text_block(draw, 660, 300, 380, wrap_lines(subline, fs, draw, 380), fs, (70, 80, 95), 5)
    draw_text_block(draw, 660, 460, 380, wrap_lines(image_cta, fc, draw, 380), fc, (18, 90, 160), 5)
    draw.text((220, 280), "◆", font=load_ttf(100, True), fill=(255, 255, 255))
    _draw_footer_bar(draw, w, h, 48, fsm)
    return img


def _pil_canva_duotone_stack(headline: str, subline: str, image_cta: str) -> Image.Image:
    w, h = 1080, 1080
    img = Image.new("RGB", (w, h), (10, 28, 62))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, w, 420], fill=(28, 88, 160))
    draw.rectangle([0, 420, w, 780], fill=(12, 40, 85))
    draw.line([(0, 420), (w, 420)], fill=ACCENT, width=6)
    fh, fs, fc, fsm = load_ttf(44, True), load_ttf(24, False), load_ttf(22, True), load_ttf(18, False)
    y = 100
    for line in wrap_lines(headline, fh, draw, 920):
        bbox = draw.textbbox((0, 0), line, font=fh)
        tw = bbox[2] - bbox[0]
        draw.text(((w - tw) // 2, y), line, font=fh, fill=(255, 255, 255))
        y += bbox[3] - bbox[1] + 8
    y = 460
    y = draw_text_block(draw, 72, y, 936, wrap_lines(subline, fs, draw, 936), fs, (200, 220, 245), 6)
    y += 20
    draw_text_block(draw, 72, y, 936, wrap_lines(image_cta, fc, draw, 936), fc, ACCENT, 5)
    _draw_footer_bar(draw, w, h, 56, fsm)
    return img


def _pil_canva_corner_frames(headline: str, subline: str, image_cta: str) -> Image.Image:
    w, h = 1080, 1080
    img = Image.new("RGB", (w, h), (248, 250, 253))
    draw = ImageDraw.Draw(img)
    for x0, y0 in ((40, 40), (w - 240, 40), (40, h - 260), (w - 240, h - 260)):
        draw.rectangle([x0, y0, x0 + 200, y0 + 200], outline=(18, 65, 130), width=5)
    draw.line([(100, 100), (980, 980)], fill=(220, 230, 240), width=2)
    fh, fs, fc, fsm = load_ttf(42, True), load_ttf(24, False), load_ttf(22, True), load_ttf(18, False)
    y = 280
    for line in wrap_lines(headline, fh, draw, 800):
        bbox = draw.textbbox((0, 0), line, font=fh)
        tw = bbox[2] - bbox[0]
        draw.text(((w - tw) // 2, y), line, font=fh, fill=(12, 45, 95))
        y += bbox[3] - bbox[1] + 8
    y += 20
    y = draw_text_block(draw, 140, y, 800, wrap_lines(subline, fs, draw, 800), fs, (70, 80, 95), 6)
    y += 16
    draw_text_block(draw, 140, y, 800, wrap_lines(image_cta, fc, draw, 800), fc, (18, 90, 160), 5)
    _draw_footer_bar(draw, w, h, 56, fsm)
    return img


def _pil_canva_wave_bottom(headline: str, subline: str, image_cta: str) -> Image.Image:
    w, h = 1080, 1080
    img = Image.new("RGB", (w, h), (20, 55, 120))
    draw = ImageDraw.Draw(img)
    pts = [(0, 480)]
    for x in range(0, w + 40, 40):
        pts.append((x, 420 + int(35 * math.sin(x / 90))))
    pts.append((w, h))
    pts.append((0, h))
    draw.polygon(pts, fill=(8, 28, 58))
    fh, fs, fc, fsm = load_ttf(46, True), load_ttf(25, False), load_ttf(23, True), load_ttf(18, False)
    lg = load_logo(96)
    if lg:
        img.paste(lg, (48, 48), lg)
    y = 120
    y = draw_text_block(draw, 56, y, 968, wrap_lines(headline, fh, draw, 968), fh, TEXT_LIGHT, 7)
    y += 14
    y = draw_text_block(draw, 56, y, 900, wrap_lines(subline, fs, draw, 900), fs, TEXT_MUTED, 6)
    y += 18
    draw_text_block(draw, 56, y, 880, wrap_lines(image_cta, fc, draw, 880), fc, ACCENT, 5)
    _draw_footer_bar(draw, w, h, 48, fsm)
    return img


def _pil_canva_sidebar_cards(headline: str, subline: str, image_cta: str) -> Image.Image:
    w, h = 1080, 1080
    img = Image.new("RGB", (w, h), (230, 235, 242))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([40, 40, 380, 780], radius=32, fill=(255, 255, 255), outline=(190, 200, 215), width=2)
    draw.rounded_rectangle([420, 60, 1040, 280], radius=24, fill=(18, 65, 130))
    draw.rounded_rectangle([420, 320, 1040, 520], radius=24, fill=(255, 255, 255), outline=(18, 65, 130), width=3)
    fh, fs, fc, fsm = load_ttf(34, True), load_ttf(22, False), load_ttf(21, True), load_ttf(18, False)
    draw_text_block(draw, 72, 100, 280, wrap_lines(headline, fh, draw, 280), fh, (10, 45, 95), 6)
    draw_text_block(draw, 72, 360, 280, wrap_lines(subline, fs, draw, 280), fs, (70, 80, 95), 5)
    draw_text_block(draw, 72, 560, 280, wrap_lines(image_cta, fc, draw, 280), fc, (18, 90, 160), 5)
    draw.text((480, 120), "SAVE THIS", font=load_ttf(22, True), fill=(220, 235, 255))
    draw.text((480, 170), "→", font=load_ttf(56, True), fill=ACCENT)
    for i, col in enumerate([(200, 220, 245), (170, 200, 230), (140, 180, 220)]):
        draw.rounded_rectangle([460, 340 + i * 52, 1000, 380 + i * 52], radius=8, fill=col)
    _draw_footer_bar(draw, w, h, 48, fsm)
    return img


CANVA_PIL_LAYOUTS = [
    _pil_layout_classic,
    _pil_layout_split,
    _pil_layout_center_poster,
    _pil_layout_topbar,
    _pil_layout_bento,
    _pil_canva_diagonal,
    _pil_canva_ribbon_right,
    _pil_canva_circle_hero,
    _pil_canva_duotone_stack,
    _pil_canva_corner_frames,
    _pil_canva_wave_bottom,
    _pil_canva_sidebar_cards,
]


def _pil_editorial_left_panel(headline: str, subline: str, image_cta: str) -> Image.Image:
    w, h = 1080, 1080
    img = Image.new("RGB", (w, h), CREAM)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 548, h], fill=CREAM_CARD)
    draw.line([(548, 0), (548, 880)], fill=DUST_ROSE_DEEP, width=4)
    draw.ellipse([500, 100, 1040, 560], fill=DUST_ROSE_LIGHT)
    draw.ellipse([560, 360, 1020, 820], fill=DUST_ROSE)
    for x0, y0 in ((420, 720), (460, 760), (420, 800)):
        draw.line([(x0, y0), (x0 + 14, y0 + 14)], fill=ED_ACCENT_LINE, width=2)
        draw.line([(x0 + 14, y0), (x0, y0 + 14)], fill=ED_ACCENT_LINE, width=2)
    _paste_logo_corner(img, "tl", 90)
    fh, fs, fc, ff = load_ttf(42, True), load_ttf(24, False), load_ttf(22, True), load_ttf(17, False)
    y = 200
    y = draw_text_block(draw, 52, y, 460, wrap_lines(headline, fh, draw, 460), fh, CHARCOAL, 8)
    y += 12
    y = draw_text_block(draw, 52, y, 460, wrap_lines(subline, fs, draw, 460), fs, ED_MUTED, 6)
    y += 18
    _editorial_pill(draw, 52, y, image_cta, fc, dark=True)
    _draw_footer_editorial(draw, w, h, 52, ff)
    return img


def _pil_editorial_right_panel(headline: str, subline: str, image_cta: str) -> Image.Image:
    w, h = 1080, 1080
    img = Image.new("RGB", (w, h), CREAM)
    draw = ImageDraw.Draw(img)
    draw.rectangle([532, 0, w, h], fill=CREAM_CARD)
    draw.line([(532, 0), (532, 880)], fill=DUST_ROSE_DEEP, width=4)
    draw.ellipse([40, 120, 500, 540], fill=DUST_ROSE_LIGHT)
    draw.ellipse([80, 400, 480, 800], fill=DUST_ROSE)
    draw.ellipse([60, 60, 120, 120], outline=DUST_ROSE_DEEP, width=2)
    draw.ellipse([140, 80, 188, 128], outline=ED_ACCENT_LINE, width=2)
    _paste_logo_corner(img, "tr", 90)
    fh, fs, fc, ff = load_ttf(42, True), load_ttf(24, False), load_ttf(22, True), load_ttf(17, False)
    y = 200
    y = draw_text_block(draw, 572, y, 460, wrap_lines(headline, fh, draw, 460), fh, CHARCOAL, 8)
    y += 12
    y = draw_text_block(draw, 572, y, 460, wrap_lines(subline, fs, draw, 460), fs, ED_MUTED, 6)
    y += 18
    _editorial_pill(draw, 572, y, image_cta, fc, dark=True)
    _draw_footer_editorial(draw, w, h, 52, ff)
    return img


def _pil_editorial_rose_band(headline: str, subline: str, image_cta: str) -> Image.Image:
    w, h = 1080, 1080
    img = Image.new("RGB", (w, h), CREAM_DEEP)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, w, 420], fill=DUST_ROSE_LIGHT)
    draw.ellipse([180, 40, 900, 400], fill=DUST_ROSE)
    for i in range(0, 200, 14):
        draw.line([(840 + i, 60), (880 + i, 200)], fill=(255, 252, 250), width=1)
    draw.rectangle([0, 410, w, h], fill=CREAM)
    draw.line([(0, 410), (w, 410)], fill=CHARCOAL, width=2)
    _paste_logo_corner(img, "tl", 82)
    fh, fs, fc, ff = load_ttf(46, True), load_ttf(25, False), load_ttf(22, True), load_ttf(17, False)
    y = 460
    for line in wrap_lines(headline, fh, draw, 920):
        bbox = draw.textbbox((0, 0), line, font=fh)
        tw = bbox[2] - bbox[0]
        draw.text(((w - tw) // 2, y), line, font=fh, fill=CHARCOAL)
        y += bbox[3] - bbox[1] + 8
    y += 14
    y = draw_text_block(draw, 72, y, 936, wrap_lines(subline, fs, draw, 936), fs, ED_MUTED, 6)
    y += 20
    tb = draw.textbbox((0, 0), image_cta, font=fc)
    tw = tb[2] - tb[0]
    _editorial_pill(draw, (w - tw - 64) // 2, y, image_cta, fc, dark=False)
    _draw_footer_editorial(draw, w, h, 56, ff)
    return img


def _pil_editorial_card_stack(headline: str, subline: str, image_cta: str) -> Image.Image:
    w, h = 1080, 1080
    img = Image.new("RGB", (w, h), CREAM)
    draw = ImageDraw.Draw(img)
    draw.ellipse([680, 80, 1040, 400], fill=DUST_ROSE_LIGHT)
    for ox, oy in [(48, 100), (76, 100), (48, 128)]:
        draw.line([(ox, oy), (ox + 12, oy + 12)], fill=ED_ACCENT_LINE, width=2)
        draw.line([(ox + 12, oy), (ox, oy + 12)], fill=ED_ACCENT_LINE, width=2)
    draw.rounded_rectangle([64, 140, w - 64, 760], radius=44, fill=CREAM_CARD)
    draw.rounded_rectangle([64, 140, w - 64, 760], radius=44, outline=DUST_ROSE_DEEP, width=3)
    _paste_logo_corner(img, "tl", 76)
    fh, fs, fc, ff = load_ttf(40, True), load_ttf(23, False), load_ttf(21, True), load_ttf(17, False)
    y = 220
    y = draw_text_block(draw, 112, y, 856, wrap_lines(headline, fh, draw, 856), fh, CHARCOAL, 8)
    y += 14
    y = draw_text_block(draw, 112, y, 820, wrap_lines(subline, fs, draw, 820), fs, ED_MUTED, 6)
    y += 22
    _editorial_pill(draw, 112, y, image_cta, fc, dark=True)
    _draw_footer_editorial(draw, w, h, 56, ff)
    return img


def _pil_editorial_sidebar_strip(headline: str, subline: str, image_cta: str) -> Image.Image:
    w, h = 1080, 1080
    img = Image.new("RGB", (w, h), CREAM_CARD)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 132, h], fill=DUST_ROSE_DEEP)
    draw.rectangle([132, 0, w, h], fill=CREAM)
    draw.ellipse([720, 200, 1040, 680], fill=DUST_ROSE_LIGHT)
    draw.ellipse([760, 420, 1000, 760], fill=DUST_ROSE)
    _paste_logo_corner(img, "tl", 72)
    fh, fs, fc, ff = load_ttf(44, True), load_ttf(24, False), load_ttf(22, True), load_ttf(17, False)
    y = 160
    y = draw_text_block(draw, 168, y, 860, wrap_lines(headline, fh, draw, 860), fh, CHARCOAL, 8)
    y += 12
    y = draw_text_block(draw, 168, y, 820, wrap_lines(subline, fs, draw, 820), fs, ED_MUTED, 6)
    y += 20
    _editorial_pill(draw, 168, y, image_cta, fc, dark=False)
    _draw_footer_editorial(draw, w, h, 56, ff)
    return img


def _pil_editorial_minimal_grid(headline: str, subline: str, image_cta: str) -> Image.Image:
    w, h = 1080, 1080
    img = Image.new("RGB", (w, h), CREAM_DEEP)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([72, 96, w - 72, 780], radius=36, fill=CREAM_CARD, outline=DUST_ROSE_DEEP, width=2)
    for gx in range(100, 220, 36):
        for gy in range(120, 200, 36):
            draw.ellipse([gx, gy, gx + 6, gy + 6], fill=DUST_ROSE)
    bx, by, bw, bh = w - 280, 120, 200, 140
    for i in range(0, bh, 10):
        draw.line([(bx, by + i), (bx + bw, by + i + 40)], fill=DUST_ROSE_LIGHT, width=1)
    _paste_logo_corner(img, "tl", 88)
    fh, fs, fc, ff = load_ttf(42, True), load_ttf(24, False), load_ttf(22, True), load_ttf(17, False)
    y = 200
    y = draw_text_block(draw, 120, y, 840, wrap_lines(headline, fh, draw, 840), fh, CHARCOAL, 8)
    y += 12
    y = draw_text_block(draw, 120, y, 800, wrap_lines(subline, fs, draw, 800), fs, ED_MUTED, 6)
    y += 22
    _editorial_pill(draw, 120, y, image_cta, fc, dark=True)
    _draw_footer_editorial(draw, w, h, 56, ff)
    return img


EDITORIAL_PIL_LAYOUTS = [
    _pil_editorial_left_panel,
    _pil_editorial_right_panel,
    _pil_editorial_rose_band,
    _pil_editorial_card_stack,
    _pil_editorial_sidebar_strip,
    _pil_editorial_minimal_grid,
]


def _active_pil_layouts():
    if POST_VISUAL_STYLE == "canva_editorial":
        return EDITORIAL_PIL_LAYOUTS
    return CANVA_PIL_LAYOUTS


def create_branded_post_image(headline: str, subline: str, image_cta: str, layout_index: int = 0) -> bytes:
    layouts = _active_pil_layouts()
    n = len(layouts)
    idx = abs(layout_index) % n
    label = "Editorial cream/rose" if layouts is EDITORIAL_PIL_LAYOUTS else "Canva-style"
    print(f"{label} PIL template #{idx} / {n}")
    img = layouts[idx](headline, subline, image_cta)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _fallback_layout_index(archetype: dict, headline: str) -> int:
    raw = f"{archetype['key']}|{headline}|{datetime.now().date().isoformat()}|pil"
    return int(hashlib.sha256(raw.encode()).hexdigest(), 16) % len(_active_pil_layouts())


def upload_image(image_bytes: bytes) -> str:
    res = cloudinary.uploader.upload(
        io.BytesIO(image_bytes),
        folder="harix-instagram",
        resource_type="image",
    )
    return res["secure_url"]


def build_instagram_caption(caption: str, hashtags: str, limit: int = 2200) -> str:
    sep = "\n\n"
    full = (caption or "").strip() + sep + (hashtags or "").strip()
    if len(full) <= limit:
        return full
    cap = (caption or "").strip()
    tags = (hashtags or "").strip()
    room = limit - len(sep)
    if len(cap) + len(sep) + min(len(tags), 80) > limit:
        return cap[: max(0, limit - 3)] + "..."
    keep_tags = tags[: max(0, room - len(cap) - len(sep))]
    return cap + sep + keep_tags.rstrip()


def wait_ig_media_container(container_id: str, poll_s: float = 4.0, timeout_s: float = 180.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = requests.get(
            f"{GRAPH_API_ROOT}/{container_id}",
            params={"fields": "status_code,status", "access_token": IG_TOKEN},
            timeout=45,
        )
        data = r.json()
        if data.get("error"):
            print("Container status error:", data)
            return False
        code = data.get("status_code")
        if code == "FINISHED":
            return True
        if code in ("ERROR", "EXPIRED"):
            print("Instagram container failed:", data)
            return False
        time.sleep(poll_s)
    print("Timeout: Instagram media container did not reach FINISHED.")
    return False


def post_to_instagram(image_url: str, caption: str, hashtags: str) -> bool:
    full_caption = build_instagram_caption(caption, hashtags)

    r1 = requests.post(
        f"{GRAPH_API_ROOT}/{IG_BIZ_ID}/media",
        data={
            "image_url": image_url,
            "caption": full_caption,
            "access_token": IG_TOKEN,
        },
        timeout=60,
    )
    body = r1.json()
    cid = body.get("id")
    if not cid:
        print("Container error:", body)
        err = body.get("error") or {}
        if err.get("code") == 190:
            print(
                "Meta rejected IG_ACCESS_TOKEN (invalid or unparsable). "
                "Fix: use a valid long-lived User or Page token from the app linked to this "
                "Instagram Business account; no extra quotes/spaces in .env; regenerate if expired."
            )
        return False

    if not wait_ig_media_container(cid):
        return False

    r2 = requests.post(
        f"{GRAPH_API_ROOT}/{IG_BIZ_ID}/media_publish",
        data={"creation_id": cid, "access_token": IG_TOKEN},
        timeout=60,
    )
    result = r2.json()
    print("Instagram response:", result)
    if "id" not in result and result.get("error"):
        print("Publish error detail:", result["error"])
    return "id" in result


def run():
    print(f"\n[{datetime.now().strftime('%H:%M %d %b %Y')}] Harix Bot running...")
    try:
        if not IG_TOKEN or not IG_BIZ_ID:
            print("Set IG_ACCESS_TOKEN and IG_BUSINESS_ID in .env (Instagram Business account id).")
            return

        archetype = pick_post_archetype()
        print(f"Post format: {archetype['name']} ({archetype['key']})")

        data = get_viral_post_idea(archetype)
        print(f"Topic: {data.get('topic', '')}")
        print(f"Trend: {data.get('trend_note', '')}")
        print(f"Headline: {data.get('headline', '')}")

        headline = data.get("headline") or "Build Smarter With Harix"
        subline = data.get("subline") or "Software, AI & web that grows your business"
        image_cta = data.get("image_cta") or "DM us — free consult"
        img_bytes = generate_social_image(data, archetype)
        print("Post image ready.")

        img_url = upload_image(img_bytes)
        print(f"Image uploaded: {img_url}")

        success = post_to_instagram(
            img_url,
            data.get("caption", ""),
            data.get("hashtags", ""),
        )

        if success:
            print("Posted to Instagram successfully!")
            with open("post_log.json", "a") as f:
                json.dump(
                    {
                        "time": str(datetime.now()),
                        "topic": data.get("topic"),
                        "trend_note": data.get("trend_note"),
                        "post_format": archetype.get("key"),
                        "image_url": img_url,
                    },
                    f,
                )
                f.write("\n")
        else:
            print("Posting failed. Check IG_ACCESS_TOKEN, IG_BUSINESS_ID, and Meta app permissions.")

    except Exception as e:
        print(f"Error: {e}")


schedule.every().day.at("09:00").do(run)
schedule.every().day.at("18:00").do(run)

if __name__ == "__main__":
    print("Harix Instagram Bot started.")
    print("Next scheduled posts: 09:00 and 18:00 daily.")
    print(
        f"Text: {GEMINI_TEXT_MODEL} (fallbacks: {GEMINI_TEXT_FALLBACK}) | "
        f"Image strategy: {IMAGE_GEN_STRATEGY} | "
        f"Visual style: {POST_VISUAL_STYLE} | "
        f"Gemini image: {GEMINI_IMAGE_PRIMARY} → {GEMINI_IMAGE_FALLBACK}"
    )
    run()
    while True:
        schedule.run_pending()
        time.sleep(30)
