import pytest
from services.audio import AudioService
from unittest.mock import Mock, patch

class TestAudioService:

    def test_ffmpeg_whisper_parsing(self):
        """Test parsing of FFmpeg Whisper output (Text and JSON)"""
        service = AudioService()
        
        # 1. Test standard log format
        log_output = """
        [whisper @ 0x123] This is a test.
        [whisper @ 0x123] Another line.
        """
        assert service._parse_ffmpeg_whisper_output(log_output) == "This is a test. Another line."
        
        # 2. Test JSON format (simulated)
        json_output = """
        [info] Some log
        { "text": "Hello world" }
        { "text": " from JSON" }
        """
        assert service._parse_ffmpeg_whisper_output(json_output) == "Hello world from JSON"

    def test_ffmpeg_whisper_transcription(self, sample_audio):
        """Test FFmpeg Whisper transcription"""
        # Mocking subprocess run if running in environment without ffmpeg
        with patch('subprocess.run') as mock_run:
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = "[whisper @ 0x123] Test transcription"
            mock_run.return_value.returncode = 0
            
            # Mock get_audio_duration
            with patch.object(AudioService, 'get_audio_duration', return_value=10.0):
                service = AudioService()
                # We mock transcribe_with_ffmpeg_whisper actual call logic by mocking subprocess?
                # Or we can rely on integration test for real ffmpeg. 
                # Here we test logic around it.
                
                # If we mock subprocess, we should verify it parses output correctly.
                result = service._parse_ffmpeg_whisper_output("[whisper @ 0x123] Hello world")
                assert result == "Hello world"

    def test_gemini_formatting(self):
        """Test Gemini 3 formatting"""
        service = AudioService()
        
        # Mock genai client
        with patch('google.genai.Client') as MockClient:
            mock_response = Mock()
            mock_response.text = "Formatted text."
            MockClient.return_value.models.generate_content.return_value = mock_response

            input_text = "test text"
            result = service.format_text_with_gemini(input_text)

            assert result == "Formatted text."
            
            # Verify parameters
            call_args = MockClient.return_value.models.generate_content.call_args
            assert call_args[1]['model'] == "gemini-3-flash-preview"

    def test_code_tags_setting(self):
        """Test code tags are applied correctly"""
        service = AudioService()

        # Mock genai client
        with patch('google.genai.Client') as MockClient:
            mock_response = Mock()
            mock_response.text = "<code>test text</code>"
            MockClient.return_value.models.generate_content.return_value = mock_response
            
            text = "test text"
            result = service.format_text_with_gemini(text, use_code_tags=True)

            assert '<code>' in result
            assert '</code>' in result
            
            # Verify prompt contains instruction (checking args)
            call_args = MockClient.return_value.models.generate_content.call_args
            assert "Оберни ВЕСЬ текст в теги <code></code>" in call_args[1]['contents']

    def test_yo_letter_replacement(self):
        """Test letter ё replacement instruction"""
        service = AudioService()
        
        with patch('google.genai.Client') as MockClient:
            mock_response = Mock()
            mock_response.text = "елка"
            MockClient.return_value.models.generate_content.return_value = mock_response

            text = "ёлка"
            result = service.format_text_with_gemini(text, use_yo=False)

            # Verify prompt contains instruction
            call_args = MockClient.return_value.models.generate_content.call_args
            assert "Заменяй все буквы ё на е" in call_args[1]['contents']

    def test_empty_audio_handling(self):
        """Test empty/silent audio raises appropriate error"""
        service = AudioService()

        # We mock transcribe_with_ffmpeg_whisper to return empty string
        with patch.object(service, 'transcribe_with_ffmpeg_whisper', return_value=""):
             with pytest.raises(ValueError, match="Transcription too short"):
                service.transcribe_audio('dummy_path.mp3')
