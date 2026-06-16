"""
MCP (Model Context Protocol) Stdio 客户端
为智采-Agent 提供与 MCP 服务器通信的通用适配层

用法:
    from utils.mcp_client import McpClient, BochaSearchMCP, PricePeekMCP

    # 使用封装好的搜索
    with BochaSearchMCP() as bocha:
        results = bocha.search("线缆 1688", count=10)

    # 使用 PricePeek
    with PricePeekMCP() as pp:
        prices = pp.search_products("iPhone 15", platforms=["taobao", "jd"])
        lowest = pp.get_lowest_price("Dyson V15")
"""
import json
import subprocess
import time
import threading
from typing import Optional, Any


# ─── 基础 MCP Stdio 客户端 ───

class McpClient:
    """
    通用 MCP Stdio 客户端（JSON-RPC 2.0）

    通过 stdio 与 MCP 服务器进程通信。
    支持 Windows（自动处理 .cmd 包装）和 Unix。
    """

    def __init__(self, command: list[str], name: str = "mcp"):
        self.command = command
        self.name = name
        self.process: Optional[subprocess.Popen] = None
        self._request_id = 0
        self._lock = threading.Lock()
        self._initialized = False
        self._stderr_thread = None
        self._env = None  # 自定义环境变量（由子类设置）

    def _read_stderr(self):
        """后台线程读取 stderr，防止管道阻塞"""
        try:
            for line in self.process.stderr:
                pass  # 静默消费 stderr
        except Exception:
            pass

    def start(self) -> bool:
        """启动 MCP 服务器进程并初始化"""
        try:
            # 使用列表形式启动（不用 shell），node 进程支持良好
            creationflags = 0
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                creationflags = subprocess.CREATE_NO_WINDOW

            self.process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",  # MCP 协议使用 UTF-8，Windows 默认 GBK 会乱码
                errors="replace",  # 防止个别字节解码失败导致管道阻塞
                env=self._env,     # 传递自定义环境变量（如 API Key）
                creationflags=creationflags,
            )

            # 启动后台线程消费 stderr
            self._stderr_thread = threading.Thread(
                target=self._read_stderr, daemon=True
            )
            self._stderr_thread.start()

            # 发送 initialize 请求
            init_result = self._send_request("initialize", {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "zhica-agent", "version": "1.0.0"},
            }, timeout=15)
            if init_result is None:
                print(f"[MCP:{self.name}] initialize 失败")
                return False
            self._initialized = True
            print(f"[MCP:{self.name}] 已连接")
            return True
        except FileNotFoundError as e:
            print(f"[MCP:{self.name}] 命令未找到: {e}")
            return False
        except Exception as e:
            print(f"[MCP:{self.name}] 启动失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def list_tools(self) -> list[dict]:
        """列出所有可用工具"""
        result = self._send_request("tools/list", {})
        if result and "tools" in result:
            return result["tools"]
        return []

    def call_tool(self, name: str, arguments: dict = None) -> Optional[dict]:
        """
        调用 MCP 工具

        Returns:
            工具的返回内容（已解析为 dict），失败返回 None
        """
        result = self._send_request("tools/call", {
            "name": name,
            "arguments": arguments or {},
        })
        if result is None:
            return None

        # 检查错误
        if "isError" in result and result["isError"]:
            print(f"[MCP:{self.name}] 工具错误: {result}")
            return None

        # 提取 content（MCP 返回格式）
        content = result.get("content", [])
        if isinstance(content, list) and len(content) > 0:
            first = content[0]
            if isinstance(first, dict) and "text" in first:
                text = first["text"]
                # 尝试解析 JSON
                try:
                    return json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    return {"text": text}

        return result

    def _read_response(self, timeout: float = 60) -> Optional[str]:
        """
        从 stdout 读取一行 JSON 响应，带超时。

        readline() 在 Node.js 管道 stdout 上可能永久阻塞，
        所以放在后台线程读取，主线程等待超时。
        """
        from queue import Queue, Empty

        q: Queue = Queue()

        def _read_thread():
            try:
                line = self.process.stdout.readline()
                q.put(line.strip() if line else None)
            except Exception as e:
                q.put(None)

        t = threading.Thread(target=_read_thread, daemon=True)
        t.start()
        t.join(timeout=timeout)

        try:
            result = q.get_nowait()
            if result is None or result == '':
                # 检查进程是否已退出
                if self.process and self.process.poll() is not None:
                    print(f"[MCP:{self.name}] 进程已退出 (code={self.process.returncode})")
                    try:
                        stderr_data = self.process.stderr.read()
                        if stderr_data and stderr_data.strip():
                            print(f"[MCP:{self.name}] stderr: {stderr_data.strip()[:500]}")
                    except Exception:
                        pass
                return None
            return result
        except Empty:
            if self.process and self.process.poll() is not None:
                print(f"[MCP:{self.name}] 进程已退出 (code={self.process.returncode})")
            return None

    def _send_request(self, method: str, params: dict, timeout: float = 30) -> Optional[dict]:
        """发送 JSON-RPC 请求并等待响应"""
        if self.process is None or self.process.poll() is not None:
            return None

        with self._lock:
            self._request_id += 1
            request_id = self._request_id

        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }

        try:
            request_str = json.dumps(request, ensure_ascii=False) + "\n"
            self.process.stdin.write(request_str)
            self.process.stdin.flush()

            # 带超时读取响应
            response_line = self._read_response(timeout)
            if response_line is None:
                # 检查进程是否已退出
                if self.process.poll() is not None:
                    print(f"[MCP:{self.name}] 进程已退出 (code={self.process.returncode})")
                else:
                    print(f"[MCP:{self.name}] 无响应 (method={method})")
                return None

            response = json.loads(response_line)
            if "error" in response:
                print(f"[MCP:{self.name}] 错误: {response['error']}")
                return None

            return response.get("result")
        except (BrokenPipeError, OSError) as e:
            print(f"[MCP:{self.name}] 通信断开: {e}")
            return None
        except Exception as e:
            print(f"[MCP:{self.name}] 请求异常 ({method}): {e}")
            return None

    def close(self):
        """关闭 MCP 服务器进程"""
        if self.process:
            try:
                self.process.stdin.close()
                self.process.stdout.close()
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None
            self._initialized = False

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.close()

    @property
    def connected(self) -> bool:
        return self._initialized and self.process is not None and self.process.poll() is None


