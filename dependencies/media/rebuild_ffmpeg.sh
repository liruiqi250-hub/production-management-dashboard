#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -ne 2 ]; then echo "Usage: rebuild_ffmpeg.sh SOURCE_DIR OUTPUT_DIR" >&2; exit 2; fi
cd "$1"
./configure --prefix="$2" --target-os=mingw32 --arch=x86_64 --cc=x86_64-w64-mingw32-clang --cxx=x86_64-w64-mingw32-clang++ --ar=llvm-ar --ranlib=llvm-ranlib --nm=llvm-nm --strip=llvm-strip --windres=x86_64-w64-mingw32-windres --enable-shared --disable-static --disable-gpl --disable-nonfree --disable-version3 --disable-autodetect --disable-x86asm --disable-doc --disable-debug --disable-network --disable-avdevice --disable-ffplay --disable-hwaccels --disable-decoders --enable-decoder=aac,aac_fixed,mp3,mp3float,mp2,pcm_s16le,pcm_s16be,pcm_f32le,pcm_f64le,pcm_u8,flac,vorbis,opus,alac,h264,hevc,mpeg4,mpeg2video,mjpeg,rawvideo,ffv1 --disable-demuxers --enable-demuxer=aac,mp3,wav,flac,ogg,mov,matroska,avi,image2,image2pipe,pcm_s16le,pcm_f32le --disable-encoders --enable-encoder=pcm_s16le,pcm_s16be,pcm_f32le,pcm_f64le,rawvideo,mjpeg,png,ffv1 --disable-muxers --enable-muxer=wav,pcm_s16le,pcm_f32le,image2,image2pipe,matroska,null --disable-protocols --enable-protocol=file,pipe --disable-bzlib --disable-iconv --disable-lzma --disable-zlib
# Use MSYS GNU make (Git Bash/MSYS runtime), not native mingw32-make:
# native Windows command shells truncate long object lists at link time.
"${MSYS_MAKE:-make}" -j8
"${MSYS_MAKE:-make}" install
