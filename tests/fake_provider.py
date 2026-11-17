"""A deterministic HTTP test double. It never contacts a real model service."""
import copy
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from backend.sample import DEMO_GUIDE


class FakeProvider:
    def __init__(self):
        self.mode = "normal"
        self.calls = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                owner.calls.append({"path": self.path, "payload": payload, "authorization": self.headers.get("Authorization")})
                if owner.mode == "unauthorized":
                    self.send_response(401)
                    self.end_headers()
                    return
                if owner.mode == "rate_limited":
                    self.send_response(429)
                    self.end_headers()
                    return
                messages = payload["messages"]
                system = messages[0].get("content", "")
                if "JSON Schema" in system:
                    guide = copy.deepcopy(DEMO_GUIDE)
                    request = json.loads(messages[1]["content"])
                    refs = re.findall(r"\[((?:p\d+|note)-s\d+)\]", request["material"])
                    for point in guide["knowledge_points"]:
                        point["source_ids"] = list(dict.fromkeys(refs)) if refs else []
                    if owner.mode == "html_content":
                        guide["overview"] = '<img src=x onerror="window.__injected=1"> 这只是模型返回的文字。'
                        guide["knowledge_points"][0]["title"] = '<svg onload="window.__injected=1">'
                        guide["knowledge_points"][0]["explanation"] = '<script>window.__injected=1</script>'
                        guide["quiz"][0]["options"][0] = '<img src=x onerror="window.__injected=1">'
                    if owner.mode == "bad_citations":
                        guide["knowledge_points"][0]["source_ids"] = ["fabricated-page-999"]
                    if owner.mode == "blank_steps" or (owner.mode == "repair_blank_steps" and len(messages) == 2):
                        guide["worked_examples"][0]["steps"] = ["   "]
                    content = json.dumps(guide, ensure_ascii=False)
                    if owner.mode == "bad_json":
                        content = "this is not json"
                elif "提取教材知识结构" in system:
                    content = "梳理提纲：" + messages[-1]["content"][:2000]
                elif "文字识别助手" in system:
                    content = "网络层负责将分组从源主机传递到目的主机。路由器根据目的 IP 地址进行最长前缀匹配和分组转发。"
                elif "连接成功" in messages[-1]["content"]:
                    content = "连接成功"
                else:
                    content = '<img src=x onerror="window.__injected=1"> [note-s1]' if owner.mode == "html_content" else "这是测试服务生成的讲解，用于验证追问和保存流程。IP 提供尽力而为的服务，不保证可靠交付。"
                result = {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "length" if owner.mode == "truncated" else "stop"}]}
                data = json.dumps(result, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}/v1"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
