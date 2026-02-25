.PHONY: install install-backend install-frontend build clean dev dev-backend dev-frontend run check otel-jaeger otel-jaeger-stop help

# ---------- 安装依赖 ----------

install: install-backend install-frontend  ## 安装全部依赖

install-backend:  ## 安装后端 Python 依赖
	pip install -r requirements.txt

install-frontend:  ## 安装前端 npm 依赖
	cd frontend && npm install

# ---------- 构建 ----------

build:  ## 构建前端生产包
	cd frontend && npm run build

clean:  ## 清理前端构建产物
	rm -rf frontend/dist

# ---------- 开发模式（前后端分离，各自热重载） ----------

dev: dev-backend  ## 同时启动前后端开发服务（后台）+ 前端 dev server
	@cd frontend && npm run dev

dev-backend:  ## 启动后端开发服务（热重载，后台运行）
	@echo "Starting backend (dev mode)..."
	@python server.py --reload &

dev-frontend:  ## 单独启动前端 dev server
	cd frontend && npm run dev


otel-jaeger:  ## 启动 Jaeger all-in-one（Docker，UI: http://localhost:16686）
	@docker run -d --name jaeger \
		-p 16686:16686 \
		-p 4317:4317 \
		-p 4318:4318 \
		jaegertracing/all-in-one:latest
	@echo "Jaeger UI: http://localhost:16686"

otel-jaeger-stop:  ## 停止并移除 Jaeger 容器
	@docker rm -f jaeger 2>/dev/null || true

# ---------- 生产模式 ----------

run: build  ## 构建前端并启动服务（生产模式）
	python server.py --reload

# ---------- 检查 ----------

check:  ## 前端类型检查 + lint
	cd frontend && npx tsc --noEmit
	cd frontend && npm run lint

help:  ## 显示帮助
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
