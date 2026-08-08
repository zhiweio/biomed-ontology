#!/usr/bin/env sh
set -eu

if [ ! -d /app/resources ] || [ -z "$(ls -A /app/resources 2>/dev/null || true)" ]; then
  echo "BERN2 resources 未挂载或为空。"
  echo "请先：task foundation:bern2:fetch"
  echo "或设置 BERN2_RESOURCES 指向已解压的 resources/ 目录。"
  exit 1
fi

if [ ! -f /app/server.py ]; then
  echo "BERN2 源码缺失（期望 /app/server.py）。请先 task foundation:bern2:fetch"
  exit 1
fi

# 无 GPU 时仍尝试启动（极慢）；有 CUDA 时由宿主机传入 NVIDIA_VISIBLE_DEVICES
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-}"

mkdir -p /app/logs

# 精简启动：走官方 scripts 逻辑的容器内同步版（前台跑 server，便于 compose 托管）
cd /app

# 后台起依赖服务（与 scripts/run_bern2.sh 对齐，去掉 nohup/tail）
python multi_ner/ner_server.py --mtner_home multi_ner --mtner_port 18894 \
  >> logs/nohup_multi_ner.out 2>&1 &

if [ -f resources/GNormPlusJava/GNormPlusServer.main.jar ]; then
  (cd resources/GNormPlusJava && java -Xmx8G -Xms4G -jar GNormPlusServer.main.jar 18895 \
    >> ../../logs/nohup_gnormplus.out 2>&1 &)
fi
if [ -f resources/tmVarJava/tmVar2Server.main.jar ]; then
  (cd resources/tmVarJava && java -Xmx4G -Xms2G -jar tmVar2Server.main.jar 18896 \
    >> ../../logs/nohup_tmvar.out 2>&1 &)
fi

# 等 NER 端口
for i in 1 2 3 4 5 6 7 8 9 10 12 15 20 30; do
  if curl -sf "http://127.0.0.1:18894/" >/dev/null 2>&1 || true; then
    break
  fi
  sleep 2
done

exec python -u server.py \
  --mtner_home ./multi_ner \
  --mtner_port 18894 \
  --gnormplus_home ./resources/GNormPlusJava \
  --gnormplus_port 18895 \
  --tmvar2_home ./resources/tmVarJava \
  --tmvar2_port 18896 \
  --gene_norm_port 18888 \
  --disease_norm_port 18892 \
  --use_neural_normalizer \
  --port 8888
