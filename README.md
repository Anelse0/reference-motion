# reference-motion

一个本地、项目制的代码动效 Skill。`create` 从 brief 创作，`match` 从真实参考锁帧重建。画面默认 Canvas 2D，声音由项目自己的 Python 代码合成。工具只负责空画布协议、测量、信号原语、版本与媒体文件，不包含影片、运行骨架、风格、叙事槽位或配乐模板。

本包交付 Skill、8 个命令行脚本、一个共享工具模块、依赖说明和工程测试。没有 SaaS、数据库、调度器、模型路由、Remotion 或审片应用。宿主使用其实际模型配置，无法取得模型身份时记录 `unknown`。

## v0.2.0 变化

本次迭代补齐 match 的测量与执行门槛：局部 MAD 峰值候选、颜色 ROI、墨迹/大写字高、逐帧模板追踪、多地标缩放/位移拟合、声音事件候选，以及绑定参考帧哈希的结构化 SPEC。每项明确 measured/reviewed/inferred/unknown，未知项只能进行限定帧诊断。完整制作必须先通过分析检查；最终导出还需关键对象逐帧误差、离散事件零帧误差和真实观看/听审证据。完整字段、命令和边界见 [match-analysis.md](references/match-analysis.md)。

v0.1 快照与反馈保持原版本绑定；旧工具哈希禁止被新工具悄悄重跑。需补全分析后建立新快照，或使用旧版本工具复现。create 的空画布与程序化声音协议继续保留。

## 本次结果与限制

实际执行、产物路径和通过/失败范围见 [TEST_RESULTS.md](TEST_RESULTS.md)。测试项目由 [build_demos.py](tests/build_demos.py) 和 [build_match_fixture.py](tests/build_match_fixture.py) 生成；它们不是正常创作可选模板。视频、逐帧图片、分轨、版本快照和工作区记忆保留在本地，不提交到源码仓库。

两个原创测试分别演示信息分包重组和两个接近频率的叠加。前者用空间分散与汇合、离散短音；后者用坐标波形、持续正弦声。二者的 brief、图形和声音均为本次按目标编写。match 使用的受控合成素材只验证分析、渲染和对齐工程，不能证明对真实未知参考的逆向能力。

没有真实用户评片反馈，不填充用户偏好或接受记录。反馈修订和跨项目学习的测试数据须明确标为 fixture，并与生产记忆分开。未实际听审或连续观看的项目保留 `unverified`，只能交付 preview。数值测量、静帧检查、连续观看、听审和用户接受是不同证据。

## 安装与依赖

需要 Python 3.9+、NumPy、Pillow、Node.js、Playwright/Chromium、FFmpeg/ffprobe，以及本项目声明的字体。精确依赖文件记录在包内。工具不自动下载浏览器、不安装语音服务、不访问付费 API，不把关闭浏览器沙箱作为默认修复。

