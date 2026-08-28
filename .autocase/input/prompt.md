0. 铁律（违反即不合格）
允许新增第三方依赖：Python/Node.js/Go 均可按需通过 pip install、npm install、go get 引入新依赖包，但引入前需确认包与项目协议兼容、体积合理、维护活跃，不引入与现有功能明显重复的包。

允许新增代码文件：不删/不重命名/不合并六大模块（core/live2d_builder/drivers/llm_bridge/api/web）；不新增顶级模块目录；允许在现有模块/子目录下按需创建新的 .py/.ts/.go 代码文件（如工具类、拆分过长的类、新增子模块组件），但每个新文件必须职责单一、被现有代码显式引用、不得成为孤儿文件。

零字段删除：以下常量字典只追加键，不删/不改现有键——live2d_builder/blendshapes/parameters.py 中的 STANDARD_PARAMETERS、live2d_builder/bones/deformers.py 中的 BoneHierarchy.STANDARD_BONES 和 LAYER_BONE_MAP、drivers/desktop_pet/pet.py 中的 DesktopPet.IDLE_CONFIG 和 EXPRESSIONS、llm_bridge/emotion/analyzer.py 中的 EMOTIONS 和 EMOTION_KEYWORDS 和 EMOTION_TO_PHYSICS。

向后兼容：core/workflow.py 中 WorkflowManager.run 的 9 阶段命名顺序（generating→qa_check→optimizing→layering→psd_export→mapping→live2d_export→done）原样保留；api/handlers/handlers.go 注册的所有 HTTP 路径（/api/health、/api/status、/api/generate、/api/chat、/api/chat/stream、/api/export/live2d、/api/export/spine、/api/export/psd、/api/deploy/desktop、/ws/progress）必须保留且响应结构兼容。

测试必绿：每完成一项必须跑对应模块测试，退出码为 0。

禁止提交调试语句：Python 不用 print()，TS 不用 console.log()，统一走现有 logger。

【最高优先级】最大跑通 · 最大优化：

端到端全链路必须跑通：从"输入提示词/上传图片"→ 图像生成 → QA → 分层 → PSD 导出 → 52层映射 → Live2D 模型导出 → 桌宠启动 → Web 预览，整条管线不得在任何环节因非致命错误而中断。
失败开放（Fail-Open）原则：每个可能失败的环节都必须有工作的降级兜底。模型加载失败→fallback下一级模型；MediaPipe摄像头不可用→桌宠进入IDLE纯动画模式；某参数映射缺失→跳过该参数而非崩溃；外部API超时→返回本地兜底结果；纹理烘焙失败→输出简化model3但仍可加载；权重计算失败→输出无权重但合法的model3。所有 try/except 必须跟随功能性 fallback，禁止仅 log 然后 raise 或 return None 导致下游崩溃。
关键路径必须有 smoke test：修改后必须能完成一次端到端 WorkflowManager.run() 调用不抛异常、API服务启动可响应 /api/health、前端 npm run build 产出可部署静态文件。
性能优化要求：语义分割单张图 ≤15s（GPU）/≤45s（CPU fallback）；桌宠渲染帧率稳定 ≥30fps；前端Web预览加载模型 ≤5s；避免主线程阻塞。
优先级排序：跑通 > 功能完整 > 性能 > 代码美观。宁可降级输出可用结果，也不要因追求完美而让管线在中间断掉。
1. 项目真实结构（以代码为唯一事实）
本项目是端到端 AI 驱动的 Live2D 二次元桌面伴侣全栈平台，六大模块：

core/ 是 Python 内核：segment_engine/ 目录下 semantic.py 是主分层器 SemanticLayerer（支持 ISNet/SAM/rembg→HSV 兜底）、kmeans.py 是 KMeansLayerer 颜色聚类兜底、layers52.py 是 52 层 ARKit 标准映射、composer.py 是合成器；image_gen/ 是图像生成路由（支持 pollinations/seedream/sensenova）；psd/creator.py 负责 PSD 导出；qa/engine.py 负责图像质量打分；workflow.py 是 WorkflowManager，核心入口，编排 9 阶段管线。

