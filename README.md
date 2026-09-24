# 综测计算器（含教务成绩自动抓取）

单页「综合测评」计算器：填数字即时算总分；点「扫码登录教务系统」用学习通 App 扫码后，
可自动抓取**本人**各门课程的成绩与学分并填入，综合测评分实时更新。

- **学校**：南通师范高等专科学校
- **教务系统**：超星（<https://ntsf.jw.chaoxing.com>）
- **登录方式**：仅学习通 App 扫码（账密登录已关停）

> **合规提示**：本工具仅限查询**本人**学业成绩，请遵守学校教务系统使用规定，
> 保持低频、不要高频请求，更不要用于抓取他人信息。本项目是个人自用工具，非学校官方项目。

## 功能

| 功能 | 说明 |
| --- | --- |
| 综测分计算 | `0.60×专业学习 + 0.20×思想品德 + 0.10×劳动素养 + 0.05×能力发展 + 0.05×身心素质`，满分 93.00，输入即算 |
| 成绩自动抓取 | 扫码登录 → 选学年/学期（可选「全部」）→ 抓取成绩学分并自动填入课程行 |
| 等级成绩与剔除项 | 等级成绩（优秀/通过等）单独列出、不参与加权；体育等不纳入专业成绩的课程自动剔除并提示 |
| 本地优先 | 服务默认只监听 `127.0.0.1`，不上传任何账号信息；填写内容仅存浏览器 localStorage |
| 部署友好 | 令牌鉴权、CORS 白名单、无头模式、多人会话隔离、并发上限、诊断快照自动脱敏 |

## 快速开始

需要 Python 3.9+。

```bash
pip install -r requirements.txt
playwright install chromium        # 或用系统 Chrome/Chromium（见 JW_CHROMIUM）
python app.py                      # 浏览器打开 http://127.0.0.1:8765
```

Windows 用户可直接双击 **`run_local.bat`**（自动装依赖，并设好本机开发参数）。

### 使用步骤

1. 打开 `http://127.0.0.1:8765`，点「扫码登录教务系统」，用学习通 App 扫码并在手机上确认；
2. 登录成功后，学年/学期下拉会自动读出系统里的真实选项，选好点「开始抓取」；
3. 抓取到的成绩与学分自动填入「专业学习成绩」，总分实时更新；
4. 其余各项（思想品德考评分、宿舍平均分、PU 分、体育总评、心理总评）手填即可。

> 也可以直接双击 `static/index.html`（`file://` 方式）使用——页面会自动连接本机
> `127.0.0.1:8765` 的服务，前提是先启动 `python app.py`。

## 抓取原理

1. **扫码完全走官方页面**：登录页二维码来自 `passport2.chaoxing.com/cloudscanlogin?pcrefer=...`，
   二维码页每 3 秒轮询 `/getauthstatus`，手机确认后跳转 `/admin/scanLogin` 建立教务会话。
   引擎在后台浏览器里保留该页面自行轮询，只取出二维码图片显示，不碰账密、不逆向加密。
2. **双路抓取自动合并**：XHR 拦截（接口原始 JSON）+ DOM 解析（渲染表格，自动调大每页条数并逐页翻完），
   按「课程号/课程名 + 学年 + 学期」去重、互补缺失字段。
3. **选择器自适应**：成绩页在哪个 iframe 就遍历全部 frame 按关键词打分定位；
   学年/学期先读出真实选项再按值精确选择，不硬编码。
4. **本机二次校验**：抓完后再按所选学年学期过滤一次——即便教务页面忽略了筛选条件
   （曾出现过），也只会得到所选学期的课程。
5. **失败可诊断**：异常时自动把截图、各 frame HTML、截获的 JSON 存入 `debug/`，
   落盘前打码学号/姓名/内部 ID/手机号，可放心发给他人排查。

## 自测

```bash
python -m tests._selftest_deploy    # 部署改造自测：76 项断言，不需要浏览器
python -m tests._selftest           # 端到端抓取自测：63 项断言，本地 fixture 页面 + 真实 Chromium
```

两套都要**在仓库根目录**用 `-m` 方式执行（脚本内部从仓库根导入 `app` / `crawler`）。
`_selftest.py` 使用 `tests/_selftest_fixture.html` 模拟 jqGrid 成绩页（数据为虚构示例），
不需要登录真实教务系统。

## 目录结构

```
.
├── app.py                        # Flask 服务：扫码登录代理、令牌鉴权、多用户会话与并发控制
├── crawler.py                    # Playwright 引擎：扫码登录 + frame 定位 + XHR/DOM 抓取 + 归一化
├── static/index.html             # 单页综测计算器（服务托管的页面，含全部前端逻辑）
├── tests/
│   ├── _selftest.py              # 端到端自测（本地 fixture 页面）
│   ├── _selftest_deploy.py       # 部署改造自测（CORS/鉴权/多租户/脱敏/清理）
│   └── _selftest_fixture.html/js # jqGrid 成绩页模拟 + 虚构成绩数据
├── requirements.txt              # flask>=3.0、playwright>=1.40
├── run_local.bat                 # Windows 一键启动
├── README.md
└── 部署方案.md                    # 云服务器部署（systemd / Caddy / Nginx / 验收清单）
```

