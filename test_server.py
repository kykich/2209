from mcp.server.mcpserver import MCPServer

# MCPServer — высокоуровневая обёртка над MCP: декоратор @tool
# сам генерирует JSON-схему инструмента и регистрирует его.
mcp = MCPServer("demo-server")


@mcp.tool()
def add(a: int, b: int) -> int:
    """Сложить два целых числа."""
    return a + b


@mcp.tool()
def multiply(a: int, b: int) -> int:
    """Умножить два целых числа."""
    return a * b


@mcp.tool()
def echo(text: str) -> str:
    """Вернуть переданный текст без изменений."""
    return text


if __name__ == "__main__":
    # Транспорт по умолчанию — stdio.
    mcp.run()