live2d_builder/ 是 Live2D 自动 Rigging 模块，负责 Cubism4 格式导出：pipeline.py 是 Live2DBuilder 加 RiggingPipeline 兼容包装；bones/deformers.py 是 BoneHierarchy（32 骨骼）加 DeformerHierarchy（Warp/Rotation 变形器）；mesh/ 目录下 generator.py 是 MeshGenerator（Delaunay 三角化）、uv_unwrapper.py 是 UVUnwrapper（skyline 图集打包）；blendshapes/ 目录下 parameters.py 是 ParameterSet（28 个 Cubism 标准参数加自定义参数）、expressions.py 是 ExpressionBuilder（28 个表情）；physics/config.py 是 PhysicsBuilder（支持 hair/body/breath/skirt/ear/tail 物理）；exporter/ 目录下 model3_exporter.py 是 Model3Exporter（导出 model3.json）、texture_atlas.py 是纹理烘焙；validator/model_validator.py 是模型校验器。

drivers/ 是实时驱动模块：face_tracker/ 目录下 mediapipe_tracker.py 是 MediaPipeFaceTracker（468 特征点）、blendshape_mapper.py 是 BlendShapeMapper（52 个 ARKit BlendShape）；audio/capture.py 是 AudioCapture 音频采集；desktop_pet/ 目录下 window.py 是 DesktopPetWindow（pygame 透明窗口）、animator.py 是 DesktopPetAnimator、pet.py 是 DesktopPet（主 Runtime，核心渲染循环）；live2d_runtime/renderer.py 是 Live2D 渲染器。

llm_bridge/ 是 LLM 对话网关：providers/router.py 是 ProviderRouter 提供商路由；emotion/analyzer.py 是 EmotionAnalyzer（支持 7 种情绪：happy/sad/angry/surprised/shy/neutral/confused）；chat_session.py 是会话管理；asr/ 和 tts/ 分别是语音识别和语音合成目录。

api/ 是 Go REST API（Gin 框架）：main.go 是入口；handlers/handlers.go 是 Handler，包含所有 HTTP 端点处理；models/models.go 是数据模型；services/ 目录下 cache.go 是 RequestCache、chat_service.go 是聊天服务、python_bridge.py 是 Python 桥接、websocket_hub.go 是 WSHub（WebSocket 进度广播）。

web/ 是 Next.js 前端：pages/ 目录下有 index.tsx、generate.tsx、layers.tsx、live2d.tsx（Live2D 参数调试页，核心交互）、chat.tsx、export.tsx；lib/ 目录下 live2d-player.ts 是 Live2DPlayer（PixiJS，当前是 Sprite 假渲染）、layer-renderer.ts、api-client.ts、websocket.ts；components/ 目录下有 ModelCanvas.tsx、ParameterSlider.tsx 等组件。

核心管线位于 core/workflow.py 中 WorkflowManager.run 方法（第299-504行）：Prompt/图片 → generating（生成）→ qa_check（打分）→ optimizing（降噪超分）→ layering（语义/聚类/HSV 分层）→ psd_export（PSD导出）→ mapping（52层映射）→ live2d_export（RiggingPipeline→model3.json）→ deploy（可选桌宠部署）→ done。

2. P0 Bug 修复（共 6 项，必做）
Bug 1 · API 路径穿越漏洞（最高优先级）
文件：api/handlers/handlers.go 中的 isPathSafe 函数。 问题：当前只检查路径中的 ".."，未阻止绝对路径（如 /etc/passwd 可绕过）和空字节注入。 修复：在函数开头添加绝对路径检测（调用 filepath.IsAbs）和空字节检测（调用 strings.ContainsRune 检查 0 值），若任一命中直接返回 false；保留原有的 ".." 检查和 filepath.Clean 调用。 验证：进入 api 目录执行 go test ./... 测试全部通过。

Bug 2 · API 版本号硬编码
文件：api/handlers/handlers.go 中 HealthCheck 方法第59行。 问题：版本字符串 "v10.1-go" 在代码中出现三处硬编码，而 api/config/config.go 中已有 Version 字段未被使用。 修复：全局搜索 "v10.1-go" 字符串，将所有硬编码版本号替换为 h.cfg.Version；若 cfg.Version 为空则 fallback 到默认值，但代码中不再出现字面量硬编码。 验证：请求 /api/health 接口，返回结果中不再包含硬编码的版本字符串。

