# V0.2.3 初始密码与本人改密实施计划

> 按已授权范围直接在当前会话执行，子代理负责隔离权限回归，主代理负责实现与真实部署。

**目标：** 统一现有账号初始密码，登录后要求本人改密。

**架构：** Store持久保存must_change_password，服务端在统一鉴权入口拦截业务访问，前端依据/api/me显示专用改密页面。一次性重置与服务启动分离，业务台账保持。

**技术：** Python、SQLite、现有原生JavaScript与响应式CSS。

- [x] Store：为旧users表增加must_change_password默认0；命名列INSERT保证迁移兼容；新增/重置用户设为1，成功改密设0；批量重置撤销全部旧凭据并审计用户名，禁止明文入库。API令牌认证和生成都检查标记。
- [x] Server：require增加allow_password_change，只有/api/me、POST/api/password、POST/api/logout允许待改密session，其它返回403和password_change_required；密码修改仍校验Origin/CSRF并清cookie。
- [x] Web：增加与登录卡片同样的手机改密页面，包含原密码、新密码、重复新密码、退出；load先检查标记，避免提前获取业务数据；两处改密均核对重复输入。
- [x] Version：更新VERSION、app.__version__、页面资源参数为0.2.3，新增启动入口，更新README及CHANGELOG，说明旧会话/令牌需重新登录/生成。
- [x] Verification：子代理在tests/test_password_onboarding.py测试迁移、批量重置、待改密读写拦截、CSRF、改密解除限制、旧凭据失效；主代理运行python -m unittest discover -s tests -v及node --check web/app.js。
- [x] Deployment：核对本任务8769服务进程后停止；执行一次重置并更新本机私有账号文件；重启读取VERSION的现有启动脚本。核验7账号可初始登录、全部被业务API拦截，375px改密页正常；隔离数据库中完成浏览器改密到看板全流程。
