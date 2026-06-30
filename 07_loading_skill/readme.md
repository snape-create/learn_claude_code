### 
- 你的项目有一套 React 组件规范、一份 SQL 风格指南、一份 API 设计文档。你希望 Agent 自动遵守这些规范。最直接的想法，全塞进 system prompt：

### 工作原理
**skills/ 目录**，每个技能一个子目录，包含 `SKILL.md` 文件：
```skills/
  agent-builder/SKILL.md
  code-review/SKILL.md
  mcp-builder/SKILL.md
  pdf/SKILL.md```
  hello