Bug 3 · Chat/ChatStream 重复代码 + sessionKey 风险
文件：api/handlers/handlers.go 中的 Chat 和 ChatStream 两个方法。 问题：两个方法有 40 多行重复的会话恢复逻辑，且 sessionKey 字段名大小写处理可能不一致。 修复：抽取私有辅助方法 getChatContext（签名：接收 gin.Context，返回 ChatRequest、ChatContext、error），统一完成 BindJSON、session_id 提取、LoadOrCreateSession、返回上下文这几步；让 Chat 和 ChatStream 都调用该方法，删除重复代码，确保两个方法对 session_id 字段名、大小写、默认值的处理完全一致。该方法可直接加在 handlers.go 内，也可按需在 api/handlers/ 或 api/services/ 下新建小工具文件，保持职责单一。 验证：进入 api 目录执行 go test ./handlers/... -run Chat -v 测试通过；两个端点行为向后兼容。

Bug 4 · WSHub 引用路径校验
文件：api/handlers/handlers.go 的 import 部分和 Handler 结构体。 现状：WSHub 位于 api/services/websocket_hub.go，类型是 services.WSHub。 修复：全局搜索 "websocket." 引用，确保没有错误引用不存在的 api/websocket 包；如有错误 import 统一改为 services.WSHub。 验证：进入 api 目录执行 go build ./... 无编译错误。

Bug 5 · RiggingPipeline 返回字段与 workflow 读取不一致
文件：live2d_builder/pipeline.py 中 RiggingPipeline.run 方法（第408-420行）；core/workflow.py 中 live2d_export 步骤读取 rig_result 的位置（约第361-372行）。 问题：model3.json 路径字段名依赖 exporter 返回键，workflow 有时拿到 build_meta 路径而非 model3 路径，导致下游导出和部署失败。 修复：在 RiggingPipeline.run 返回前显式对齐结果字段——调用 builder.build(layers) 后，检查结果中是否有 model3_json 键且值以 .model3.json 结尾；若没有，依次尝试 model3_json、model_json、model3_path 这几个键名，找到第一个以 .model3.json 结尾的值就设为 model3_json。同时在 workflow.py 取值处加 isinstance 校验：若结果是 dict 则取 model3_json 字段；若是字符串则直接使用；都不是则在 build_meta 同目录下找第一个 .model3.json 文件。 验证：执行 python -m pytest tests/unit/test_live2d_builder.py tests/integration/test_workflow_contract.py -v 测试通过。

Bug 6 · 前端 Export Model3 按钮 onClick 为空
文件：web/pages/live2d.tsx，搜索 "Export Model3" 或 "导出" 对应的 Button 组件。 问题：onClick 属性是 undefined，点击按钮无反应。 修复：绑定真实的导出逻辑，复用现有的 /api/export/live2d 接口。点击时设置导出中状态，然后 POST 请求该接口，请求体传入当前模型目录（从页面现有 state 中取 currentModelDir），成功后将返回的 blob 创建为 Object URL，创建临时 a 标签触发下载，文件名用 live2d_加时间戳.zip，下载完成后释放 Object URL；失败时弹出提示；最后无论成功失败都重置导出中状态。setExporting 用于控制按钮 loading 状态。 验证：进入 web 目录执行 npm run build 无 TypeScript 错误；页面上点击导出按钮可成功下载 zip 文件。

3. P1 核心行为升级（共 4 项）
升级 1 · 眼球 IK 旋转器锚点质心化
文件：live2d_builder/bones/deformers.py，重点关注 DeformerHierarchy.DEFAULT_ROTATION_DEFORMERS（第372-377行，pivot 硬编码为 (-35,-40) 和 (35,-40)）和 DeformerHierarchy.build 方法（第383-420行）。 问题：Rotation Deformer 的 pivot 是硬编码常数，无视 centroids 算出的真实眼球质心，导致眼球会绕错误点旋转，出现"飞出"效果。 修复：第一步，在 build 方法签名中添加可选参数 centroids（类型为 Dict[str, Tuple[float, float]]，默认 None）；第二步，遍历 DEFAULT_ROTATION_DEFORMERS，对每个 rotation deformer 遍历其 targets，在 centroids 字典中做大小写不敏感的子串匹配，找到对应 eyeball 则用其质心坐标 (float(cx), float(cy)) 覆盖 pivot；第三步，在调用方 live2d_builder/pipeline.py 的 _setup_deformers 方法（第274-281行）把已计算好的 centroids 传入 self.deformers.build 调用。 验证：使用眼球偏离中心的测试图跑 RiggingPipeline，导出的 model3.json 中 EyeTrack 旋转器的 pivot 应等于 eyeball 图层的质心坐标，误差不超过 1 像素。

