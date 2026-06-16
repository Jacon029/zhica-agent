"""
Prompt 加载工具

所有 AI 功能相关的系统提示词、用户提示词模板统一存放在 src/prompts/ 目录下。
通过此模块的 load_prompt() 函数动态读取，禁止在业务代码中硬编码长提示词。
"""

from pathlib import Path

# prompts 目录的绝对路径
PROMPTS_DIR = Path(__file__).parent.absolute()


def load_prompt(filename: str, **kwargs) -> str:
    """
    从 src/prompts/ 目录加载 prompt 模板文件

    Args:
        filename: prompt 文件名（如 "demand_structuring_system.md"）
        **kwargs: 模板变量，用于替换模板中的 {key} 占位符

    Returns:
        渲染后的 prompt 字符串

    Example:
        prompt = load_prompt("demand_structuring_system.md")
        prompt = load_prompt("web_search_product_extraction.md",
                             context="采购需求上下文",
                             results="搜索结果列表")
    """
    file_path = PROMPTS_DIR / filename

    if not file_path.exists():
        raise FileNotFoundError(f"Prompt 文件不存在: {file_path}")

    template = file_path.read_text(encoding="utf-8")

    # 模板变量替换
    if kwargs:
        for key, value in kwargs.items():
            placeholder = "{" + key + "}"
            template = template.replace(placeholder, str(value))

    return template.strip()
