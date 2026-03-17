"""
Text Summarizer Skill
~~~~~~~~~~~~~~~~~~~~~

这是一个演示用的文本摘要技能，遵循代码规范。
"""


from .config import DEFAULT_MAX_LENGTH

class Summarizer:
    """
    文本摘要类，提供基础的文本处理逻辑。
    """

    def __init__(self, max_length: int = DEFAULT_MAX_LENGTH):
        """
        初始化摘要器。

        :param max_length: 摘要的最大长度
        """
        self.max_length = max_length

    def summarize(self, text: str) -> str:
        """
        生成文本摘要。

        :param text: 输入的长文本
        :return: 摘要文本
        """
        if not text:
            return ""

        # 演示逻辑：简单截断
        if len(text) <= self.max_length:
            return text

        return text[: self.max_length] + "..."


def main():
    # 示例调用
    sample_text = (
        "Webeye CSBU 致力于通过 AI 技术提升业务效率。这个技能库是我们的技能沉淀基础。"
    )
    summarizer = Summarizer(max_length=20)
    result = summarizer.summarize(sample_text)
    print(f"Original: {sample_text}")
    print(f"Summary: {result}")


if __name__ == "__main__":
    main()