升级 2 · 桌宠情绪切换平滑 lerp 过渡
文件：drivers/desktop_pet/pet.py，重点关注 init 中 self._expression 和 self._expression_target（第148-150行）以及 run 主渲染循环。 问题：情绪切换直接替换 EXPRESSIONS 字典取值，硬跳变无过渡，像翻页一样生硬；前端 live2d-player.ts（第221-225行）已实现 lerp 平滑过渡，桌宠反而没有。 修复：第一步，在 init 中添加 self._expr_blend（float，初始 0.0）和 self._expr_from（str，初始 "normal"）；第二步，外部设置 self._expression_target 时，若与当前 self._expression 不同，则将 self._expr_from 设为当前 self._expression，self._expr_blend 重置为 0.0；第三步，在主渲染循环每帧加入混合逻辑：计算帧间隔 dt（1.0/self.fps），若 _expr_blend 小于 1.0 则按每秒 4.0 的速率递增（即约 0.25 秒完成过渡），然后取源表情和目标表情字典，对数值类型参数按 blend 比例线性插值，非数值类型在 blend 超过 0.5 时切换到目标值，最后将混合结果写入 ParamMouthOpenY、ParamEyeLOpen、ParamCheek 等参数；过渡完成后将 self._expression 设为目标值。严禁修改 EXPRESSIONS 字典结构。 验证：连续触发 happy→angry→shy→happy 情绪切换，参数值平滑变化，无 0→1 或 1→0 的瞬时跳变。

升级 3 · 桌宠自动边缘停靠 + 鼠标靠近弹出
文件：drivers/desktop_pet/pet.py（pygame 已在代码中 try-import）。 问题：窗口初始固定在 (100,100)，拖动后停留原地，无屏幕边缘自动停靠、无鼠标靠近时的互动反馈。 修复（全部使用 pygame 自带 API，无需新包）：第一步，在 init 添加 self._popout（float，初始 0.0）和 self._last_drag_time（float，初始 0.0）；第二步，新增两个私有方法：_screen_size 返回屏幕宽高（调用 pygame.display.Info() 取 current_w 和 current_h）；_update_edge_dock 计算窗口中心到屏幕四边的距离，找出最近边，非拖动中状态（距 last_drag_time 超过 0.5 秒）则以每帧移动剩余距离 20% 的速率 lerp 吸附到最近边（防止瞬移），同时用 pygame.mouse.get_pos() 计算鼠标到窗口中心的距离 d，self._popout 设为 max(0.0, 1.0 - d/220.0)，根据停靠边方向向屏幕内侧偏移 int(self._popout*80) 像素；第三步，在 run 主循环开头调用 _update_edge_dock()；第四步，在现有拖动事件处理中，按下和拖动时更新 self._last_drag_time 为当前 time.time()。 验证：拖动桌宠到屏幕右半部分松手，自动贴到右边缘；鼠标靠近时桌宠向鼠标方向伸出最多 80px；鼠标远离时缩回贴边。

