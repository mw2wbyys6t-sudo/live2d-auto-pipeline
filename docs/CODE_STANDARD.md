# 📏 代码规范

## Python
- 全部添加类型注解
- 函数必须有 Docstring（Google 风格）
- 使用 `core.logger.get_logger()` 而非 print
- 可选依赖必须 try/except 优雅降级
- 遵循 PEP 8，行宽 100

## Go
- 结构体、函数、核心逻辑逐行注释（中文）
- 错误处理使用 if err != nil
- Handler 不直接写业务逻辑，委托给 Service

## TypeScript
- 严格 TypeScript（no any）
- 组件使用函数式 + Hooks
- 所有 API 响应有 interface 定义

## 测试
- Python: pytest，覆盖率目标 ≥80%
- Go: go test
- 前端: npm run build 类型检查
