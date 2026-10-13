# 对应源码与重建材料

运行本软件无需编译。两份二进制依赖 ZIP 与对应源码 ZIP 在同一私有 GitHub Release v0.2.7 提供：

https://github.com/liruiqi250-hub/production-management-dashboard/releases/tag/v0.2.7

源码附件名：`media-corresponding-sources-ffmpeg8.0.1-sndfile1.2.2-sherpa1.13.6.zip`。`SOURCE_MANIFEST.json` 记录原始来源、精确版本、归档 SHA-256；`media-runtime.lock.json` 记录所发二进制逐文件 SHA-256。FFmpeg、libsndfile、Sherpa及其固定依赖的原始源码归档保持完整；FFmpeg 与 libsndfile 的源码差异为空，只有构建配置不同。ONNX Runtime 附件保留全部编译源码、头文件、构建脚本及许可，仅排除995个二进制测试样本；其内`PRODUCTION_SOURCE_EXCLUSIONS.json`逐项记录排除内容和原归档校验值，实际代码未修改。

## FFmpeg 与 libsndfile

构建工具为 Git for Windows Bash、LLVM-MinGW **20261006 UCRT x86_64**（Clang **23.1.3**）与 CMake **4.4.4**。工具来源和 SHA-256 在 manifest 的 build_tools 中；它们仅为维护者重建用，不进入正式运行包。

将两份原始源码 tar.xz 解压到一个不含空格的构建目录；将 LLVM-MinGW 解压到同目录，并把其 `bin` 加入 PATH。在 Git Bash 中运行附件`materials/rebuild_ffmpeg.sh <FFmpeg源目录> <输出目录>`。使用MSYS GNU make 4.4.1（来源和SHA见manifest），通过`MSYS_MAKE`指定其入口；不要使用原生mingw32-make，其Windows命令长度限制会截断链接对象列表。此脚本保留实际构建的 configure 开关，使用共享库，未启用外部编解码库。它只编译常见语音/视频解码、PCM/WAV 转换与 JPEG/原始帧输出；PNG 压缩编码未启用（未链接 zlib）。

在 PowerShell 中将 LLVM-MinGW 的 `bin` 加入 PATH，用 CMake 执行：

```powershell
cmake -S <libsndfile源目录> -B <构建目录> -G "MinGW Makefiles" `
  "-DCMAKE_C_COMPILER=x86_64-w64-mingw32-clang" `
  "-DCMAKE_CXX_COMPILER=x86_64-w64-mingw32-clang++" `
  "-DCMAKE_MAKE_PROGRAM=mingw32-make" `
  "-DCMAKE_POLICY_VERSION_MINIMUM=3.5" `
  "-DCMAKE_BUILD_TYPE=Release" "-DBUILD_SHARED_LIBS=ON" `
  "-DENABLE_EXTERNAL_LIBS=OFF" "-DENABLE_MPEG=OFF" `
  "-DBUILD_PROGRAMS=OFF" "-DBUILD_EXAMPLES=OFF" "-DBUILD_TESTING=OFF"
cmake --build <构建目录> -j 8
```

把生成的 `libsndfile.dll` 复制为 SoundFile 约定的 `libsndfile_x64.dll`。没有改变其导出接口或源码。其 PE 依赖仅为 Windows 系统 DLL 与 UCRT，不依赖旧电脑的 Python、vcpkg 或媒体 DLL。源码原许可、版权及编译器运行时 notices 一起保留。

## Sherpa-ONNX / eSpeak NG 对应源码

原 wheel 的 `sherpa_onnx.git_sha1` 为 `1cb484af`，与 v1.13.6 的完整提交 `1cb484af5e69d3c7803c1eb0b3b5ab8041e0e911` 一致；`sherpa_onnx.onnxruntime_version` 为 `1.27.1`。`sources/sherpa-onnx-v1.13.6-source.zip` 保留该版本完整代码、`setup.py`、CMake、`.github/workflows/` 的 Python wheel / Windows 构建脚本与依赖下载逻辑。此二进制来自原 PyPI wheel，本项目未修改它。

`sources/sherpa-dependencies/` 保存其固定 CMake 源依赖，含 eSpeak NG、piper-phonemize、kaldi-decoder、kaldi-native-fbank、Eigen、OpenFST、pybind11 等；文件名与原 archive 及 SHA 在 manifest 对照。重建时先解压 sherpa 源码，将依赖原档按其 `cmake/*.cmake` 的 `possible_file_locations` 中预期文件名复制到源码根目录（manifest 的 original_filename 字段）。上游脚本会校验固定哈希并优先复用这些档案。再按同版本原 Windows/Python workflow 设置 Visual Studio x64、CMake、Python 3.12 与对应 ONNX Runtime 版本构建。

eSpeak NG 由该版本 CMake 的 `espeak-ng-for-piper.cmake` 固定提交并配置，piper-phonemize 也固定提交；其 GPL、Apache、BSD、Unicode 原文和所有源码保持原样。ONNX Runtime 原源码单独保存在附件，MIT 和三方 notices 均随运行包。

本文件不声称不同编译工具时间戳产生位对位相同的二进制；它提供本次二进制所对应的精确上游源码、修改情况、构建开关及重建入口。文件完整性以发布的 SHA-256 为准。