升级 4 · 物理参数图层几何感知（短发硬长发软）
文件：live2d_builder/pipeline.py 中的 _generate_physics 方法（第299-315行）；live2d_builder/physics/config.py。 问题：build_hair_physics 和 build_skirt_physics 只接收图层名列表，不接收图层尺寸信息，齐耳短发和拖地长发使用同一套 stiffness（硬度）和 length（摆长）参数，物理表现不真实。 修复：第一步，在 _generate_physics 内为每个 hair/skirt 图层测量 alpha 非空区域的像素高度——将图层图片转为 RGBA 数组，找 alpha>32 的像素行，取最大行号减最小行号即为高度，若全透明则默认 80px；第二步，构造 hair_metrics 列表，每项为 (图层名, 像素高度) 的元组；第三步，检查 physics/config.py 中 build_hair_physics 和 build_skirt_physics 的方法签名，若不支持 metrics 参数则添加可选参数 metrics（类型 Optional[List[Tuple[str,int]]]，默认 None），为每个图层的 spring 按高度像素动态计算 stiffness：stiffness = max(15, 90 - height_px/6)（高度越高越软），length 按高度比例缩放；第四步，调用时传入 hair_metrics。 验证：短发（高度 <100px）的 stiffness 显著大于长发（高度 >400px），短发摆动更硬、长发摆动更柔软。

4. P2 前端假渲染 → Mesh 变形（共 4 项，含真校验）
升级 5 · Sprite → PlaneGeometry Mesh 替换
文件：web/lib/live2d-player.ts，重点关注 LayerEntry 接口、加载图层处（原 new PIXI.Sprite(tex)，约第112-155行）、update 方法每帧参数更新处（约第219-246行）。 问题：当前每个图层使用一个 PIXI.Sprite（四边形），参数只能施加 rotation/scale/skew 变换，无顶点级变形能力，导致张嘴靠 scale_y、转头靠 skew——这是假渲染的根源。 修复（使用 PixiJS 自带的 PIXI.PlaneGeometry 和 PIXI.Mesh，不引入新包）：第一步，修改 LayerEntry 接口，删除 sprite 字段，改为包含 name（string）、mesh（PIXI.Mesh<PIXI.PlaneGeometry, PIXI.MeshMaterial>）、baseX/baseY（number，记录初始位置）、basePositions（Float32Array，记录初始顶点位置）、partGroup（枚举值：'hair'|'face'|'eyes'|'mouth'|'body'|'other'）；第二步，图层加载处替换为创建 4×4 细分的 PlaneGeometry（宽高取纹理宽高，列数4、行数4），用该几何和 MeshMaterial（传入纹理）创建 Mesh，记录初始位置和初始顶点数组副本，调用 _classifyPartGroup 根据图层名分组，将 Mesh 添加到 stage；第三步，新增 _classifyPartGroup 方法：根据图层名小写子串分组——名称含 hair/bangs/ahoge 归为 hair，含 eye/iris/pupil 归为 eyes，含 mouth/lip/teeth 归为 mouth，含 face/cheek/nose 归为 face，含 body/chest/clothes 归为 body，其余归为 other；第四步，将代码中所有 layer.sprite 引用改为 layer.mesh。 验证：进入 web 目录执行 npm run build 无 TypeScript 错误；页面加载后视觉效果与原来基本一致（不出现图层错位）。

升级 6 · Mesh 级头部转动 Warp（替代整图 skew）
文件：web/lib/live2d-player.ts 的 update 方法。 修复：在每帧对每个 layer.mesh 施加顶点级变形。首先读取 ParamAngleX（范围 -30~30）和 ParamAngleY 参数值，获取该图层的 basePositions（初始顶点数组）和当前 geometry.positions，取纹理宽高 tw 和 th，根据图层索引计算 depthFactor（越深层越靠后，变形量越小）。然后遍历每个顶点（步长为2，每次处理 x,y 两个分量），根据顶点在网格中的列 col 和行 row 计算归一化坐标 u（横向0~1）和 vv（纵向0~1），计算 ox = angleX*(u-0.5)2.0depthFactor*(tw0.025)（横向偏移）、oy = angleY(vv-0.5)1.5depthFactor*(th*0.025)（纵向偏移），将偏移加到基础位置上写入 positions 数组。最后调用 geometry.getBuffer('aVertexPosition').update() 提交顶点数据更新。保留原有的位置 lerp 和呼吸摆动（作用于 layer.mesh.position），删除原来对 Sprite 的 skew 和 rotation 操作。 验证：在页面上拖动 Angle X 参数滑块从 -30 到 +30，脸部左右两侧顶点产生反向位移，呈现立体转头效果，不再是整张图的斜切变形。

