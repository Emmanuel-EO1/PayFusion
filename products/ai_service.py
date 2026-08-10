import base64
import json
import logging

from django.conf import settings

logger = logging.getLogger('payfusion')

SYSTEM_PROMPT = """You are a product listing assistant for an e-commerce marketplace.
Analyse the product image and generate compelling listing content.
You MUST respond with valid JSON only — no markdown, no explanation, no preamble.
Response format:
{
    "title": "Product title, max 80 characters, specific and descriptive",
    "description": "Product description, 150-200 words, highlight key features, materials, uses, and benefits. Write in second person (you/your).",
    "tags": "up to 5 relevant comma-separated tags, lowercase, e.g. shoe accessory, leather, handmade"
}"""


def generate_product_content_from_image(image_file):
    """
    Sends a product image to OpenAI Vision API and returns
    AI-generated title, description, and tags.

    Args:
        image_file: an uploaded file object (from request.FILES)

    Returns:
        dict with keys: title, description, tags
        or raises ValueError with a user-friendly message on failure
    """
    from openai import OpenAI

    client = OpenAI(api_key=settings.OPENAI_API_KEY)

    image_data = image_file.read()
    base64_image = base64.b64encode(image_data).decode('utf-8')

    content_type = getattr(image_file, 'content_type', 'image/jpeg')

    try:
        response = client.chat.completions.create(
            model='gpt-4o',
            max_tokens=600,
            messages=[
                {
                    'role': 'user',
                    'content': [
                        {
                            'type': 'image_url',
                            'image_url': {
                                'url': f'data:{content_type};base64,{base64_image}',
                                'detail': 'low',
                            }
                        },
                        {
                            'type': 'text',
                            'text': SYSTEM_PROMPT,
                        }
                    ]
                }
            ]
        )

        raw = response.choices[0].message.content.strip()

        # Strip markdown code fences if model wraps output in them
        if raw.startswith('```'):
            raw = raw.split('```')[1]
            if raw.startswith('json'):
                raw = raw[4:]

        result = json.loads(raw)

        return {
            'title': result.get('title', '').strip(),
            'description': result.get('description', '').strip(),
            'tags': result.get('tags', '').strip(),
        }

    except json.JSONDecodeError as e:
        logger.error(f'AI description generator: JSON parse failed — {e}')
        raise ValueError(
            'The AI returned an unexpected response. Please try again.'
        )
    except Exception as e:
        logger.error(f'AI description generator: OpenAI API error — {e}')
        raise ValueError(
            'Could not connect to the AI service. Please try again later.'
        )