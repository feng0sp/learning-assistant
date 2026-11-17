# 验证说明

验证分为后端行为检查、真实浏览器操作和持续运行检查。所有自动化检查使用临时数据目录，独立于真实的 `data/`。

## 后端检查

运行：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_app -v
```

检查内容包括：

- 标题模式不冒充读取教材、没有虚构来源。
- 文件导入、页码选择和资料模式的来源引用。
- 扫描 / 空白页、错误页码及不存在的资料在模型调用前失败。
- 长章节的前后部分都会进入梳理流程。
- 模型格式和引用出错时修正一次，仍错误则不保存为成功章节。
- 密钥不通过接口回传，换服务地址不会复用旧密钥。
- 鉴权失败、限流和回答截断有可操作的错误信息。
- 笔记、作答、进度和追问能够读取恢复。
- 跨站写操作、错误参数、无效文件类型被拒绝。
- 中文文本编码与图片请求格式。

模型服务使用 `tests/fake_provider.py` 中的**本地 HTTP 测试替身**。这能检查实际请求协议、错误传播和数据库处理，但不能替代真实模型的质量验证。

## 浏览器检查

首次准备测试依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

运行：

```powershell
.\.venv\Scripts\python.exe -m tests.browser_check
```

测试会在独立本地服务中打开 Chromium，点击界面并检查：示例展示、例题展开、类比、选题与重做、笔记保存、学习完成标记、模型设置、两种生成流程、来源查看、追问、导出、失败重试、窄屏和键盘操作。

截图和机器可读报告输出到 `test-results/`。测试报告明确标记模型服务为测试替身。

补充边界检查可运行 `python -m tests.browser_edges`，覆盖长标题、模型或资料中的 HTML 文本、追问引用跳转、嵌套设置窗口、失效任务和损坏的浏览器草稿。

`python -m tests.browser_review` 验证章节搜索与完成状态筛选、跨章节错题重做、答案揭示、与原章节的同步、其他题目的草稿保留、手机布局和返回原题。它不使用任何模型服务，题目来自隔离测试数据。

`python -m tests.browser_backups` 验证导出前保存笔记、密钥不进入备份、文件校验、冲突副本、重复导入、手机布局和不可信标题文本。它还模拟服务端已完成导入但浏览器丢失响应，再验证重试不会生成重复副本。后端测试通过受控的数据库写入失败检查整批回滚，包括去重记录一起回滚。

`python -m tests.browser_materials` 使用含真实文字层和嵌套书签的测试 PDF，验证选章填入标题与页码、范围预览、目录标题转义、草稿恢复和手机端生成。它将早期预览请求延后返回，检查旧文字不会覆盖新页；还核对生成后保存的来源只包含所选页。乱序目录只定位起始页，不推断整章范围。后端也分别检查正常及乱序目录。

资料测试另覆盖替换上传期间点击移除、合并文字超过上限时保留原输入，以及已移除资料的迟到错误不能覆盖新操作提示。中英文追问检索的后端测试使用超过上下文预算的干扰片段，验证「请解释拥塞窗口」仍可选中后面的相关原文，且独立的 TCP 不会被 tcpip 的子串匹配挤出上下文。

结构校验测试包含模型返回空白解题步骤的情况：修正后合法才保存，连续两次不合法则失败。它验证内容结构，不判断解题思路在知识上是否正确。

`python -m tests.browser_handout` 验证保存笔记后打开打印窗口、调用浏览器打印、A4 PDF 的中文内容与页码、自测与答案分离，以及隐藏答案、笔记和来源时 PDF 中确实不包含这些部分。导入的 HTML 仍按普通文字显示。生成的 `test-results/handout-full.pdf` 另用 Poppler 渲染逐页检查；未测试真实打印机硬件或所有浏览器的分页。

`python -m tests.large_library_check` 用隔离数据验证单份备份的 1000 章上限：整批导入、重复请求去重、浏览器搜索与保存最后一章、导出完整内容，以及 1001 章的文件整份拒绝。报告记录本次操作时间；这些是合成样本的观察值，不作为通用性能承诺。

笔记保存还有专门的时序检查 `python -m tests.browser_notes_race`：在切换章节的请求期间、笔记保存响应返回前继续输入，确认最后的文字能够保存；多个保存请求排队时，旧响应不会把后续编辑误判为已经保存；等待保存时改选章节，以最后一次选择为准。这三个场景使用浏览器请求拦截控制时序，实际写入隔离的 SQLite 数据库。

该脚本还在 Markdown 导出触发的保存响应返回前补写笔记，再读取实际下载的文件，确认新文字也在文件中。这个场景曾复现遗漏问题，修复后作为第四组回归检查保留。

## 干净安装与真实进程重启

`tests/cold_install_check.py` 可以使用一个仅安装 `requirements.lock.txt` 的新虚拟环境，验证应用不依赖开发环境中的浏览器工具或其他包。它通过真正的启动入口启动服务，保存笔记与作答，停止并重新启动进程，再检查数据和导出结果。

示例：

```powershell
python -m venv test-results/clean-env
.\test-results\clean-env\Scripts\python.exe -m pip install -r requirements.lock.txt
.\test-results\clean-env\Scripts\python.exe -m tests.cold_install_check --python test-results/clean-env/Scripts/python.exe
```

`scripts/build_release.py` 按明确的文件清单生成源码压缩包，并生成文件校验清单。运行数据、模型密钥、虚拟环境与测试产物不会放入压缩包。

交付前还会检查压缩包本身：核对其中每个文件的 SHA-256，解压到临时目录，再使用仅含运行依赖的 Python 启动解压后的程序，完成启动、重复启动和重启恢复检查。这样可以发现“开发目录能用，但压缩包缺文件”的问题。

```powershell
.\.venv\Scripts\python.exe scripts/build_release.py
.\.venv\Scripts\python.exe -m tests.release_check --archive release/知章学习助手.zip --python test-results/clean-env/Scripts/python.exe
```

Windows 首次启动入口另用 `python -m tests.bootstrap_check --archive release/知章学习助手.zip` 检查。它将压缩包解压到带中文和空格的临时目录，实际运行 BAT，由它创建新的 `.venv`、安装依赖并启动。这项检查需要网络或可用的 pip 缓存；不会打开可见的命令窗口，也不会访问真实模型。

## 持续运行检查

`tests/endurance_check.py` 是按需运行的持续使用检查，不属于每次改动都要运行的快速测试。

它保持浏览器与后端运行，定期写入笔记、作答并刷新读回，交替检查模型服务报错与恢复，周期性重启测试服务，并进行 SQLite 完整性检查。测试同时记录进程内存和每轮耗时，用于发现持续运行中的资源或状态问题。

当前脚本还反复检查错题队列、已上传 PDF 的选页生成，以及重复导入旧备份时保留更新后的笔记。可用 `--report-name endurance-sep26` 单独保存本次报告，避免覆盖此前版本的运行记录。

调用时提供带时区的结束时间：

```powershell
.\.venv\Scripts\python.exe -m tests.endurance_check --until-utc 2026-09-23T10:36:15+00:00 --interval 60
```

上面的时间是开发时示例。自行运行时请替换为未来的时间。报告持续写入 `test-results/endurance-report.json`；只有 `status` 为 `passed` 才代表本次检查运行到设定结束时间并通过。

报告中的应用代码指纹覆盖 `backend/*.py` 与 `dist/` 的文件，用于判断检查期间后端和页面代码是否改变。说明文档和其他测试脚本不包含在这个指纹里。

## 需要真实模型继续验证的部分

- 所选择服务的具体协议兼容性、网络延迟和账户权限。
- 不同教材章节的知识覆盖与事实准确性。
- 新生成计算题、选择题和解析是否正确。
- 视觉模型对你的截图、公式和表格的识别质量。
- 资料引用是否在语义上真正支持对应知识点。

没有真实模型凭据时，不能将这些项目报告为已经通过。

## 示例与交互的参考依据

内置示例的最长前缀匹配和 TTL 表述对照了 [RFC 1812](https://www.rfc-editor.org/rfc/rfc1812.html)；关于 /31 点对点链路不应套用普通子网「减 2」规则的说明对照了 [RFC 3021](https://www.rfc-editor.org/rfc/rfc3021.html)。示例仍属于本项目编写的教学材料，不是这些文档的逐字摘录。

标签页支持左右方向键、Home / End 与单一 Tab 焦点入口，参考 [W3C 的 Tabs Pattern](https://www.w3.org/WAI/ARIA/apg/patterns/tabs/)。浏览器检查还覆盖 200% 文字放大。上述检查不代表完整的无障碍合规认证。

WebMCP 在普通浏览器中会先检测支持情况。当前测试浏览器没有原生注册接口，已通过测试用注册器验证工具的名称、有效输入、页面状态变化和无效输入；原生环境兼容性未验证。

## 追问切换与延迟响应回归

运行 `.\.venv\Scripts\python.exe -m tests.browser_questions`。该脚本使用隔离数据库、本地测试模型和真实 Chromium，覆盖章节草稿隔离、已加载回答与迟到响应去重、修改后恢复同样文字的草稿保留、切走后只清除已提交且未修改的草稿、失败后保留输入。报告为 `test-results/questions-report.json`，不验证真实模型内容质量。

## 页面加载与大记录量

新增检查均使用隔离数据，运行方式：

```powershell
.\.venv\Scripts\python.exe -m tests.browser_startup
.\.venv\Scripts\python.exe -m tests.browser_density
.\.venv\Scripts\python.exe -m tests.browser_mobile
.\.venv\Scripts\python.exe -m tests.browser_drafts
```

- `browser_startup` 挂起模型设置请求，确认章节仍能显示；再检查过期设置隔离、失败重试、首章加载前禁止编辑，以及取消导航后的旧错误。
- `browser_density` 建立 2000 条问答与 31 道错题，检查初次渲染数量、查看旧问答时的位置、完整 JSON 备份与翻页选项保留。
- `browser_mobile` 检查紧凑导航的键盘开关、章节搜索、返回阅读、320/390/720 像素宽度、两倍字体和恢复桌面布局。
- `browser_drafts` 在 8 万字符正文上连续派发 50 次编辑事件，检查合并写入及关闭、刷新前最后输入的恢复。派发事件是压力测试，不是对真人打字速度的推断。

千章测试还检查每页最多 50 条、上一页/下一页、跨全部记录搜索，以及深链接打开后定位对应页。报告中的毫秒数是本机合成样本观察，不能推广为所有设备上的速度保证。

持续运行脚本每三轮使用手机尺寸打开错题，每五轮通过本地测试模型追问，并检查页面不出现水平溢出；仍覆盖笔记、作答、备份、生成、数据库检查和定期重启。

可选使用 `python -m tests.browser_density --cpu-rate 6` 通过 Chromium 的 CPU 降速模拟检查大记录量交互。该参数不改变程序代码，输出独立的 `density-cpu6-report.json`；它不代表某一真实手机或电脑的基准测试。

另外可运行 `python -m tests.session_check --until-utc <未来的UTC时间>` 检查不刷新页面的长会话。它在两个章节间反复切换、保存笔记、作答、维护追问草稿与展开旧问答，记录可见元素数量和 Chromium 内存观察值。数据仍位于独立临时目录；本轮报告为 `test-results/session-sep27-report.json`。内存观察值不是泄漏证明，JavaScript 垃圾回收会让数值波动。

`python -m tests.browser_quiz_pending` 用真实接口配合延迟响应，覆盖其他作答返回或切换标签后的提交锁、自测与错题复习之间的互锁、关闭复习窗口后仍等待提交，以及失败时释放状态并保留选项。该检查曾复现按钮被提前解锁的问题，修复后 3 组通过。

## 2026-09-29 增补的恢复与编辑检查

- `python -m tests.browser_quiz_pending` 现覆盖 5 组：另加迟到的复习结果不能清除原题的新选项，以及不能抢走另一题的键盘焦点。
- `python -m tests.browser_settings_race` 覆盖 4 组设置操作：重复打开、保存期间继续编辑、后续提交、读取或保存失败重试。只使用虚构测试密钥。
- `python -m tests.browser_job_recovery` 覆盖 4 组生成完成后的恢复：列表请求失败、笔记冲突合并、用户主动切换章节优先，以及 320 像素两倍字号下的操作与布局。
- `python -m tests.browser_progress_race` 覆盖 3 组完成标记的章节隔离、返回等待中的课时和失败重试。
- `python -m tests.browser_startup` 现覆盖 8 组，包含首页恢复上次阅读章节、明确链接优先，以及浏览器存储不可用或记录不存在时回退。

长会话脚本现在可传 `--report-name` 设置报告名称，避免覆盖历史验收记录，例如 `python -m tests.session_check --until-utc <未来UTC时间> --report-name session-sep29`。名称仅允许字母、数字、连字符与下划线。

## 完整计网课程与计算实验

```powershell
.\.venv\Scripts\python.exe -m tests.network_course_check
.\.venv\Scripts\python.exe -m tests.browser_network_course
```

接口脚本的 7 项检查覆盖全部课程结构与引用契约、幂等打开及进度、综合卷覆盖、备份兼容、子网边界、流水线时延与 CRC。CRC 另用整数位运算独立校验发送码字可被生成式整除；时延以已知手算数值校验。计算检查不宣称覆盖全部教学内容的事实正确性。

浏览器脚本的 9 组检查覆盖离线目录和搜索、逐课全部内容、笔记/前后课/刷新、完成与正确数分开统计、综合练习、三种实验和无效输入、旧响应隔离、手机双倍字号、目录故障重试及笔记冲突后继续学习。浏览器记录不得出现外部请求或页面脚本错误。报告为 `test-results/network-course-browser-report.json`。

交付包检查另验证解压后的课程接口、24 课目录、课程 JavaScript、CRC 与综合练习，确保新内容真实进入压缩包。

## 成长奖励与专注模式

运行 `python -m tests.rewards_check` 和 `python -m tests.browser_rewards`。9 项后端检查覆盖首次奖励、全对奖励、并发重复请求、次日复习去重、重新初始化持久性、每日推荐、全课程 1200 分上限，以及完成标记/演示/综合卷/无效请求不发奖。

浏览器 6 组检查覆盖奖励反馈与徽章、刷新保留、专注切换、窄屏两倍字号、读取失败重试、另一窗口答题后的更新，以及课程请求/重绘合并。性能检查明确观察到连续三次打开只发起一条在途目录请求；同一帧内 50 次合成搜索输入触发一次列表重绘。这个结果不是整站提速比例或真人打字速度基准。截图和报告保存在 `test-results/`。

## 成长备份、每日练习与问答分页

- `python -m tests.reward_backup_check`：7 项检查覆盖第 2 版导出、空库恢复、去重、纯成长恢复、旧版兼容、无效记录、事务回滚，以及并发恢复与答题共用账本时不重复加分。
- `python -m tests.browser_reward_backup`：4 组检查覆盖真实下载、预览、纯成长恢复、服务已提交但响应丢失后的重试，以及旧版提示和窄屏布局。
- `python -m tests.browser_daily_practice`：4 组检查覆盖导航失败重试、重复点击阻止、题目焦点、笔记冲突和完成每日目标后的可选继续。
- `python -m tests.browser_growth_completion`：3 组检查覆盖全课程 1200 分与全部徽章、窄屏放大字体，以及通关后再答错仍能进入复习。
- `python -m tests.message_page_check`：4 项检查覆盖首屏响应上限、翻页时并发新增、完整备份及输入边界。
- `python -m tests.browser_message_pages`：5 组检查覆盖失败重试、切章时迟到响应、全部分页顺序和焦点、旧后端兼容，以及较早提交的回答迟到时仍不遗漏中间记录。

持续奖励检查：`python -m tests.rewards_endurance --until-utc <未来UTC时间> --interval 20 --restart-every 24 --report-name <报告名>`。它使用独立临时数据库和真实服务子进程，循环学习 24 课、校验积分上限、重复导入与笔记保存，并定期真实重启进程。报告记录源码是否在运行期间变化；有变化的运行不能称为最终代码的完整冻结验收。

可运行 `python -m tests.regression_check --report-name regression` 一次执行 30 个独立回归脚本，每个脚本在不同进程中使用隔离数据。各脚本日志、退出状态、运行时间及源码指纹存放在 `test-results/regression/`；首个失败会停止后续脚本，以便先修复问题。此命令需要安装开发依赖和 Playwright Chromium。


## 微课堂前三章

`python -m tests.microclass_check` 的 13 项检查覆盖 3 章/12 节/120 题、逐项解析、来源标识、原 32 项独立计算、旧记录保留、跨课程每日推荐、并发奖励去重及超过原 96 条上限的备份恢复。补充检查覆盖新增例题的数值与过程推演、原题指纹、12 节原版讲义更新、启动与恢复触发更新、用户改写资料保护，以及更新时所有用户字段与奖励不变。

`python -m tests.browser_microclass` 的 9 组检查覆盖全部小节离线呈现、逐项解析和刷新、错题跳转、独立课程导航、迟到目录请求隔离、笔记冲突合并、窄屏放大字号、备份往返、补充资料链接与非可信正文转义；记录脚本错误与外部请求。交付包检查另验证解压后的微课堂接口和真实进程重启后笔记、答题、积分保留。