升级 7 · 嘴型参数真驱动（非 scale_y）
文件：web/lib/live2d-player.ts 的 update 方法顶点循环内。 修复：在顶点变形循环中，对 partGroup 为 'mouth' 的图层追加嘴型变形逻辑。读取 ParamMouthOpenY（范围 0~1，嘴开合度）和 ParamMouthForm（范围 -1~1，嘴形，正值微笑、负值难过）。在遍历顶点时，对于下半部分顶点（vv>0.5），按嘴开合度向下拉伸：pos[v+1] += moth0.28*(vv-0.5)2；对于嘴角附近顶点（edge=min(u,1-u)<0.3 且 vv>0.3），按嘴形参数上下偏移：pos[v+1] -= mfth0.15(1-edge/0.3)（正值 mf 使嘴角上翘、负值使嘴角下垂）。eyes 分组可选择性做类似的开合变形，但嘴型是必做项。 验证：将 Mouth Open 参数调到 0.9，下半张嘴真实向下张开；将 Mouth Form 调到 +1，嘴角上翘呈现微笑，调到 -1，嘴角下垂呈现难过表情。

升级 8 · runValidation 从 mock 变真校验
文件：web/pages/live2d.tsx 中的 runValidation 函数（约第199-211行）。 问题：当前硬编码返回 5 条 mock issues，不做真实检查，校验面板永远显示同样内容。 修复：改为基于 canvasRef 的真实检查（不调用新的后端接口）。函数内首先获取 canvasRef.current，依次检查：图层加载数 layersLoaded（>0 显示 info 级，否则 error 级）、是否启用 Mesh 几何 hasMeshGeometry（true 显示 ENABLED/info，false 显示 LEGACY SPRITE/warn）、参数数量 paramsCount（≥20 显示 info，否则 warn）、Model3 元数据是否已加载 modelMeta（true 显示 Loaded/info，false 显示 warn）、Mesh 三角形数 triangleCount（Mesh模式下 >0 显示 info，否则 error；非Mesh模式显示 N/A/info），将这些检查结果组装为 issues 数组设置到 state。ModelCanvas 组件需通过 useImperativeHandle 对外暴露 layersLoaded、hasMeshGeometry、paramsCount、modelMeta、triangleCount 这几个 getter；若组件中已有类似字段则复用命名，不要硬造新字段。 验证：在 Sprite 旧模式下，"Mesh变形"校验项显示 warn；完成升级5替换为 Mesh 后显示 ENABLED，且三角形数 >0。

5. P3 高级特性（共 3 项，加分项，至少完成 2 项）
升级 9 · 顶点 Skinning Weight（让 Rigging 成真骨骼绑定）
文件：live2d_builder/mesh/generator.py 和 live2d_builder/pipeline.py。 问题：MeshGenerator 只输出 vertices 和 indices，BoneHierarchy 只输出 layer_assignments（图层→骨骼名映射），两者之间缺少顶点→骨骼的权重映射，导致导出的 model3.json 中 ArtMesh 没有 skinning 权重，在 Cubism Editor 中无法被骨骼真实驱动变形。 修复：第一步，在 MeshGenerator 中新增 compute_bone_weights 方法，接收 mesh 数据、bone_positions 字典、assigned_bone 名称、可选的 parent_bone 名称，返回包含 bone_names（骨骼名列表）和 weights（每顶点对应骨骼权重列表）的字典，每顶点权重和归一化为 1.0。算法采用反距离加权：取 assigned_bone 和 parent_bone（若存在于 bone_positions）作为影响骨骼，计算每个顶点到两根骨骼位置的欧氏距离，距离的倒数归一化后作为权重（距离越近权重越大）。第二步，在 pipeline.py 的 _generate_meshes 方法中，每个 mesh 生成后调用 compute_bone_weights，结果写入 meshes[name]["weights"]。第三步，修改 model3_exporter 导出 ArtMesh 时读取 mesh["weights"] 写入顶点权重字段；若现有导出方法不支持权重则扩展方法签名，保持向后兼容（无权重时输出原格式）。 验证：导出的 model3.json 中顶点权重数组长度等于顶点数，每个顶点的权重和约等于 1.0（浮点误差范围内）。