# ─── Bocha 搜索 MCP 封装 ───

class BochaSearchMCP:
    """
    Bocha 搜索引擎 MCP 客户端

    封装 web_search 工具调用
    """

    # 使用 node 直接运行（跳过 npx，避免首次下载延迟和 shell 兼容问题）
    BASE_COMMAND = [
        "node",
        "D:/Node.js/node_global/node_modules/@chenpu17/web-bridge-mcp/dist/cli.js",
        "--web-search", "bocha",
        "--ignore-ssl",
    ]

    def __init__(self, api_key: str = None):
        # 构建带 env 的命令配置
        from config import BOCHA_API_KEY
        self.api_key = api_key or BOCHA_API_KEY
        self.client = McpClient(
            self.BASE_COMMAND,
            name="bocha-search",
        )
        self._cache = {}

    def start(self) -> bool:
        """启动并传递 API Key 环境变量"""
        if not self.api_key:
            print("[BochaMCP] 缺少 BOCHA_API_KEY，跳过")
            return False

        # 将 API Key 注入子进程环境
        import os
        env = os.environ.copy()
        env["BOCHA_API_KEY"] = self.api_key
        self.client._env = env
        return self.client.start()

    def start(self) -> bool:
        return self.client.start()

    def search(self, query: str, count: int = 10) -> list[dict]:
        """
        执行网页搜索

        Args:
            query: 搜索关键词
            count: 返回结果数量

        Returns:
            [{"title": str, "url": str, "summary": str, "site_name": str}, ...]
        """
        # 缓存检查
        cache_key = f"{query}:{count}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        if not self.client.connected:
            print("[BochaMCP] 未连接，返回空")
            return []

        try:
            result = self.client.call_tool("web_search", {
                "query": query,
                "numResults": count,
            })

            if result is None:
                return []

            # ── 解析 MCP 返回的各种格式 ──
            items = []

            if isinstance(result, dict):
                # 格式 0: 简单文本消息（如 "No search results found"）
                text_msg = result.get("text", "")
                if text_msg and not isinstance(text_msg, str):
                    text_msg = str(text_msg)
                if text_msg and "No search results" in text_msg:
                    print(f"[BochaMCP] 无搜索结果: {text_msg}")
                    return []
                if text_msg and text_msg not in ("",) and not result.get("results") and not result.get("data"):
                    # 尝试从 text 解析 JSON 数组
                    try:
                        parsed = json.loads(text_msg)
                        if isinstance(parsed, list):
                            items = parsed
                    except (json.JSONDecodeError, TypeError):
                        pass

                # 格式 1: {"results": [...]}
                if not items:
                    raw = result.get("results", result.get("data", result.get("webPages", {})))
                    if isinstance(raw, list):
                        items = raw
                    elif isinstance(raw, dict):
                        items = raw.get("value", [])
                    # 格式 2: 直接嵌套在某个 key 下
                    if not items:
                        for key in ["webPages", "pages", "items", "entries"]:
                            val = result.get(key)
                            if isinstance(val, list):
                                items = val
                                break
                            elif isinstance(val, dict) and "value" in val:
                                items = val["value"]
                                break
            elif isinstance(result, list):
                items = result

            # 标准化格式
            standardized = []
            for item in items:
                if isinstance(item, dict):
                    standardized.append({
                        "title": item.get("title", item.get("name", "")),
                        "url": item.get("url", ""),
                        "summary": item.get("summary", item.get("snippet", item.get("description", ""))),
                        "site_name": item.get("site_name", item.get("siteName", item.get("source", ""))),
                    })

            self._cache[cache_key] = standardized
            return standardized

        except Exception as e:
            print(f"[BochaMCP] 搜索异常: {e}")
            return []

    def close(self):
        self.client.close()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.close()


