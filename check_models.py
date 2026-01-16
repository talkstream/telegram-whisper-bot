
import os
from google import genai

PROJECT_ID = os.environ.get('GCP_PROJECT', 'editorials-robot')

def list_models(location):
    print(f"--- Checking models in {location} ---")
    try:
        client = genai.Client(vertexai=True, project=PROJECT_ID, location=location)
        # Using the v1 API style to list models if available, or try-catch generation
        # The SDK might differ, let's try to get model list
        # Since I don't have exact 'list_models' syntax for this specific 1.0.0 SDK handy in memory for 2026, 
        # I'll try a generation call with the target model to see if it works.
        
        target_model = "gemini-3-flash-preview"
        try:
            client.models.generate_content(
                model=target_model,
                contents="Hello",
            )
            print(f"✅ Model '{target_model}' IS available in {location}")
        except Exception as e:
            print(f"❌ Model '{target_model}' FAILED in {location}: {e}")

        # Also try "gemini-3.0-flash" just in case naming changed
        target_model_stable = "gemini-3.0-flash"
        try:
            client.models.generate_content(
                model=target_model_stable,
                contents="Hello",
            )
            print(f"✅ Model '{target_model_stable}' IS available in {location}")
        except Exception as e:
            print(f"❌ Model '{target_model_stable}' FAILED in {location}: {e}")

    except Exception as e:
        print(f"Failed to init client for {location}: {e}")

if __name__ == "__main__":
    list_models("europe-west1")
    list_models("us-central1")
