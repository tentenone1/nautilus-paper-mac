#!/usr/bin/env python3
"""OpenAI-to-Ollama proxy for LLM scoring.

Listens on 8080, accepts OpenAI /v1/chat/completions requests,
forwards to Ollama /api/chat, returns OpenAI-format response.

Usage: python3 ollama_proxy.py [port] [model]
Default model: qwen3.5:9b
"""
import json
import sys
import threading
import urllib.request
import urllib.error

BIND_PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
OLLAMA_MODEL = sys.argv[2] if len(sys.argv) > 2 else "qwen3.5:9b"
OLLAMA_BASE = "http://127.0.0.1:11434"

def translate_request(openai_data: dict) -> dict:
    """Convert OpenAI chat completions format to Ollama /api/chat format."""
    messages = openai_data.get("messages", [])
    ollama_messages = []
    for msg in messages:
        ollama_messages.append({
            "role": msg.get("role", "user"),
            "content": msg.get("content", ""),
        })
    return {
        "model": OLLAMA_MODEL,
        "messages": ollama_messages,
        "stream": False,
    }


def translate_response(ollama_resp: dict) -> dict:
    """Convert Ollama /api/chat response to OpenAI chat completions format."""
    content = ""
    if isinstance(ollama_resp, dict):
        # Ollama /api/chat returns {"message": {"role": ..., "content": ...}}
        msg = ollama_resp.get("message", {})
        if isinstance(msg, dict):
            content = msg.get("content", "")
        else:
            content = str(msg)
    elif isinstance(ollama_resp, str):
        content = ollama_resp

    return {
        "id": "chatcmpl-proxy",
        "object": "chat.completion",
        "created": 0,
        "model": OLLAMA_MODEL,
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": content,
            },
            "finish_reason": "stop",
        }],
    }


def handler(sock, client_addr):
    """Handle a single proxy connection."""
    try:
        # Read the HTTP request
        request = b""
        while b"\r\n\r\n" not in request:
            request += sock.recv(1)
        header = request.decode("utf-8", errors="replace")
        lines = header.split("\r\n")
        content_length = 0
        for line in lines:
            if line.lower().startswith("content-length:"):
                content_length = int(line.split(":", 1)[1].strip())
        body = b""
        while len(body) < content_length:
            body += sock.recv(content_length - len(body))
        openai_data = json.loads(body.decode("utf-8"))

        # Translate and forward to Ollama /api/chat (native, not /v1)
        ollama_req = translate_request(openai_data)
        req = urllib.request.Request(
            f"{OLLAMA_BASE}/api/chat",
            data=json.dumps(ollama_req).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                ollama_resp = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            ollama_resp = {"error": f"HTTP {e.code}: {e.reason}"}
        except Exception as e:
            ollama_resp = {"error": str(e)}

        response_data = translate_response(ollama_resp)
        response_body = json.dumps(response_data).encode()

        response = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: " + str(len(response_body)).encode() + b"\r\n"
            b"Access-Control-Allow-Origin: *\r\n"
            b"\r\n"
        ) + response_body

        sock.sendall(response)
    except Exception as e:
        print(f"Proxy error: {e}")
        try:
            err = json.dumps({"error": str(e)}).encode()
            sock.sendall(
                b"HTTP/1.1 500 OK\r\nContent-Length: " + str(len(err)).encode() + b"\r\n\r\n" + err
            )
        except:
            pass
    finally:
        sock.close()

def main():
    import socket
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", BIND_PORT))
    server.listen(10)
    print(f"OpenAI→Ollama proxy listening on 127.0.0.1:{BIND_PORT}")
    print(f"Model: {OLLAMA_MODEL} → Ollama at {OLLAMA_BASE}")
    print(f"Forward to: http://127.0.0.1:{BIND_PORT}/v1/chat/completions")
    while True:
        conn, addr = server.accept()
        t = threading.Thread(target=handler, args=(conn, addr), daemon=True)
        t.start()

if __name__ == "__main__":
    main()
