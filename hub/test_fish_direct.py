import requests
import json
import os

# Configuration
# Ensure this matches your Docker host IP if not running locally
BASE_URL = "http://localhost:18080" 
OUTPUT_FILE = "test_fish_output.wav"

# The text to test. Note the (joyful) tag.
TEXT_TO_SPEAK = "(joyful) Hello! This is a direct test of the Fish Speech engine."

# Optional: specific reference ID if you have one. 
# If None, it might use a random voice or fail depending on your model setup.
REFERENCE_ID = None 
# REFERENCE_ID = "7f92f8afb8ec43bf81429cc1c9199cb1" 

def test_fish_tts():
    url = f"{BASE_URL}/v1/tts"
    
    payload = {
        "text": TEXT_TO_SPEAK,
        "chunk_length": 200,
        "format": "wav",
        "references": [],
        "reference_id": REFERENCE_ID,
        "normalize": True,
        "streaming": False,
        "max_new_tokens": 1024,
        "top_p": 0.7,
        "repetition_penalty": 1.1,
        "temperature": 0.7
    }

    print(f"Sending request to {url}...")
    print(f"Payload: {json.dumps(payload, indent=2)}")

    try:
        response = requests.post(url, json=payload, timeout=120)
        
        if response.status_code == 200:
            with open(OUTPUT_FILE, "wb") as f:
                f.write(response.content)
            print(f"Success! Audio saved to {OUTPUT_FILE}")
        else:
            print(f"Error: {response.status_code}")
            print(response.text)
            
    except Exception as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    test_fish_tts()
