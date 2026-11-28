# Copy to env.sh (ignored by git), fill in, and `. ./env.sh` before run.sh. Keys never go in the repo.
# One OpenAI-compatible provider serves every model role here: the tools' own LLM and embedder
# (read by the harness adapters and the containers) and the reader (read by extbench).
export UPSTAGE_API_KEY=
export OPENAI_API_KEY=$UPSTAGE_API_KEY OPENAI_BASE_URL=https://api.upstage.ai/v1
export HARNESS_LLM_MODEL=solar-pro4 HARNESS_EMBEDDING_MODEL=solar-embedding-2-passage HARNESS_EMBEDDING_DIMS=1024
export EXTBENCH_BASE_URL=$OPENAI_BASE_URL EXTBENCH_API_KEY=$UPSTAGE_API_KEY
# Optional: a local llama.cpp embedding server for the engine arms' index (see README, Arms).
# export EXTBENCH_EMBED_URL=http://127.0.0.1:18081/v1 EXTBENCH_EMBED_MODEL=Qwen3-Embedding-4B-Q4_K_M.gguf
# Optional: a judge on another OpenAI-compatible server than the reader (falls back to EXTBENCH_*).
# export EXTBENCH_JUDGE_BASE_URL=http://127.0.0.1:8081/v1 EXTBENCH_JUDGE_API_KEY=local
