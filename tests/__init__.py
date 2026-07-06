import warnings

# 测试运行时只保留和本项目无关的 Pydantic 噪声过滤；LangChain warning 不再隐藏。
warnings.filterwarnings("ignore", message=".*ArbitraryTypeWarning.*", module="pydantic")
