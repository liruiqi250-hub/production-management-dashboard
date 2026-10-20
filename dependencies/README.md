# Windows x64 便携依赖

V0.3.29生产资料新增production-documents.lock.json，固定openpyxl、et-xmlfile、pypdf、pypdfium2与Pillow的官方轮子、文件大小、SHA和原许可证。scripts/build_production_document_runtime.py将校验后的组件放入vendor/python/site-packages，不依赖pip或Codex运行环境；生产profile构建时自动包含。实际Excel到PDF转换调用目标电脑已安装的LibreOffice；PDF页面转PNG使用锁定的PDFium/Pillow。未安装LibreOffice时保留Excel并清晰说明预览不可用。模板与业务文件不进运行包。

`windows-x64.lock.json` 固定官方 CPython embeddable 与 Chrome for Testing headless shell 的版本、HTTPS 下载地址、原始压缩包 SHA-256 和许可证 SHA-256。构建时不会自动追随最新版本；升级组件必须更新锁文件、验证后迭代软件版本。

运行 `python scripts/build_portable.py` 构建带依赖的便携 ZIP；`--offline` 仅使用本机已校验缓存。二进制缓存位于 `runtime/dependency_cache`，成品位于 `outputs/V<版本>`，两者都不会提交到 Git 历史。成品作为对应版本的 GitHub Release 附件上传。

成品完整保留依赖压缩包中的原始文件：

- `vendor/python/LICENSE.txt`：Python Software Foundation 许可、历史许可与随附第三方声明。
- `vendor/chromium/LICENSE.headless_shell`：Chromium BSD 许可及随附第三方许可全文；`ABOUT` 同时保留。

Python 仅调整 `python313._pth`，加入相对项目根目录；不启用 `import site`，不读取本机 Codex Python、浏览器配置或用户 Python 依赖。PDF 使用包内无头浏览器生成，不要求目标电脑安装 Edge。

本便携包仅包含看板代码和上述运行组件。微信采集仍依赖办公室电脑上已授权的本地读取工具、微信登录状态，以及 Codex 的每日整理任务；这些个人环境、账户和业务资料不会复制进包。新机器部署时须单独完成授权与采集配置。
