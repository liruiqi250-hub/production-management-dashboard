# 钉钉端内桥接组件

上游：dingtalk-jsapi 3.2.9（npm，author: junlong.hjl），包元数据声明许可MIT。原包未附独立LICENSE文件，随附原package.json、README及打包依赖promise-polyfill的原许可。编译工具esbuild 0.25.12仅用于构建，接收电脑运行不需要Node或npm。

来源：https://registry.npmjs.org/dingtalk-jsapi/-/dingtalk-jsapi-3.2.9.tgz

固定依赖及npm完整性值见 dependencies/dingtalk-jsapi-build.lock.json。入口源码及重建命令见 scripts/build_dingtalk_sdk.ps1。

只包含微应用免登码桥接，不进行企业权限申请、用户赋权或网络配置变更。

## MIT permission notice

The upstream package declares MIT in its original package metadata. The metadata author is retained in dingtalk-jsapi-package.json; the package did not ship a separate copyright notice.

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
