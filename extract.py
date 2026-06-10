import json
import re
from google.genai import types
from tools.gemini_router import GeminiRouter

router = GeminiRouter()

PROMPT = """Extract ALL text and information from this business card image.
Return a JSON object where each key is a descriptive field name and the value is the extracted text.

Rules:
- If text appears in multiple languages, create separate keys (e.g. name_english, name_german, tagline_english, tagline_german)
- Include EVERY piece of text on the card — nothing is unimportant
- Use snake_case for key names (e.g. company_website, mobile_phone, linkedin_url)
- Common keys: first_name, last_name, full_name, title, company, email, phone, mobile, website, linkedin, address, tagline
- Do not infer or guess — only extract what is visually present on the card
- Return ONLY the JSON object, no explanation"""


def extract_card(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
    img_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
    text_part = types.Part.from_text(text=PROMPT)
    contents = [types.Content(role="user", parts=[img_part, text_part])]

    response, _ = router.call(contents, task="default")
    raw = response.text.strip()

    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    return json.loads(raw.strip())
