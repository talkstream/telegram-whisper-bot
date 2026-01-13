# FFmpeg 8.0 Build Configuration

## Build Date
2026-01-13

## Configuration
```bash
./configure \
    --enable-gpl \
    --enable-version3 \
    --enable-nonfree \
    --enable-whisper \
    --extra-cflags="-I/usr/local/include" \
    --extra-ldflags="-L/usr/local/lib"
```

## Verification
```bash
ffmpeg -version
# Expected: ffmpeg version 8.0

ffmpeg -filters | grep whisper
# Expected: whisper filter listed
```

## Whisper Model
- **Model:** ggml-base.bin
- **Size:** ~140MB
- **Languages:** 90+ including Russian
- **Location:** /opt/whisper/models/ggml-base.bin

## Updates
- Monitor https://github.com/FFmpeg/FFmpeg/releases
- Update quarterly or when security patches released

