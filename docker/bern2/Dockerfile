# BERN2 本地服务（需 ~70GB resources + 建议 GPU）
# 构建前：task foundation:bern2:fetch（克隆源码并下载 resources）
# 启动：task foundation:up:bern2
FROM python:3.9-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl git default-jre-headless build-essential \
    && rm -rf /var/lib/apt/lists/*

# 源码由 fetch 放入 docker/bern2/upstream
COPY upstream/ /app/
RUN if [ -f /app/requirements.txt ]; then pip install --no-cache-dir -r /app/requirements.txt; fi

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EXPOSE 8888
# NER/normalizer 内部端口（同一容器内）
EXPOSE 18888 18892 18894 18895 18896
ENTRYPOINT ["/entrypoint.sh"]
