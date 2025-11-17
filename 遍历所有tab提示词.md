目前的问题是：对于一些文档站（例如 LangChain 文档）中的 多 Tab 代码块 / 内容区域，我们只抓到了默认激活的第一个 tab 的内容，其他 tab（比如“uv”那一项）没有被抓到。
	•	典型例子：
https://docs.langchain.com/oss/python/langchain/overview
在页面的「Install」部分，有两个 tab：pip 和 uv。
现在的抓取结果只包含 pip tab 中的安装命令，没有包含 uv tab 中的安装命令。

二、本次要实现的目标

在不推翻现有项目结构的前提下，为当前的 Crawl4AI 批量爬虫增加下面的能力：
	1.	对于每一个被爬取的页面，自动遍历页面中所有「tab 组件」的每个 tab（例如 pip / uv / conda 等），让每个 tab 都处于“被点击激活”的状态至少一次。
	2.	在遍历完所有 tab 之后，把所有 tab 对应的内容都 采集出来，并 append 到同一个该页面的 Markdown 输出文件中。
	3.	对于典型的代码 tab 区域（例如安装命令、示例代码），要确保 每个 tab 的内容都有清晰的标记，如：
	•	使用 ### Install (pip)、### Install (uv)，或者
	•	#### [Tab: pip] / #### [Tab: uv]
这样在后续做 RAG 时可以保留 tab 的语义。

目标效果：
	•	对于 LangChain 这个示例页面来说，最终的 .md 文件中既要有 pip install -U langchain，也要有 uv add langchain 等 uv 安装命令。

建议的实现思路

请基于 Crawl4AI 已有的 Page Interaction / session / js_code 机制实现。整体思路大致如下，你可以在此基础上优化：
	1.	复用同一个浏览器会话：
	•	使用 session_id 来保证同一个 URL 的多次 arun() 调用运行在同一个浏览器页面上。
	•	第一次请求负责“正常加载页面并抓取默认内容”，后续请求只在当前页面上执行 JS 操作（js_only=True）。
	2.	识别页面上的 tab 组件：
	•	使用浏览器端的 JavaScript 遍历符合以下特征的元素：
	•	有 role="tab" 或 role="tablist"；
	•	或者常见 tab 组件使用的 CSS class / data-attribute，例如 [data-state]、[aria-selected] 等；
	•	也可以先写一个通用版：查找所有 button / a / div 元素，筛选出文本较短且包含于 ["pip", "uv", "conda", "curl", ...] 这类典型 tab 文案中的那些。
	•	最好是按照 tab-group 来处理：
	•	每个 tab group 内有多个 tab（例如 pip / uv），每个 tab 对应一个 panel；
	•	优先利用 aria-controls、id、data-* 等属性来建立 tab 与 panel 的对应关系。
	3.	依次点击每个 tab：
	•	在当前页面上执行 JS，将每个 tab 依次 click()：
	•	每 click 一次之前，记录即将激活 tab 的 label 文本（例如 "pip", "uv"）；
	•	click 之后，使用 wait_for 做两层等待：
	1.	等待 aria 属性，例如 aria-selected="true"；
	2.	等待 tab 对应 panel 的内容发生变化，比如代码块中的文本变化。
	•	每切换到一个新的 tab，都要抓取一次“当前 DOM 下的内容”（或至少抓取该 tab-panel 的节点内容）。
	4.	采集并合并 tab 内容：
	•	采集方式可以有两种，你可以择一实现：
	1.	全页截取再后处理：
	•	每激活一个 tab 后，调用 Crawl4AI 把全页 HTML → Markdown；
	•	再在 Python 侧根据 tab 文本给这一次截取的 Markdown 加上一个标题，例如：
## [Tab: uv]；
	•	然后 append 到该 URL 对应的 .md 文件中。
	2.	前端 JS 局部截取：
	•	在浏览器端 JS 中，找到当前激活 tab 对应的 panel（例如 .tab-panel[aria-labelledby=...]）；
	•	只把 panel 内部的 HTML 作为字符串返回给 Python 端（可以挂到 window.__C4A_COLLECT__ 之类的变量上，让 Crawl4AI 抓取）；
	•	再在 Python 中把这段 HTML 转 Markdown 并附上 tab 标题。
	•	不强制要求去重，但如果你能识别“同一 tab group 的多个 tab 共享大段重复描述”的情况，可以尝试简单去重。
	5.	保证对现有功能的最小侵入：
	•	当前项目已经有完善的：
	•	URL 遍历与任务队列；
	•	单页抓取配置（代理、超时、重试策略等）；
	•	Markdown 输出目录结构管理；
	•	请尽量用下面的方式扩展：
	•	在原有单页抓取逻辑外围，增加一个“可选的 tab 遍历流程”，通过配置开关控制；
	•	或者新增一个专门的 TabAwareCrawler / crawl_with_tabs() 函数，并在批量任务里调用它，而不是改动所有通用基础组件。

技术约束与风格要求
	1.	语言与框架：
	•	使用 Python 3.10+；
	•	保持与现有项目相同的依赖体系（Crawl4AI + asyncio）；
	•	不要引入重量级新依赖，除非绝对必要。
	2.	健壮性：
	•	对于 没有 tab 的页面：
	•	需要优雅跳过，不影响现有逻辑。
	•	对于 tab 结构不符合预期的页面：
	•	做好异常捕获和日志输出（例如 logger.warning("Tab detection failed for URL ...")），但不能中断整个批量任务。
	•	必须考虑在批量跑很多 URL 时的性能和内存占用，不要做极其低效的 DOM 操作。
	3.	代码风格：
	•	保持与现有项目一致的编码风格（类型注解、日志、异常处理等）；
	•	关键函数和关键 JS 代码块要有清晰的注释，尤其是：
	•	如何发现 tab；
	•	如何匹配 tab 与 panel；
	•	如何等待 tab 内容加载完成。
	4.	配置化：
	•	提供一个配置开关，例如：
	•	ENABLE_TAB_TRAVERSAL = True，或者
	•	在某个 CrawlerSettings / Config 对象中增加 traverse_tabs: bool = True；
	•	允许通过配置限制最多处理的 tab 数量（防止极端页面有非常多 tab，导致抓取时间过长）。