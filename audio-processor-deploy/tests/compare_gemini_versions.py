#!/usr/bin/env python3
"""
Compare Gemini 2.5-flash vs 3-flash-preview quality
"""
import google.genai as genai
import os

def format_with_gemini(text: str, model: str) -> str:
    """Format text with specified Gemini model"""
    client = genai.Client(
        vertexai=True,
        project=os.getenv('GCP_PROJECT', 'editorials-robot'),
        location='europe-west1'
    )

    prompt = f"""Отформатируй транскрипцию аудиозаписи. Правила:

1. Исправь ошибки распознавания речи
2. Добавь знаки препинания
3. Раздели на абзацы по смыслу

Текст: {text}"""

    response = client.models.generate_content(
        model=model,
        contents=prompt
    )

    return response.text.strip()


def compare_models(test_text: str):
    """Compare formatting quality"""
    print("Original text:")
    print(test_text)
    print("\n" + "="*80 + "\n")

    # Gemini 2.5
    print("Gemini 2.5-flash:")
    try:
        result_25 = format_with_gemini(test_text, "gemini-2.5-flash")
        print(result_25)
    except Exception as e:
        print(f"Error with Gemini 2.5: {e}")
        result_25 = ""
    print("\n" + "="*80 + "\n")

    # Gemini 3
    print("Gemini 3-flash-preview:")
    try:
        result_3 = format_with_gemini(test_text, "gemini-3-flash-preview")
        print(result_3)
    except Exception as e:
        print(f"Error with Gemini 3: {e}")
        result_3 = ""
    print("\n" + "="*80 + "\n")

    # Compare
    print("Comparison:")
    print(f"  2.5 length: {len(result_25)} chars")
    print(f"  3.0 length: {len(result_3)} chars")


if __name__ == '__main__':
    # Test with sample transcription (typical Whisper output with errors)
    test_text = """
    привет сегодня я хочу рассказать вам о том как работает наш новый продукт
    э э это очень интересная тема мы потратили много времени на разработку
    и сейчас готовы представить вам результаты тестирования показали что
    производительность выросла на сорок процентов это отличный показатель
    мы очень довольны результатом
    """

    compare_models(test_text)
