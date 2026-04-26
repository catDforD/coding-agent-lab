# Claude Code Phase 5 失败样例记录

这份记录用于落实 [claude-code-todo.md](./claude-code-todo.md) 里 `Phase 5. 验证最小闭环` 的最后一项：至少保留一个可复现的失败样例，并明确说明问题属于哪一层。

当前先记录一个最稳定、最容易重复验证的样例。它不是“模型没做好”，而是当前 cleanroom 复现故意保留的控制层边界，正好对应《[claude-code-study.md](./claude-code-study.md)》里的 `5.5 Safety / Boundaries` 和 `8. 评测、效果与局限`。

## 样例 1：`bash` 被 denylist 拦截，循环在 act 阶段停住

- 分类：权限
- 对应学习文档章节：
  - `4. 核心运行循环`
  - `5.5 Safety / Boundaries`
  - `8. 评测、效果与局限`
- 对应代码链：
  - CLI 参数
  - `permission_rules.load_permission_rules`
  - `permissions.InteractivePermissionGate.confirm_tool_use`
  - `tools.execute_named_tool`
  - `tool_result.status=denied`
  - `runtime.verify`

### 复现目的

这个样例用来证明：当前最小闭环里，一次任务失败不一定是检索错了、上下文丢了，或者工具输出不够；也可能是控制层按设计提前拦截了高风险工具。

### 复现条件

workspace 下存在 `.claude-code/permission-rules.json`，内容如下：

```json
{
  "bash": {
    "denylist": ["printf"]
  }
}
```

然后运行：

```bash
cd reproductions/claude-code
CLAUDE_CODE_STATE_DIR=/tmp/cc-phase5-state \
CLAUDE_CODE_WORKSPACE_ROOT=/tmp/cc-phase5-workspace \
CLAUDE_CODE_ENV_FILE=/tmp/cc-phase5-state/test.env \
CLAUDE_CODE_PERMISSION_RULES=/tmp/cc-phase5-workspace/.claude-code/permission-rules.json \
python -m claude_code --tool-direct bash "printf ready"
```

### 观察结果

本地复现时，CLI 会进入下面这条链：

```text
user_message(bash printf ready)
-> tool_call(bash)
-> tool_result(status=denied)
-> model_response(解释为什么没执行)
-> verify_status=loop-needs-attention
```

一次实际输出如下：

```text
executed_tools: bash
verify_status: loop-needs-attention
assistant_response:
工具 `bash` 未执行：denylist matched `printf` for `bash` input `printf ready`。当前最小 control layer 会在这里停止，不继续触发真实写入或命令执行。
```

对应 session 事件里，`tool_result` 会带上结构化权限信息：

```json
{
  "tool_name": "bash",
  "status": "denied",
  "tool_output": {
    "permission": {
      "status": "denied",
      "reason": "denylist matched `printf` for `bash` input `printf ready`",
      "source": "denylist"
    }
  }
}
```

### 为什么把它归类为“权限问题”

- 不是检索问题：这条链没有走 `search` 或 `read_file`。
- 不是上下文问题：任务在真正执行命令前就被规则命中，不依赖 prompt packing 或历史摘要。
- 不是工具反馈不足：相反，这里的工具反馈是足够清晰的，已经把 `denied`、`reason`、`source=denylist` 写回了统一事件流。

所以这个失败样例的根因很明确：控制层主动阻止了命令执行，闭环因此停在 `act -> verify` 之间。

### 当前结论

这个失败样例是“按当前设计预期失败”，不是 bug。它说明当前 cleanroom 复现已经具备最小控制层边界：

- 能在真实执行前拦住受控工具。
- 能把失败原因结构化写回 session。
- 能让 `verify` 阶段把这次任务归类成 `loop-needs-attention`，而不是伪装成成功完成。

这也解释了当前 Phase 5 的边界：我们已经能验证“最小闭环通不通”，但还没有实现 Claude Code 更完整的恢复体验，比如 plan mode、自动改计划、hooks 派生处理或更细的 remediation 建议。

### 当前仓库里的对应验证

- 行为验证：
  - [test_cli.py](../../reproductions/claude-code/tests/test_cli.py)
  - `CliEntryTest.test_bash_tool_can_auto_deny_via_permission_denylist`
- 闭环主验证：
  - [test_phase5_validation.py](../../reproductions/claude-code/tests/test_phase5_validation.py)
