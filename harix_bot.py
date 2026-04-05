import io
import json
import os
import re
import sys
import time
from datetime import datetime

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

BRAND_TOP = (22, 88, 160)
BRAND_BOTTOM = (6, 28, 62)
ACCENT = (255, 214, 120)
TEXT_LIGHT = (240, 248, 255)
TEXT_MUTED = (176, 210, 245)

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


def get_viral_post_idea() -> dict:
    service = SERVICES[datetime.now().weekday() % len(SERVICES)]
    today = datetime.now().strftime("%B %d, %Y")

    prompt = f"""Today is {today}. You are an elite Instagram growth strategist for a B2B IT company.
{HARIX}

Focus service area for this post: {service}

Pick ONE angle that matches high-performing 2025–2026 business content:
- timely / trending in tech, AI, SaaS, or digital transformation (name the trend plainly)
- strong hook: contrarian take, common mistake, stat-style claim, or sharp question
- clear value for SMBs and founders in India and globally

Return ONLY valid JSON (no markdown):
{{
  "topic": "short internal label",
  "trend_note": "what is trending now that this post rides on (one sentence)",
  "viral_angle": "why someone stops scrolling (one sentence)",
  "caption": "Hook first line, then 3-5 short lines with line breaks. 2-4 tasteful emojis. Mention Harix Global Solutions. End with: Visit harixsolutions.com | Link in bio",
  "hashtags": "18-28 hashtags separated by spaces, each starting with #",
  "headline": "MAX 6 words, Title Case, punchy — will be painted on the image",
  "subline": "MAX 14 words — supporting line on the image",
  "image_cta": "MAX 8 words — CTA on the image",
  "visual_scene": "3-5 sentences of art direction for an AI image model: composition, lighting, metaphor (e.g. isometric app, abstract data flow), style (editorial / 3D / gradient luxury). Colors: deep blue #1859a5, charcoal, white, subtle gold. No stock-photo cliché humans unless diverse professional silhouettes. Must feel premium and current."
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


def build_nano_banana_prompt(data: dict, has_logo: bool) -> str:
    headline = data.get("headline") or "Build With Harix"
    subline = data.get("subline") or ""
    cta = data.get("image_cta") or "DM for a free consult"
    angle = data.get("viral_angle") or ""
    scene = data.get("visual_scene") or ""
    trend = data.get("trend_note") or ""

    logo_clause = (
        "The FIRST attached image is the official Harix company logo. "
        "Place it prominently in the upper area (top-left or top-center), sharp and undistorted, "
        "on a clean card or subtle glass panel if needed for contrast. Preserve logo colors."
        if has_logo
        else "No logo file was supplied; use elegant text wordmark 'HARIX' in a minimal tech style "
        "in the top area — do not invent a fake complex logo."
    )

    return f"""Create ONE square (1:1) Instagram feed graphic for Harix Global Solutions (IT services, India).

{logo_clause}

Trend context to reflect in mood (not as fine print): {trend}
Scroll-stopping angle: {angle}

Visual direction:
{scene}

Typography on the image (large, high-contrast, perfectly readable):
• Headline: "{headline}"
• Subline: "{subline}"
• Small CTA line: "{cta}"

Footer micro-type (small but legible): harixsolutions.com · Bhavnagar, Gujarat, India

Style: 2026 premium B2B tech marketing — depth, soft gradients, subtle glass or light rays, optional abstract AI/data motifs. No watermarks, no "AI generated" labels, no competitor logos.

Output a single polished image."""


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


def generate_nano_banana_post_image(data: dict) -> bytes:
    logo_path = _resolve_logo_path()
    prompt = build_nano_banana_prompt(data, has_logo=bool(logo_path))
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


def ensure_square_instagram(raw_bytes: bytes, size: int = 1080) -> bytes:
    img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    if img.size != (size, size):
        img = img.resize((size, size), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def create_branded_post_image(headline: str, subline: str, image_cta: str) -> bytes:
    w, h = 1080, 1080
    img = Image.new("RGB", (w, h))
    fill_vertical_gradient(img, BRAND_TOP, BRAND_BOTTOM)
    draw = ImageDraw.Draw(img)

    margin = 64
    content_w = w - 2 * margin

    logo = load_logo(200)
    y_cursor = margin
    if logo:
        img.paste(logo, (margin, margin), logo)
        y_cursor = margin + logo.size[1] + 36
    else:
        font_mark = load_ttf(42, bold=True)
        draw.rounded_rectangle(
            [margin, margin, margin + 200, margin + 72],
            radius=12,
            fill=(35, 75, 130),
            outline=ACCENT,
            width=2,
        )
        draw.text((margin + 20, margin + 18), "HARIX", font=font_mark, fill=ACCENT)
        draw.text((margin, margin + 84), "Global Solutions", font=load_ttf(22, bold=False), fill=TEXT_MUTED)
        y_cursor = margin + 140

    font_head = load_ttf(56, bold=True)
    font_sub = load_ttf(32, bold=False)
    font_cta = load_ttf(28, bold=True)
    font_small = load_ttf(22, bold=False)

    hl_lines = wrap_lines(headline.strip(), font_head, draw, content_w)
    y_cursor = draw_text_block(draw, margin, y_cursor, content_w, hl_lines, font_head, TEXT_LIGHT, 8)
    y_cursor += 20

    sub_lines = wrap_lines(subline.strip(), font_sub, draw, content_w)
    y_cursor = draw_text_block(draw, margin, y_cursor, content_w, sub_lines, font_sub, TEXT_MUTED, 6)
    y_cursor += 28

    cta_lines = wrap_lines(image_cta.strip(), font_cta, draw, content_w)
    draw_text_block(draw, margin, y_cursor, content_w, cta_lines, font_cta, ACCENT, 4)

    footer_top = 780
    draw.rectangle([0, footer_top, w, h], fill=(4, 18, 40))
    draw.line([0, footer_top, w, footer_top], fill=ACCENT, width=3)

    fy = footer_top + 36
    draw.text((margin, fy), "Harix Global Solutions Pvt Ltd", font=font_small, fill=TEXT_LIGHT)
    fy += 34
    draw.text(
        (margin, fy),
        "harixsolutions.com  ·  Bhavnagar, Gujarat, India",
        font=font_small,
        fill=TEXT_MUTED,
    )
    fy += 34
    draw.text(
        (margin, fy),
        "Custom Software  ·  AI & Automation  ·  Web & eCommerce  ·  Mobile Apps",
        font=font_small,
        fill=TEXT_MUTED,
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


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

        data = get_viral_post_idea()
        print(f"Topic: {data.get('topic', '')}")
        print(f"Trend: {data.get('trend_note', '')}")
        print(f"Headline: {data.get('headline', '')}")

        headline = data.get("headline") or "Build Smarter With Harix"
        subline = data.get("subline") or "Software, AI & web that grows your business"
        image_cta = data.get("image_cta") or "DM us — free consult"

        try:
            img_bytes = generate_nano_banana_post_image(data)
        except Exception as e:
            print(f"Nano Banana image failed ({e}); using PIL fallback.")
            img_bytes = create_branded_post_image(headline, subline, image_cta)

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
        f"Image: {GEMINI_IMAGE_PRIMARY} → {GEMINI_IMAGE_FALLBACK}"
    )
    run()
    while True:
        schedule.run_pending()
        time.sleep(30)
