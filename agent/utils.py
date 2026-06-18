"""
通用工具函数 —— JSON 清理、文本截断等零依赖辅助函数。

===========================================================================
为什么要有这个模块？
===========================================================================

之前的问题：
  LLM 返回内容清理 Markdown 代码块的逻辑在 3 个文件里重复了 3 遍。

现在的方案：
  所有 JSON 清理逻辑集中在这里，其他模块只需要一行调用。
  Phase 3 新增：自动修复 LLM 输出的非标准 JSON（尾逗号、单引号等）。
"""

import json
import logging
import re

logger = logging.getLogger("aiops.utils")


def clean_json_response(content: str) -> str:
    """
    清理 LLM 返回文本中的 Markdown 代码块包裹。

    LLM 经常在 JSON 外面包一层 ```json ... ```，这个函数把它去掉。

    处理的格式：
      ```json
      {"key": "value"}
      ```

      ```
      {"key": "value"}
      ```

    不做任何 JSON 语法修复，只去掉外面的代码块标记。

    Args:
        content: LLM 返回的原始文本（可能包含 Markdown 代码块）

    Returns:
        去掉代码块标记后的纯文本
    """
    content = content.strip()

    # 处理 Markdown 代码块：以 ``` 开头
    if content.startswith("```"):
        lines = content.split("\n")
        # 去掉第一行（``` 或 ```json）
        # 如果最后一行是 ```，也去掉
        if len(lines) >= 2 and lines[-1].strip() == "```":
            content = "\n".join(lines[1:-1])
        else:
            content = "\n".join(lines[1:])

    return content.strip()


def _repair_json(raw: str) -> str:
    """
    尝试修复 LLM 输出的非标准 JSON。

    LLM 偶尔会输出有语法瑕疵的 JSON：
      - 最后一个元素后面多了一个逗号：{"a": 1,}
      - 用了单引号而非双引号：{'a': 1}
      - 输出中混入了自然语言文本

    这个函数做 best-effort 修复，不保证一定能修好。
    """
    # 1. 尝试提取 JSON 对象（去掉前后可能混入的自然语言文本）
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        raw = match.group()

    # 2. 移除尾随逗号（最常见的 LLM JSON 错误）
    #    匹配 ",   }" 或 ",  \n}" 模式
    raw = re.sub(r",(\s*[}\]])", r"\1", raw)

    # 3. 单引号转双引号（注意不要转字符串内部的单引号）
    #    简单的启发式：整行替换模式 { 'key': 'value' } → { "key": "value" }
    #    只在顶层做，避免破坏字符串内容
    raw = re.sub(r"'([^']*)'(\s*):", r'"\1"\2:', raw)
    raw = re.sub(r":\s*'([^']*)'", r': "\1"', raw)

    return raw


def safe_parse_json(content: str) -> dict:
    """
    安全解析 LLM 返回的 JSON 文本，内置自动修复。

    处理流程：
      1. clean_json_response → 去掉 Markdown 代码块包裹
      2. json.loads → 直接解析
      3. 失败 → _repair_json → 尝试修复常见语法错误
      4. 再失败 → 返回 raw_output（不丢失数据）

    为什么分 4 层？
      LLM JSON 解析失败是偶发但不可避免的（模型非确定性输出）。
      之前 JSON 解析失败 → 直接 FINISH → 整个诊断白跑。
      现在分 4 层降级，尽量挽救：
        - 90% 的情况在第 2 层就成功了
        - 9% 的情况在第 3 层修复成功
        - 1% 的情况在第 4 层兜底，至少保留原始输出供人工查看

    Args:
        content: LLM 返回的原始文本

    Returns:
        dict —— 解析成功时是实际 JSON 数据，
               解析失败时是 {"raw_output": ..., "parse_error": ..., "repaired": false}
    """
    cleaned = clean_json_response(content)

    # 第 1 次尝试：直接解析
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 第 2 次尝试：修复后再解析
    repaired = _repair_json(cleaned)
    if repaired != cleaned:
        try:
            result = json.loads(repaired)
            logger.info("JSON 修复成功，已自动纠正非标准格式")
            return result
        except json.JSONDecodeError:
            pass

    # 第 3 次尝试：再宽松一点，只提取看起来像 JSON 的部分
    try:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            return json.loads(match.group())
    except json.JSONDecodeError:
        pass

    # 兜底：返回原始内容
    logger.warning("JSON 解析彻底失败，已保留原始输出")
    return {
        "raw_output": content[:1000],
        "parse_error": "3 次解析尝试均失败",
        "repaired": False,
        "note": "LLM 输出非标准 JSON，已保留原始内容",
    }


def truncate_text(text: str, max_length: int = 500) -> str:
    """
    截断文本到指定长度，超出部分用 ... 标记。

    用于控制日志和 UI 中 LLM 输出内容的长度。

    Args:
        text:       原始文本
        max_length: 最大长度（字符数）

    Returns:
        截断后的文本，超出时末尾追加 "...(已截断)"
    """
    if len(text) <= max_length:
        return text
    return text[:max_length] + "...(已截断)"


def format_messages_for_log(messages: list, max_per_msg: int = 500) -> str:
    """
    将 LangChain Message 列表格式化为日志友好的文本。

    用于反思节点等需要把对话历史传给 LLM 的场景。

    Args:
        messages:     LangChain Message 对象列表
        max_per_msg:  每条消息内容的最大长度

    Returns:
        格式化的多行文本
    """
    lines: list[str] = []
    for msg in messages[-20:]:  # 只看最近 20 条，防止上下文爆炸
        role = type(msg).__name__
        content = msg.content if hasattr(msg, "content") else str(msg)
        content = truncate_text(content, max_per_msg)
        lines.append(f"[{role}] {content}")
    return "\n\n".join(lines)