## 环境变量

云服务器上只改环境变量、不改代码。常用几项：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `ZC_TOKEN` | 空（不鉴权） | 设置后所有 `/api/*` 必须带 `X-Token` 头（本机自用可不设） |
| `ZC_TOKENS` | 空 | 多人共用：`张三:令牌A,李四:令牌B`，每人独立会话/日志/快照目录 |
| `ZC_ALLOWED_ORIGINS` | 未设置=仅放行 `file://` | 跨域白名单；同源部署设为**空值**即全关 |
| `ZC_HEADLESS` | `1` | `1`=无头（服务器）；`0`=本机保留「弹窗扫码」兜底 |
| `ZC_DEBUG_WRITE` | `1` | `0`=完全不落盘诊断快照（多人共用建议 `0`） |
| `ZC_DEBUG_DIR` / `ZC_DEBUG_KEEP` | `debug` / `5` | 快照目录 / 只保留最近 N 份（`0`=不清理） |
| `ZC_IDLE_CLOSE` | 多用户 `180`、单用户 `600` | 空闲多少秒自动关浏览器释放内存，`0`=不关 |
| `ZC_MAX_ENGINES` | `2` | 全局并发引擎上限（2G 内存机器建议 `1`） |
| `ZC_HOST` / `ZC_PORT` | `127.0.0.1` / `8765` | 监听地址与端口（**不要**改成 `0.0.0.0` 直接暴露） |
| `JW_CHROMIUM` | 空 | 指定 Chromium/Chrome 可执行文件路径 |

完整说明、生产示例与部署步骤见《[部署方案.md](部署方案.md)》。

## 接口一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 计算器页面 |
| GET | `/healthz` | 探活（用户数/引擎数/忙状态，不含隐私信息） |
| GET | `/api/capabilities` | 环境能力探测（无头/是否需令牌/多用户），前端据此调整 UI |
| POST | `/api/scan/start` | 启动扫码登录，`mode` 为 `inline`（页内二维码）或 `window`（弹窗，仅本机） |
| GET | `/api/scan/qr.png` | 二维码图片（`<img>` 无法带请求头，该路径额外支持 `?token=`） |
| GET | `/api/scan/state` | 扫码状态：等待/已扫码/已失效/已确认 |
| POST | `/api/scan/refresh` | 二维码失效后重新加载 |
| POST | `/api/scan/wait` | 窗口模式：阻塞等待用户在弹出的浏览器中完成扫码 |
| POST | `/api/options` | 读取教务系统的学年/学期真实选项 |
| POST | `/api/crawl` | 按 `{year, term}` 抓取成绩，返回归一化成绩行与被剔除项 |
| GET | `/api/progress` | 抓取进度日志（只返回本人） |
| POST | `/api/dump` | 手动生成诊断快照（已脱敏） |
| POST | `/api/close` | 关闭本人浏览器释放内存 |
| POST | `/api/debug/clean` | 清空**本人**的诊断快照 |

## 二次开发接口

页面（`static/index.html`）暴露 `window.ZongCeGradeIntegration`：

```js
// 抓取完成后的事件
window.addEventListener('zongce:grades-filled', e => { const rows = e.detail; /* ... */ });

// 读取 / 写入（写入项为 {score: number, credit: number}）
window.ZongCeGradeIntegration.getRows();
window.ZongCeGradeIntegration.fillRows([{ score: 88, credit: 3 }]);
```

归一化后的成绩行字段：`code, name, nature, credit, score, score_raw, gpa, year, term, teacher, classname, sources`。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| 二维码加载失败 / 空白 | 网络拦截了 `passport2.chaoxing.com`；点页面上的「改用弹窗扫码」（需 `ZC_HEADLESS=0` 的本机环境） |
| 提示二维码已失效 | 点「刷新二维码」再扫（约 2~3 分钟有效） |
| 抓取结果为空 | 确认所选学年学期已有成绩；改选「全部」再抓一次；必要时查看 `debug/` 快照 |
| 浏览器启动报错 | 执行 `playwright install chromium`，或用 `JW_CHROMIUM` 指定系统 Chrome 路径 |
| 页面提示「需要访问令牌」 | 服务端设了 `ZC_TOKEN`：用「网址?token=你的令牌」打开一次（会记住，之后无需再带） |
| 提示「你已有任务在进行中」 | 同一人同时只能跑一个重型任务；等它结束，或点「关闭浏览器」后重试 |
| 抓取失败 / 页面改版 | 已自动存快照到 `debug/`（含截图与脱敏后的 HTML/JSON），据此适配选择器 |

## 许可

本项目采用 [MIT 协议](LICENSE)。软件按「原样」提供、不附带任何担保，
使用本工具产生的一切后果由使用者自行承担；请务必遵守学校的教务系统使用规定（见文首合规提示）。

