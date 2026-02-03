#!/usr/bin/env python3
"""
Check available Gemini models in Vertex AI
Run: python check_models.py
"""

import os

def check_gemini_models():
    """List available Gemini models and check for Gemini 3 Flash"""
    try:
        import google.genai as genai

        # Initialize client with Vertex AI
        project_id = os.environ.get('GCP_PROJECT', 'editorials-robot')
        client = genai.Client(
            vertexai=True,
            project=project_id,
            location='europe-west1'
        )

        print(f"Project: {project_id}")
        print(f"Location: europe-west1")
        print("-" * 50)

        # List available models
        print("\nAvailable Gemini models:")
        models = client.models.list()

        gemini_models = []
        for model in models:
            if 'gemini' in model.name.lower():
                gemini_models.append(model.name)
                print(f"  - {model.name}")

        # Check for Gemini 3 Flash specifically
        print("\n" + "-" * 50)
        gemini3_flash = [m for m in gemini_models if 'gemini-3' in m.lower() and 'flash' in m.lower()]

        if gemini3_flash:
            print(f"\nGemini 3 Flash AVAILABLE: {gemini3_flash}")

            # Test model
            print("\nTesting Gemini 3 Flash...")
            model_name = gemini3_flash[0].split('/')[-1]  # Extract model ID
            response = client.models.generate_content(
                model=model_name,
                contents="Say 'Hello, I am Gemini 3 Flash!' in Russian"
            )
            print(f"Response: {response.text}")
        else:
            print("\nGemini 3 Flash NOT available in this region/project")
            print("  Current best option: gemini-2.5-flash")

        # Test current model (gemini-2.5-flash)
        print("\n" + "-" * 50)
        print("\nTesting current model (gemini-2.5-flash)...")
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents="Say 'Hello!' in Russian"
            )
            print(f"Response: {response.text}")
            print("gemini-2.5-flash is working")
        except Exception as e:
            print(f"Error testing gemini-2.5-flash: {e}")

    except ImportError:
        print("Error: google-genai not installed")
        print("Run: pip install google-genai")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_gemini_models()