升级 10 · 情绪 Top2 混合输出
文件：llm_bridge/emotion/analyzer.py 中 EmotionAnalyzer.analyze 方法（约第337-355行）。 问题：当前使用 max(scores, key=scores.get) 取唯一主导情绪，丢弃了其他 6 类情绪的得分，无法表达"70%开心+30%害羞"这类混合情绪，情绪表现单一。 修复：第一步，保留现有返回字段 emotion、confidence、expression 不变（确保向后兼容）；第二步，新增返回字段 secondary_emotion（str，第二情绪名）、secondary_confidence（float，第二情绪置信度）、mixed_physics（Dict[str,float]，混合后物理参数）；第三步，实现 Top2 混合逻辑：将情绪得分为按值降序排序，取第一名 e1/s1 和第二名 e2/s2（若只有一类则第二名为自身、得分为0），按得分比例计算 blend 权重（w1=s1/(s1+s2)），分别取两类情绪对应的 EMOTION_TO_PHYSICS 参数，对数值类型按 blend 比例线性插值，非数值类型在 blend>0.5 时取第一情绪值否则取第二情绪值，结果存入 mixed_physics；第四步，下游 pet.py 和 live2d-player.ts 若读到 mixed_physics 字段则优先使用混合参数，否则 fallback 到原来的单情绪参数逻辑。 验证：输入文本"嘿嘿被你夸得好害羞"，返回的 mixed_physics 中 mouth_scale_y 应介于 happy 的 1.15 和 shy 的 0.85 之间，blush 值介于 0.6~0.8 之间。

升级 11 · 音频节拍驱动身体摆动（音乐跳舞）
文件：drivers/desktop_pet/pet.py（项目已有 numpy 和 drivers/audio/capture.py 中的 AudioCapture，无需新包）。 问题：IDLE 模式下的 body_swing 是固定频率和幅度的 sin 波，与实际播放的音频无关，桌宠不会随音乐跳舞。 修复：第一步，在 init 添加 self._beat_intensity（float，初始 0.0）和 self._bass_ma（float，初始 1.0）；第二步，新增 _audio_beat_pulse 方法，返回 float 类型的节奏强度乘数：若无 audio_capture 或未启用 lip-sync 则直接返回 1.0（无音乐时正常摆动）；否则取最新音频 chunk，施加 hanning 窗后做 rfft，取 30-200Hz 低频段能量均值作为 bass 值，用指数移动平均更新 self._bass_ma（新值权重 0.1，旧值权重 0.9），当 bass > 1.8*bass_ma 时判定为鼓点 onset，将 self._beat_intensity 设为 1.0，否则每帧 self._beat_intensity 乘以 0.92 自然衰减，最终返回 1.0 + self._beat_intensity（范围 1.0 无鼓点 ~ 2.0 最强鼓点）；第三步，在 IDLE 模式（非 tracking 状态）下，将 body_swing 和 breath 的摆幅乘以该返回值，实现音乐节拍驱动摆幅变化。 验证：播放鼓点明显的音乐时，桌宠身体摆动幅度随鼓点显著增大；静音或无音乐时摆幅恢复正常水平。

6. 验收命令
总验收命令（最终必须全部通过）：bash /workspace/run_all_tests.sh。

分项验收命令：

Python 测试：进入 /workspace 目录执行 python -m pytest tests/unit/test_emotion.py tests/unit/test_live2d_builder.py tests/unit/test_segment_engine.py tests/integration/test_workflow_contract.py -v
Go 编译和测试：进入 /workspace/api 目录执行 go build ./... && go test ./... -v
前端构建：进入 /workspace/web 目录执行 npm run build
最终交付标准（按优先级排序）：

【一票否决】端到端全链路跑通：能从提示词输入完成到 model3.json 导出，全程无未捕获异常（允许降级兜底输出可用结果）；API 启动后 /api/generate 接口走完全流程返回 model_dir；桌宠可启动（无摄像头则进入 IDLE 模式不崩溃）；Web 端可预览模型。
bash run_all_tests.sh 退出码为 0。
cd web && npm run build 构建成功。
cd api && go build ./... 无编译错误。
6 个 P0 Bug 全部修复。
P1 四项核心行为升级全部完成。
P2 四项 Mesh/校验升级全部完成（嘴型可真实张开和微笑）。
（加分项）P3 三项高级特性至少完成 2 项。