在此目录准备隔离依赖：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
. .venv/bin/activate
npm ci
```

可使用已安装的兼容 Chromium，渲染时传 `--browser /absolute/path/to/browser`；需要下载 Playwright 浏览器时，由有相应网络权限的操作者执行 `npx playwright install chromium`。不使用本地 npm 依赖时，`RM_PLAYWRIGHT_MODULE` 可指向已安装 Playwright 的模块入口。`RM_PYTHON` 或渲染命令的 `--python` 选择具有 Python 依赖的解释器；`RM_BROWSER` 可代替 `--browser`。

将必要文件复制到用户指定工作区的 `.agents/skills/reference-motion/`（Codex）或 `.claude/skills/reference-motion/`（Claude Code）。示例中的目标工作区由用户决定：

```sh
mkdir -p /absolute/workspace/.agents/skills/reference-motion
rsync -a SKILL.md scripts references package.json package-lock.json requirements.txt /absolute/workspace/.agents/skills/reference-motion/
```

随后在**安装副本**中安装或配置所需依赖；不要将开发机的 `node_modules` 当作可移植包。项目、历史与工作区记忆继续保存在独立 motion-workspace。不要自动修改全局指令文件，也不要复制完整偏好库、密钥、字体或无关客户素材。

文件存在和 frontmatter 校验只证明可安装形状；宿主是否发现并实际加载，还需在对应宿主新会话调用 `$reference-motion` 验证。未执行的宿主或跨宿主接续不得标记通过。

## 实际调用

以下从 Skill 根目录运行。项目路径始终显式传入，源文件路径相对于项目解析。所有命令提供 `--help`。

```sh
python3 scripts/project.py init --workspace /absolute/motion-workspace --id film-a --mode create
python3 scripts/project.py init --workspace /absolute/motion-workspace --id film-b --mode match --ref /absolute/reference.mp4
```

初始化只建立身份和必要元数据，不生成示例镜头、SPEC 正文、HTML 或 core.js。Agent 根据任务编写 BRIEF.md、SPEC.md、timeline.json、src/main.js 和 audio/score.py，并补全 project.json。协议见 [rendering.md](references/rendering.md) 与 [audio.md](references/audio.md)；完整生产与验收见 [workflow.md](references/workflow.md)。

```sh
python3 scripts/project.py check --project /absolute/motion-workspace/projects/film-a --stage render
python3 scripts/project.py snapshot --project /absolute/motion-workspace/projects/film-a --revision r001
node scripts/render.mjs --project /absolute/motion-workspace/projects/film-a --revision r001 stills 0,12,24
node scripts/render.mjs --project /absolute/motion-workspace/projects/film-a --revision r001 full 0 96
python3 scripts/audio.py --project /absolute/motion-workspace/projects/film-a --revision r001 synth
python3 scripts/audio.py --project /absolute/motion-workspace/projects/film-a --revision r001 check
python3 scripts/measure.py check --project /absolute/motion-workspace/projects/film-a --revision r001
python3 scripts/encode.py --project /absolute/motion-workspace/projects/film-a --revision r001 --preview
```

`96` 和示例帧号仅展示命令语法，须改成当前项目的总帧数和检查点。帧区间是 0-based `[0,N)`；fps 使用 `{num,den}`。只有内部效果采样可以为分数帧，不自动改变成片帧率。`timeline.json` 是共享事件时间的唯一数值来源。

match 在建立真实合同后先运行 `python3 scripts/analyze.py --project P`，用 `measure.py track --help` 查看实际分割与跟踪选项，按 [match-analysis.md](references/match-analysis.md) 完成分析并通过 `match.py gate` 后才可创建生产快照。编码 preview 后运行 `match.py verify --project P --revision R`，并运行 `python3 scripts/sync.py --project P --revision R --kind reference`。create 不抽取假参考或生成无意义的参考相似度。前后版本比较使用 `sync.py --project P --revision R --kind revision --before BEFORE.mp4 --after AFTER.mp4`，并清楚标为版本对照。对比视频为静音检查辅助，不拉伸较短版本，声音另外验收。

每次外发审阅都绑定一个冻结 revision。渲染和合成只读取该 revision 的快照，拒绝悄悄复用不同来源的输出。修改工作源码后另建 r002。恢复命令会保留当前工作副本，并从历史产生新版本：

```sh
python3 scripts/project.py restore --project P --from r001 --revision r003
```

最终 QA 在 `out/R/review/qa.json`，每项包含 `check/status/evidence/note`，其中 status 为 `pass/fail/unverified/not_applicable`。必要检查的证据须有绑定实际 preview SHA-256 的 JSON 报告；内容、动态、听审及参考审查另需真实 reviewer、method、observations。`requiredChecks` 只能增加检查；不能用空数组移除默认门槛。只有必要检查确实通过、无未解决失败或关键未知时才可调用 `encode.py --final`。它不会因为生成了 MP4 就自动宣称听过、看过或用户满意。填写 JSON 不能替代真实审查。

## 反馈、偏好与能力

真实反馈由 Agent 写成记录，再用 `project.py feedback --project P --record RECORD.json` 追加。记录保留原话、来源、被评版本、解释、修改、技术结果、用户结果和证据。新结果或更正另追加，不能改写用户原话。没有准确版本时使用 `null`，不能猜测用户看过哪版。

`project.py memory --project P` 只列出当前作用域适用、未撤销的记录及忽略原因。确需采用的记录 ID、输出中的 `recordHash` 和用途写到项目 `used-memory.json`，快照再次校验。偏好 scope 使用 `{type,id}`；type 为 project/brand/task/workspace，brand/task 还需明确 client。当前要求通过 `memoryOverrides` 排除被覆盖的记录，match 锁定区优先于偏好。

记忆文件是工作区下两个普通 JSON 数组。`memory-write --workspace W --kind preferences --input FILE --expected-hash HASH` 用旧文件 SHA-256 检测并发覆盖；capabilities 使用同一入口。确认偏好需要明确用户来源，已验证能力需要带 hash 的真实检查与代码证据。详细字段、条件和撤销规则见 [learning.md](references/learning.md)。

```sh
python3 scripts/project.py forget --workspace W --id RECORD_ID
python3 scripts/project.py forget --workspace W --id RECORD_ID --delete
```

撤销/删除阻止后续使用，包括从旧项目恢复后继续制作。源反馈、历史成片和外部备份是不同删除范围，不能宣称一次 memory 删除已抹除一切。学习不开启也不妨碍正常生产。示例规范和单次“喜欢”不会自动变成普遍规则或技术能力。

## 测试和复现

工程测试入口及实际版本见 [TEST_RESULTS.md](TEST_RESULTS.md)。`tests/build_demos.py --workspace EMPTY_OR_NEW_WORKSPACE` 只为两个明确的测试 brief 建立本次原创源码，不渲染，也不复用已有项目。它拒绝覆盖同 ID 项目。正常创作不应导入测试源码作为起点。

最终限制以测试报告为准：工具不自动判断创意成立或听感，测试媒体不替代真实参考；本地执行的项目 JS/Python 仍是代码，需要按普通工程信任边界检查。数值测量不能证明不可观察的感知属性。环境、字体、浏览器、工具变动后需要重验，不承诺跨机器逐像素一致。

## 仓库内容

仓库保留 Skill、脚本、依赖锁定、可复现测试源码，以及去除本机路径的测试证据。`.gitignore` 排除缓存、虚拟环境、安装依赖、生成的媒体、打包文件、项目输出与本地记忆。生成测试项目时选择仓库之外的工作区。
