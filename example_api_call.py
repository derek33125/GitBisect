import os
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

# Load environment
load_dotenv(Path(__file__).parent / ".env")
api_key = os.getenv("CHATANYWHERE_KEY")
base_url = "https://api.chatanywhere.tech/v1"
model_name = "gpt-5.4-mini"

"""
Alternative choices:
deepseek-v4-pro
claude-haiku-4-5-20251001
"""

# Generate code
client = OpenAI(api_key=api_key, base_url=base_url)
response = client.chat.completions.create(
    model=model_name,
    messages=[
        {"role": "user", "content": "Hello, how are you?"}
    ]
)
print(response.choices[0].message.content)