# ─── PricePeek 电商比价 MCP 封装 ───

class PricePeekMCP:
    """
    PricePeek 电商比价 MCP 客户端

    封装 search_products, get_lowest_price, get_affiliate_link 工具
    """

    # 使用本地已编译的版本（npm 原版有 bug）
    COMMAND = [
        "node",
        "D:/Node.js/node_global/node_modules/mcp-pricepeek/dist/index.js"
    ]

    def __init__(self):
        self.client = McpClient(self.COMMAND, name="pricepeek")
        self._cache = {}

    def start(self) -> bool:
        return self.client.start()

    def search_products(
        self,
        query: str,
        platforms: list[str] = None,
        limit: int = 10,
    ) -> dict:
        """
        跨电商平台搜索商品价格

        Args:
            query: 商品搜索关键词
            platforms: 平台列表 ["taobao", "jd", "pinduoduo", "all"]
            limit: 每平台最大结果数

        Returns:
            {"success": bool, "query": str, "results_count": int, "data": [...]}
        """
        cache_key = f"pp_search:{query}:{str(platforms)}:{limit}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        if not self.client.connected:
            print("[PricePeekMCP] 未连接")
            return {"success": False, "query": query, "results_count": 0, "data": [], "error": "MCP not connected"}

        try:
            result = self.client.call_tool("search_products", {
                "query": query,
                "platforms": platforms or ["all"],
                "limit": limit,
            })

            if result is None:
                return {"success": False, "query": query, "results_count": 0, "data": []}

            # 标准化
            if isinstance(result, dict):
                data = result.get("data", result.get("results", []))
                if not isinstance(data, list):
                    data = [result] if result.get("title") else []
                return {
                    "success": result.get("success", True),
                    "query": result.get("query", query),
                    "results_count": len(data),
                    "data": data,
                }

            return {"success": False, "query": query, "results_count": 0, "data": []}

        except Exception as e:
            print(f"[PricePeekMCP] 搜索异常: {e}")
            return {"success": False, "query": query, "results_count": 0, "data": [], "error": str(e)}

    def get_lowest_price(self, product_name: str) -> Optional[dict]:
        """
        获取指定商品在各平台的最低价格

        Returns:
            {"product_name": str, "lowest": {"title": str, "price": float, "platform": str, "url": str}} or None
        """
        if not self.client.connected:
            return None

        try:
            result = self.client.call_tool("get_lowest_price", {
                "product_name": product_name,
            })
            if result and isinstance(result, dict):
                return result
            return None
        except Exception as e:
            print(f"[PricePeekMCP] 最低价查询异常: {e}")
            return None

    def get_affiliate_link(self, product_url: str, platform: str = "taobao") -> Optional[dict]:
        """
        获取商品返利链接

        Returns:
            {"success": bool, "affiliate_url": str, "commission": float} or None
        """
        if not self.client.connected:
            return None

        try:
            result = self.client.call_tool("get_affiliate_link", {
                "product_url": product_url,
                "platform": platform,
            })
            return result
        except Exception as e:
            print(f"[PricePeekMCP] 返利链接异常: {e}")
            return None

    def close(self):
        self.client.close()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.close()


# ─── 工厂函数 ───

def create_bocha_search() -> BochaSearchMCP:
    """创建 Bocha 搜索 MCP 客户端"""
    return BochaSearchMCP()


def create_pricepeek() -> PricePeekMCP:
    """创建 PricePeek 电商比价 MCP 客户端"""
    return PricePeekMCP()


# ─── 自测 ───

if __name__ == "__main__":
    print("=== Bocha 搜索 MCP 测试 ===")
    with BochaSearchMCP() as bocha:
        results = bocha.search("线缆 1688", count=5)
        print(f"返回 {len(results)} 条结果")
        for r in results[:3]:
            print(f"  - {r['title'][:60]}")
            print(f"    {r['url'][:80]}")

    print("\n=== PricePeek MCP 测试 ===")
    with PricePeekMCP() as pp:
        tools = pp.client.list_tools()
        print(f"可用工具: {[t.get('name') for t in tools]}")

        result = pp.search_products("线缆 6平方", platforms=["all"], limit=5)
        print(f"搜索 '{result.get('query')}' 返回 {result.get('results_count', 0)} 条")
        for item in result.get("data", [])[:3]:
            print(f"  - {item.get('title', 'N/A')} | ¥{item.get('price', 'N/A')} | {item.get('platform', 'N/A')}")
