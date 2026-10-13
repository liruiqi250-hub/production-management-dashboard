# 媒体与离线语音组件的许可及出处

本文件随运行包保留。各组件由各自作者持有版权，项目未取得这些组件的独占所有权。以下各许可原文、版权与限制均继续适用；允许依相应许可修改、替换和调试这些独立组件，不对许可允许的反向工程施加额外限制。

## FFmpeg 8.0.1

本软件使用 FFmpeg 项目提供的库，按 LGPL-2.1-or-later 使用和再分发。FFmpeg 由 Fabrice Bellard、Michael Niedermayer 及 FFmpeg contributors 开发；项目及源代码出处为 https://ffmpeg.org/ 。本包自行从官方未修改的 `ffmpeg-8.0.1.tar.xz` 编译 Windows x64 共享库，禁用 GPL、nonfree、version3、自动外部库检测和网络协议，未包含 libx264、libx265 或原 imageio-ffmpeg 的 GPL 版 FFmpeg。二进制保留原名 `ffmpeg.exe`、`ffprobe.exe` 与 `av*.dll`。

许可原文在 `vendor/wechat/ffmpeg/licenses/`。精确源码、配置、构建步骤和哈希见同一 Release 的 `media-corresponding-sources-ffmpeg8.0.1-sndfile1.2.2-sherpa1.13.6.zip`。源代码与二进制在同一 GitHub Release 提供；私有仓库接收者使用同一访问权限下载。

## libsndfile 1.2.2 与 SoundFile 0.14.0

libsndfile 由 Erik de Castro Lopo 及 libsndfile contributors 开发，项目 https://github.com/libsndfile/libsndfile ，按 LGPL-2.1-or-later 分发。本包从官方未修改的 `libsndfile-1.2.2.tar.xz` 自行编译共享 DLL，关闭 `ENABLE_EXTERNAL_LIBS` 与 `ENABLE_MPEG`。替换 SoundFile wheel 原来的 `libsndfile_x64.dll`，未再分发其原 DLL 中静态链接的 FLAC、Ogg、Vorbis、Opus、LAME 或 mpg123。音频先经 FFmpeg 转成 PCM/WAV，再由最小 libsndfile 读取。DLL 可被相容替代品替换。

SoundFile Python wrapper 由 Bastian Bechtold 及 contributors 开发，保持 BSD-3-Clause 原许可与上游 `licensing/license_notes.md`。后者描述上游完整版 wheel 的可选编解码库；本包实际 DLL 以本文件与 `media-runtime.lock.json` 为准。`_soundfile_data/COPYING` 保留 libsndfile LGPL 原文。精确源码同上述源码附件提供。

## Sherpa-ONNX 1.13.6、eSpeak NG 与 ONNX Runtime

Sherpa-ONNX 项目：https://github.com/k2-fsa/sherpa-onnx ，作者 k2-fsa / sherpa-onnx contributors；精确源提交 `1cb484af5e69d3c7803c1eb0b3b5ab8041e0e911`，上游源代码 Apache-2.0，保留 `Sherpa-ONNX-LICENSE.txt`。本包所用 wheel 的 `_sherpa_onnx.pyd` 和原生 core 包含静态 eSpeak NG / piper 组件；不能把该组合二进制只称为 Apache-2.0。

该链接组合按 eSpeak NG 的 GPL-3.0-or-later 条款分发，保留 `Espeak-NG-COPYING.txt` 以及其 Apache、BSD、Unicode notices。eSpeak NG 固定来源 https://github.com/csukuangfj/espeak-ng/tree/ed530aa113046142eb5115cf2fc9157854d0ffe1 ，piper-phonemize 固定来源 https://github.com/csukuangfj/piper-phonemize/tree/f3ff95afc03640bc1399e113e83361192a2fafb4 。本看板不提供语音合成功能，但上游二进制的静态代码仍保留其许可义务。

同一源码附件包含 Sherpa-ONNX 完整源归档、上游构建工作流、固定哈希的 CMake 源依赖以及相关源码和构建说明。保留原版权，允许按原许可修改、重建、替换和再分发这些组件；上游原 Apache / MIT / BSD / MPL 许可声明也一并保留。

ONNX Runtime 实际版本 1.27.1，由 Microsoft 及 ONNX Runtime contributors 开发，出处 https://github.com/microsoft/onnxruntime/tree/v1.27.1 。保留 MIT 原文 `ONNXRuntime-LICENSE.txt` 与完整 `ONNXRuntime-ThirdPartyNotices.txt`，附对应源归档。音频识别模型未嵌入该 GPL 二进制；它是独立数据文件，适用下项自己的模型协议。

## SenseVoice Small Int8 2024-07-17

模型名称保留为 **SenseVoice Small Int8 2024-07-17**。原模型作者为 **Alibaba Group / FunAudioLLM / FunASR team**，出处 https://github.com/FunAudioLLM/SenseVoice ；本包的 ONNX int8 转换模型由 k2-fsa / sherpa-onnx 发布于 https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models 。

模型适用 **FunASR Model Open Source License Agreement v1.1**，保留模型目录中的完整 `MODEL_LICENSE.txt`，保留作者、出处和模型名。该模型协议与推理程序的 Apache / GPL / MIT 许可分别适用。模型文件 SHA-256 在微信采集依赖锁中；本项目未修改模型权重。

## 编译器运行时

FFmpeg 与 libsndfile 使用 LLVM-MinGW 20261006 / Clang 23.1.3 编译，静态使用的 LLVM compiler-rt 与 MinGW runtime 片段分别保留 `LLVM-RUNTIME-LICENSE.txt` 和 `MinGW-RUNTIME-LICENSE.txt`。LLVM 的许可为 Apache-2.0 WITH LLVM-exception 等原许可组合；MinGW runtime 以其原文为准。构建工具本身不在运行包中，Windows 的 UCRT 和系统 DLL 由 Windows 10/11 提供。
