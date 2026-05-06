import os

# Dual-endpoint config: 5900x LM Studio or 1700 llama-server
USE_5900X = os.environ.get('USE_5900X', 'false').lower() == 'true'

if USE_5900X:
    # 5900x LM Studio - higher max_tokens for reasoning overhead
    LLM_URL = "http://192.168.50.148:1234/v1/chat/completions"
    LLM_MODEL = "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"  # 16GB VRAM, IQ2_M
    MAX_TOKENS = 2500  # Reasoning + JSON output
else:
    # 1700 llama-server - direct JSON output, no reasoning overhead
    LLM_URL = "http://127.0.0.1:8080/v1/chat/completions"
    LLM_MODEL = "Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-IQ2_M.gguf"
    MAX_TOKENS = 800  # Sufficient for JSON-only output
